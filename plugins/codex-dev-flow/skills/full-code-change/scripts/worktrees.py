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


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _ensure_safe_owned_path(state_root: Path, target: Path) -> None:
    canonical_root = Path(state_root).absolute()
    candidate = Path(target)
    try:
        relative = candidate.relative_to(canonical_root)
    except ValueError as error:
        raise WorktreeError(f"target is outside canonical state root: {candidate}") from error
    current = canonical_root
    if _path_exists(current):
        if current.is_symlink() or not current.is_dir():
            raise WorktreeError(f"unsafe state path component {current} for target {candidate}")
    else:
        current.mkdir(parents=True, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise WorktreeError(f"unsafe state path component {current} for target {candidate}")
    components = relative.parts
    for component in components[:-1]:
        current = current / component
        if _path_exists(current):
            if current.is_symlink() or not current.is_dir():
                raise WorktreeError(f"unsafe state path component {current} for target {candidate}")
        else:
            current.mkdir()
            if current.is_symlink() or not current.is_dir():
                raise WorktreeError(f"unsafe state path component {current} for target {candidate}")
    if _path_exists(candidate) and candidate.is_symlink():
        raise WorktreeError(f"unsafe state path component {candidate} for target {candidate}")
    if candidate.exists() and not candidate.is_dir():
        raise WorktreeError(f"unsafe state path component {candidate} for target {candidate}")
    if candidate.resolve() != candidate:
        raise WorktreeError(f"target resolves outside its exact owned path: {candidate}")


def _branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def _resolve_commit(repo: Path, reference: str) -> tuple[str | None, str | None]:
    if not isinstance(reference, str) or not reference:
        return None, f"unknown ref {reference!r}"
    result = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{reference}^{{commit}}", check=False)
    if result.returncode:
        return None, result.stderr.strip() or result.stdout.strip() or f"unknown ref {reference}"
    commit = result.stdout.strip()
    if not commit:
        return None, f"unknown ref {reference}"
    return commit, None


def _configured_upstream_commit(repo: Path, branch: str) -> tuple[str | None, str | None]:
    remote = _git(repo, "config", "--get", f"branch.{branch}.remote", check=False)
    merge = _git(repo, "config", "--get", f"branch.{branch}.merge", check=False)
    if remote.returncode == 1 and merge.returncode == 1:
        return None, None
    if remote.returncode or merge.returncode:
        return None, f"branch {branch} has an incomplete configured upstream"
    upstream, error = _resolve_commit(repo, f"{branch}@{{upstream}}")
    if error:
        return None, f"configured upstream for {branch} is unavailable: {error}"
    return upstream, None


def _directory_access_error(directory: Path, require_write: bool) -> str | None:
    if not directory.is_dir() or directory.is_symlink():
        return f"directory is unavailable: {directory}"
    required = os.R_OK | os.X_OK
    if require_write:
        required |= os.W_OK
    if not os.access(directory, required):
        access = "read, execute, and write" if require_write else "read and execute"
        return f"directory lacks {access} access: {directory}"
    return None


def _scan_removal_tree(directory: Path, reasons: list[str]) -> None:
    try:
        entries = list(directory.iterdir())
    except OSError as error:
        reasons.append(f"cannot inspect removal directory {directory}: {error}")
        return
    access_error = _directory_access_error(directory, bool(entries))
    if access_error:
        reasons.append(access_error)
        return
    for entry in entries:
        if entry.is_symlink():
            continue
        try:
            is_directory = entry.is_dir()
        except OSError as error:
            reasons.append(f"cannot inspect removal entry {entry}: {error}")
            continue
        if is_directory:
            _scan_removal_tree(entry, reasons)


def _worktree_admin_path(path: Path) -> Path | None:
    git_file = path / ".git"
    try:
        contents = git_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in contents:
        if line.startswith("gitdir: "):
            admin = Path(line[8:])
            if not admin.is_absolute():
                admin = git_file.parent / admin
            return admin.resolve()
    return None


def _check_existing_write_chain(root: Path, target: Path, reasons: list[str]) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError:
        reasons.append(f"Git path is outside its common directory: {target}")
        return
    current = root
    if _path_exists(current):
        if current.is_symlink() or not current.is_dir():
            reasons.append(f"Git common directory is unavailable: {current}")
            return
        access_error = _directory_access_error(current, True)
        if access_error:
            reasons.append(access_error)
    else:
        reasons.append(f"Git common directory is unavailable: {current}")
        return
    for component in relative.parts:
        current = current / component
        if not _path_exists(current):
            break
        if current.is_symlink() or not current.is_dir():
            reasons.append(f"Git branch-deletion path is unavailable: {current}")
            break
        access_error = _directory_access_error(current, True)
        if access_error:
            reasons.append(access_error)


def _branch_delete_preflight(repo: Path, branch: str) -> None:
    common_result = _git(repo, "rev-parse", "--git-common-dir", check=False)
    if common_result.returncode or not common_result.stdout.strip():
        raise WorktreeError("cannot resolve Git common directory for branch deletion")
    common_directory = Path(common_result.stdout.strip())
    if not common_directory.is_absolute():
        common_directory = repo / common_directory
    common_directory = common_directory.resolve()
    reasons: list[str] = []
    ref_path = common_directory / "refs" / "heads" / branch
    reflog_path = common_directory / "logs" / "refs" / "heads" / branch
    config_path = common_directory / "config"
    packed_refs_path = common_directory / "packed-refs"
    for target in (common_directory, ref_path.parent, reflog_path.parent, config_path.parent):
        _check_existing_write_chain(common_directory, target, reasons)
    for path in (config_path, packed_refs_path):
        if _path_exists(path):
            if path.is_symlink() or not path.is_file():
                reasons.append(f"Git branch-deletion file is unavailable: {path}")
            elif not os.access(path, os.W_OK):
                reasons.append(f"Git branch-deletion file is not writable: {path}")
    if reasons:
        raise WorktreeError("; ".join(dict.fromkeys(reasons)))


def _removal_preflight(repo: Path, path: Path) -> None:
    reasons: list[str] = []
    parent_error = _directory_access_error(path.parent, True)
    if parent_error:
        reasons.append(parent_error)
    if path.is_dir() and not path.is_symlink():
        _scan_removal_tree(path, reasons)
    else:
        reasons.append(f"worktree path is not an accessible directory: {path}")
    admin = _worktree_admin_path(path)
    if admin is not None:
        admin_parent_error = _directory_access_error(admin.parent, True)
        if admin_parent_error:
            reasons.append(admin_parent_error)
        if admin.is_dir() and not admin.is_symlink():
            _scan_removal_tree(admin, reasons)
        else:
            reasons.append(f"worktree administrative path is not an accessible directory: {admin}")
    if reasons:
        raise WorktreeError("; ".join(dict.fromkeys(reasons)))


def create_worktree(repo: Path, base: str, run_id: str, task: str, state_home: Path) -> WorktreeRecord:
    canonical_repo = _canonical_repository(repo)
    _validate_slug(run_id, "run_id")
    _validate_slug(task, "task")
    branch = f"devflow/{run_id}/{task}"
    canonical_state = _state_home(state_home)
    target = _owned_path(canonical_repo, run_id, task, canonical_state)
    if _path_exists(target):
        raise WorktreeError(f"worktree target already exists: {target}; branch remains preserved: {branch}")
    if _branch_exists(canonical_repo, branch):
        raise WorktreeError(f"branch already exists: {branch}; target remains preserved: {target}")
    base_commit, base_error = _resolve_commit(canonical_repo, base)
    if base_error:
        raise WorktreeError(f"invalid base {base!r}: {base_error}; target remains absent: {target}; branch remains absent: {branch}")
    try:
        _ensure_safe_owned_path(canonical_state, target)
    except (OSError, WorktreeError) as error:
        raise WorktreeError(
            f"cannot prepare worktree target {target} on branch {branch}: {error}"
        ) from error
    if _path_exists(target):
        raise WorktreeError(f"worktree target already exists: {target}; branch remains preserved: {branch}")
    result = _git(canonical_repo, "worktree", "add", "-b", branch, str(target), base_commit, check=False)
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
    return _resolve_commit(repo, reference)


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0
    )


