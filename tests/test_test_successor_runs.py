import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_test_evidence_finalizer import write_draft


ROOT = Path(__file__).resolve().parents[1]
HELPERS = Path(os.environ.get("EXPSKILL_TEST_HELPERS", ROOT / "plugins/expskill/content/skills/test/scripts"))
IDENTITY = "g-imhoff <152416066+g-imhoff@users.noreply.github.com>"


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


class Runtime:
    def __init__(self, repository):
        self.repository = repository
        repository.mkdir()
        self.git("init", "-q", "-b", "feature/successor-test")
        self.git("config", "user.name", "g-imhoff")
        self.git("config", "user.email", "152416066+g-imhoff@users.noreply.github.com")
        (repository / "client.py").write_text("print('consumer=pass')\n")
        (repository / "harness.py").write_text("from pathlib import Path\nimport sys\nprint('harness=' + Path('test_fixture.txt').read_text().strip())\nsys.exit(0 if Path('test_fixture.txt').read_text().strip() == 'pass' else 1)\n")
        (repository / "test_fixture.txt").write_text("broken\n")
        (repository / ".gitignore").write_text(".test-evidence/\n")
        self.git("add", ".")
        self.commit("fixture")
        self.scope = {
            "schema_version": "test-charter-preparation.v2", "workflow_id": None,
            "accepted_behavior": "The consumer and test harness accept the observed outcome.",
            "scope": {"accepted_behavior": "The consumer and test harness accept the observed outcome.",
                      "inner_ring": ["consumer"], "adjacent_ring": ["test harness"], "broader_ring": ["canary"]},
            "material_oracles": [{"oracle_id": "accepted-outcome", "behavior": "Consumer and harness succeed.",
                                  "consumer_surface": "client.py and harness.py", "required_action_ids": ["consumer", "canary", "harness"]}],
            "exemption_grounding_artifact_ids": [],
            "execution_budget": {"semantic_actions_max": "8", "waves_max": "2", "usable_budget_seconds": "780",
                                 "rationale": "Reserve complete scope reruns after recovery.", "waves": [["consumer", "canary", "harness"], ["final-proof"]]},
        }

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repository, text=True).strip()

    def commit(self, message):
        assert self.git("var", "GIT_AUTHOR_IDENT").startswith(IDENTITY + " ")
        assert self.git("var", "GIT_COMMITTER_IDENT").startswith(IDENTITY + " ")
        self.git("commit", "-q", "-m", message)
        assert self.git("show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") == IDENTITY + " | " + IDENTITY

    def helper(self, name, *args):
        return subprocess.run([sys.executable, str(HELPERS / name), *map(str, args)], cwd=self.repository,
                              capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def bootstrap(self, *args):
        result = self.helper("bootstrap_run.py", *args)
        assert result.returncode == 0, result.stderr
        opening = json.loads(result.stdout.splitlines()[0].split("=", 1)[1])
        return Path(opening["root"])

    def freeze(self, root, scope=None):
        write_json(root / "charter-preparation.json", self.scope if scope is None else scope)
        return self.helper("freeze_charter.py", "--root", root)

    def spec(self, action):
        return {"schema_version": "test-final-action.v2", "observation_path": f"artifacts/{action}.raw",
                "metadata_path": f"artifacts/{action}.record.json", "expected_exit_code": "0",
                "output_predicate": {"mode": "exact-text", "value": "harness=pass\n" if action == "harness" else "consumer=pass\n"},
                "integrity_paths": ["client.py", "harness.py"], "cleanup_absent_paths": []}

    def record(self, root, action):
        entry = {"action_id": action, "role": "check", "ring": "inner", "action": "Observe " + action,
                 "path": [], "expected": "Accepted outcome passes.", "oracle_ids": ["accepted-outcome"],
                 "artifact_ids": [action + "-raw", action + "-record"]}
        value = {"schema_version": "test-recorded-action.v1", "entry": entry,
                 "command": [sys.executable, "harness.py" if action == "harness" else "client.py"], "execution": self.spec(action)}
        write_json(root / (action + ".json"), value)
        return self.helper("record_final_action.py", "record", "--root", root, "--spec", action + ".json")

    def terminal(self, root):
        final = {"action_id": "final-proof", "role": "check", "ring": "inner", "action": "Repeat consumer",
                 "path": [], "expected": "Accepted consumer outcome passes.", "actual": "exit=0 output=match teardown=pass integrity=pass",
                 "status": "pass", "oracle_ids": ["accepted-outcome"], "artifact_ids": ["final-proof-raw", "final-proof-record"]}
        write_json(root / "ledger-batch.json", {"schema_version": "test-ledger-batch.v1", "entries": [final]})
        result = self.helper("append_ledger.py", "--root", root)
        if result.returncode:
            return result
        write_draft(root)
        draft_path = root / "draft.json"
        draft = json.loads(draft_path.read_text())
        draft["artifacts"] = [{"artifact_id": action + "-" + kind, "kind": "log", "path": self.spec(action)[field]}
                              for action in ("consumer", "canary", "harness", "final-proof")
                              for kind, field in (("raw", "observation_path"), ("record", "metadata_path"))]
        draft["artifacts"].append({"artifact_id": "lineage", "kind": "log", "path": "bootstrap.json"})
        active = [rule["rule_id"] for rule in draft.pop("rule_applicability") if rule["status"] == "active"]
        draft_path.unlink()
        write_json(root / "draft-preparation.json", {"schema_version": "test-draft-preparation.v3", "draft_candidate": draft,
                   "rule_disposition": "evaluate", "active_rule_conditions": [],
                   "rule_assessment_groups": [{"rule_ids": active, "outcome": "satisfied", "evidence_action_ids": ["consumer", "canary", "harness", "final-proof"]}]})
        write_json(root / "draft-final-delta.json", {"schema_version": "test-draft-final-delta.v2", "resolutions": []})
        write_json(root / "final-action.json", self.spec("final-proof"))
        return self.helper("record_final_action.py", "handoff", "--root", root, "--", sys.executable, "client.py")


@pytest.fixture
def runtime(tmp_path):
    instance = Runtime(tmp_path / "repository")
    yield instance
    evidence = instance.repository / ".test-evidence"
    if evidence.exists():
        evidence.chmod(0o700)


def failed_run(runtime):
    root = runtime.bootstrap()
    result = runtime.freeze(root)
    assert result.returncode == 0, result.stderr
    for action in ("consumer", "canary", "harness"):
        result = runtime.record(root, action)
        assert result.returncode == (1 if action == "harness" else 0), result.stderr
    return root


@pytest.mark.parametrize("kind", ("test-system-defect", "environment-blocker"))
def test_actual_failed_recording_recovers_only_in_successor_with_full_scope_and_original_allowance(runtime, kind):
    previous = failed_run(runtime)
    snapshot = {path.relative_to(previous): path.read_bytes() for path in previous.rglob("*") if path.is_file()}
    (runtime.repository / "test_fixture.txt").write_text("pass\n")
    successor = runtime.bootstrap("--successor-of", previous, "--recovery-kind", kind, "--correction", "Corrected the owned local fixture prerequisite.")
    frozen = runtime.freeze(successor)
    assert frozen.returncode == 0, frozen.stderr
    for action in ("consumer", "canary", "harness"):
        result = runtime.record(successor, action)
        assert result.returncode == 0, result.stderr
    result = runtime.terminal(successor)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "terminal_preflight=PASS" in result.stdout
    assert all((previous / path).read_bytes() == raw for path, raw in snapshot.items())
    old_opening = json.loads((previous / "bootstrap.json").read_text())
    opening = json.loads((successor / "bootstrap.json").read_text())
    assert opening["started_at"] == old_opening["started_at"]
    assert opening["successor"]["classification"] == kind
    assert json.loads((previous / "ledger.json").read_text())["entries"][-1]["status"] == "fail"
    closed = runtime.record(previous, "harness")
    assert closed.returncode != 0 and "closed-run" in closed.stderr


def test_successor_rejects_scope_reduction_and_budget_reset(runtime):
    previous = failed_run(runtime)
    successor = runtime.bootstrap("--successor-of", previous, "--recovery-kind", "test-system-defect", "--correction", "Corrected owned fixture.")
    scope = json.loads(json.dumps(runtime.scope))
    scope["execution_budget"]["semantic_actions_max"] = "9"
    result = runtime.freeze(successor, scope)
    assert result.returncode != 0 and "reset the execution budget" in result.stderr
    scope = json.loads(json.dumps(runtime.scope))
    scope["scope"]["broader_ring"] = []
    result = runtime.freeze(successor, scope)
    assert result.returncode != 0 and "change accepted scope" in result.stderr
    assert not (successor / "charter.json").exists()


def test_successor_history_tampering_blocks_recording_before_execution(runtime):
    previous = failed_run(runtime)
    successor = runtime.bootstrap("--successor-of", previous, "--recovery-kind", "test-system-defect", "--correction", "Corrected owned fixture.")
    assert runtime.freeze(successor).returncode == 0
    (previous / "artifacts/harness.raw").write_text("forged passing history\n")
    result = runtime.record(successor, "consumer")
    assert result.returncode != 0 and "predecessor evidence changed" in result.stderr
    assert not (successor / "artifacts/consumer.raw").exists()


def test_recovery_does_not_allow_failed_history_to_be_relabelled_as_pass(runtime):
    previous = failed_run(runtime)
    (runtime.repository / "test_fixture.txt").write_text("pass\n")
    result = runtime.terminal(previous)
    assert result.returncode != 0
    assert json.loads((previous / "ledger.json").read_text())["entries"][2]["status"] == "fail"


def test_successor_cannot_spend_predecessor_actions_again(runtime):
    runtime.scope["execution_budget"]["semantic_actions_max"] = "6"
    previous = failed_run(runtime)
    successor = runtime.bootstrap("--successor-of", previous, "--recovery-kind", "test-system-defect", "--correction", "Corrected owned fixture.")
    result = runtime.freeze(successor)
    assert result.returncode != 0 and "remaining cumulative allowance" in result.stderr
    assert not (successor / "charter.json").exists()


def test_successor_cannot_restart_an_expired_original_deadline(runtime):
    previous = failed_run(runtime)
    opening = json.loads((previous / "bootstrap.json").read_text())
    opening["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=900)).isoformat()
    write_json(previous / "bootstrap.json", opening)
    result = runtime.helper("bootstrap_run.py", "--successor-of", previous, "--recovery-kind", "environment-blocker", "--correction", "Corrected local prerequisite.")
    assert result.returncode != 0 and "allowance-exhausted" in result.stderr
    assert not (previous / "successor.json").exists()


@pytest.mark.parametrize("initially_failed", (False, True))
def test_actual_test_side_commit_requires_new_head_successor_and_complete_reruns(runtime, initially_failed):
    previous = runtime.bootstrap()
    assert runtime.freeze(previous).returncode == 0
    if not initially_failed:
        (runtime.repository / "test_fixture.txt").write_text("pass\n")
    for action in ("consumer", "canary", "harness"):
        result = runtime.record(previous, action)
        assert result.returncode == (1 if action == "harness" and initially_failed else 0), result.stderr
    old_head = json.loads((previous / "charter.json").read_text())["head"]
    old_ledger = (previous / "ledger.json").read_bytes()
    (runtime.repository / "test_fixture.txt").write_text("pass\n")
    runtime.git("add", "test_fixture.txt")
    runtime.commit("retain test fixture correction")
    new_head = runtime.git("rev-parse", "HEAD")
    assert new_head != old_head
    stale = runtime.record(previous, "stale-consumer")
    assert stale.returncode != 0 and "revision-mismatch" in stale.stderr
    options = ["--successor-of", previous, "--after-test-commit", "--test-owned-path", "test_fixture.txt", "--correction", "One permitted fixture-only commit; accepted behavior unchanged."]
    if initially_failed:
        options.extend(["--recovery-kind", "test-system-defect"])
    successor = runtime.bootstrap(*options)
    result = runtime.freeze(successor)
    assert result.returncode == 0, result.stderr
    for action in ("consumer", "canary", "harness"):
        result = runtime.record(successor, action)
        assert result.returncode == 0, result.stderr
    result = runtime.terminal(successor)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout.splitlines()[-1])["receipt"]
    assert receipt["head"] == new_head
    assert (previous / "ledger.json").read_bytes() == old_ledger
    opening = json.loads((successor / "bootstrap.json").read_text())
    assert opening["successor"]["test_owned_paths"] == ["test_fixture.txt"]
    assert opening["started_at"] == json.loads((previous / "bootstrap.json").read_text())["started_at"]


def test_postcommit_successor_rejects_misdeclared_paths_or_unchanged_head(runtime):
    previous = failed_run(runtime)
    options = ("--successor-of", previous, "--after-test-commit", "--test-owned-path", "test_fixture.txt", "--correction", "Owned fixture commit.")
    unchanged = runtime.helper("bootstrap_run.py", *options)
    assert unchanged.returncode != 0
    (runtime.repository / "test_fixture.txt").write_text("pass\n")
    runtime.git("add", "test_fixture.txt")
    runtime.commit("fixture correction")
    incorrect = runtime.helper("bootstrap_run.py", "--successor-of", previous, "--after-test-commit", "--test-owned-path", "wrong-fixture.txt", "--correction", "Owned fixture commit.")
    assert incorrect.returncode != 0 and "changed paths" in incorrect.stderr
    unclassified = runtime.helper("bootstrap_run.py", *options)
    assert unclassified.returncode != 0 and "classified permitted recovery" in unclassified.stderr
    assert not (previous / "successor.json").exists()
