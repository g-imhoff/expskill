from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "plugins" / "codex-dev-flow" / "skills" / "full-code-change" / "scripts" / "worktrees.py"


def load_helper() -> object:
    specification = importlib.util.spec_from_file_location("devflow_worktrees", HELPER_PATH)
    if specification is None or specification.loader is None:
        raise ImportError(f"cannot load worktree helper: {HELPER_PATH}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class WorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "product"
        self.repo.mkdir()
        self.state_home = self.root / "state"
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Dev Flow Tests")
        self._git("config", "user.email", "devflow-tests@example.invalid")
        (self.repo / "README.md").write_text("initial\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial")

    def _git(self, *arguments: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd or self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise AssertionError(
                f"git command failed ({result.returncode}): {' '.join(arguments)}\n{result.stdout}\n{result.stderr}"
            )
        return result.stdout.strip()

    def _helper(self) -> object:
        return load_helper()

    def _expected_path(self, run_id: str, task: str) -> Path:
        canonical_repo = Path(self._git("rev-parse", "--show-toplevel")).resolve()
        repository_hash = hashlib.sha256(str(canonical_repo).encode("utf-8")).hexdigest()
        return self.state_home / "codex-dev-flow" / "worktrees" / repository_hash / run_id / task

    def _create(self, run_id: str = "run-1", task: str = "task-one") -> object:
        return self._helper().create_worktree(self.repo, "HEAD", run_id, task, self.state_home)

    def test_create_uses_external_hashed_path_and_exact_branch(self) -> None:
        record = self._create()
        expected_path = self._expected_path("run-1", "task-one")
        self.assertEqual(record.path, expected_path)
        self.assertEqual(record.branch, "devflow/run-1/task-one")
        self.assertTrue(expected_path.is_dir())
        listing = self._git("worktree", "list", "--porcelain")
        self.assertIn(f"worktree {expected_path}", listing)
        self.assertIn("branch refs/heads/devflow/run-1/task-one", listing)
        self.assertEqual(self._git("rev-parse", "--show-toplevel", cwd=expected_path), str(expected_path))
        self.assertFalse((self.repo / ".git" / "worktrees").resolve() == expected_path.resolve())

    def test_invalid_identifiers_are_rejected_without_side_effects(self) -> None:
        invalid_values = ("", "Run-1", "run_1", "/tmp", "../run", "run/task", ".", "..", "run-", "-run", "run name", "run..1", "run--1")
        helper = self._helper()
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    helper.create_worktree(self.repo, "HEAD", value, "task-one", self.state_home)
                with self.assertRaises(ValueError):
                    helper.create_worktree(self.repo, "HEAD", "run-1", value, self.state_home)
        self.assertFalse(self.state_home.exists())
        self.assertNotIn("devflow/", self._git("branch", "--format=%(refname:short)"))

    def test_existing_target_is_refused_without_overwriting_it(self) -> None:
        target = self._expected_path("run-1", "task-one")
        target.mkdir(parents=True)
        marker = target / "keep.txt"
        marker.write_text("keep\n", encoding="utf-8")
        with self.assertRaises(Exception) as context:
            self._create()
        self.assertIn(str(target), str(context.exception))
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
        self.assertNotIn("devflow/run-1/task-one", self._git("branch", "--format=%(refname:short)"))

    def test_existing_branch_is_refused_without_removing_it(self) -> None:
        self._git("branch", "devflow/run-1/task-one", "HEAD")
        target = self._expected_path("run-1", "task-one")
        with self.assertRaises(Exception) as context:
            self._create()
        self.assertIn("devflow/run-1/task-one", str(context.exception))
        self.assertFalse(target.exists())
        self.assertIn("devflow/run-1/task-one", self._git("branch", "--format=%(refname:short)"))

    def test_invalid_base_is_refused_without_creating_target_or_branch(self) -> None:
        target = self._expected_path("run-1", "task-one")
        with self.assertRaises(Exception) as context:
            self._helper().create_worktree(self.repo, "missing-base", "run-1", "task-one", self.state_home)
        self.assertIn("missing-base", str(context.exception))
        self.assertFalse(target.exists())
        self.assertNotIn("devflow/run-1/task-one", self._git("branch", "--format=%(refname:short)"))

    def test_option_like_base_uses_the_resolved_commit(self) -> None:
        base_commit = self._git("rev-parse", "HEAD")
        self._git("update-ref", "refs/tags/--force", base_commit)
        (self.repo / "README.md").write_text("second\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "second")
        record = self._helper().create_worktree(
            self.repo,
            "--force",
            "run-1",
            "task-one",
            self.state_home,
        )
        self.assertEqual(self._git("rev-parse", "HEAD", cwd=record.path), base_commit)

    def test_state_hash_symlink_redirection_is_refused_without_git_state_changes(self) -> None:
        canonical_repo = Path(self._git("rev-parse", "--show-toplevel")).resolve()
        repository_hash = hashlib.sha256(str(canonical_repo).encode("utf-8")).hexdigest()
        worktree_root = self.state_home / "codex-dev-flow" / "worktrees"
        worktree_root.mkdir(parents=True)
        (worktree_root / repository_hash).symlink_to(self.repo, target_is_directory=True)
        target = self._expected_path("run-1", "task-one")
        before_status = self._git("status", "--porcelain")
        with self.assertRaises(Exception) as context:
            self._helper().create_worktree(self.repo, "HEAD", "run-1", "task-one", self.state_home)
        self.assertIn(str(target), str(context.exception))
        self.assertFalse(target.exists())
        self.assertFalse((self.repo / "run").exists())
        self.assertEqual(self._git("status", "--porcelain"), before_status)
        self.assertNotIn("devflow/run-1/task-one", self._git("branch", "--format=%(refname:short)"))
        self.assertNotIn(str(target), self._git("worktree", "list", "--porcelain"))

    def test_duplicate_path_and_branch_are_refused_and_first_worktree_survives(self) -> None:
        record = self._create()
        with self.assertRaises(Exception) as context:
            self._create()
        self.assertIn(str(record.path), str(context.exception))
        self.assertIn(record.branch, str(context.exception))
        self.assertTrue(record.path.is_dir())
        self.assertIn(record.branch, self._git("branch", "--format=%(refname:short)"))

    def test_dirty_task_preserves_worktree_and_branch_with_recovery_paths(self) -> None:
        record = self._create()
        (record.path / "uncommitted.txt").write_text("unfinished\n", encoding="utf-8")
        with self.assertRaises(Exception) as context:
            self._helper().finish_worktree(self.repo, record.path, record.branch, "main")
        message = str(context.exception)
        self.assertIn(str(record.path), message)
        self.assertIn(record.branch, message)
        self.assertTrue(record.path.exists())
        self.assertTrue((record.path / "uncommitted.txt").exists())
        self.assertIn(record.branch, self._git("branch", "--format=%(refname:short)"))
        self.assertIn(f"worktree {record.path}", self._git("worktree", "list", "--porcelain"))

    def test_unmerged_commit_preserves_worktree_and_branch(self) -> None:
        record = self._create()
        (record.path / "feature.txt").write_text("unmerged\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=record.path)
        self._git("commit", "-m", "unmerged task", cwd=record.path)
        with self.assertRaises(Exception) as context:
            self._helper().finish_worktree(self.repo, record.path, record.branch, "main")
        message = str(context.exception)
        self.assertIn(str(record.path), message)
        self.assertIn(record.branch, message)
        self.assertTrue(record.path.exists())
        self.assertIn(record.branch, self._git("branch", "--format=%(refname:short)"))
        self.assertEqual(self._git("status", "--porcelain", cwd=record.path), "")

    def test_dirty_integration_checkout_preserves_task_worktree_and_branch(self) -> None:
        record = self._create()
        (self.repo / "integration.txt").write_text("dirty integration\n", encoding="utf-8")
        with self.assertRaises(Exception) as context:
            self._helper().finish_worktree(self.repo, record.path, record.branch, "main")
        message = str(context.exception)
        self.assertIn(str(record.path), message)
        self.assertIn(record.branch, message)
        self.assertTrue(record.path.exists())
        self.assertTrue((self.repo / "integration.txt").exists())
        self.assertIn(record.branch, self._git("branch", "--format=%(refname:short)"))

    def test_invalid_integration_repository_preserves_supplied_recovery_context(self) -> None:
        record = self._create()
        invalid_repository = self.root / "not-a-repository"
        invalid_repository.mkdir()
        before_branches = self._git("branch", "--format=%(refname:short)")
        with self.assertRaises(Exception) as context:
            self._helper().finish_worktree(invalid_repository, record.path, record.branch, "main")
        message = str(context.exception)
        self.assertIn(str(record.path), message)
        self.assertIn(record.branch, message)
        self.assertEqual(self._git("branch", "--format=%(refname:short)"), before_branches)
        self.assertTrue(record.path.exists())
        self.assertIn(f"worktree {record.path}", self._git("worktree", "list", "--porcelain"))

    def test_branch_delete_precondition_failure_preserves_everything(self) -> None:
        record = self._create()
        (record.path / "feature.txt").write_text("task commit\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=record.path)
        self._git("commit", "-m", "task commit", cwd=record.path)
        task_tip = self._git("rev-parse", "HEAD", cwd=record.path)
        self._git("branch", "integrated", task_tip)
        original_state_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.state_home)
        self.addCleanup(self._restore_state_home, original_state_home)
        with self.assertRaises(Exception) as context:
            self._helper().finish_worktree(self.repo, record.path, record.branch, "integrated")
        message = str(context.exception)
        self.assertIn(str(record.path), message)
        self.assertIn(record.branch, message)
        self.assertTrue(record.path.exists())
        self.assertIn(record.branch, self._git("branch", "--format=%(refname:short)"))
        self.assertIn(f"worktree {record.path}", self._git("worktree", "list", "--porcelain"))

    def test_registered_worktree_outside_owned_root_is_refused(self) -> None:
        helper = self._helper()
        outside = self.root / "outside-worktree"
        branch = "devflow/run-1/task-one"
        self._git("worktree", "add", "-b", branch, str(outside), "HEAD")
        original_state_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.state_home)
        self.addCleanup(self._restore_state_home, original_state_home)
        with self.assertRaises(Exception) as context:
            helper.finish_worktree(self.repo, outside, branch, "main")
        message = str(context.exception)
        self.assertIn(str(outside), message)
        self.assertIn(branch, message)
        self.assertTrue(outside.exists())
        self.assertIn(branch, self._git("branch", "--format=%(refname:short)"))
        self.assertIn(f"worktree {outside}", self._git("worktree", "list", "--porcelain"))

    def test_branch_path_mismatch_is_refused(self) -> None:
        record = self._create()
        wrong_branch = "devflow/run-1/other-task"
        with self.assertRaises(Exception) as context:
            self._helper().finish_worktree(self.repo, record.path, wrong_branch, "main")
        message = str(context.exception)
        self.assertIn(str(record.path), message)
        self.assertIn(wrong_branch, message)
        self.assertIn(record.branch, message)
        self.assertTrue(record.path.exists())
        self.assertIn(record.branch, self._git("branch", "--format=%(refname:short)"))

    def test_integrated_clean_worktree_removes_only_exact_branch(self) -> None:
        record = self._create()
        similar_branch = "devflow/run-1/task-one-follow-up"
        self._git("branch", similar_branch, "HEAD")
        (record.path / "feature.txt").write_text("integrated task\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=record.path)
        self._git("commit", "-m", "integrated task", cwd=record.path)
        self._git("merge", "--no-ff", record.branch, "-m", "integrate task")
        original_state_home = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(self.state_home)
        self.addCleanup(self._restore_state_home, original_state_home)
        self._helper().finish_worktree(self.repo, record.path, record.branch, "main")
        self.assertFalse(record.path.exists())
        branches = self._git("branch", "--format=%(refname:short)").splitlines()
        self.assertNotIn(record.branch, branches)
        self.assertIn(similar_branch, branches)
        self.assertNotIn(str(record.path), self._git("worktree", "list", "--porcelain"))

    def _restore_state_home(self, original: str | None) -> None:
        if original is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = original


if __name__ == "__main__":
    unittest.main()