def _finish_error(path: Path, branch: str, reasons: list[str], attached_branch: str | None = None) -> WorktreeError:
    recovery_branches = [branch]
    if attached_branch and attached_branch not in recovery_branches:
        recovery_branches.append(attached_branch)
    recovery = ", ".join(recovery_branches)
    return WorktreeError(
        f"cannot finish worktree: {'; '.join(reasons)}; preserve worktree at {path}; preserve branch {recovery}"
    )


def _restore_worktree(repo: Path, path: Path, branch: str, state_root: Path) -> tuple[bool, str]:
    try:
        _ensure_safe_owned_path(state_root, path)
    except (OSError, WorktreeError) as error:
        return False, f"cannot restore worktree path {path}: {error}"
    if _path_exists(path):
        return False, f"cannot restore worktree path {path}: path already exists"
    restored = _git(repo, "worktree", "add", str(path), branch, check=False)
    if restored.returncode:
        details = restored.stderr.strip() or restored.stdout.strip() or "git worktree add failed"
        return False, f"cannot restore worktree path {path}: {details}"
    return True, f"worktree restored at {path}"


def _post_removal_error(
    repo: Path,
    path: Path,
    branch: str,
    reasons: list[str],
) -> WorktreeError:
    try:
        registered = _registered_worktrees(repo)
    except WorktreeError as error:
        registered = {}
        reasons.append(f"cannot inspect residual worktree registration: {error}")
    if registered.get(path) == branch:
        residual_path = f"worktree restored at {path}"
    elif _path_exists(path):
        residual_path = f"worktree path exists but is not registered at {path}"
    else:
        residual_path = f"worktree path is absent at {path}"
    if _branch_exists(repo, branch):
        residual_branch = f"branch remains at refs/heads/{branch}"
    else:
        residual_branch = f"branch is absent at refs/heads/{branch}"
    if _path_exists(path):
        status_ok, status_details = _status(path)
        residual_status = (
            "residual worktree status is usable"
            if status_ok
            else f"residual worktree status is unavailable: {status_details.strip()}"
        )
    else:
        residual_status = "residual worktree status is unavailable because its path is absent"
    reasons.extend((residual_path, residual_branch, residual_status))
    return WorktreeError(
        f"cannot finish worktree after removal: {'; '.join(reasons)}; recovery worktree path {path}; recovery branch {branch}"
    )


