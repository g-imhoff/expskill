from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages" / "codex" / "scripts" / "worktrees.py"
HEADINGS = (
    "Status", "Established Method", "Prerequisites", "Commands",
    "Specimens and Scenarios", "Project Context", "Responsive Inspection",
    "Canary", "Owned Files", "Agent-Only Support", "Limitations", "Updating",
)


def load_helper():
    spec = importlib.util.spec_from_file_location("worktree_ui_harness", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    result = __import__("subprocess").run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "feature/ui")
    git(repo, "config", "user.name", "Worktree Tests")
    git(repo, "config", "user.email", "worktree@example.invalid")
    (repo / "README.md").write_text("project\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "baseline")
    exclude = Path(git(repo, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = repo / exclude
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.ui-harness/\n")
    harness = repo / ".ui-harness"
    (harness / "agent").mkdir(parents=True)
    (harness / "evidence").mkdir()
    guide = ["<!-- expskill:setup-ui-testing:v1 -->", ""]
    for heading in HEADINGS:
        guide.extend((f"## {heading}", "Configured for this fixture.", ""))
    (harness / "README.md").write_text("\n".join(guide), encoding="utf-8")
    agent_script = harness / "agent" / "inspect.sh"
    agent_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    agent_script.chmod(0o700)
    (harness / "evidence" / "private.png").write_bytes(b"not copied")
    return repo


def test_ready_ui_harness_is_seeded_without_evidence_and_removed_on_finish(tmp_path: Path, monkeypatch) -> None:
    module = load_helper()
    repo = repository(tmp_path)
    state_home = tmp_path / "state"
    record = module.create_worktree(repo, "HEAD", "design-run", "ui", state_home)
    seed = module.seed_ui_harness(repo, record.path, state_home)
    assert seed.files == (".ui-harness/README.md", ".ui-harness/agent/inspect.sh")
    assert (record.path / ".ui-harness" / "README.md").is_file()
    assert (record.path / ".ui-harness" / "agent" / "inspect.sh").is_file()
    assert (record.path / ".ui-harness" / "agent" / "inspect.sh").stat().st_mode & 0o100
    assert not (record.path / ".ui-harness" / "evidence").exists()
    (record.path / ".ui-harness" / "evidence").mkdir()
    (record.path / ".ui-harness" / "evidence" / "render.png").write_bytes(b"temporary")
    (record.path / "feature.txt").write_text("done\n", encoding="utf-8")
    git(record.path, "add", "feature.txt")
    git(record.path, "commit", "-m", "candidate")
    git(repo, "merge", "--ff-only", record.branch)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    module.finish_worktree(repo, record.path, record.branch, "HEAD")
    assert not record.path.exists()
    assert record.branch not in git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_ui_harness_seed_rejects_symlinked_agent_support_without_partial_copy(tmp_path: Path) -> None:
    module = load_helper()
    repo = repository(tmp_path)
    state_home = tmp_path / "state"
    (repo / ".ui-harness" / "agent" / "inspect.sh").unlink()
    (repo / ".ui-harness" / "agent" / "inspect.sh").symlink_to(repo / "README.md")
    record = module.create_worktree(repo, "HEAD", "design-run", "ui", state_home)
    with pytest.raises(module.WorktreeError):
        module.seed_ui_harness(repo, record.path, state_home)
    assert not (record.path / ".ui-harness").exists()


def test_ui_harness_seed_rejects_existing_destination(tmp_path: Path) -> None:
    module = load_helper()
    repo = repository(tmp_path)
    state_home = tmp_path / "state"
    record = module.create_worktree(repo, "HEAD", "design-run", "ui", state_home)
    (record.path / ".ui-harness").mkdir()
    marker = record.path / ".ui-harness" / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    with pytest.raises(module.WorktreeError):
        module.seed_ui_harness(repo, record.path, state_home)
    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_ui_harness_seed_rejects_invalid_guide(tmp_path: Path) -> None:
    module = load_helper()
    repo = repository(tmp_path)
    state_home = tmp_path / "state"
    (repo / ".ui-harness" / "README.md").write_text("incomplete\n", encoding="utf-8")
    record = module.create_worktree(repo, "HEAD", "design-run", "ui", state_home)
    with pytest.raises(module.WorktreeError):
        module.seed_ui_harness(repo, record.path, state_home)
    assert not (record.path / ".ui-harness").exists()
