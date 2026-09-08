from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = (
    ROOT
    / "packages"
    / "expskill"
    / "skills"
    / "test"
    / "scripts"
    / "bootstrap_run.py"
)


class TestRunBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = Path(temporary.name) / "repository"
        self.repository.mkdir()
        self.addCleanup(self.unseal_evidence_for_cleanup)
        subprocess.run(
            ["git", "init", "-b", "feature/test-bootstrap"],
            cwd=self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Bootstrap"],
            cwd=self.repository,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "bootstrap@example.invalid"],
            cwd=self.repository,
            check=True,
        )
        (self.repository / "product.txt").write_text(
            "public behavior\n", encoding="utf-8"
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

    def unseal_evidence_for_cleanup(self) -> None:
        evidence = self.repository / ".test-evidence"
        if evidence.is_dir():
            evidence.chmod(0o700)

    def bootstrap(self, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BOOTSTRAP)],
            cwd=self.repository if cwd is None else cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @staticmethod
    def payload(completed: subprocess.CompletedProcess[str]) -> dict[str, str]:
        first = completed.stdout.splitlines()[0]
        marker = "test_run_bootstrap="
        if not first.startswith(marker):
            raise AssertionError(f"missing bootstrap marker: {first!r}")
        value = json.loads(first.removeprefix(marker))
        if not isinstance(value, dict):
            raise AssertionError("bootstrap payload is not an object")
        return value

    def test_allocates_one_private_nonempty_root_with_portable_time_values(self) -> None:
        completed = self.bootstrap()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = self.payload(completed)
        self.assertEqual(payload["branch"], "feature/test-bootstrap")
        self.assertEqual(payload["repository"], str(self.repository.resolve()))
        self.assertRegex(payload["run_id"], r"\Atest-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
        self.assertRegex(
            payload["started_at"], r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
        )
        self.assertRegex(
            payload["cutoff_at"], r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
        )
        root = Path(payload["root"])
        self.assertEqual(root, self.repository / ".test-evidence" / payload["run_id"])
        self.assertTrue(root.is_dir())
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(root.parent.stat().st_mode), 0o500)
        self.assertEqual(len(list(root.parent.iterdir())), 1)

    def test_seals_parent_against_unbootstrapped_siblings_but_keeps_root_writable(
        self,
    ) -> None:
        first = self.bootstrap()

        self.assertEqual(first.returncode, 0, first.stderr)
        first_root = Path(self.payload(first)["root"])
        (first_root / "retained-artifact.txt").write_text(
            "evidence\n", encoding="utf-8"
        )
        with self.assertRaises(PermissionError):
            (first_root.parent / "mistyped-root").mkdir()

        second = self.bootstrap()

        self.assertEqual(second.returncode, 0, second.stderr)
        second_root = Path(self.payload(second)["root"])
        self.assertNotEqual(first_root, second_root)
        self.assertTrue(second_root.is_dir())
        self.assertEqual(stat.S_IMODE(first_root.parent.stat().st_mode), 0o500)

    def test_prints_sorted_inventory_and_bounded_text_contents(self) -> None:
        completed = self.bootstrap()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("FIRST_PARTY_INVENTORY_BEGIN\nproduct.txt\n", completed.stdout)
        self.assertIn("FIRST_PARTY_FILE_BEGIN=product.txt", completed.stdout)
        self.assertIn("public behavior", completed.stdout)
        self.assertIn("FIRST_PARTY_FILE_END=product.txt", completed.stdout)

    def test_repeated_invocations_allocate_distinct_roots_without_repair(self) -> None:
        first = self.bootstrap()
        second = self.bootstrap()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(self.payload(first)["root"], self.payload(second)["root"])
        evidence = self.repository / ".test-evidence"
        self.assertEqual(len([path for path in evidence.iterdir() if path.is_dir()]), 2)

    def test_rejects_invocation_outside_the_repository_root(self) -> None:
        child = self.repository / "child"
        child.mkdir()

        completed = self.bootstrap(cwd=child)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("wrong-working-directory", completed.stderr)
        self.assertFalse((self.repository / ".test-evidence").exists())

    def test_rejects_detached_head_before_allocating_a_run_root(self) -> None:
        subprocess.run(
            ["git", "checkout", "--detach"],
            cwd=self.repository,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        completed = self.bootstrap()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unnamed-branch", completed.stderr)
        self.assertFalse((self.repository / ".test-evidence").exists())


if __name__ == "__main__":
    unittest.main()
