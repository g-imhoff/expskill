from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class WorktreeError(RuntimeError):
    pass


class WorktreeRecord(NamedTuple):
    path: Path
    branch: str


def _git(cwd: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise WorktreeError(f"{details}: git -C {cwd} {' '.join(arguments)}")
    return result


def _canonical_repository(repo: Path) -> Path:
    candidate = Path(repo).expanduser().resolve()
    result = _git(candidate, "rev-parse", "--show-toplevel", check=False)
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "not a Git repository"
        raise WorktreeError(f"cannot resolve repository {repo}: {details}")
    top_level = result.stdout.strip()
    if not top_level:
        raise WorktreeError(f"cannot resolve repository {repo}: Git returned no top-level path")
    return Path(top_level).resolve()


def _validate_slug(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty lowercase slug: {value!r}")


def _repository_hash(repo: Path) -> str:
    return hashlib.sha256(str(repo).encode("utf-8")).hexdigest()


def _state_home(state_home: Path | None = None) -> Path:
    if state_home is not None:
        return Path(state_home).expanduser().resolve()
    configured = os.environ.get("XDG_STATE_HOME")
    return Path(configured or (Path.home() / ".local" / "state")).expanduser().resolve()


def _owned_path(repo: Path, run_id: str, task: str, state_home: Path) -> Path:
    return (
        _state_home(state_home)
        / "codex-dev-flow"
        / "worktrees"
        / _repository_hash(repo)
        / run_id
        / task
    )


def _branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def _base_exists(repo: Path, base: str) -> bool:
    return (
        _git(repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}", check=False).returncode
        == 0
    )


def create_worktree(repo: Path, base: str, run_id: str, task: str, state_home: Path) -> WorktreeRecord:
    canonical_repo = _canonical_repository(repo)
    _validate_slug(run_id, "run_id")
    _validate_slug(task, "task")
    branch = f"devflow/{run_id}/{task}"
    target = _owned_path(canonical_repo, run_id, task, state_home)
    if target.exists() or target.is_symlink():
        raise WorktreeError(f"worktree target already exists: {target}; branch remains preserved: {branch}")
    if _branch_exists(canonical_repo, branch):
        raise WorktreeError(f"branch already exists: {branch}; target remains preserved: {target}")
    if not isinstance(base, str) or not base or not _base_exists(canonical_repo, base):
        raise WorktreeError(f"invalid base {base!r}; target remains absent: {target}; branch remains absent: {branch}")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _git(canonical_repo, "worktree", "add", "-b", branch, str(target), base, check=False)
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "git worktree add failed"
        raise WorktreeError(f"cannot create worktree {target} on branch {branch}: {details}")
    return WorktreeRecord(path=target, branch=branch)


def _registered_worktrees(repo: Path) -> dict[Path, str | None]:
    result = _git(repo, "worktree", "list", "--porcelain", check=False)
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "cannot list worktrees"
        raise WorktreeError(details)
    registered: dict[Path, str | None] = {}
    current_path: Path | None = None
    current_branch: str | None = None
    for line in result.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            if current_path is not None:
                registered[current_path] = current_branch
            current_path = Path(line[9:]).resolve()
            current_branch = None
        elif line.startswith("branch ") and current_path is not None:
            reference = line[7:]
            current_branch = reference.removeprefix("refs/heads/")
        elif not line and current_path is not None:
            registered[current_path] = current_branch
            current_path = None
            current_branch = None
    return registered


def _branch_parts(branch: str) -> tuple[str, str]:
    parts = branch.split("/")
    if len(parts) != 3 or parts[0] != "devflow":
        raise ValueError(f"branch must have the form devflow/<run-id>/<task>: {branch!r}")
    _validate_slug(parts[1], "run_id")
    _validate_slug(parts[2], "task")
    return parts[1], parts[2]


def _status(repo: Path) -> tuple[bool, str]:
    result = _git(repo, "status", "--porcelain", check=False)
    if result.returncode:
        return False, result.stderr.strip() or result.stdout.strip() or "cannot read Git status"
    return not result.stdout, result.stdout


def _commit_for(repo: Path, reference: str) -> tuple[str | None, str | None]:
    result = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{reference}^{{commit}}", check=False)
    if result.returncode:
        return None, result.stderr.strip() or result.stdout.strip() or f"unknown ref {reference}"
    return result.stdout.strip(), None


def _finish_error(path: Path, branch: str, reasons: list[str], attached_branch: str | None = None) -> WorktreeError:
    recovery_branches = [branch]
    if attached_branch and attached_branch not in recovery_branches:
        recovery_branches.append(attached_branch)
    recovery = ", ".join(recovery_branches)
    return WorktreeError(
        f"cannot finish worktree: {'; '.join(reasons)}; preserve worktree at {path}; preserve branch {recovery}"
    )


def finish_worktree(repo: Path, path: Path, branch: str, integrated_ref: str) -> None:
    canonical_repo = _canonical_repository(repo)
    target = Path(path).expanduser().resolve()
    reasons: list[str] = []
    attached_branch: str | None = None
    try:
        run_id, task = _branch_parts(branch)
        expected = _owned_path(canonical_repo, run_id, task, _state_home())
        if target != expected:
            reasons.append(f"path is outside its exact owned target {expected}")
    except (ValueError, OSError) as error:
        reasons.append(str(error))
    registered = _registered_worktrees(canonical_repo)
    if target not in registered:
        reasons.append("path is not a registered worktree of this repository")
    else:
        attached_branch = registered[target]
        if attached_branch != branch:
            reasons.append(f"worktree is attached to {attached_branch!r}, not {branch!r}")
        clean_task, task_status = _status(target)
        if not clean_task:
            reasons.append(f"task worktree is dirty or unavailable: {task_status.strip()}")
    branch_commit, branch_error = _commit_for(canonical_repo, f"refs/heads/{branch}")
    integrated_commit, integrated_error = _commit_for(canonical_repo, integrated_ref)
    if branch_error:
        reasons.append(f"branch tip is unavailable: {branch_error}")
    if integrated_error:
        reasons.append(f"integrated ref is unavailable: {integrated_error}")
    if branch_commit and integrated_commit:
        ancestor = _git(
            canonical_repo,
            "merge-base",
            "--is-ancestor",
            branch_commit,
            integrated_commit,
            check=False,
        )
        if ancestor.returncode:
            reasons.append(f"branch tip {branch} is not an ancestor of {integrated_ref}")
    clean_integration, integration_status = _status(canonical_repo)
    if not clean_integration:
        reasons.append(f"integration checkout is dirty or unavailable: {integration_status.strip()}")
    if reasons:
        raise _finish_error(target, branch, reasons, attached_branch)
    removed = _git(canonical_repo, "worktree", "remove", str(target), check=False)
    if removed.returncode:
        details = removed.stderr.strip() or removed.stdout.strip() or "git worktree remove failed"
        raise _finish_error(target, branch, [details], attached_branch)
    deleted = _git(canonical_repo, "branch", "-d", "--", branch, check=False)
    if deleted.returncode:
        details = deleted.stderr.strip() or deleted.stdout.strip() or "git branch -d failed"
        raise _finish_error(target, branch, [details], attached_branch)


def _default_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worktrees.py")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--repo", required=True)
    create.add_argument("--base", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--task", required=True)
    finish = commands.add_parser("finish")
    finish.add_argument("--repo", required=True)
    finish.add_argument("--path", required=True)
    finish.add_argument("--branch", required=True)
    finish.add_argument("--integrated-ref", required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parser = _default_parser()
    options = parser.parse_args(arguments)
    try:
        if options.command == "create":
            record = create_worktree(
                Path(options.repo),
                options.base,
                options.run_id,
                options.task,
                _state_home(),
            )
            print(f"path={record.path}")
            print(f"branch={record.branch}")
        else:
            finish_worktree(Path(options.repo), Path(options.path), options.branch, options.integrated_ref)
    except (ValueError, WorktreeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
