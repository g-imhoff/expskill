import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import live_routed_plan_design as probe
from scripts import live_trials
from tests.design_state_test_support import passing_technical


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = json.loads(probe.DEFAULT_CASE.read_bytes())


@pytest.fixture(scope="session")
def framework_source(tmp_path_factory):
    source = Path(os.environ.get("EXPSKILL_TRIAL_FRAMEWORK_SOURCE", ROOT))
    frozen = tmp_path_factory.mktemp("routed-framework-source")
    shutil.copytree(source / "plugins/expskill/content", frozen / "plugins/expskill/content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    live_trials.git(frozen, "init", "-b", "fixture/framework")
    live_trials.git(frozen, "config", "user.name", "g-imhoff")
    live_trials.git(frozen, "config", "user.email", "152416066+g-imhoff@users.noreply.github.com")
    live_trials.git(frozen, "add", ".")
    for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        assert live_trials.git(frozen, "var", kind).startswith(probe.IDENTITY + " ")
    live_trials.git(frozen, "commit", "-qm", "Freeze current helper source for offline routed contracts")
    assert live_trials.git(frozen, "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") == probe.IDENTITY + " | " + probe.IDENTITY
    return frozen, live_trials.git(frozen, "rev-parse", "HEAD")


def valid_ui(repository):
    path = repository / "index.html"
    path.write_text(path.read_text().replace(">Notifications</label>", ">Send email notifications</label>"))
    (repository / "styles.css").write_text("body { font-family: system-ui,sans-serif; margin: 1rem; } main { max-width: 32rem; } label { overflow-wrap: anywhere; } input:focus-visible { outline: 2px solid blue; } @media(prefers-color-scheme:dark) { body { color: white; background: #222; } }\n")


def test_actual_public_helper_import_preserves_frozen_snapshot_inventory(tmp_path):
    content = tmp_path / "framework"
    scripts = content / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "design_state.py"
    shutil.copyfile(ROOT / "plugins/expskill/content/scripts/design_state.py", helper)
    before = probe.freeze_framework(content)
    command = "import importlib.util; spec=importlib.util.spec_from_file_location('frozen_design'," + repr(str(helper)) + "); value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value); print('PUBLIC HELPER IMPORT COMPLETE')"
    completed = subprocess.run([sys.executable, "-I", "-c", command], capture_output=True, text=True, timeout=10, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "PUBLIC HELPER IMPORT COMPLETE"
    assert probe.tree_pin(content) == before
    assert not (scripts / "__pycache__").exists()


def offline_static_fixture(repository, output, browser, timeout_seconds=60):
    assert ">Send email notifications</label>" in (repository / "index.html").read_text()
    assert "min-width: 800px" not in (repository / "styles.css").read_text()
    result = {"passed": True, "kind": "offline-static-fixture-only", "observations": [],
        "limitations": ["Scripted contract test establishes no browser, keyboard, accessibility, or model behavior."],
        "tooling_exceptions": {name: "Synthetic offline contract fixture" for name in ("format", "lint", "type", "build")}}
    probe.retain(output / "native-checks.json", result)
    return result


class ScriptedOwners:
    git = staticmethod(live_trials.git)
    snapshot = staticmethod(live_trials.snapshot)

    def __init__(self, fault=None, native=False):
        self.fault = fault
        self.native = native
        self.calls = []

    def run_actor(self, *, prompt, cwd, state_home, evidence_dir, resume_from=None, **configuration):
        output = evidence_dir.parent.parent
        content = output / "framework-snapshot/plugins/expskill/content"
        plan = probe.module(content / "scripts/plan_graph.py", "offline_routed_plan")
        design = probe.module(content / "scripts/design_state.py", "offline_routed_design")
        accepted = json.loads((output / "accepted-input.json").read_bytes())
        input_digest = probe.digest((output / "accepted-input.json").read_bytes())
        canonical_state = output / "state"
        root = canonical_state / "expskill"
        target = output / "repository"
        name = evidence_dir.name
        self.calls.append((name, prompt, {**configuration, "cwd": str(cwd), "state_home": str(state_home)}))
        thread = resume_from["thread_ids"][0] if resume_from is not None else "offline-" + name
        if self.fault == "wrong-resume" and name == "plan-answer":
            thread = "offline-wrong-owner"
        if self.fault == "interruption" and name == "plan-initial":
            raise KeyboardInterrupt("offline dispatch interruption")
        if self.fault == "unavailable-interpreter" and name == "plan-initial":
            payload = {"phase": "plan", "status": "blocked", "workflow_id": None, "revision": None,
                "canonical_locator": None, "graph_path": None, "last_saved_workflow_revision": None,
                "failure": {"operation": "Read public plan_graph.py helper usage", "exit_code": 127, "message": "zsh:1: command not found: python"}}
        elif name.startswith("plan-") and name != "plan-independent-audit":
            if name == "plan-initial":
                template = {
                    "outcomes": {"O1": {"kind": "outcome", "result": "Accessible responsive native preference control and preserved unrelated bytes"}},
                    "evidence": {"E1": {"kind": "repository", "fact": "Existing independent native browser checker defines the UI seam", "source": "native_checks.py", "fresh": True, "supports": ["T1"]}},
                    "decisions": {},
                    "work": {"T1": {"kind": "slice", "result": "Integrate the exact approved native UI", "covers": ["O1"], "requires": [], "based_on": ["E1"], "decisions": [], "proof": ["P1"], "repository_boundary": probe.SOURCE_FILES, "owner": "target", "concurrency": "serial"}},
                    "proof": {"P1": {"claim": "UI supports the accepted states and preserves unrelated bytes", "covers": ["O1"], "required_by": ["T1"], "planned_method": {"surface": "native_checks.py", "positive": "native checkbox state and keyboard actions", "negative": "overflow or wrong accessible name fails"}, "evidence": []}},
                    "git": {"target": {"branch": "trial/routed-ui", "protected": False, "reproducible": True, "dirty_dependency": False}, "lanes": {}, "joins": {}, "delivery": {"state": "planning"}},
                    "projections": {"U1": {"covers": ["T1", "P1"], "version": 1, "presented": True, "confirmed": False, "stale": False, "presentation": "Integrate one exact approved native UI candidate, preserving the independent checker and unrelated files."}},
                    "design_join": {"required": True, "record_version": 1, "receipt": None, "fresh": False, "operation_receipt": None},
                    "invalidations": [], "unresolved": [],
                }
                receipt = plan.initialize_workflow(target, "trial/routed-ui", template, root)
                self.graph_path = receipt.path
            else:
                graph = plan.load_workflow(target, "trial/routed-ui", root)
                if name == "plan-answer":
                    value = copy.deepcopy(graph["projections"]["U1"])
                    value["confirmed"] = True
                    self.typed(plan, target, root, graph, "reconfirm-projection", ["projections", "U1"], value, value["version"])
                elif name == "plan-design-join":
                    delivery = json.loads((state_home / "outputs/design-delivery.json").read_bytes())
                    joined = plan.issue_design_join_receipt(workflow_id=graph["workflow_id"], plan_revision=graph["graph_revision"], baseline=graph["baseline"]["repository_revision"], design_workflow_id=delivery["workflow_id"], design_revision=delivery["revision"], design_branch=delivery["identity"]["branch"], candidate_commit=delivery["candidate_commit"], brief_digest=delivery["brief_digest"], approval_digest=delivery["approval_digest"], manifest_digest=delivery["manifest_digest"], approved=True, design_delivery_receipt=delivery)
                    value = copy.deepcopy(graph["design_join"])
                    value.update(receipt=joined, fresh=True)
                    self.typed(plan, target, root, graph, "record-design-join", ["design_join"], value, value["record_version"])
                    graph = plan.load_workflow(target, "trial/routed-ui", root)
                    self.audit_dispatch = plan.reserve_plan_audit(target, "trial/routed-ui", graph["workflow_id"], graph["graph_revision"], "routed-probe-audit-1", {"path": str(output / "accepted-input.json"), "digest": input_digest}, "initial", "Independent retained routed probe audit", root)
                elif name == "plan-audit-result":
                    audit = json.loads((output / "independent-audit-result.json").read_bytes())
                    plan.record_plan_audit_result(target, "trial/routed-ui", graph["workflow_id"], "routed-probe-audit-1", audit, root)
                    if not audit["findings"]:
                        plan.apply_plan_audit_result(target, "trial/routed-ui", graph["workflow_id"], graph["graph_revision"], "routed-probe-audit-1", root)
            graph = plan.load_workflow(target, "trial/routed-ui", root)
            payload = {"phase": "plan", "status": "awaiting-answer" if name == "plan-initial" else graph["lifecycle"]["derived_state"], "workflow_id": graph["workflow_id"], "revision": graph["graph_revision"], "graph_path": str(self.graph_path)}
            if name == "plan-initial":
                payload["question"] = {"id": "plan-approval", "text": "Approve this retained technical projection?", "projection_id": "U1"}
            if name == "plan-answer":
                payload["resolved_question_id"] = "plan-approval"
            if name == "plan-design-join":
                payload["audit_dispatch"] = self.audit_dispatch
            if name == "plan-audit-result":
                payload["audit_records"] = plan.load_plan_audits(target, "trial/routed-ui", graph["workflow_id"], root)
        elif name == "design-initial":
            baseline = self.git(cwd, "rev-parse", "HEAD")
            receipt = design.initialize_workflow(repository=cwd, branch=self.git(cwd, "branch", "--show-current"), worktree=cwd, baseline=baseline, dirty_fingerprint=probe.digest(b""), ui_contract={"digest": input_digest}, scope={"components": ["Preference"], "exclusions": ["backend"], "owned_paths": probe.SOURCE_FILES + probe.review_paths()}, state_home=root, invocation_mode="routed")
            brief = {"objective": accepted["objective"], "requirements": accepted["requirements"], "responsive_expectations": {name: str(width) for name, width in probe.WIDTHS.items()}, "non_goals": accepted["non_goals"], "source": {"kind": "accepted-input", "path": str(output / "accepted-input.json"), "digest": input_digest, "decision_reference": accepted["decision_reference"]}}
            receipt = design.confirm_brief(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], brief=brief, confirmed=True, state_home=root)
            valid_ui(cwd)
            self.git(cwd, "add", *probe.SOURCE_FILES)
            for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
                assert self.git(cwd, "var", kind).startswith(probe.IDENTITY + " ")
            self.git(cwd, "commit", "-qm", "Offline scripted native Design candidate")
            assert self.git(cwd, "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") == probe.IDENTITY + " | " + probe.IDENTITY
            receipt = design.checkpoint_candidate(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], candidate_commit=self.git(cwd, "rev-parse", "HEAD"), state_home=root)
            candidate = {"files": [{"path": name, "digest": probe.digest((cwd / name).read_bytes()), "classification": "component"} for name in probe.SOURCE_FILES]}
            if self.native:
                argv = [sys.executable, "native_checks.py", "native-checks", "--browser", str(probe.browser_path()), "--output", "review"]
            else:
                offline_static_fixture(cwd, cwd / "review", sys.executable)
                argv = [sys.executable, "-c", "from pathlib import Path; assert '>Send email notifications</label>' in Path('index.html').read_text(); print('OFFLINE STATIC FIXTURE ONLY: NO BROWSER OR MODEL CLAIM')"]
            record = design.record_check(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], argv=argv, candidate_payload=candidate, state_home=root)
            assert record["exit"] == 0
            native = json.loads((cwd / "review/native-checks.json").read_bytes())
            assert native["passed"]
            (cwd / "design-manifest.json").write_text(json.dumps({"preview_mode": "local", "entry": "index.html", "launch": "python3 -m http.server --bind 127.0.0.1 8765", "limitations": native["limitations"]}))
            review = {"files": [{"path": "review/native-checks.json", "digest": probe.digest((cwd / "review/native-checks.json").read_bytes()), "classification": "review"}]}
            manifest = {"files": [{"path": "design-manifest.json", "digest": probe.digest((cwd / "design-manifest.json").read_bytes()), "classification": "manifest"}]}
            technical = passing_technical(record["output_digest"], record["command"])
            technical["results"] = [record]
            for gate, reason in native["tooling_exceptions"].items():
                technical["gates"][gate] = {"status": "not-applicable", "evidence_digest": record["output_digest"], "details": {"reason": reason}}
            state = design.load_workflow(workflow_id=receipt["workflow_id"], state_home=root)
            binding = {"code_digest": design.component_digest(candidate["files"]), "contract_digest": input_digest, "brief_digest": state["brief"]["digest"]}
            records = {
                "components": {"Preference": {"id": "Preference", **binding, "files": candidate["files"], "evidence_ids": ["E1"], "dependency_ids": ["D1"], "approval_id": "A1"}},
                "dependencies": {"D1": {"id": "D1", "digest": input_digest, "component_ids": ["Preference"]}},
                "evidence": {"E1": {"id": "E1", **binding, "component_id": "Preference", "digest": review["files"][0]["digest"], "widths": list(probe.WIDTHS), "themes": probe.THEMES, "states": probe.STATES, "technical": technical}},
                "approvals": {},
                "delivery": {"classifications": {"candidate": "component", "review": "review", "manifest": "manifest"}, **{key: {"inventory_digest": design.canonical_digest(value), "files": value["files"]} for key, value in zip(("candidate", "review", "manifest"), (candidate, review, manifest))}},
            }
            self.layers, self.binding = (candidate, review, manifest), binding
            receipt = design.apply_updates(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], updates=records, state_home=root)
            self.design_id = receipt["workflow_id"]
            payload = {"phase": "design", "status": "awaiting-answer", "workflow_id": self.design_id, "revision": receipt["revision"], "candidate_commit": self.git(cwd, "rev-parse", "HEAD"), "review_entry": str(cwd / "index.html"), "question": {"id": "design-approval", "text": "Approve this exact native candidate and recorded states?"}}
        elif name == "design-answer":
            state = design.load_workflow(workflow_id=self.design_id, state_home=root)
            approval = {"id": "A1", **self.binding, "component_id": "Preference", "evidence_ids": ["E1"], "dependency_ids": ["D1"], "decision": "approved", "provenance": {"kind": "trusted-attestation", "actor": "authorized-agent", "authority_reference": "explicit synthetic fixture answer", "decision_reference": "synthetic-design-answer"}}
            receipt = design.apply_updates(workflow_id=self.design_id, expected_revision=state["revision"], updates={"approvals": {"A1": approval}}, state_home=root)
            command = [sys.executable, str(content / "scripts/design_state.py"), "deliver"]
            request = {"workflow_id": self.design_id, "expected_revision": receipt["revision"], **dict(zip(("candidate_payload", "review_evidence", "manifest"), self.layers))}
            result = subprocess.run(command, cwd=cwd, input=json.dumps(request), text=True, capture_output=True, env={**os.environ, "XDG_STATE_HOME": str(state_home)}, check=False)
            assert result.returncode == 0, result.stderr
            path = state_home / "outputs/design-delivery.json"
            path.parent.mkdir()
            path.write_text(result.stdout)
            delivery = json.loads(result.stdout)
            payload = {"phase": "design", "status": "delivered", "workflow_id": self.design_id, "revision": delivery["revision"], "delivery_receipt_path": str(path), "resolved_question_id": "design-approval"}
        elif name == "design-replacement":
            state = design.load_workflow(workflow_id=self.design_id, state_home=root)
            payload = {"phase": "design", "status": "delivered", "workflow_id": self.design_id, "revision": state["revision"], "delivery_receipt_path": str(canonical_state / "outputs/design-delivery.json"), "replaced_thread_id": "offline-design-initial"}
            if self.fault == "reader-source-write":
                (Path(state["identity"]["worktree"]) / "styles.css").write_text("reader mutation")
            if self.fault == "reader-state-write":
                (root / "design" / self.design_id).write_text("{}")
        else:
            graph = json.loads(Path(self.audit_dispatch["dispatch"]["graph_snapshot"]["path"]).read_bytes())
            payload = {"input_digest": input_digest, "workflow_id": graph["workflow_id"], "revision": graph["graph_revision"], "graph_digest": self.audit_dispatch["dispatch"]["graph_digest"], "evidence": list(graph["evidence"]), "constraints": ["Offline scripted actor, not model judgment"], "findings": [], "limitations": ["Offline scripted actor, not model judgment"]}
            if self.fault == "audit-finding":
                payload["findings"] = [{"id": "F1", "severity": "high", "evidence": ["E1"], "description": "synthetic negative audit control", "disposition": "open"}]
        payload["input_digest"] = input_digest
        if self.fault == "foreign-state" and name == "plan-initial":
            foreign = root / "design/foreign.json"
            foreign.parent.mkdir(mode=0o700)
            foreign.write_text("{}")
        if self.fault == "target-write" and name == "plan-initial":
            (target / "unrelated.txt").write_text("changed by wrong owner")
        if self.fault == "input-mutation" and name == "plan-initial":
            source = output / "accepted-input.json"
            source.chmod(0o600)
            source.write_text("{}")
        if self.fault == "runtime-evidence" and name == "plan-initial":
            source = output / "python-runtime.json"
            source.chmod(0o600)
            source.write_text("{}")
        if self.fault == "framework-changed" and name == "design-initial":
            source = content / "skills/design/SKILL.md"
            source.chmod(0o600)
            source.write_text(source.read_text() + "\nACTUAL PROTECTED SOURCE MUTATION\n")
        if self.fault == "framework-missing" and name == "design-initial":
            source = content / "skills/design/SKILL.md"
            source.parent.chmod(0o755)
            source.unlink()
        if self.fault == "framework-added" and name == "design-initial":
            directory = content / "scripts"
            directory.chmod(0o755)
            (directory / ".lock").write_text("UNAUTHORIZED FROZEN SOURCE ADDITION")
        evidence_dir.mkdir(parents=True)
        events = [{"type": "thread.started", "thread_id": thread}, {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}}]
        raw = evidence_dir / "events.jsonl"
        raw.write_text("\n".join(json.dumps(row) for row in events) + "\n")
        return {"outcome": "completed-ungraded", "thread_ids": [thread], "final_text": json.dumps(payload), "attempts": [{"raw_events": str(raw), "raw_events_sha256": probe.digest(raw.read_bytes())}], "transport": {"persistent": configuration["persistent"], "resumed_from_thread_id": resume_from["thread_ids"][0] if resume_from else None}, "runtime": {"configuration": "offline scripted public-helper operations"}}

    def typed(self, helper, target, root, graph, operation, path, value, version):
        receipt = helper.issue_operation_receipt(operation=operation, workflow_id=graph["workflow_id"], prior_graph_revision=graph["graph_revision"], target=path, record_version=version, value=value)
        helper.apply_updates(target, "trial/routed-ui", graph["workflow_id"], graph["graph_revision"], [{"op": operation, "path": path, "value": value, "prior_graph_revision": graph["graph_revision"], "record_version": version, "receipt": receipt}], root)