def _post_remove_failure(repo: Path, path: Path, branch: str, reasons: list[str]) -> WorktreeError:
    try:
        registered = _registered_worktrees(repo)
    except WorktreeError as error:
        registered = {}
        reasons.append(f"cannot inspect residual worktree registration: {error}")
    path_present = _path_exists(path)
    attached_branch = registered.get(path)
    branch_present = _branch_exists(repo, branch)
    status_ok, status_details = _status(path) if path_present else (False, "worktree path is absent")
    if path_present and attached_branch == branch and branch_present and status_ok:
        reasons.append("worktree removal failed before changing registration, path, branch, or usable status")
        return _finish_error(path, branch, reasons, attached_branch)
    if not path_present and branch_present:
        try:
            state_root = _state_home()
        except OSError as error:
            reasons.append(f"cannot determine worktree recovery root: {error}")
        else:
            _, restore_detail = _restore_worktree(repo, path, branch, state_root)
            reasons.append(restore_detail)
    if path_present and not status_ok:
        reasons.append(f"residual worktree status is unavailable: {status_details.strip()}")
    elif path_present and status_details:
        reasons.append(f"residual worktree status: {status_details.strip()}")
    return _post_removal_error(repo, path, branch, reasons)


def finish_worktree(repo: Path, path: Path, branch: str, integrated_ref: str) -> None:
    supplied_path = Path(path).expanduser()
    try:
        canonical_repo = _canonical_repository(repo)
    except (OSError, WorktreeError) as error:
        raise _finish_error(supplied_path, branch, [str(error)]) from error
    try:
        target = supplied_path.resolve()
    except OSError as error:
        raise _finish_error(supplied_path, branch, [f"cannot resolve supplied worktree path: {error}"]) from error
    reasons: list[str] = []
    attached_branch: str | None = None
    canonical_state: Path | None = None
    try:
        run_id, task = _branch_parts(branch)
        canonical_state = _state_home()
        expected = _owned_path(canonical_repo, run_id, task, canonical_state)
        if target != expected:
            reasons.append(f"path is outside its exact owned target {expected}")
    except (ValueError, OSError) as error:
        reasons.append(str(error))
    try:
        registered = _registered_worktrees(canonical_repo)
    except (OSError, WorktreeError) as error:
        raise _finish_error(target, branch, [str(error)]) from error
    if target not in registered:
        reasons.append("path is not a registered worktree of this repository")
    else:
        attached_branch = registered[target]
        if attached_branch != branch:
            reasons.append(f"worktree is attached to {attached_branch!r}, not {branch!r}")
        clean_task, task_status = _status(target)
        if not clean_task:
            reasons.append(f"task worktree is dirty or unavailable: {task_status.strip()}")
    branch_attachments = [registered_path for registered_path, registered_branch in registered.items() if registered_branch == branch]
    if len(branch_attachments) != 1 or branch_attachments[0] != target:
        reasons.append(f"branch {branch!r} is attached to {branch_attachments!r}, not only to {target}")
    branch_commit, branch_error = _commit_for(canonical_repo, f"refs/heads/{branch}")
    integrated_commit, integrated_error = _commit_for(canonical_repo, integrated_ref)
    integration_head, integration_head_error = _commit_for(canonical_repo, "HEAD")
    upstream_commit, upstream_error = _configured_upstream_commit(canonical_repo, branch)
    if branch_error:
        reasons.append(f"branch tip is unavailable: {branch_error}")
    if integrated_error:
        reasons.append(f"integrated ref is unavailable: {integrated_error}")
    if integration_head_error:
        reasons.append(f"integration checkout HEAD is unavailable: {integration_head_error}")
    if upstream_error:
        reasons.append(upstream_error)
    if branch_commit and integrated_commit and not _is_ancestor(canonical_repo, branch_commit, integrated_commit):
        reasons.append(f"branch tip {branch} is not an ancestor of {integrated_ref}")
    if branch_commit and integration_head and not _is_ancestor(canonical_repo, branch_commit, integration_head):
        reasons.append(f"branch tip {branch} is not an ancestor of integration checkout HEAD")
    if branch_commit and upstream_commit and not _is_ancestor(canonical_repo, branch_commit, upstream_commit):
        reasons.append(f"branch tip {branch} is not an ancestor of its configured upstream")
    clean_integration, integration_status = _status(canonical_repo)
    if not clean_integration:
        reasons.append(f"integration checkout is dirty or unavailable: {integration_status.strip()}")
    if reasons:
        raise _finish_error(target, branch, reasons, attached_branch)
    try:
        _branch_delete_preflight(canonical_repo, branch)
        _removal_preflight(canonical_repo, target)
    except (OSError, WorktreeError) as error:
        raise _finish_error(target, branch, [str(error)], attached_branch) from error
    removed = _git(canonical_repo, "worktree", "remove", str(target), check=False)
    if removed.returncode:
        details = removed.stderr.strip() or removed.stdout.strip() or "git worktree remove failed"
        raise _post_remove_failure(canonical_repo, target, branch, [details])
    deleted = _git(canonical_repo, "branch", "-d", "--", branch, check=False)
    if deleted.returncode:
        details = deleted.stderr.strip() or deleted.stdout.strip() or "git branch -d failed"
        if canonical_state is None:
            canonical_state = _state_home()
        _, restore_detail = _restore_worktree(canonical_repo, target, branch, canonical_state)
        raise _post_removal_error(canonical_repo, target, branch, [details, restore_detail])


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
