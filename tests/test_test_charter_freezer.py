from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZER = (
    ROOT
    / "packages"
    / "expskill"
    / "skills"
    / "test"
    / "scripts"
    / "freeze_charter.py"
)


class CharterFreezerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name) / "repository"
        self.repository.mkdir()
        subprocess.run(
            ["git", "init", "-b", "feature/test-freezer"],
            cwd=self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Freezer"],
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "freezer@example.invalid"],
            cwd=self.repository,
            check=True,
        )
        (self.repository / "product.txt").write_text(
            "unchanged\n", encoding="utf-8"
        )
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

    def preparation(self) -> dict[str, object]:
        behavior = "The public changed journey returns its accepted outcome."
        return {
            "schema_version": "test-charter-preparation.v1",
            "workflow_id": None,
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
        }

    def write_preparation(self, value: object | None = None) -> Path:
        path = self.run_root / "charter-preparation.json"
        path.write_text(
            json.dumps(
                self.preparation() if value is None else value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def run_freezer(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(FREEZER), "--root", str(self.run_root)],
            cwd=self.repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_freezes_machine_bound_charter_and_empty_ledger(self) -> None:
        self.write_preparation()

        completed = self.run_freezer()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        reported = json.loads(completed.stdout)
        self.assertEqual(reported["status"], "FROZEN")
        self.assertEqual(reported["repository"], str(self.repository.resolve()))
        self.assertEqual(reported["branch"], "feature/test-freezer")
        self.assertEqual(reported["head"], self.head)
        self.assertEqual(reported["run_id"], "run-1")
        charter = json.loads(
            (self.run_root / "charter.json").read_text(encoding="utf-8")
        )
        self.assertEqual(charter["schema_version"], "test-charter.v1")
        self.assertEqual(charter["repository"], str(self.repository.resolve()))
        self.assertEqual(charter["branch"], "feature/test-freezer")
        self.assertEqual(charter["head"], self.head)
        self.assertEqual(charter["run_id"], "run-1")
        ledger = json.loads(
            (self.run_root / "ledger.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ledger,
            {
                "schema_version": "test-action-ledger.v2",
                "run_id": "run-1",
                "entries": [],
            },
        )
        for name in ("charter.json", "ledger.json"):
            mode = stat.S_IMODE((self.run_root / name).stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_rejects_caller_supplied_identity_fields_without_outputs(self) -> None:
        preparation = self.preparation()
        preparation["head"] = self.head
        self.write_preparation(preparation)

        completed = self.run_freezer()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid-preparation", completed.stderr)
        self.assertFalse((self.run_root / "charter.json").exists())
        self.assertFalse((self.run_root / "ledger.json").exists())

    def test_rejects_non_private_preparation(self) -> None:
        path = self.write_preparation()
        path.chmod(0o644)

        completed = self.run_freezer()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid-preparation-permissions", completed.stderr)
        self.assertFalse((self.run_root / "charter.json").exists())

    def test_refuses_to_replace_frozen_outputs(self) -> None:
        self.write_preparation()
        first = self.run_freezer()
        self.assertEqual(first.returncode, 0, first.stderr)
        charter_before = (self.run_root / "charter.json").read_bytes()
        ledger_before = (self.run_root / "ledger.json").read_bytes()

        second = self.run_freezer()

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already-frozen", second.stderr)
        self.assertEqual((self.run_root / "charter.json").read_bytes(), charter_before)
        self.assertEqual((self.run_root / "ledger.json").read_bytes(), ledger_before)

    def test_accepts_exact_exempt_shape(self) -> None:
        preparation = self.preparation()
        preparation["material_oracles"] = []
        preparation["exemption_grounding_artifact_ids"] = ["revision-grounding"]
        self.write_preparation(preparation)

        completed = self.run_freezer()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        charter = json.loads(
            (self.run_root / "charter.json").read_text(encoding="utf-8")
        )
        self.assertEqual(charter["material_oracles"], [])
        self.assertEqual(
            charter["exemption_grounding_artifact_ids"], ["revision-grounding"]
        )


if __name__ == "__main__":
    unittest.main()