def test_probe_requires_opt_in_before_fixture_or_transport(tmp_path):
    owners = ScriptedOwners()
    with pytest.raises(probe.ProbeError, match="explicit"):
        probe.run_probe(output_root=tmp_path / "run", revision="0" * 40, driver=owners)
    assert owners.calls == []
    assert not (tmp_path / "run").exists()


def test_missing_durable_audit_protocol_blocks_before_actors(tmp_path, framework_source, monkeypatch):
    owners = ScriptedOwners()
    original = probe.module
    def incomplete_module(path, name):
        value = original(path, name)
        if name == "routed_probe_plan":
            value.reserve_plan_audit = None
        return value
    monkeypatch.setattr(probe, "module", incomplete_module)
    repository, revision = framework_source
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert "durable Plan audit operations" in report["failure"]["reason"]
    assert owners.calls == []


def test_interpreter_prerequisite_is_checked_before_first_actor(tmp_path, framework_source, monkeypatch):
    repository, revision = framework_source
    owners = ScriptedOwners()
    monkeypatch.setattr(probe.sys, "executable", str(tmp_path / "missing-python3"))
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert "Python3 interpreter prerequisite" in report["failure"]["reason"]
    assert owners.calls == []
    assert report["budgets"]["spent_or_reserved_actor_calls"] == 0


