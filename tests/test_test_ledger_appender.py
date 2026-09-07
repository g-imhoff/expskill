from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APPENDER = (
    ROOT
    / "packages"
    / "codex"
    / "skills"
    / "test"
    / "scripts"
    / "append_ledger.py"
)


def load_appender_module():
    specification = importlib.util.spec_from_file_location(
        "test_ledger_appender_atomicity", APPENDER
    )
    if specification is None or specification.loader is None:
        raise AssertionError(f"cannot import {APPENDER}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class TestLedgerAppenderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name) / "repository"
        self.repository.mkdir()
        subprocess.run(
            ["git", "init", "-b", "feature/test-ledger"],
            cwd=self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Ledger"],
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "ledger@example.invalid"],
            cwd=self.repository,
            check=True,
        )
        (self.repository / "product.txt").write_text("unchanged\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "product.txt"], cwd=self.repository, check=True
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
        self.run_root = evidence / "run-1"
        self.run_root.mkdir(mode=0o700)
        behavior = "The public changed journey returns its accepted outcome."
        self.write_private(
            "charter.json",
            {
                "schema_version": "test-charter.v1",
                "run_id": "run-1",
                "workflow_id": None,
                "repository": str(self.repository.resolve()),
                "branch": "feature/test-ledger",
                "head": self.head,
                "accepted_behavior": behavior,
                "scope": {
                    "accepted_behavior": behavior,
                    "inner_ring": ["Changed public journey."],
                    "adjacent_ring": ["Affected neighbour."],
                    "broader_ring": ["Application canary."],
                },
                "material_oracles": [
                    {
                        "oracle_id": "changed-outcome",
                        "behavior": behavior,
                        "consumer_surface": "python3 product_client.py changed",
                        "required_action_ids": ["changed"],
                    }
                ],
                "exemption_grounding_artifact_ids": [],
            },
        )
        self.write_private(
            "ledger.json",
            {
                "schema_version": "test-action-ledger.v2",
                "run_id": "run-1",
                "entries": [],
            },
        )

    def write_private(self, name: str, value: object) -> Path:
        path = self.run_root / name
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def entry(self, **updates: object) -> dict[str, object]:
        value: dict[str, object] = {
            "action_id": "changed",
            "role": "journey",
            "ring": "inner",
            "action": "python3 product_client.py changed",
            "path": ["public client", "changed journey"],
            "expected": "The public journey exits zero and reports pass.",
            "actual": "The public journey exited zero and reported pass.",
            "status": "pass",
            "oracle_ids": ["changed-outcome"],
            "artifact_ids": ["changed-output"],
        }
        value.update(updates)
        return value

    def write_batch(self, entries: list[dict[str, object]]) -> Path:
        return self.write_private(
            "ledger-batch.json",
            {"schema_version": "test-ledger-batch.v1", "entries": entries},
        )

    def append(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(APPENDER), "--root", str(self.run_root)],
            cwd=self.repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_validates_candidate_then_derives_head_and_appends_atomically(self) -> None:
        self.write_batch([self.entry()])

        completed = self.append()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ledger_append=PASS", completed.stdout)
        ledger = json.loads((self.run_root / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["head"], self.head)
        self.assertEqual(set(ledger["entries"][0]), {
            "action_id", "role", "ring", "head", "action", "path", "expected",
            "actual", "status", "oracle_ids", "artifact_ids",
        })
        self.assertFalse((self.run_root / "ledger-batch.json").exists())
        self.assertEqual(stat.S_IMODE((self.run_root / "ledger.json").stat().st_mode), 0o600)

    def test_accepts_multiline_observations_and_repeated_journey_steps(self) -> None:
        self.write_batch([
            self.entry(
                path=["public client", "retry boundary", "public client"],
                expected="line one\nline two",
                actual="line one\nline two",
            )
        ])

        completed = self.append()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        ledger = json.loads((self.run_root / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ledger["entries"][0]["path"],
            ["public client", "retry boundary", "public client"],
        )
        self.assertEqual(ledger["entries"][0]["actual"], "line one\nline two")

    def test_missing_status_is_rejected_before_ledger_publication(self) -> None:
        entry = self.entry()
        entry.pop("status")
        self.write_batch([entry])
        before = (self.run_root / "ledger.json").read_bytes()

        completed = self.append()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid-batch-entry", completed.stderr)
        self.assertEqual((self.run_root / "ledger.json").read_bytes(), before)
        self.assertTrue((self.run_root / "ledger-batch.json").exists())

    def test_caller_supplied_head_is_rejected_before_publication(self) -> None:
        self.write_batch([self.entry(head=self.head)])
        before = (self.run_root / "ledger.json").read_bytes()

        completed = self.append()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid-batch-entry", completed.stderr)
        self.assertEqual((self.run_root / "ledger.json").read_bytes(), before)

    def test_duplicate_action_id_is_rejected_without_rewriting_prior_entries(self) -> None:
        self.write_batch([self.entry()])
        first = self.append()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = (self.run_root / "ledger.json").read_bytes()
        self.write_batch([self.entry()])

        second = self.append()

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("duplicate-action-id", second.stderr)
        self.assertEqual((self.run_root / "ledger.json").read_bytes(), before)

    def test_accepts_eight_actions_and_rejects_a_ninth_atomically(self) -> None:
        """The executable ledger ceiling must match the documented 7+final budget."""

        entries = [
            self.entry(
                action_id=f"action-{index}",
                artifact_ids=[f"artifact-{index}"],
            )
            for index in range(8)
        ]
        self.write_batch(entries)

        accepted = self.append()

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        ledger_path = self.run_root / "ledger.json"
        self.assertEqual(len(json.loads(ledger_path.read_text())["entries"]), 8)
        before = ledger_path.read_bytes()
        self.write_batch(
            [self.entry(action_id="action-8", artifact_ids=["artifact-8"])]
        )

        rejected = self.append()

        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("action-budget-exceeded", rejected.stderr)
        self.assertEqual(ledger_path.read_bytes(), before)

    def test_rejects_non_private_batch_without_changing_ledger(self) -> None:
        batch = self.write_batch([self.entry()])
        batch.chmod(0o644)
        before = (self.run_root / "ledger.json").read_bytes()

        completed = self.append()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid-batch-permissions", completed.stderr)
        self.assertEqual((self.run_root / "ledger.json").read_bytes(), before)

    def test_post_publish_directory_sync_error_cannot_report_a_false_rejection(self) -> None:
        """Once replacement succeeds, retrying the consumed batch would duplicate evidence."""

        batch = self.write_batch([self.entry()])
        ledger_path = self.run_root / "ledger.json"
        ledger_raw = ledger_path.read_bytes()
        value = {
            "schema_version": "test-action-ledger.v2",
            "run_id": "run-1",
            "entries": [self.entry(head=self.head)],
        }
        appender = load_appender_module()
        real_fsync = appender.os.fsync
        calls = 0

        def fail_directory_sync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated directory sync failure")
            real_fsync(descriptor)

        with mock.patch.object(appender.os, "fsync", side_effect=fail_directory_sync):
            appender._publish(
                self.run_root,
                ledger_raw,
                ledger_path.lstat(),
                batch.lstat(),
                value,
            )

        self.assertEqual(json.loads(ledger_path.read_text())["entries"], value["entries"])
        self.assertFalse(batch.exists())
        self.assertFalse(any(self.run_root.glob(".ledger-batch-consumed-*")))


if __name__ == "__main__":
    unittest.main()
