"""Shared stdlib-only git identity helpers for Expskill scripts.

Dependency-free (stdlib only). Scripts load it lazily by sibling path so
single-file execution, importlib loading, and packaged layouts all work
without sys.path assumptions.

Used only to derive default values when the caller omits them.
Verification of explicitly passed values stays in each script to preserve
its own error contract.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def run_git(path: Path | str, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed in {path}")
    return completed.stdout.strip()


def toplevel(worktree: Path | str) -> Path:
    return Path(run_git(worktree, "rev-parse", "--show-toplevel")).resolve()


def branch(worktree: Path | str) -> str:
    return run_git(worktree, "branch", "--show-current")


def head(worktree: Path | str) -> str:
    return run_git(worktree, "rev-parse", "HEAD")


def dirty_fingerprint(worktree: Path | str) -> str:
    raw = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain=v1", "--untracked-files=all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).hexdigest()


def default_home(value: Path | str | None = None) -> Path:
    if value is not None:
        return Path(value)
    return Path.home().expanduser().resolve() / ".expskill"


def auto_identity(worktree_hint: Path | str | None = None) -> dict:
    wt = Path(worktree_hint).resolve() if worktree_hint is not None else Path.cwd().resolve()
    if not wt.is_dir():
        raise ValueError("cannot derive git identity: worktree is not a directory")
    try:
        repo = toplevel(wt)
        current = branch(wt)
        commit = head(wt)
    except ValueError as exc:
        raise ValueError("cannot derive git identity: not a git worktree") from exc
    if not current:
        raise ValueError("cannot derive branch: detached HEAD, pass branch explicitly")
    return {
        "repository": str(repo),
        "branch": current,
        "worktree": str(wt),
        "baseline": commit,
        "dirty_fingerprint": dirty_fingerprint(wt),
    }