def test_unavailable_interpreter_blocked_json_preserves_cause_without_opening_null_graph(tmp_path, framework_source, monkeypatch):
    repository, revision = framework_source
    owners = ScriptedOwners("unavailable-interpreter")
    def reject_canonical_pointer(*args):
        raise AssertionError("Blocked return must not open a canonical pointer")
    monkeypatch.setattr(probe, "private_file", reject_canonical_pointer)
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert len(owners.calls) == 1
    assert report["failure"]["type"] == "ActorBlocked"
    assert report["failure"]["reason"] == "zsh:1: command not found: python"
    assert report["failure"]["actor_failure"]["exit_code"] == 127
    assert report["failure"]["workflow_id"] is None
    assert report["failure"]["revision"] is None
    assert report["failure"]["last_saved_workflow_revision"] is None
    assert report["failure"]["canonical_locator"] is None
    assert report["failure"]["dispatch_id"] == "plan-initial"
    assert report["relay"] == []
    assert not (tmp_path / "run/state/expskill/plan-graphs").exists()
    assert "host_integration" not in report["observations"]
    actor = report["actors"]["plan-initial"]
    assert json.loads(actor["final_text"])["failure"] == report["failure"]["actor_failure"]
    assert Path(actor["attempts"][0]["raw_events"]).is_file()
    assert report["budgets"]["spent_or_reserved_actor_calls"] == 1


