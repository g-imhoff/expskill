import base64
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


runner = module(ROOT / "scripts/live_skill_builder_workflows.py", "public_workflow_runner_test")
driver = module(ROOT / "scripts/live_trials.py", "public_workflow_native_driver_test")
probe = module(ROOT / "scripts/live_skill_builder_benchmark.py", "public_workflow_probe_test")
fixtures = module(ROOT / "tests/test_skill_builder_state.py", "public_workflow_contract_fixtures")


class PublicCLI:
    def __init__(self, workflow, role):
        self.workflow = workflow
        self.role = role
        self.commands = []
        self.helper = fixtures.load_helper()

    def __getattr__(self, name):
        operations = {"initialize_run": "initialize", "load_run": "load", "retain_artifact": "retain",
                      "transition_run": "transition", "invalidate_run": "invalidate", "finalize_run": "finalize"}
        if name not in operations:
            return getattr(self.helper, name)

        def operation(**payload):
            state_root = payload.pop("state_root", self.workflow.state_root)
            if name == "retain_artifact":
                payload["files_base64"] = {key: base64.b64encode(value).decode() for key, value in payload.pop("files").items()}
            result = driver.run_process_group([sys.executable, str(self.workflow.helper), operations[name], "--state-root", str(state_root)],
                                              prompt=runner.canonical(payload).decode(), cwd=self.workflow.workspace,
                                              env=None, timeout=60)
            command = shlex.join([sys.executable, str(self.workflow.helper), operations[name], "--state-root", str(state_root)])
            self.commands.append({"type": "item.completed", "item": {"type": "command_execution", "command": command, **result}})
            if result["exit_code"]:
                raise self.helper.RunStateError(result["stderr"])
            return json.loads(result["stdout"])

        return operation


def offline_workflow(tmp_path, mode="create", transport=None, budget=None):
    definition = json.loads(runner.FIXTURE.read_text())
    definition["cases"] = [dict(case, case_id=f"case-{index}", partition=partition)
                           for index, (case, partition) in enumerate(zip(definition["cases"][:3],
                           ("visible_development", "frozen_validation", "hidden_release")), start=1)]
    definition["cases"][2]["requests"] = ["Use $count-lines on {target}/input.txt."]
    evidence = runner.save(tmp_path / "synthetic.json", definition)
    base = ROOT / "plugins/expskill/content/skills/skill-builder"
    sources = {name: {"path": str(base / path), "sha256": runner.digest((base / path).read_bytes())} for name, path in runner.SOURCES.items()}
    return runner.Workflow(driver=transport or driver, probe=probe, root=tmp_path / mode, helper=base / "scripts/run_state.py",
                           sources=sources, definition=definition, definition_evidence=evidence, mode=mode,
                           budget=budget or runner.Budget(64, 240, 30), cli="unused-no-model")


