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


def offline_workflow(tmp_path, mode="create", transport=None):
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
                           budget=runner.Budget(64, 240, 30), cli="unused-no-model")


class ScriptedTransport:
    run_process_group = staticmethod(driver.run_process_group)
    workflow = None

    def run_actor(self, *, prompt, cwd, state_home, evidence_dir, timeout, sandbox, live, cli, infrastructure_retries,
                  model=None, persistent=False, resume_from=None):
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
            for name in ("skill", "contracts", "rubric"):
                events.append({"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(["cat", workflow.sources[name]["path"]]), "exit_code": 0}})
            before = set(workflow.current["artifact_index"]) if workflow.current else set()
            if role.startswith("research-"):
                path = Path(cwd) / "offline-synthetic-research.json"
                runner.save(path, {"origin": "scripted offline fixture; no live research", "question": role})
                events.append({"type": "item.completed", "item": {"type": "web_search", "origin": "scripted offline fixture"}})
                reply = json.dumps({"workflow_id": workflow.workflow_id, "stage": "baseline", "artifact_ids": [str(path)]})
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
        elif role == "synthesis-contract":
            for name, kind, event in (("research", "research-pack", "complete-research"), ("sieve", "evidence-sieve", "sieve-evidence"), ("design", "design-record", "accept-design")):
                retained(kind, fixtures.stage_payload(public, state, workflow.workflow_id, kind), name)
                transition(event, name, [name])
            contract = retained("skill-contract", fixtures.contract_payload(public, state, workflow.workflow_id), "contract")
            transition("accept-contract", "contract", ["contract"])
            retained("user-confirmation-record", fixtures.confirmation_payload(contract_id="contract", contract_digest=contract["artifact_digest"],
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
    assert not (tmp_path / "never").exists()


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