def test_private_artifact_locator_rejects_traversal_and_symlink(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    with pytest.raises(probe.ProbeError, match="out-of-scope"):
        probe.private_file(private / "../outside.json", private)
    (private / "linked.json").symlink_to(outside)
    with pytest.raises(probe.ProbeError, match="Symlinked"):
        probe.private_file(private / "linked.json", private)


@pytest.mark.parametrize("fault", [None, "screenshot", "record-output", "scope", "missing-state"])
@pytest.mark.parametrize("bytecode_disabled", [False, True])
def test_synthetic_inventory_binding_verifier_preserves_byte_and_command_checks(tmp_path, fault, bytecode_disabled):
    worktree, state = tmp_path / "worktree", tmp_path / "state"
    worktree.mkdir()
    (state / "records").mkdir(parents=True)
    observations = []
    for width in probe.WIDTHS.values():
        for theme in probe.THEMES:
            for control in probe.STATES:
                name = f"{width}-{theme}-{control}.png"
                path = worktree / "review" / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"SYNTHETIC INVENTORY CONTRACT FIXTURE, NOT A BROWSER IMAGE")
                observations.append({"viewport": width, "theme": theme, "state": control, "screenshot": name, "screenshot_sha256": probe.digest(path.read_bytes())})
    proof = {"schema_version": "native-ui-fixture-check.v1", "passed": True, "observations": observations, "limitations": ["Synthetic byte-binding fixture, no browser claim"]}
    if fault == "missing-state":
        proof["observations"].pop()
    probe.retain(worktree / "review/native-checks.json", proof)
    output = state / "records/fixture.output"
    output.write_bytes(probe.canonical(proof))
    record = {"record_id": "fixture", "exit": 0, "command": "python3 " + ("-B " if bytecode_disabled else "") + "native_checks.py native-checks --browser /browser --output review", "output_digest": probe.digest(output.read_bytes())}
    design = {"scope": {"owned_paths": probe.SOURCE_FILES + probe.review_paths()}, "evidence": {"E1": {"technical": {"results": [record]}}}}
    if fault == "screenshot":
        (worktree / "review" / observations[0]["screenshot"]).write_bytes(b"changed")
    if fault == "record-output":
        output.write_bytes(b"changed")
    if fault == "scope":
        design["scope"]["owned_paths"].append("unrelated.txt")
    if fault is not None:
        with pytest.raises(probe.ProbeError):
            probe.verify_native_preview(design, worktree, state, "/browser")
    else:
        assert probe.verify_native_preview(design, worktree, state, "/browser")["observations"] == 18


