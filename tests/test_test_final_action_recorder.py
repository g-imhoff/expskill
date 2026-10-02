from __future__ import annotations

import json
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RECORDER = (
    ROOT
    / "plugins"
    / "expskill"
    / "content"
    / "skills"
    / "test"
    / "scripts"
    / "record_final_action.py"
)


def _load_recorder_module():
    spec = importlib.util.spec_from_file_location(
        "test_final_action_recorder_handoff", RECORDER
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {RECORDER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class FinalActionRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name) / "repository"
        self.repository.mkdir()
        subprocess.run(
            ["git", "init", "-b", "feature/test-recorder"],
            cwd=self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Recorder"],
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "recorder@example.invalid"],
            cwd=self.repository,
            check=True,
        )
        (self.repository / "product.txt").write_text("unchanged\n", encoding="utf-8")
        (self.repository / "product_action.py").write_text(
            """from pathlib import Path
Path('.runtime').mkdir(exist_ok=True)
Path('.runtime/owned').write_text('owned\\n', encoding='utf-8')
print('consumer-result=pass')
""",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "add", "product.txt", "product_action.py"],
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "fixture"],
            cwd=self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repository, text=True
        ).strip()
        evidence = self.repository / ".test-evidence"
        evidence.mkdir(mode=0o700)
        self.addCleanup(self.unseal_evidence_for_cleanup)
        self.run_root = evidence / "run-1"
        self.run_root.mkdir(mode=0o700)
        behavior = "The public changed journey returns its accepted outcome."
        charter = {
            "schema_version": "test-charter.v1",
            "run_id": "run-1",
            "workflow_id": None,
            "repository": str(self.repository.resolve()),
            "branch": "feature/test-recorder",
            "head": self.head,
            "accepted_behavior": behavior,
            "scope": {
                "accepted_behavior": behavior,
                "inner_ring": ["Changed public journey."],
                "adjacent_ring": [],
                "broader_ring": [],
            },
            "material_oracles": [
                {
                    "oracle_id": "changed-outcome",
                    "behavior": behavior,
                    "consumer_surface": "python3 product_action.py",
                    "required_action_ids": ["changed"],
                }
            ],
            "exemption_grounding_artifact_ids": [],
        }
        (self.run_root / "charter.json").write_text(
            json.dumps(charter, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (self.run_root / "charter.json").chmod(0o600)

    def unseal_evidence_for_cleanup(self) -> None:
        evidence = self.repository / ".test-evidence"
        if evidence.is_dir():
            evidence.chmod(0o700)

    def write_spec(self, **updates: object) -> None:
        spec: dict[str, object] = {
            "schema_version": "test-final-action.v2",
            "observation_path": "final-observation.raw",
            "metadata_path": "final-action-metadata.json",
            "expected_exit_code": "0",
            "output_predicate": {
                "mode": "exact-text",
                "value": "consumer-result=pass\n",
            },
            "integrity_paths": ["product.txt", "product_action.py"],
            "cleanup_absent_paths": [".runtime"],
        }
        spec.update(updates)
        path = self.run_root / "final-action.json"
        path.write_text(
            json.dumps(spec, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def recorder(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RECORDER), *arguments],
            cwd=self.repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_validate_then_run_records_exact_output_cleanup_and_integrity(self) -> None:
        self.write_spec()

        validated = self.recorder("validate", "--root", str(self.run_root))
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertIn("final_action_validation=PASS", validated.stdout)
        self.assertFalse((self.repository / ".runtime").exists())
        self.assertFalse((self.run_root / "final-observation.raw").exists())

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            "product_action.py",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("final_action_predicate=MATCH", completed.stdout)
        self.assertEqual(
            (self.run_root / "final-observation.raw").read_bytes(),
            b"consumer-result=pass\n",
        )
        self.assertFalse((self.repository / ".runtime").exists())
        metadata = json.loads(
            (self.run_root / "final-action-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["outcome"], "match")
        self.assertEqual(metadata["teardown_status"], "pass")
        self.assertEqual(metadata["integrity_status"], "pass")
        mode = stat.S_IMODE((self.run_root / "final-observation.raw").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_mismatch_is_recorded_and_never_reports_success(self) -> None:
        self.write_spec(
            output_predicate={"mode": "exact-text", "value": "wrong\n"}
        )

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            "product_action.py",
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("final_action_predicate=MISMATCH", completed.stdout)
        self.assertIn(
            'final_action_components={"integrity":"pass","output_predicate":"mismatch","teardown":"pass"}',
            completed.stdout,
        )
        self.assertEqual(
            (self.run_root / "final-observation.raw").read_bytes(),
            b"consumer-result=pass\n",
        )
        self.assertFalse((self.repository / ".runtime").exists())
        metadata = json.loads(
            (self.run_root / "final-action-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["outcome"], "mismatch")

    def test_ordinary_record_derives_ledger_observation_from_actual_execution(self) -> None:
        self.write_spec()
        execution = json.loads((self.run_root / "final-action.json").read_text())
        execution["observation_path"] = "artifacts/changed.raw"
        execution["metadata_path"] = "artifacts/changed.record.json"
        entry = {
            "action_id": "changed", "role": "check", "ring": "inner",
            "action": "Run the consumer", "path": [], "expected": "The consumer passes.",
            "oracle_ids": ["changed-outcome"], "artifact_ids": ["changed-raw", "changed-record"],
        }
        manifest = {"schema_version": "test-recorded-action.v1", "entry": entry,
                    "command": [sys.executable, "product_action.py"], "execution": execution}
        for name, value in (("action-spec.json", manifest), ("ledger.json", {
            "schema_version": "test-action-ledger.v2", "run_id": "run-1", "entries": []
        })):
            path = self.run_root / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        completed = self.recorder("record", "--root", str(self.run_root), "--spec", "action-spec.json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        recorded = json.loads((self.run_root / "ledger.json").read_text())["entries"][0]
        self.assertEqual(recorded["status"], "pass")
        self.assertEqual(recorded["actual"], "exit=0 output=match teardown=pass integrity=pass")
        self.assertEqual((self.run_root / "artifacts/changed.raw").read_bytes(), b"consumer-result=pass\n")
        receipt = json.loads((self.run_root / "artifacts/changed.record.json").read_text())
        self.assertEqual(receipt["entry"], recorded)
        self.assertEqual(receipt["schema_version"], "test-execution-record.v1")
        from tests.test_test_evidence_finalizer import write_draft, invoke_finalizer
        write_draft(self.run_root)
        draft_path = self.run_root / "draft.json"
        draft = json.loads(draft_path.read_text())
        draft["artifacts"] = [
            {"artifact_id": "changed-raw", "kind": "log", "path": "artifacts/changed.raw"},
            {"artifact_id": "changed-record", "kind": "log", "path": "artifacts/changed.record.json"},
        ]
        draft_path.write_text(json.dumps(draft))
        draft_path.chmod(0o600)
        metadata_path = self.run_root / "artifacts/changed.record.json"
        original = metadata_path.read_bytes()
        receipt["entry"]["oracle_ids"] = []
        metadata_path.write_text(json.dumps(receipt))
        tampered = invoke_finalizer(self.run_root)
        self.assertNotEqual(tampered.returncode, 0, tampered.stdout)
        metadata_path.write_bytes(original)
        batch_entry = {
            "action_id": "final-proof", "role": "check", "ring": "inner", "action": "Repeat the changed consumer",
            "path": [], "expected": "Consumer passes", "actual": "exit=0 output=match teardown=pass integrity=pass",
            "status": "pass", "oracle_ids": ["changed-outcome"], "artifact_ids": ["final-raw", "final-record"],
        }
        batch_path = self.run_root / "ledger-batch.json"
        batch_path.write_text(json.dumps({"schema_version": "test-ledger-batch.v1", "entries": [batch_entry]}))
        batch_path.chmod(0o600)
        appender = RECORDER.with_name("append_ledger.py")
        appended = subprocess.run([sys.executable, str(appender), "--root", str(self.run_root)], cwd=self.repository, capture_output=True, text=True)
        self.assertEqual(appended.returncode, 0, appended.stderr)
        draft["artifacts"].extend([
            {"artifact_id": "final-raw", "kind": "log", "path": "final-observation.raw"},
            {"artifact_id": "final-record", "kind": "log", "path": "final-action-metadata.json"},
        ])
        active = [rule["rule_id"] for rule in draft.pop("rule_applicability") if rule["status"] == "active"]
        preparation = {"schema_version": "test-draft-preparation.v3", "draft_candidate": draft,
                       "rule_disposition": "evaluate", "active_rule_conditions": [],
                       "rule_assessment_groups": [{"rule_ids": active, "outcome": "satisfied", "evidence_action_ids": ["changed", "final-proof"]}]}
        draft_path.unlink()
        for name, value in (("draft-preparation.json", preparation), ("draft-final-delta.json", {"schema_version": "test-draft-final-delta.v2", "resolutions": []})):
            path = self.run_root / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        finalized = self.recorder("handoff", "--root", str(self.run_root), "--", sys.executable, "product_action.py")
        self.assertEqual(finalized.returncode, 0, finalized.stdout)

    def test_recorded_wave_runs_independent_argv_before_publishing_results(self) -> None:
        script = self.repository / "parallel_action.py"
        script.write_text("""import sys
import time
from pathlib import Path
root = Path('.test-parallel')
root.mkdir(exist_ok=True)
(root / sys.argv[1]).write_text('ready')
end = time.monotonic() + 3
while not (root / sys.argv[2]).exists():
    if time.monotonic() >= end:
        raise SystemExit(7)
    time.sleep(0.01)
print('consumer-result=pass')
""")
        subprocess.run(["git", "add", "parallel_action.py"], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-m", "parallel fixture"], cwd=self.repository, check=True, stdout=subprocess.PIPE)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repository, text=True).strip()
        charter_path = self.run_root / "charter.json"
        charter = json.loads(charter_path.read_text())
        charter["head"] = head
        charter_path.write_text(json.dumps(charter))
        self.write_spec()
        execution = json.loads((self.run_root / "final-action.json").read_text())
        actions = []
        for index, action_id in enumerate(("changed", "neighbor")):
            spec = dict(execution, observation_path=f"artifacts/{action_id}.raw", metadata_path=f"artifacts/{action_id}.json",
                        integrity_paths=["parallel_action.py"], cleanup_absent_paths=[".test-parallel"] if index == 0 else [])
            entry = {"action_id": action_id, "role": "check", "ring": "inner", "action": "Observe a parallel consumer",
                     "path": [], "expected": "Consumer passes", "oracle_ids": ["changed-outcome"],
                     "artifact_ids": [action_id + "-raw", action_id + "-record"]}
            actions.append({"schema_version": "test-recorded-action.v1", "entry": entry, "execution": spec,
                            "command": [sys.executable, "parallel_action.py", str(index), str(1 - index)]})
        for name, value in (("wave.json", {"schema_version": "test-recorded-wave.v1", "actions": actions}),
                            ("ledger.json", {"schema_version": "test-action-ledger.v2", "run_id": "run-1", "entries": []})):
            path = self.run_root / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        completed = self.recorder("record", "--root", str(self.run_root), "--spec", "wave.json")
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        entries = json.loads((self.run_root / "ledger.json").read_text())["entries"]
        self.assertEqual([entry["status"] for entry in entries], ["pass", "pass"])
        self.assertFalse((self.repository / ".test-parallel").exists())

    def test_ordinary_mismatch_is_derived_as_fail_and_handwritten_result_fields_are_rejected(self) -> None:
        self.write_spec(output_predicate={"mode": "exact-text", "value": "unobserved success\n"})
        execution = json.loads((self.run_root / "final-action.json").read_text())
        entry = {"action_id": "changed", "role": "check", "ring": "inner", "action": "Run consumer", "path": [],
                 "expected": "Expected response", "oracle_ids": ["changed-outcome"], "artifact_ids": ["raw", "record"]}
        manifest = {"schema_version": "test-recorded-action.v1", "entry": entry,
                    "command": [sys.executable, "product_action.py"], "execution": execution}
        ledger_path = self.run_root / "ledger.json"
        ledger_path.write_text(json.dumps({"schema_version": "test-action-ledger.v2", "run_id": "run-1", "entries": []}))
        ledger_path.chmod(0o600)
        path = self.run_root / "action-spec.json"
        entry["actual"] = "NOTRUN: expected success"
        entry["status"] = "pass"
        path.write_text(json.dumps(manifest))
        path.chmod(0o600)
        rejected = self.recorder("record", "--root", str(self.run_root), "--spec", "action-spec.json")
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse((self.run_root / "final-observation.raw").exists())
        del entry["actual"]
        del entry["status"]
        path.write_text(json.dumps(manifest))
        completed = self.recorder("record", "--root", str(self.run_root), "--spec", "action-spec.json")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        recorded = json.loads(ledger_path.read_text())["entries"][0]
        self.assertEqual(recorded["status"], "fail")
        self.assertEqual(recorded["actual"], "exit=0 output=mismatch teardown=pass integrity=pass")

    def test_json_predicate_accepts_stable_success_fields_with_variable_timing(self) -> None:
        module = _load_recorder_module()
        predicate = [
            {"path": ["status"], "operator": "equals", "value": "pass"},
            {"path": ["checks"], "operator": "integer-equals", "value": "3"},
        ]
        self.assertTrue(module._output_matches(b'{"status":"pass","checks":3,"elapsed":0.12,"run_id":"one"}', "json-fields", predicate))
        self.assertTrue(module._output_matches(b'{"status":"pass","checks":3,"elapsed":8.91,"run_id":"two"}', "json-fields", predicate))
        self.assertFalse(module._output_matches(b'{"status":"fail","checks":3,"elapsed":0.12}', "json-fields", predicate))

    def test_json_predicate_rejects_missing_duplicate_and_wrongly_typed_fields(self) -> None:
        module = _load_recorder_module()
        predicate = [{"path": ["checks"], "operator": "integer-equals", "value": "3"}]
        for output in (b'{}', b'{"checks":"3"}', b'{"checks":true}', b'{"checks":3,"checks":0}', b'{"checks":NaN}'):
            with self.subTest(output=output):
                self.assertFalse(module._output_matches(output, "json-fields", predicate))

    def test_json_predicate_is_validated_without_regex_or_executable_expressions(self) -> None:
        self.write_spec(output_predicate={"mode": "json-fields", "value": [{"path": ["status"], "operator": "equals", "value": "pass"}]})
        validated = self.recorder("validate", "--root", str(self.run_root))
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.write_spec(output_predicate={"mode": "json-fields", "value": [{"path": ["status"], "operator": "regex", "value": ".*"}]})
        rejected = self.recorder("validate", "--root", str(self.run_root))
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("invalid-predicate", rejected.stderr)

    def test_exit_only_predicate_keeps_the_exact_exit_condition(self) -> None:
        self.write_spec(output_predicate={"mode": "exit-only", "value": ""})
        completed = self.recorder("run", "--root", str(self.run_root), "--", sys.executable, "product_action.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_real_variable_json_command_matches_without_transcribing_output(self) -> None:
        (self.repository / "json_action.py").write_text(
            "import json, secrets, time\nprint(json.dumps({'status': 'pass', 'elapsed': time.monotonic(), 'run_id': secrets.token_hex(8)}))\n"
        )
        self.write_spec(
            output_predicate={"mode": "json-fields", "value": [{"path": ["status"], "operator": "equals", "value": "pass"}]},
            cleanup_absent_paths=[],
        )

        completed = self.recorder("run", "--root", str(self.run_root), "--", sys.executable, "json_action.py")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        observation = json.loads((self.run_root / "final-observation.raw").read_text())
        self.assertEqual(observation["status"], "pass")
        self.assertIsInstance(observation["elapsed"], float)
        self.assertEqual(len(observation["run_id"]), 16)

    def test_validation_rejects_a_dirty_integrity_path_before_product_execution(
        self,
    ) -> None:
        """A known dirty path must be corrected before the final action runs."""

        self.write_spec()
        (self.repository / "product.txt").write_text("intentionally changed\n")

        completed = self.recorder("validate", "--root", str(self.run_root))

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dirty-integrity-path", completed.stderr)
        self.assertFalse((self.repository / ".runtime").exists())
        self.assertFalse((self.run_root / "final-observation.raw").exists())

    def test_run_rejects_inline_interpreters_before_product_execution(self) -> None:
        self.write_spec()

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            "-c",
            "print('consumer-result=pass')",
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("inline-command-forbidden", completed.stderr)
        self.assertFalse((self.run_root / "final-observation.raw").exists())

    def test_handoff_rejects_inline_interpreters_before_composition(self) -> None:
        """An invalid command cannot publish a draft or consume the run root."""

        commands = (
            [sys.executable, "-c", "print('consumer-result=pass')"],
            [sys.executable, "-I", "-c", "print('consumer-result=pass')"],
            [sys.executable, "-Ic", "print('consumer-result=pass')"],
            ["env", "python3", "-c", "print('consumer-result=pass')"],
            ["env", "-S", "python3 -c print('consumer-result=pass')"],
            ["bash", "-xc", "printf accepted"],
        )
        for command in commands:
            with self.subTest(command=command):
                recorder = _load_recorder_module()
                root = self.run_root.resolve()
                child_run = mock.Mock()
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        recorder, "_repository", return_value=self.repository
                    ),
                    mock.patch.object(recorder, "_run_root", return_value=root),
                    mock.patch.object(recorder, "_read_spec", return_value={}),
                    mock.patch.object(recorder, "_validate_spec", return_value={}),
                    mock.patch.object(recorder.subprocess, "run", child_run),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(stderr),
                ):
                    result = recorder.main(
                        ["handoff", "--root", str(root), "--", *command]
                    )

                self.assertEqual(result, 2)
                self.assertIn("inline-command-forbidden", stderr.getvalue())
                child_run.assert_not_called()
                self.assertFalse((root / "draft.json").exists())

    def test_product_cannot_mutate_authenticated_evidence_before_recording(self) -> None:
        """Evidence changed by the product action must never reach finalization."""

        self.write_spec()
        action = self.repository / "tamper_evidence.py"
        action.write_text(
            """from pathlib import Path
Path('.test-evidence/run-1/charter.json').write_text('{}\\n', encoding='utf-8')
print('consumer-result=pass')
""",
            encoding="utf-8",
        )

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            action.name,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("run-root-changed", completed.stderr)
        self.assertFalse((self.run_root / "final-observation.raw").exists())
        self.assertFalse((self.run_root / "final-action-metadata.json").exists())

    def test_product_cannot_redirect_outputs_through_a_parent_symlink(self) -> None:
        """Recorder output stays beneath the authenticated run root."""

        outside = self.repository / "outside-evidence"
        outside.mkdir()
        self.write_spec(
            observation_path="outputs/final.raw",
            metadata_path="outputs/final.json",
        )
        action = self.repository / "replace_output_parent.py"
        action.write_text(
            f"""from pathlib import Path
import shutil
parent = Path('.test-evidence/run-1/outputs')
shutil.rmtree(parent)
parent.symlink_to({str(outside)!r}, target_is_directory=True)
print('consumer-result=pass')
""",
            encoding="utf-8",
        )

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            action.name,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("run-root-changed", completed.stderr)
        self.assertFalse((outside / "final.raw").exists())
        self.assertFalse((outside / "final.json").exists())

    def test_run_allows_literal_python_module_argv(self) -> None:
        self.write_spec()

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            "-m",
            "product_action",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("final_action_predicate=MATCH", completed.stdout)

    def test_validation_rejects_preexisting_cleanup_target(self) -> None:
        self.write_spec()
        (self.repository / ".runtime").mkdir()

        completed = self.recorder("validate", "--root", str(self.run_root))

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cleanup-target-not-absent", completed.stderr)

    def test_validation_hardens_owned_evidence_directories(self) -> None:
        """Regression: harmless umask defaults must not consume a whole run root."""

        self.write_spec()
        self.run_root.chmod(0o755)
        self.run_root.parent.chmod(0o755)

        completed = self.recorder("validate", "--root", str(self.run_root))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            stat.S_IMODE(self.run_root.parent.stat().st_mode),
            0o700,
        )
        self.assertEqual(stat.S_IMODE(self.run_root.stat().st_mode), 0o700)

    def test_validation_creates_private_output_parent_directories(self) -> None:
        """Regression: nested evidence outputs are recorder-owned infrastructure."""

        self.write_spec(
            observation_path="observations/final.raw",
            metadata_path="metadata/final.json",
        )

        completed = self.recorder("validate", "--root", str(self.run_root))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for path in (self.run_root / "observations", self.run_root / "metadata"):
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_validation_rejects_overlapping_output_paths_without_side_effects(self) -> None:
        """One recorder output cannot become the other output's parent."""

        self.write_spec(
            observation_path="outputs",
            metadata_path="outputs/final.json",
        )

        completed = self.recorder(
            "run",
            "--root",
            str(self.run_root),
            "--",
            sys.executable,
            "product_action.py",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("invalid-output-path", completed.stderr)
        self.assertFalse((self.run_root / "outputs").exists())
        self.assertFalse((self.repository / ".runtime").exists())

    def test_identity_is_derived_from_the_frozen_charter(self) -> None:
        self.write_spec()

        completed = self.recorder("validate", "--root", str(self.run_root))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        authored = json.loads(
            (self.run_root / "final-action.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("expected_head", authored)
        self.assertNotIn("expected_branch", authored)

        charter_path = self.run_root / "charter.json"
        charter = json.loads(charter_path.read_text(encoding="utf-8"))
        charter["head"] = "0" * 40
        charter_path.write_text(
            json.dumps(charter, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        charter_path.chmod(0o600)
        second_root = self.run_root.parent / "run-2"
        second_root.mkdir(mode=0o700)
        self.run_root = second_root
        charter["run_id"] = "run-2"
        (second_root / "charter.json").write_text(
            json.dumps(charter, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (second_root / "charter.json").chmod(0o600)
        self.write_spec()

        rejected = self.recorder("validate", "--root", str(second_root))

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("revision-mismatch", rejected.stderr)

    def test_handoff_uses_one_root_for_compose_record_and_finalize(self) -> None:
        """Regression: terminal stages must not require repeated root/path copying."""

        recorder = _load_recorder_module()
        root = self.run_root.resolve()
        spec = {"closed": "spec"}
        values = {
            "observation": root / "final-observation.raw",
            "metadata": root / "final-action-metadata.json",
        }
        (root / "draft.json").write_text(
            json.dumps(
                {
                    "artifacts": [
                        {"path": "final-observation.raw"},
                        {"path": "final-action-metadata.json"},
                    ]
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        (root / "draft.json").chmod(0o600)
        compose = subprocess.CompletedProcess([], 0, "draft_composition=PASS\n", "")
        finalize = subprocess.CompletedProcess(
            [],
            0,
            'terminal_preflight=PASS\n{"result":"PASS"}\n',
            "",
        )
        root.parent.chmod(0o500)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(recorder, "_repository", return_value=self.repository),
            mock.patch.object(recorder, "_run_root", return_value=root) as resolve_root,
            mock.patch.object(recorder, "_read_spec", return_value=spec),
            mock.patch.object(recorder, "_validate_spec", return_value=values),
            mock.patch.object(recorder, "_run", return_value=True) as run_action,
            mock.patch.object(
                recorder.subprocess,
                "run",
                side_effect=[compose, finalize],
            ) as child_run,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = recorder.main(
                ["handoff", "--root", str(root), "--", "python3", "product_action.py"]
            )

        self.assertEqual(result, 0, stderr.getvalue())
        resolve_root.assert_called_once_with(self.repository, str(root))
        run_action.assert_called_once_with(
            self.repository, values, ["--", "python3", "product_action.py"]
        )
        self.assertEqual(child_run.call_count, 2)
        compose_argv = child_run.call_args_list[0].args[0]
        finalize_argv = child_run.call_args_list[1].args[0]
        self.assertEqual(compose_argv.count(str(root)), 1)
        self.assertEqual(finalize_argv.count(str(root)), 1)
        self.assertIn("compose-draft", compose_argv)
        self.assertNotIn("compose-draft", finalize_argv)
        self.assertIn("terminal_preflight=PASS", stdout.getvalue())
        self.assertEqual(stat.S_IMODE(root.parent.stat().st_mode), 0o700)

    def test_failed_handoff_keeps_the_evidence_parent_sealed(self) -> None:
        """Only a terminally successful handoff may release cleanup access."""

        recorder = _load_recorder_module()
        root = self.run_root.resolve()
        root.parent.chmod(0o500)
        failed = subprocess.CompletedProcess([], 1, '{"status":"ERROR"}\n', "")
        with (
            mock.patch.object(recorder, "_repository", return_value=self.repository),
            mock.patch.object(recorder, "_run_root", return_value=root),
            mock.patch.object(recorder, "_read_spec", return_value={}),
            mock.patch.object(recorder, "_validate_spec", return_value={}),
            mock.patch.object(recorder, "_run") as run_action,
            mock.patch.object(recorder.subprocess, "run", return_value=failed),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = recorder.main(
                ["handoff", "--root", str(root), "--", "python3", "product_action.py"]
            )

        self.assertEqual(result, 1)
        run_action.assert_not_called()
        self.assertEqual(stat.S_IMODE(root.parent.stat().st_mode), 0o500)

    def test_handoff_never_runs_the_product_when_composition_fails(self) -> None:
        """The combined handoff preserves preflight-before-product ordering."""

        recorder = _load_recorder_module()
        root = self.run_root.resolve()
        failed = subprocess.CompletedProcess([], 1, '{"status":"ERROR"}\n', "")
        with (
            mock.patch.object(recorder, "_repository", return_value=self.repository),
            mock.patch.object(recorder, "_run_root", return_value=root),
            mock.patch.object(recorder, "_read_spec", return_value={}),
            mock.patch.object(recorder, "_validate_spec", return_value={}),
            mock.patch.object(recorder, "_run") as run_action,
            mock.patch.object(recorder.subprocess, "run", return_value=failed),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = recorder.main(
                ["handoff", "--root", str(root), "--", "python3", "product_action.py"]
            )

        self.assertEqual(result, 1)
        run_action.assert_not_called()

    def test_handoff_binds_both_recorder_outputs_to_draft_before_product_execution(
        self,
    ) -> None:
        """A mistyped draft artifact path must fail before the final product action."""

        recorder = _load_recorder_module()
        root = self.run_root.resolve()
        values = {
            "observation": root / "final-observation.raw",
            "metadata": root / "final-action-metadata.json",
        }
        compose = subprocess.CompletedProcess([], 0, "draft_composition=PASS\n", "")
        for mismatched in ("observation", "metadata"):
            with self.subTest(mismatched=mismatched):
                paths = {
                    "observation": "final-observation.raw",
                    "metadata": "final-action-metadata.json",
                }
                paths[mismatched] = f"mistyped-{mismatched}.out"
                draft_path = root / "draft.json"
                draft_path.write_text(
                    json.dumps(
                        {
                            "artifacts": [
                                {"path": paths["observation"]},
                                {"path": paths["metadata"]},
                            ]
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                draft_path.chmod(0o600)
                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        recorder, "_repository", return_value=self.repository
                    ),
                    mock.patch.object(recorder, "_run_root", return_value=root),
                    mock.patch.object(recorder, "_read_spec", return_value={}),
                    mock.patch.object(
                        recorder, "_validate_spec", return_value=values
                    ),
                    mock.patch.object(
                        recorder, "_run", return_value=False
                    ) as run_action,
                    mock.patch.object(
                        recorder.subprocess, "run", return_value=compose
                    ),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(stderr),
                ):
                    result = recorder.main(
                        [
                            "handoff",
                            "--root",
                            str(root),
                            "--",
                            "python3",
                            "product_action.py",
                        ]
                    )

                self.assertEqual(result, 2)
                self.assertIn("handoff-artifact-mismatch", stderr.getvalue())
                run_action.assert_not_called()
                self.assertFalse(draft_path.exists())


if __name__ == "__main__":
    unittest.main()
