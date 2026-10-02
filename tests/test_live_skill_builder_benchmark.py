import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("live_builder_probe", ROOT / "scripts/live_skill_builder_benchmark.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
CORRECT = (
    "import json\nfrom pathlib import Path\nimport sys\n"
    "text = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
    "print(json.dumps({'line_count': len(text.splitlines()), 'elapsed': 0.123}))\n"
)
SKILL = "---\nname: count-lines\ndescription: Count lines in one UTF-8 file.\n---\nRun scripts/count_lines.py with the input file path.\n"


def run_probe(**options):
    options.setdefault("framework_revision", "a" * 40)
    return PROBE.run_probe(**options)


class MockTransport:
    def __init__(self, *, initially_correct=True, fault=None):
        self.initially_correct = initially_correct
        self.fault = fault
        self.calls = []

    def git(self, repository, *args):
        return args[-1].split("^")[0]

    def snapshot(self, repository, revision, destination):
        content = destination / "plugins/expskill/content"
        for relative in ("skills/skill-builder/SKILL.md", "skills/skill-builder/references/evaluation-rubric.md"):
            target = content / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "plugins/expskill/content" / relative, target)
        return content

    def run_process_group(self, argv, *, prompt, cwd, env, timeout):
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
        return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode, "timed_out": False}

    def run_actor(self, *, prompt, cwd, state_home, evidence_dir, **options):
        role = evidence_dir.name
        self.calls.append({"role": role, "prompt": prompt, **options})
        evidence_dir.mkdir(parents=True)
        package = cwd / "candidate"
        root = evidence_dir.parents[1]
        if role == "candidate-author":
            (package / "scripts").mkdir()
            (package / "SKILL.md").write_text(SKILL)
            (package / "scripts/count_lines.py").write_text(CORRECT if self.initially_correct else PROBE.FAULT_SOURCE)
        elif role == "repair-author":
            if self.fault != "no-repair-change":
                (package / "scripts/count_lines.py").write_text(CORRECT)
            if self.fault == "repair-scope":
                (package / "SKILL.md").write_text(SKILL + "Changed outside repair scope.\n")
        pin = PROBE.manifest(package)["digest"]
        thread = "mock-same-thread" if self.fault == "reused-thread" else f"mock-{role}"
        events = [{"type": "thread.started", "thread_id": thread, "mock_transport": True}]
        for relative in ("skills/skill-builder/SKILL.md", "skills/skill-builder/references/evaluation-rubric.md"):
            source = root / "framework-snapshot/plugins/expskill/content" / relative
            if self.fault != "missing-framework-read":
                events.append({"type": "item.completed", "item": {"type": "command_execution", "command": shlex.join(["cat", str(source)]), "exit_code": 0}})
        if self.fault == "framework-source-tamper":
            source.chmod(0o644)
            source.write_text("Changed framework source")
        needs_commands = role in {"trial-before", "trial-after", "independent-verifier"}
        if needs_commands and not (role == "independent-verifier" and self.fault == "missing-verifier-execution"):
            for case_id in ("two-lines", "empty-file", "missing-file"):
                argv = [sys.executable, str(package / "scripts/count_lines.py"), str(root / "fixtures" / f"{case_id}.txt")]
                result = self.run_process_group(argv, prompt="", cwd=cwd, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10)
                command = " ".join(argv)
                if self.fault == "echo-only-trial" and role == "trial-before":
                    command = "echo " + command
                events.append({"type": "item.completed", "item": {"type": "command_execution", "command": command,
                              "exit_code": result["exit_code"], "aggregated_output": result["stdout"] + result["stderr"]}})
        if role in {"trial-before", "trial-after"}:
            reply = {"candidate_digest": pin, "executed_case_ids": ["two-lines", "empty-file", "missing-file"]}
        elif role in {"reviewer-before", "independent-grader", "independent-verifier"}:
            observation_role = {"reviewer-before": "trial-before", "independent-grader": "trial-after", "independent-verifier": "verification"}[role]
            observation_path = root / "checks" / observation_role / "observations.json"
            observation = json.loads(observation_path.read_text())
            results = {item["case_id"]: item["passed"] for item in observation["checks"]}
            reply = {"candidate_digest": pin, "criterion_results": results,
                     "grade": "pass" if all(results.values()) else "fail",
                     "evidence_digests": [PROBE.digest(observation_path.read_bytes())], "reason": "Mocked unit-test judgment of recorded command facts."}
            if role == "independent-grader":
                if self.fault == "stale-grade":
                    reply["candidate_digest"] = "0" * 64
                if self.fault == "wrong-grade":
                    reply["grade"] = "fail"
                if self.fault == "unbound-grade":
                    reply["evidence_digests"] = ["0" * 64]
                if self.fault == "integer-criteria":
                    reply["criterion_results"] = {key: 1 for key in results}
                if self.fault == "read-only-write":
                    (package / "scripts/count_lines.py").write_text(PROBE.FAULT_SOURCE)
                if self.fault == "past-output-tamper":
                    (root / "checks/trial-before/two-lines.stdout").write_text("rewritten historical output")
        else:
            reply = {"stage": role, "candidate_digest": pin}
        text = json.dumps(reply)
        events.append({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
        raw = evidence_dir / "events.jsonl"
        raw.write_text("".join(json.dumps(event) + "\n" for event in events))
        attempt = {"raw_events": str(raw), "raw_events_sha256": PROBE.digest(raw.read_bytes()), "thread_ids": [thread],
                   "exit_code": 0, "timed_out": False, "outcome": "completed-ungraded"}
        if self.fault == "raw-digest":
            attempt["raw_events_sha256"] = "0" * 64
        actor = {"outcome": "completed-ungraded", "final_text": text, "thread_ids": [thread],
                 "attempts": [attempt], "selected_attempt": 1, "argv": ["mock-transport"], "evidence": str(evidence_dir / "actor.json")}
        PROBE.retain_json(evidence_dir / "actor.json", actor)
        return actor


def test_live_opt_in_and_missing_shared_driver_are_explicit(tmp_path):
    output = tmp_path / "probe"
    with pytest.raises(PROBE.ProbeError, match="explicit --live"):
        run_probe(output_root=output, driver_path=tmp_path / "missing.py")
    with pytest.raises(PROBE.ProbeError, match="Missing shared live driver.*prerequisite"):
        run_probe(output_root=output, live=True, driver_path=tmp_path / "missing.py")
    assert not output.exists()


def test_correct_initial_candidate_does_not_claim_repair(tmp_path):
    driver = MockTransport()
    report = run_probe(output_root=tmp_path / "probe", live=True, driver=driver)
    assert report["outcome"] == "repair-not-demonstrated"
    assert [call["role"] for call in driver.calls] == ["candidate-author", "trial-before"]
    assert not report["fault_injection"]["applied"]
    assert report["full_builder_completion"] is False
    assert report["scorecard_created"] is False
    assert report["transport_mode"] == "injected-test-transport"


@pytest.mark.parametrize("initially_correct,inject_defect", ((True, True), (False, False)))
def test_actual_command_failure_repair_rerun_and_separate_grades_are_retained(tmp_path, initially_correct, inject_defect):
    driver = MockTransport(initially_correct=initially_correct)
    report = run_probe(output_root=tmp_path / "probe", live=True, driver=driver, inject_defect=inject_defect)
    assert report["outcome"] == "repair-observed"
    assert [call["role"] for call in driver.calls] == list(PROBE.ROLES)
    assert report["fault_injection"]["applied"] is inject_defect
    assert not report["observations"]["trial-before"]["all_passed"]
    assert report["observations"]["trial-after"]["all_passed"]
    assert report["observations"]["verification"]["all_passed"]
    assert report["candidate_authored"]["digest"] != report["candidate_repaired"]["digest"] or inject_defect
    assert len({actor["origin"]["thread_id"] for actor in report["actors"].values()}) == 7
    assert report["actors"]["reviewer-before"]["bounded_judgment"]["grade"] == "fail"
    assert report["actors"]["independent-grader"]["bounded_judgment"]["grade"] == "pass"
    assert report["actors"]["independent-verifier"]["bounded_judgment"]["grade"] == "pass"
    assert report["live_public_state_transitions"] == []
    assert report["framework"]["revision"] == "a" * 40
    for call in driver.calls:
        assert "Framework commit:" in call["prompt"]
        assert "Apply Skill Builder stage" in call["prompt"]
    for source in report["framework"]["sources"].values():
        assert PROBE.digest(Path(source["path"]).read_bytes()) == source["sha256"]
    for observations in report["observations"].values():
        evidence = observations["evidence"]
        assert PROBE.digest(Path(evidence["path"]).read_bytes()) == evidence["sha256"]
        for check in observations["checks"]:
            assert PROBE.digest(Path(check["stdout"]).read_bytes()) == check["stdout_sha256"]


@pytest.mark.parametrize("fault", (
    "reused-thread", "raw-digest", "stale-grade", "wrong-grade", "unbound-grade",
    "integer-criteria", "read-only-write", "repair-scope", "no-repair-change", "missing-verifier-execution", "past-output-tamper", "echo-only-trial", "missing-framework-read", "framework-source-tamper",
))
def test_origin_scope_and_judgment_failures_cannot_report_repair_success(tmp_path, fault):
    driver = MockTransport(fault=fault)
    report = run_probe(output_root=tmp_path / "probe", live=True, driver=driver, inject_defect=True)
    assert report["outcome"] == "probe-blocked"
    assert report["failure"]["reason"]
    retained = json.loads((tmp_path / "probe/report.json").read_text())
    assert retained["outcome"] == "probe-blocked"
    assert retained["actors"]


@pytest.mark.parametrize("limits", ({"actor_timeout": 241}, {"deadline_seconds": 1801}, {"deadline_seconds": 0}))
def test_probe_keeps_hard_budget_ceilings(tmp_path, limits):
    with pytest.raises(PROBE.ProbeError, match="timeout.*deadline"):
        run_probe(output_root=tmp_path / "probe", live=True, driver=MockTransport(), **limits)


def test_exact_command_origin_accepts_shell_wrapper_and_rejects_echo_or_other_candidate(tmp_path):
    package = tmp_path / "candidate"
    expected = tmp_path / "fixtures/two-lines.txt"
    argv = [sys.executable, str(package / "scripts/count_lines.py"), str(expected)]
    exact = shlex.join(argv)
    assert PROBE.helper_invocation(exact, package) == expected
    assert PROBE.helper_invocation(shlex.join(["/bin/zsh", "-lc", exact]), package) == expected
    assert PROBE.helper_invocation("echo " + exact, package) is None
    assert PROBE.helper_invocation(exact + " ; true", package) is None
    argv[1] = str(tmp_path / "other-candidate/scripts/count_lines.py")
    assert PROBE.helper_invocation(shlex.join(argv), package) is None