def test_offline_real_helper_flow_retains_answers_delivery_join_and_current_plan(tmp_path, framework_source, monkeypatch):
    owners = ScriptedOwners()
    monkeypatch.setattr(probe, "native_checks", offline_static_fixture)
    repository, revision = framework_source
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "offline-protocol-observed", report.get("failure")
    assert len(owners.calls) == 9
    assert report["observations"]["current_plan"]["derived_state"] == "ready"
    assert report["observations"]["host_integration"]["after"] == report["observations"]["delivery"]["candidate_commit"]
    assert report["observations"]["native_verification"]["passed"]
    assert report["observations"]["native_verification"]["kind"] == "offline-static-fixture-only"
    assert report["claims"]["human_visual_approval"] is False
    runtime = report["python_runtime"]
    assert runtime["executable"] == str(Path(sys.executable).absolute())
    assert runtime["resolved_executable"] == str(Path(sys.executable).resolve())
    assert runtime["sha256"] == probe.digest(Path(sys.executable).read_bytes())
    assert runtime["check"]["exit_code"] == 0
    runtime_artifact = Path(runtime["artifact"]["path"])
    assert probe.digest(runtime_artifact.read_bytes()) == runtime["artifact"]["sha256"]
    assert all(runtime["executable"] in prompt and "For every Python helper command" in prompt for _, prompt, _ in owners.calls)
    assert all(runtime["executable"] + " -B" in prompt for _, prompt, _ in owners.calls)
    assert "-B" in runtime["check"]["argv"]
    assert runtime["bytecode_writes"] == "disabled by required -B flag"
    snapshot = report["framework_snapshot"]
    manifest = json.loads(Path(snapshot["inventory"]["path"]).read_bytes())
    assert manifest["framework_revision"] == revision
    assert manifest["files"] == probe.tree_pin(Path(snapshot["root"]), ignore_locks=False)
    by_name = {name: configuration for name, _, configuration in owners.calls}
    for name in ("design-initial", "design-answer"):
        assert by_name[name]["sandbox"] == "danger-full-access"
        assert by_name[name]["additional_write_dirs"] == []
    assert by_name["plan-initial"]["sandbox"] == "workspace-write"
    assert by_name["plan-independent-audit"]["sandbox"] == "read-only"
    reader = by_name["design-replacement"]
    assert reader["sandbox"] == "workspace-write"
    assert reader["cwd"] != report["fixture"]["design_worktree"]
    assert reader["state_home"] != str(tmp_path / "run/state")
    assert reader["additional_write_dirs"] == [tmp_path / "run/state/expskill/design"]
    assert report["capability_scope"]["design_writer"]["filesystem_isolation"] is False
    records = report["observations"]["audit_result"]["records"]
    assert records["spent_calls"] == 1
    assert records["remaining"]["calls"] == 2
    assert records["outstanding_dispatch_ids"] == []
    dispatch = records["dispatches"]["routed-probe-audit-1"]
    assert dispatch["actor_session_id"] == report["actors"]["plan-independent-audit"]["thread_ids"][0]
    for answer in report["relay"]:
        owner = report["actors"][answer["phase"] + "-initial"]
        resumed = report["actors"][answer["phase"] + "-answer"]
        assert answer["thread_id"] == owner["thread_ids"][0] == resumed["thread_ids"][0]
    audit_prompt = next(prompt for name, prompt, _ in owners.calls if name == "plan-independent-audit")
    assert "resolved_question_id" not in audit_prompt
    assert "score" not in audit_prompt.lower()
    for _, prompt, _ in owners.calls[:2]:
        assert "git var GIT_AUTHOR_IDENT" in prompt
        assert "git var GIT_COMMITTER_IDENT" in prompt