class ScriptedTransport:
    run_process_group = staticmethod(driver.run_process_group)
    workflow = None

    def run_actor(self, *, prompt, cwd, state_home, evidence_dir, timeout, sandbox, live, cli, infrastructure_retries,
                  model=None, persistent=False, resume_from=None):
        if not hasattr(self, "observed_calls"):
            self.observed_calls = []
        self.observed_calls.append({"prompt": prompt, "timeout": timeout, "infrastructure_retries": infrastructure_retries,
                                    "role": Path(evidence_dir).name, "model": model})
        workflow = self.workflow
        role = Path(evidence_dir).name
        public = PublicCLI(workflow, role)
        events = [{"type": "thread.started", "thread_id": workflow.mode + ":scripted:" + role}]
        if re.match(r"trial-\d+-", role):
            package = Path(workflow.accepted("candidate-record")[3]["isolated_locator"])
            case_id = role.rsplit("-", 1)[0].split("-", 2)[2]
            case = next(case for case in workflow.cases if case["case_id"] == case_id)
            argv = [sys.executable, str(package / "scripts/count_lines.py"), str(Path(case["target"]) / "input.txt")]
            result = driver.run_process_group(argv, prompt="", cwd=cwd, env=None, timeout=10)
            events += [{"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(["cat", str(package / "SKILL.md")]), "exit_code": 0}},
                       {"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(argv), **result}}]
            reply = result["stdout"].strip()
        else:
            for source in workflow.role_sources(role).values():
                events.append({"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(["cat", source["path"]]), "exit_code": 0}})
            before = set(workflow.current["artifact_index"]) if workflow.current else set()
            if role.startswith("research-"):
                path = Path(cwd) / "offline-synthetic-research.json"
                runner.save(path, {"origin": "scripted offline fixture; no live research", "question": role})
                events.append({"type": "item.completed", "item": {"type": "web_search", "origin": "scripted offline fixture"}})
                reply = json.dumps({"workflow_id": None, "stage": "research-only", "artifact_ids": [str(path)]})
            else:
                self.advance(public, role)
                current = public.load_run(workflow_id=workflow.workflow_id)
                reply = json.dumps({"workflow_id": workflow.workflow_id, "stage": current["stage"], "artifact_ids": sorted(set(current["artifact_index"]) - before)})
                events += public.commands
        events.append({"type": "item.completed", "item": {"type": "agent_message", "text": reply}})
        evidence_dir = Path(evidence_dir)
        evidence_dir.mkdir(parents=True)
        raw = evidence_dir / "offline-scripted-events.jsonl"
        raw.write_bytes(b"".join(runner.canonical(event) for event in events))
        thread = events[0]["thread_id"]
        return {"outcome": "completed-ungraded", "final_text": reply, "thread_ids": [thread], "selected_attempt": 1,
                "attempts": [{"raw_events": str(raw), "raw_events_sha256": runner.digest(raw.read_bytes()), "thread_ids": [thread], "exit_code": 0, "timed_out": False}],
                "transport": {"context": {"cwd": str(cwd)}}}

    def advance(self, public, role):
        workflow = self.workflow
        state = workflow.state_root
        identity = workflow.mode + ":" + role

        def current():
            return public.load_run(workflow_id=workflow.workflow_id)

        def retained(kind, payload, name, bindings=None, files=None):
            if "reviewer_identity" in payload:
                payload["reviewer_identity"] = identity
            if "verifier_identity" in payload:
                payload["verifier_identity"] = identity
            if files is None:
                result = fixtures.retain_json(public, state_root=state, workflow_id=workflow.workflow_id,
                         sequence=current()["head_sequence"], artifact_id=name, artifact_type=kind, payload=payload,
                         input_bindings=bindings if bindings is not None else fixtures.current_bindings(public, state, workflow.workflow_id),
                         retain_review_source_components=True, producer=identity)
            else:
                result = public.retain_artifact(workflow_id=workflow.workflow_id, expected_sequence=current()["head_sequence"],
                         artifact_id=name, artifact_type=kind, files={"record.json": runner.canonical(payload), **files}, primary_path="record.json",
                         producer=identity, input_bindings=fixtures.current_bindings(public, state, workflow.workflow_id), limitations=[])
            return result

        def transition(event, stage, ids, **extra):
            public.transition_run(workflow_id=workflow.workflow_id, expected_sequence=current()["head_sequence"], event=event,
                                  destination_stage=stage, artifact_ids=ids, **extra)

        if role == "resolve-baseline":
            payload = {"host_identity": fixtures.host_identity(workflow.root), "target_identity": fixtures.target_identity(workflow.target, "count-lines"),
                       "mode": workflow.mode, "authority": {**fixtures.authority(), "delivery_effects": []}, "owner_identity": identity, "git_identity": {"present": False}}
            if workflow.mode == "create":
                payload.update(absence_evidence={"searched": [str(workflow.target)], "exists": False}, overlap_map={"exact": [], "near_neighbours": []})
            else:
                payload["target_manifest"] = workflow.public("snapshot", {"target": str(workflow.target)})
            workflow.workflow_id = public.initialize_run(**payload)["workflow_id"]
            snapshot = current()["target_snapshot"]
            baseline = {"schema_version": "skill-builder-baseline.v1", "mode": workflow.mode, "target_snapshot_digest": snapshot["snapshot_digest"],
                        "absent_target_proof": {key: snapshot[key] for key in ("absence_evidence_digest", "overlap_map_digest")} if workflow.mode == "create" else None,
                        "host_conventions": [], "preserved_regressions": [], "raw_evidence_digests": [], "limitations": ["offline mechanical fixture"]}
            retained("baseline-report", baseline, "baseline")
            transition("capture-baseline", "baseline", ["baseline"])
        elif role in {"normalize-research", "sieve-evidence", "challenge-design"}:
            name, kind, event = {"normalize-research": ("research", "research-pack", "complete-research"),
                                 "sieve-evidence": ("sieve", "evidence-sieve", "sieve-evidence"),
                                 "challenge-design": ("design", "design-record", "accept-design")}[role]
            retained(kind, fixtures.stage_payload(public, state, workflow.workflow_id, kind), name)
            transition(event, name, [name])
        elif role == "author-contract":
            contract = retained("skill-contract", fixtures.contract_payload(public, state, workflow.workflow_id), "contract")
            transition("accept-contract", "contract", ["contract"])
        elif role == "confirm-contract":
            retained("user-confirmation-record", fixtures.confirmation_payload(contract_id="contract", contract_digest=current()["artifact_index"]["contract"]["digest"],
                     target=current()["target_identity"]["canonical"], snapshot_digest=current()["target_snapshot"]["snapshot_digest"], authority_digest="2" * 64), "confirmation")
            transition("confirm-contract", "confirmed", ["confirmation"], authority_event_digest="2" * 64)
        elif role == "freeze-evaluation":
            value = current()
            payload = fixtures.evaluation_payload(contract_digest=value["artifact_index"]["contract"]["digest"],
                       confirmation_digest=value["artifact_index"]["confirmation"]["digest"], snapshot_digest=value["target_snapshot"]["snapshot_digest"])
            for cases in payload["partitions"].values():
                for case in cases:
                    actual = next(value for value in workflow.cases if value["case_id"] == case["case_id"])
                    case.update(raw_request_digest=actual["request"]["sha256"], setup_manifest_digest=actual["setup_digest"])
            retained("evaluation-pack", payload, "evaluation")
            transition("freeze-evaluation", "evaluation", ["evaluation"])
        elif role == "candidate-author" or role.startswith("invalidate-repair-"):
            repair = role != "candidate-author"
            if repair:
                old = workflow.accepted("candidate-record")[0]["artifact_id"] if "artifact_id" in workflow.accepted("candidate-record")[0] else "candidate"
                public.invalidate_run(workflow_id=workflow.workflow_id, expected_sequence=current()["head_sequence"], change_kind="candidate", changed_artifact_id=old, reason="real offline command returned the wrong count")
            value = current()
            self.revision = "candidate-2" if repair else "candidate-1"
            self.candidate_name = "candidate-repaired" if repair else "candidate"
            helper = "import json\nfrom pathlib import Path\nimport sys\nprint(json.dumps({'line_count': len(Path(sys.argv[1]).read_text().splitlines())" + ("" if repair else " + 1") + "}))\n"
            payload = fixtures.candidate_payload(tmp_path=Path(workflow.root), candidate_id=self.candidate_name, revision=self.revision,
                      contract_digest=value["artifact_index"]["contract"]["digest"], confirmation_digest=value["artifact_index"]["confirmation"]["digest"],
                      evaluation_digest=value["artifact_index"]["evaluation"]["digest"], snapshot_digest=value["target_snapshot"]["snapshot_digest"],
                      additional_skill_files={"scripts/count_lines.py": helper.encode()}, owned_paths=["SKILL.md", "scripts/count_lines.py"])
            retained("candidate-record", payload, self.candidate_name)
            transition("accept-candidate", "candidate", [self.candidate_name])
        elif role.startswith("trial-assessment-"):
            cycle = int(role.rsplit("-", 1)[1])
            packet = json.loads((workflow.root / f"trial-cycle-{cycle}.json").read_text())
            payload = fixtures.trial_payload(current()["artifact_index"][self.candidate_name]["digest"], revision=self.revision)
            candidate = workflow.accepted("candidate-record")[3]
            files = {}
            names = {"request_digest": "request", "raw_prompt_digest": "prompt", "tool_event_digest": "tool-events", "output_digest": "output",
                     "before_target_manifest_digest": "before-manifest", "after_target_manifest_digest": "after-manifest", "filesystem_result_digest": "filesystem-result"}
            for case, observed in zip(payload["cases"], packet["observations"], strict=True):
                case["fresh_context_identity"] = observed["fresh_context_identity"]
                case["loaded_skill_digest"] = candidate["loaded_skill_digest"]
                case["case_digest"] = runner.digest(Path(observed["case_path"]).read_bytes())
                files[f"evidence/{case['case_id']}/case.json"] = Path(observed["case_path"]).read_bytes()
                for field, name in names.items():
                    case[field] = observed["evidence"][name]["sha256"]
                    files[f"evidence/{case['case_id']}/{name}.bin"] = Path(observed["evidence"][name]["path"]).read_bytes()
                for path in Path(observed["loaded_skill_path"]).rglob("*"):
                    if path.is_file():
                        files[f"evidence/{case['case_id']}/loaded-skill/{path.relative_to(observed['loaded_skill_path']).as_posix()}"] = path.read_bytes()
                case["verdict"] = "fail" if cycle == 0 else "pass"
            payload["status"] = "fail" if cycle == 0 else "pass"
            files["evidence/aggregate-manifest.json"] = runner.canonical({"case_ids": payload["coverage"]})
            name = "trials" if cycle == 0 else "trials-repaired"
            retained("trial-pack", payload, name, files=files)
            transition("complete-trials", "trials", [name])
        elif role.startswith("pre-review-") or role.startswith("final-review-"):
            final = role.startswith("final-review-")
            cycle = int(role.rsplit("-", 1)[1])
            if final:
                provenance, bindings = fixtures.final_review_inputs(public, state, workflow.workflow_id)
            else:
                provenance, bindings = fixtures.review_input_provenance(public, state, workflow.workflow_id), fixtures.review_envelope_bindings(public, state, workflow.workflow_id)
            self.findings = [] if cycle else [{"severity": "medium", "release_blocking": False, "evidence": [fixtures.fixture_digest(fixtures.REVIEW_FINDING_BYTES)],
                             "impact": "actual command returns one extra line", "correction": "repair helper", "affected_target_criteria": ["TR1"]}]
            payload = fixtures.review_payload(current()["artifact_index"][self.candidate_name]["digest"], revision=self.revision,
                      findings=self.findings, schema_version="skill-builder-review.v3" if final else "skill-builder-review.v2", input_artifacts=provenance)
            payload["verdict"] = "not ready" if self.findings else "ready"
            self.review_name = "post-score-review" if final else "final-review" if cycle == 0 else "pre-review-repaired"
            retained("review-record", payload, self.review_name, bindings=bindings)
            transition("accept-final-review" if final else "accept-review", "final-reviewed" if final else "reviewed", [self.review_name])
        elif role.startswith("scoring-"):
            cycle = int(role.rsplit("-", 1)[1])
            value = current()
            candidate_digest = value["artifact_index"][self.candidate_name]["digest"]
            conformance = "conformance" if cycle == 0 else "conformance-repaired"
            retained("builder-run-conformance-ledger", fixtures.conformance_payload(candidate_digest), conformance)
            overrides = {"triggering": {key: key != "TR1" for key in fixtures.SCORE_CRITERIA["triggering"]}} if not cycle else None
            score = fixtures.scorecard_payload(candidate_digest=candidate_digest, evaluation_digest=value["artifact_index"]["evaluation"]["digest"],
                    review_digest=value["artifact_index"][self.review_name]["digest"], revision=self.revision, triggering_score=10 if cycle else 9,
                    criteria_overrides=overrides, review_findings=self.findings, trial_cases=workflow.accepted("trial-pack")[3]["cases"])
            for category in score["categories"]:
                for criterion in category["criteria"].values():
                    criterion["review_artifact_id"] = self.review_name
            scores = "scores" if cycle == 0 else "scores-repaired"
            retained("target-scorecard", score, scores)
            transition("accept-scores", "scored", [conformance, scores])
        elif role.startswith("verification-"):
            payload = fixtures.verification_payload(current()["artifact_index"][self.candidate_name]["digest"], revision=self.revision)
            package = Path(workflow.accepted("candidate-record")[3]["isolated_locator"])
            argv = [sys.executable, str(package / "scripts/count_lines.py"), str(Path(workflow.cases[0]["target"]) / "input.txt")]
            result = driver.run_process_group(argv, prompt="", cwd=workflow.workspace, env=None, timeout=10)
            public.commands.append({"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(argv), **result}})
            payload["commands"] = [{"command": shlex.join(argv), "exit_status": result["exit_code"], "output_digest": runner.digest(result["stdout"].encode())}]
            retained("verification-record", payload, "verification", files={"evidence/command-output.bin": result["stdout"].encode(),
                     "evidence/target-manifest.bin": fixtures.VERIFICATION_MANIFEST_BYTES})
            transition("accept-verification", "verified", ["verification"])
        elif role.startswith("finalize-"):
            value = current()
            payload = fixtures.release_payload(target=value["target_identity"]["canonical"], candidate_digest=value["artifact_index"][self.candidate_name]["digest"],
                      contract_digest=value["artifact_index"]["contract"]["digest"], confirmation_digest=value["artifact_index"]["confirmation"]["digest"],
                      evaluation_digest=value["artifact_index"]["evaluation"]["digest"], conformance_digest=value["artifact_index"]["conformance-repaired"]["digest"],
                      scorecard_digest=value["artifact_index"]["scores-repaired"]["digest"], review_digest=value["artifact_index"]["post-score-review"]["digest"],
                      verification_digest=value["artifact_index"]["verification"]["digest"], authorized_delivery_scope=[])
            payload["candidate_revision"] = self.revision
            retained("release-record", payload, "release")
            public.finalize_run(workflow_id=workflow.workflow_id, expected_sequence=current()["head_sequence"], release_artifact_id="release")
        else:
            raise AssertionError(role)


@pytest.mark.parametrize("mode", ["create", "improve"])
def test_scripted_transport_runs_real_public_repair_and_final_gates(tmp_path, mode):
    transport = ScriptedTransport()
    workflow = offline_workflow(tmp_path, mode, transport)
    transport.workflow = workflow
    result = workflow.run(1)
    assert result["outcome"] == "public-workflow-finalized", result.get("failure")
    assert result["repair_demonstrated"]
    assert result["full_builder_conformance_claim"] is False
    assert result["cycles"][0]["negative_review"]
    assert sum(len(category["criteria"]) for category in result["cycles"][1]["scores"]["categories"]) == 100
    assert workflow.refresh()["artifact_index"]["candidate"]["derived_status"] == "superseded"
    assert workflow.refresh()["artifact_index"]["scores"]["derived_status"] == "invalidated"
    assert workflow.refresh()["stage"] == "finalized"


def test_candidate_and_blind_research_packets_exclude_frozen_cases(tmp_path):
    workflow = offline_workflow(tmp_path)
    candidate = json.loads(Path(workflow.actor_packet("candidate-author")["path"]).read_text())
    research = json.loads(Path(workflow.actor_packet("research-0")["path"]).read_text())
    assert [case["case_id"] for case in candidate["cases"]] == ["case-1"]
    assert "cases" not in research and "actor_evidence_root" not in research
    assert set(research) == {"role", "stage", "blind", "question", "maximum_primary_sources", "source_contract"}
    assert research["blind"] is True
    assert research["stage"] == "research-only"
    assert "public-state" not in json.dumps(research)
    assert "synthetic-user-decisions" not in json.dumps(research)
    decisions = json.loads(Path(candidate["synthetic_decisions"]["path"]).read_text())
    assert "cases" not in decisions


def test_public_gate_failure_is_retained_and_cannot_advance(tmp_path):
    workflow = offline_workflow(tmp_path)
    with pytest.raises(runner.WorkflowError, match="Public transition gate failed"):
        workflow.public("transition", {"workflow_id": "nonexistent", "expected_sequence": 0, "event": "accept-scores", "destination_stage": "scored", "artifact_ids": []})
    record = json.loads(Path(workflow.observation["public_observations"][-1]["evidence"]["path"]).read_text())
    assert record["exit_code"] != 0 and record["stderr"]


def test_budget_and_explicit_opt_in_reject_before_transport(tmp_path):
    with pytest.raises(runner.WorkflowError, match="opt-in"):
        runner.run_workflows(output_root=tmp_path / "never", framework_revision="0" * 40)
    with pytest.raises(runner.WorkflowError, match="ceilings"):
        runner.run_workflows(output_root=tmp_path / "never", framework_revision="0" * 40, live=True, maximum_actor_attempts=129)
    with pytest.raises(runner.WorkflowError, match="hard ceilings"):
        runner.run_workflows(output_root=tmp_path / "never", framework_revision="0" * 40, live=True, research_timeout=481)
    assert not (tmp_path / "never").exists()


def test_role_source_requirements_preserve_owner_and_judge_reads_without_leaf_rubric(tmp_path):
    workflow = offline_workflow(tmp_path)
    for role in ("resolve-baseline", "normalize-research", "sieve-evidence", "challenge-design", "author-contract", "confirm-contract", "candidate-author", "trial-assessment-0", "verification-1", "finalize-1"):
        assert set(workflow.role_sources(role)) == {"skill", "contracts"}
    for role in ("freeze-evaluation", "pre-review-0", "scoring-0", "final-review-1", "invalidate-repair-1"):
        assert set(workflow.role_sources(role)) == {"skill", "contracts", "rubric"}
    research = workflow.role_sources("research-1")
    assert set(research) == {"skill", "research-contracts"}
    excerpt = research["research-contracts"]
    parent = workflow.sources["contracts"]
    assert excerpt["parent"] == parent
    lines = Path(parent["path"]).read_bytes().splitlines(keepends=True)
    sections = excerpt["sections"]
    assert [section["heading"] for section in sections] == ["## Canonical digest serialization", "### Research pack"]
    selected = [b"".join(lines[section["start_line"] - 1:section["end_line"]]) for section in sections]
    assert [runner.digest(content) for content in selected] == [section["sha256"] for section in sections]
    assert Path(excerpt["path"]).read_bytes() == b"\n".join(selected)
    assert runner.digest(Path(excerpt["path"]).read_bytes()) == excerpt["sha256"]


def test_blind_research_native_trace_rejects_actual_shared_load_from_v2():
    value = json.loads((ROOT / "tests/fixtures/skill-builder/native-research-state-read-v2.json").read_text())
    with pytest.raises(runner.WorkflowError, match="blind research"):
        runner.require_blind_research_origin({"events": value["events"]}, helper=Path(value["helper"]),
                                            state_root=Path(value["state_root"]), root=Path(value["root"]),
                                            scratch=Path(value["scratch"]), sources={})


@pytest.mark.parametrize("command", ["cat ../research-0/cards.json", "ls ..", "cat /private/workflow/actors/research-0/reply.json"])
def test_blind_research_native_trace_rejects_parent_and_sibling_reads(command):
    origin = {"events": [{"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": 0}}]}
    with pytest.raises(runner.WorkflowError, match="blind research"):
        runner.require_blind_research_origin(origin, helper=Path("/framework/helper.py"), state_root=Path("/private/workflow/actor-state/public-state"),
                                            root=Path("/private/workflow"), scratch=Path("/private/workflow/actor-workspaces/research-1"), sources={})


def test_blind_research_native_trace_accepts_its_pinned_sources_and_owned_cards(tmp_path):
    scratch = tmp_path / "workflow/actor-workspaces/research-1"
    scratch.mkdir(parents=True)
    source = tmp_path / "framework/source"
    source.parent.mkdir()
    source.write_text("complete bounded source")
    commands = [shlex.join(["cat", str(source)]), "python3 - <<'PY'\nfrom pathlib import Path\nPath('cards.json').write_text('{\"hostile_example\": \"../outside/input\", \"reference_path\": \"/etc/passwd\"}')\nPY", "shasum -a 256 cards.json"]
    origin = {"events": [{"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": 0}} for command in commands]}
    runner.require_blind_research_origin(origin, helper=tmp_path / "framework/helper.py", state_root=tmp_path / "workflow/actor-state/public-state",
                                        root=tmp_path / "workflow", scratch=scratch, sources={"bounded": {"path": str(source)}})


def test_research_prompt_caps_and_actual_scoped_reads_keep_owner_state_unpublished(tmp_path):
    transport = ScriptedTransport()
    budget = runner.Budget(96, 7200, 240, 480)
    workflow = offline_workflow(tmp_path, transport=transport, budget=budget)
    transport.workflow = workflow
    workflow.stage("resolve-baseline", "Capture the baseline.", "baseline", ("initialize", "retain", "transition"))
    before = workflow.public("load", {"workflow_id": workflow.workflow_id})
    actor, origin, reply = workflow.actor("research-0", "Return only independently researched evidence cards.", model="gpt-5.6-luna")
    call = transport.observed_calls[-1]
    assert call["timeout"] == 480
    assert transport.observed_calls[0]["timeout"] == 240
    assert call["infrastructure_retries"] == 1
    assert str(workflow.helper) not in call["prompt"]
    assert str(workflow.state_root) not in call["prompt"]
    assert str(workflow.decisions["path"]) not in call["prompt"]
    assert workflow.sources["rubric"]["path"] not in call["prompt"]
    assert workflow.sources["contracts"]["path"] not in call["prompt"]
    assert sys.executable in call["prompt"]
    assert "workflow_id=null" in call["prompt"]
    assert reply["workflow_id"] is None and reply["stage"] == "research-only"
    assert workflow.public("load", {"workflow_id": workflow.workflow_id}) == before
    assert not (workflow.root / "research-actors.json").exists()
    assert budget.calls == 2
    assert workflow.observation["actors"][-1]["stage_timeout_seconds"] == 480
    assert workflow.observation["actors"][-1]["effective_attempt_timeout_seconds"] == 480
    probe.require_framework_reads(origin, workflow.role_sources("research-0"))


def test_contract_stages_have_fresh_producers_one_current_artifact_and_public_checkpoints(tmp_path):
    transport = ScriptedTransport()
    workflow = offline_workflow(tmp_path, transport=transport)
    transport.workflow = workflow
    workflow.stage("resolve-baseline", "Capture baseline.", "baseline", ("initialize", "retain", "transition"))
    stages = (("normalize-research", "research", "research-pack"), ("sieve-evidence", "sieve", "evidence-sieve"),
              ("challenge-design", "design", "design-record"), ("author-contract", "contract", "skill-contract"),
              ("confirm-contract", "confirmed", "user-confirmation-record"))
    for role, stage, kind in stages:
        previous_ids = set(workflow.current["artifact_index"])
        workflow.stage(role, "Perform only the assigned public stage.", stage, ("retain", "transition"), expected_artifact_types=(kind,))
        new_ids = set(workflow.current["artifact_index"]) - previous_ids
        assert len(new_ids) == 1
        artifact_id = new_ids.pop()
        record = workflow.current["artifact_index"][artifact_id]
        assert record["type"] == kind and record["derived_status"] == "accepted"
        envelope = json.loads((workflow.state_root / "live" / workflow.workflow_id / record["path"] / "envelope.json").read_text())
        assert envelope["producer"] == workflow.mode + ":" + role
        assert all(binding["digest"] == workflow.current["artifact_index"][binding["artifact_id"]]["digest"] for binding in envelope["input_bindings"])
        saved = json.loads(workflow.observation_path.read_text())
        assert saved["latest_public_state"]["stage"] == stage
        assert saved["latest_public_state"]["head_sequence"] == workflow.current["head_sequence"]
    actors = workflow.observation["actors"][-5:]
    assert len({actor["context_identity"] for actor in actors}) == 5
    assert [actor["role"] for actor in actors] == [role for role, _, _ in stages]
    assert all(actor["stage_timeout_seconds"] == 30 for actor in actors)


def test_timed_out_contract_actor_preserves_failed_evidence_and_refreshes_actual_progress(tmp_path):
    class TimedOutAfterRetain(ScriptedTransport):
        def run_actor(self, **options):
            actor = super().run_actor(**options)
            if Path(options["evidence_dir"]).name == "author-contract":
                actor["outcome"] = "timed-out"
                actor["attempts"][0]["timed_out"] = True
            return actor

        def advance(self, public, role):
            if role != "author-contract":
                return super().advance(public, role)
            current = public.load_run(workflow_id=self.workflow.workflow_id)
            fixtures.retain_json(public, state_root=self.workflow.state_root, workflow_id=self.workflow.workflow_id,
                                 sequence=current["head_sequence"], artifact_id="retained-but-not-transitioned-contract", artifact_type="skill-contract",
                                 payload=fixtures.contract_payload(public, self.workflow.state_root, self.workflow.workflow_id),
                                 input_bindings=fixtures.current_bindings(public, self.workflow.state_root, self.workflow.workflow_id),
                                 producer=self.workflow.mode + ":" + role)

    transport = TimedOutAfterRetain()
    workflow = offline_workflow(tmp_path, transport=transport)
    transport.workflow = workflow
    workflow.stage("resolve-baseline", "Capture baseline.", "baseline", ("initialize", "retain", "transition"))
    for role, stage in (("normalize-research", "research"), ("sieve-evidence", "sieve"), ("challenge-design", "design")):
        workflow.stage(role, "One stage only.", stage, ("retain", "transition"))
    prior_sequence = workflow.current["head_sequence"]
    with pytest.raises(probe.ProbeError, match="timed-out"):
        workflow.stage("author-contract", "Only retain and accept the contract.", "contract", ("retain", "transition"))
    saved = json.loads(workflow.observation_path.read_text())
    assert saved["latest_public_state"]["stage"] == "design"
    assert saved["latest_public_state"]["head_sequence"] == prior_sequence + 1
    assert saved["latest_public_state"]["artifact_index"]["retained-but-not-transitioned-contract"]["derived_status"] == "accepted"
    receipt_root = workflow.state_root / "live" / workflow.workflow_id / "receipts"
    receipt = json.loads((receipt_root / f"{prior_sequence + 1:08d}.json").read_text())
    assert receipt["event"] == "retain-artifact"
    assert not any(json.loads(path.read_text())["event"] == "accept-contract" for path in receipt_root.glob("*.json"))
    assert saved["public_state_refresh_after_actor_failure"]["outcome"] == "refreshed"
    assert workflow.observation["actors"][-1]["transport"]["outcome"] == "timed-out"
    assert "confirm-contract" not in [call["role"] for call in transport.observed_calls]


def test_stage_artifact_ownership_rejects_wrong_type_after_valid_public_transition(tmp_path):
    transport = ScriptedTransport()
    workflow = offline_workflow(tmp_path, transport=transport)
    transport.workflow = workflow
    workflow.stage("resolve-baseline", "Capture baseline.", "baseline", ("initialize", "retain", "transition"))
    with pytest.raises(runner.WorkflowError, match="exactly its owned artifact types"):
        workflow.stage("normalize-research", "Normalize research.", "research", ("retain", "transition"),
                       expected_artifact_types=("design-record",))
    assert workflow.current["stage"] == "research"
    assert workflow.current["artifact_index"]["research"]["type"] == "research-pack"


def test_retained_controller_packet_tampering_blocks_next_actor(tmp_path):
    workflow = offline_workflow(tmp_path)
    packet = workflow.actor_packet("candidate-author")
    Path(packet["path"]).write_text("{}")
    with pytest.raises(runner.WorkflowError, match="Retained actor or controller evidence changed"):
        workflow.guard()


def test_public_command_claim_requires_real_successful_direct_cli_event(tmp_path):
    helper = tmp_path / "run_state.py"
    command = shlex.join([sys.executable, str(helper), "transition"])
    event = {"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": 0}}
    runner.public_command_seen(probe, {"events": [event]}, helper, ["transition"])
    event["item"]["exit_code"] = 1
    with pytest.raises(runner.WorkflowError, match="public helper commands"):
        runner.public_command_seen(probe, {"events": [event]}, helper, ["transition"])


def test_failed_transport_retains_attempts_and_exact_framework_snapshot(tmp_path):
    class FailedTransport:
        git = staticmethod(driver.git)
        snapshot = staticmethod(driver.snapshot)
        run_process_group = staticmethod(driver.run_process_group)

        def run_actor(self, *, prompt, cwd, state_home, evidence_dir, timeout, sandbox, live, cli, infrastructure_retries,
                      persistent=False, resume_from=None):
            evidence_dir = Path(evidence_dir)
            evidence_dir.mkdir(parents=True)
            raw = evidence_dir / "offline-failed-attempt.jsonl"
            raw.write_bytes(runner.canonical({"type": "error", "message": "scripted infrastructure failure; no model trial"}))
            return {"outcome": "infrastructure-error", "attempts": [{"raw_events": str(raw), "raw_events_sha256": runner.digest(raw.read_bytes()),
                    "thread_ids": [], "exit_code": 1, "timed_out": False}], "selected_attempt": 1, "thread_ids": [], "final_text": ""}

    revision = driver.git(ROOT, "rev-parse", "HEAD")
    report = runner.run_workflows(output_root=tmp_path / "retained", repository=ROOT, framework_revision=revision,
                                 live=True, driver=FailedTransport(), maximum_actor_attempts=2, deadline_seconds=120)
    assert report["outcome"] == "public-workflows-incomplete"
    assert report["transport"] == "scripted-test-transport"
    assert report["actual_actor_attempts"] == 2
    assert [row["outcome"] for row in report["workflows"]] == ["workflow-blocked", "workflow-blocked"]
    assert report["framework_revision"] == revision
    for name, evidence in report["sources"].items():
        assert runner.digest(Path(evidence["path"]).read_bytes()) == evidence["sha256"]
        expected = subprocess_bytes(["git", "show", revision + ":plugins/expskill/content/skills/skill-builder/" + runner.SOURCES[name]], ROOT)
        assert runner.digest(expected) == evidence["sha256"]
    for workflow in report["workflows"]:
        actor = workflow["actors"][0]["transport"]
        assert Path(actor["attempts"][0]["raw_events"]).read_bytes()


def subprocess_bytes(argv, cwd):
    import subprocess
    return subprocess.check_output(argv, cwd=cwd)


def test_missing_persistent_driver_blocks_before_output_creation(tmp_path):
    class LegacyTransport:
        def run_actor(self, *, prompt):
            raise AssertionError("must not launch")

    with pytest.raises(runner.WorkflowError, match="persistent/resume"):
        runner.run_workflows(output_root=tmp_path / "never", framework_revision="0" * 40, live=True, driver=LegacyTransport())
    assert not (tmp_path / "never").exists()


@pytest.mark.parametrize("fault", ["unexecuted", "changed-output", "wrong-candidate"])
def test_verification_rejects_unobserved_or_substituted_command_claims(tmp_path, fault):
    workflow = offline_workflow(tmp_path)
    package = tmp_path / "candidate"
    package.mkdir()
    command = shlex.join([sys.executable, str(package / "scripts/count_lines.py"), str(Path(workflow.cases[0]["target"]) / "input.txt")])
    actual = command.replace(str(package), str(tmp_path / "other-candidate")) if fault == "wrong-candidate" else command
    output = '{"line_count": 2}\n'
    payload = {"commands": [{"command": command, "exit_status": 0, "output_digest": runner.digest(output.encode())}]}
    workflow.accepted = lambda kind: (None, None, None, {"isolated_locator": str(package)} if kind == "candidate-record" else payload)
    events = [] if fault == "unexecuted" else [{"type": "item.completed", "item": {"type": "command_execution", "command": actual, "exit_code": 0,
              "aggregated_output": "invented" if fault == "changed-output" else output}}]
    workflow.probe = SimpleNamespace(actor_origin=lambda actor, seen: {"events": events}, command_parts=probe.command_parts, helper_invocation=probe.helper_invocation)
    with pytest.raises(runner.WorkflowError, match="Verification claim lacks"):
        workflow.check_verification_origin({})
    events[:] = [{"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": 0, "aggregated_output": output}}]
    workflow.check_verification_origin({})


def native_helper_fixture(tmp_path):
    captured = json.loads((ROOT / "tests/fixtures/skill-builder/native-public-helper-v1.json").read_text())
    root = tmp_path / "exact-state"
    helper = tmp_path / "run_state.py"
    current = captured["current"]
    receipts = root / "live" / current["workflow_id"] / "receipts"
    receipts.mkdir(parents=True)
    for name, receipt in captured["receipts"].items():
        (receipts / name).write_bytes(runner.canonical(receipt))
    for event in captured["events"]:
        event["item"]["command"] = event["item"]["command"].replace(captured["helper"], str(helper)).replace(captured["state_root"], str(root))
    return captured, root, helper, current


def test_captured_native_public_helper_calls_accept_global_flag_order_and_shell_preparation(tmp_path):
    captured, root, helper, current = native_helper_fixture(tmp_path)
    assert captured["origin"]["thread_id"] == "01a0fdcd-99f3-73e0-9568-d25a194b775b"
    assert len(captured["events"]) == 5
    runner.public_command_seen(probe, {"events": captured["events"]}, helper, ["initialize", "retain", "transition"], state_root=root, current=current)
    for event in captured["events"]:
        item = event["item"]
        if item["aggregated_output"].startswith("error:"):
            assert item["exit_code"] == 0
            with pytest.raises(runner.WorkflowError, match="public helper commands"):
                runner.public_command_seen(probe, {"events": [event]}, helper, ["retain" if "canonical JSON" in item["aggregated_output"] else "transition"], state_root=root, current=current)


@pytest.mark.parametrize("syntax", ["echo", "literal-separator", "heredoc-data", "conditional", "conditional-newline", "if-block", "comment", "unexecuted-script", "after-exit", "after-exec", "alias", "environment"])
def test_public_helper_parser_rejects_unexecuted_text_even_with_real_receipt_output(tmp_path, syntax):
    captured, root, helper, current = native_helper_fixture(tmp_path)
    event = next(event for event in captured["events"] if '"operation":"initialize"' in event["item"]["aggregated_output"])
    argv = shlex.join([sys.executable, str(helper), "--state-root", str(root), "initialize"])
    scripts = {"echo": "echo " + shlex.quote(argv), "literal-separator": "echo ';' " + argv,
               "heredoc-data": "python3 - <<'DATA'\n" + argv + "\nDATA\ncat result.json",
               "conditional": "false && " + argv + "; cat result.json",
               "conditional-newline": "false &&\n" + argv + "; cat result.json",
               "if-block": "if false; then " + argv + "; fi; cat result.json",
               "comment": "true # " + argv,
               "unexecuted-script": "printf %s " + shlex.quote(argv) + " > script.sh; cat result.json",
               "after-exit": "cat result.json; exit 0; " + argv,
               "after-exec": "exec cat result.json; " + argv,
               "alias": "alias python3=echo; " + argv,
               "environment": "PATH=/untrusted; " + argv}
    event["item"]["command"] = shlex.join(["/usr/bin/zsh", "-lc", scripts[syntax]])
    with pytest.raises(runner.WorkflowError, match="public helper commands"):
        runner.public_command_seen(probe, {"events": [event]}, helper, ["initialize"], state_root=root, current=current)


@pytest.mark.parametrize("fault", ["failed-terminal", "other-state", "other-workflow", "fabricated-receipt", "missing-receipt", "stale-receipt"])
def test_compound_public_helper_success_requires_exact_validated_workflow_receipt(tmp_path, fault):
    captured, root, helper, current = native_helper_fixture(tmp_path)
    event = next(event for event in captured["events"] if '"operation":"initialize"' in event["item"]["aggregated_output"])
    if fault == "failed-terminal":
        event["item"]["exit_code"] = 1
    elif fault == "other-state":
        event["item"]["command"] = event["item"]["command"].replace(str(root), str(tmp_path / "unrelated-state"))
    elif fault == "other-workflow":
        result = json.loads(event["item"]["aggregated_output"])
        result["workflow_id"] = "0" * 32
        event["item"]["aggregated_output"] = runner.canonical(result).decode()
    elif fault != "stale-receipt":
        path = root / "live" / current["workflow_id"] / "receipts/00000000.json"
        if fault == "missing-receipt":
            path.unlink()
        else:
            receipt = json.loads(path.read_text())
            receipt["destination_stage"] = "fabricated"
            path.write_bytes(runner.canonical(receipt))
    with pytest.raises(runner.WorkflowError, match="public helper commands"):
        runner.public_command_seen(probe, {"events": [event]}, helper, ["initialize"], state_root=root, current=current, prior_sequence=current["head_sequence"] if fault == "stale-receipt" else -1)


@pytest.mark.parametrize("arguments", [["initialize", "--state-root", "ROOT"], ["--state-root", "ROOT", "initialize"], ["--state-root=ROOT", "initialize"]])
def test_public_helper_global_options_can_precede_or_follow_subcommand(tmp_path, arguments):
    helper, root = tmp_path / "run_state.py", tmp_path / "exact-state"
    arguments = [value.replace("ROOT", str(root)) for value in arguments]
    event = {"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join([sys.executable, str(helper), *arguments]), "exit_code": 0}}
    runner.public_command_seen(probe, {"events": [event]}, helper, ["initialize"], state_root=root)


def test_actual_public_cli_compound_baseline_and_masked_failure_are_bound_to_real_state(tmp_path):
    workflow = offline_workflow(tmp_path)
    root = workflow.state_root
    events = []

    def execute(command, payload):
        request = workflow.workspace / (command + "-request.json")
        output = workflow.workspace / (command + "-output.json")
        runner.save(request, payload)
        argv = [sys.executable, str(workflow.helper), "--state-root", str(root), command]
        script = "python3 - <<'PREP'\nfrom pathlib import Path\nPath('prepared').write_text('metadata')\nPREP\n" + shlex.join(argv) + " < " + shlex.quote(str(request)) + " > " + shlex.quote(str(output)) + "\ncat " + shlex.quote(str(output))
        result = driver.run_process_group(["/bin/sh", "-c", script], prompt="", cwd=workflow.workspace, env=None, timeout=30)
        event = {"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(["/bin/sh", "-c", script]),
                 "exit_code": result["exit_code"], "aggregated_output": result["stdout"] + result["stderr"]}}
        events.append(event)
        return result

    started = json.loads(execute("initialize", {"host_identity": fixtures.host_identity(workflow.root), "target_identity": fixtures.target_identity(workflow.target),
                  "mode": "create", "authority": fixtures.authority(), "absence_evidence": {"searched": [str(workflow.target)], "exists": False},
                  "overlap_map": {"exact": [], "near_neighbours": []}, "owner_identity": "offline-captured-parser-test"})["stdout"])
    workflow.workflow_id = started["workflow_id"]
    current = workflow.refresh()
    payload = fixtures.valid_create_baseline_payload(PublicCLI(workflow, "parser-test"), root, workflow.workflow_id)
    retained = json.loads(execute("retain", {"workflow_id": workflow.workflow_id, "expected_sequence": current["head_sequence"], "artifact_id": "baseline",
                  "artifact_type": "baseline-report", "files_base64": {"record.json": base64.b64encode(runner.canonical(payload)).decode()}, "primary_path": "record.json",
                  "producer": "offline-captured-parser-test", "input_bindings": [{"artifact_id": "resolution", "digest": current["artifact_index"]["resolution"]["digest"]}], "limitations": []})["stdout"])
    request = {"workflow_id": workflow.workflow_id, "expected_sequence": retained["sequence"], "event": "capture-baseline", "destination_stage": "baseline", "artifact_ids": ["baseline"]}
    assert execute("transition", request)["exit_code"] == 0
    current = workflow.refresh()
    runner.public_command_seen(probe, {"events": events}, workflow.helper, ["initialize", "retain", "transition"], state_root=root, current=current)
    request["expected_sequence"] = current["head_sequence"]
    failed = execute("transition", request)
    assert failed["exit_code"] == 0 and "event is not allowed from the current stage" in failed["stderr"]
    assert workflow.refresh()["head_sequence"] == current["head_sequence"]
    with pytest.raises(runner.WorkflowError, match="public helper commands"):
        runner.public_command_seen(probe, {"events": [events[-1]]}, workflow.helper, ["transition"], state_root=root, current=current)