@pytest.mark.parametrize("fault", ["wrong-resume", "audit-finding"])
def test_negative_transport_or_audit_never_integrates_or_claims_observation(tmp_path, fault, framework_source, monkeypatch):
    monkeypatch.setattr(probe, "native_checks", offline_static_fixture)
    repository, revision = framework_source
    owners = ScriptedOwners(fault)
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert "host_integration" not in report["observations"]
    assert report["failure"]["reason"]
    assert any(name == ("plan-answer" if fault == "wrong-resume" else "plan-audit-result") for name, _, _ in owners.calls)
    if fault == "audit-finding":
        retained = report["observations"]["audit_result"]["records"]["dispatches"]["routed-probe-audit-1"]
        assert retained["result"]["findings"][0]["severity"] == "high"
        assert retained["result"]["findings"][0]["disposition"] == "open"


@pytest.mark.parametrize("fault", ["foreign-state", "target-write", "input-mutation", "runtime-evidence"])
def test_owner_and_accepted_scope_mutations_stop_before_answer_relay(tmp_path, framework_source, fault):
    repository, revision = framework_source
    owners = ScriptedOwners(fault)
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert len(owners.calls) == 1
    assert report["relay"] == []
    assert "host_integration" not in report["observations"]


@pytest.mark.parametrize("fault", ["reader-source-write", "reader-state-write"])
def test_private_loader_accommodation_still_rejects_reader_product_or_state_edits(tmp_path, framework_source, monkeypatch, fault):
    repository, revision = framework_source
    monkeypatch.setattr(probe, "native_checks", offline_static_fixture)
    owners = ScriptedOwners(fault)
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert len(owners.calls) == 5
    assert owners.calls[-1][0] == "design-replacement"
    assert "host_integration" not in report["observations"]
    assert "changed" in report["failure"]["reason"]


@pytest.mark.parametrize("fault,kind,path", [
    ("framework-changed", "changed", "skills/design/SKILL.md"),
    ("framework-missing", "missing", "skills/design/SKILL.md"),
    ("framework-added", "added", "scripts/.lock"),
])
def test_actual_protected_snapshot_mutations_remain_blocking_with_exact_diagnostics(tmp_path, framework_source, monkeypatch, fault, kind, path):
    repository, revision = framework_source
    monkeypatch.setattr(probe, "native_checks", offline_static_fixture)
    owners = ScriptedOwners(fault)
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=owners, live=True, browser=sys.executable)
    assert report["outcome"] == "probe-blocked"
    assert len(owners.calls) == 2
    assert report["relay"] == []
    assert "host_integration" not in report["observations"]
    failure = report["observations"]["integrity_failure"]
    assert failure["dispatch_id"] == "design-initial"
    assert list(failure["changes"][kind]) == [path]
    assert all(not value for name, value in failure["changes"].items() if name != kind)
    assert path in report["failure"]["reason"]
    if kind == "changed":
        hashes = failure["changes"][kind][path]
        assert hashes["expected"] != hashes["actual"]


def test_interruption_keeps_reserved_dispatch_and_private_fixture(tmp_path, framework_source):
    repository, revision = framework_source
    with pytest.raises(KeyboardInterrupt):
        probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=ScriptedOwners("interruption"), live=True, browser=sys.executable)
    report = json.loads((tmp_path / "run/report.json").read_bytes())
    assert report["outcome"] == "probe-interrupted"
    assert report["budgets"]["spent_or_reserved_actor_calls"] == 1
    assert report["budgets"]["outstanding"] == ["plan-initial"]


@pytest.mark.skipif(os.environ.get("EXPSKILL_BROWSER_TESTS") != "1", reason="Actual browser integration is explicitly opt-in, not part of dependency-free contract coverage")
def test_native_browser_counterexample_rejects_unrepaired_fixture(tmp_path):
    for name, text in DEFINITION["fixture_files"].items():
        (tmp_path / name).write_text(text)
    with pytest.raises(probe.ProbeError, match="contract failed"):
        probe.native_checks(tmp_path, tmp_path / "review", probe.browser_path())


@pytest.mark.skipif(os.environ.get("EXPSKILL_BROWSER_TESTS") != "1", reason="Actual browser integration is explicitly opt-in, not part of dependency-free contract coverage")
def test_actual_browser_and_real_helpers_in_offline_scripted_protocol(tmp_path, framework_source):
    repository, revision = framework_source
    report = probe.run_probe(output_root=tmp_path / "run", repository=repository, revision=revision, driver=ScriptedOwners(native=True), live=True)
    assert report["outcome"] == "offline-protocol-observed", report.get("failure")
    assert len(report["observations"]["native_verification"]["observations"]) == 18
    assert all(item["screenshot_sha256"] for item in report["observations"]["native_verification"]["observations"])
