#!/usr/bin/env python3
"""Allocate one Test run root and print deterministic grounding context.

This helper makes no scope or testing decision. It replaces shell-dependent
timestamp and run-ID expansion with one private root, reports the current named
branch and deadline window, and emits a bounded first-party inventory/content
snapshot for the opening grounding call.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn


USABLE_BUDGET_SECONDS = 780
MAX_TEXT_FILES = 256
MAX_TEXT_BYTES = 2_000_000
SKILL_PREFIX = ".agents/skills/test/"


class BootstrapError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str) -> NoReturn:
    raise BootstrapError(code, message)


def _git(repository: Path, *arguments: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr
        detail = (
            stderr.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes)
            else stderr
        ).strip()
        _error("git-error", f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout


def _repository() -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        _error("not-a-repository", "current directory is not inside a Git repository")
    repository = Path(completed.stdout.strip()).resolve()
    if Path.cwd().resolve() != repository:
        _error("wrong-working-directory", "run bootstrap from the repository root")
    return repository


def _secure_directory(path: Path, label: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _error("invalid-run-root", f"cannot open {label}: {error}")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            _error("invalid-run-root", f"{label} must be an owned real directory")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o077:
            os.fchmod(descriptor, mode & ~0o077)
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            _error("invalid-run-root", f"{label} must be private")
    except OSError as error:
        _error("invalid-run-root", f"cannot secure {label}: {error}")
    finally:
        os.close(descriptor)


def _allocate_root(repository: Path, now: datetime) -> Path:
    evidence = repository / ".test-evidence"
    try:
        evidence.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        _error("invalid-run-root", f"cannot create .test-evidence: {error}")
    _secure_directory(evidence, ".test-evidence")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(evidence, flags)
    except OSError as error:
        _error("invalid-run-root", f"cannot open .test-evidence: {error}")
    authenticated = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            _error(
                "invalid-run-root",
                ".test-evidence must be an owned real directory",
            )
        authenticated = True
        try:
            os.fchmod(descriptor, 0o700)
        except OSError as error:
            _error("invalid-run-root", f"cannot open root allocation: {error}")
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
            _error("invalid-run-root", "cannot open root allocation")

        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        for _ in range(32):
            run_id = f"test-{timestamp}-{secrets.token_hex(6)}"
            root = evidence / run_id
            try:
                os.mkdir(run_id, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                continue
            except OSError as error:
                _error("invalid-run-root", f"cannot create run root: {error}")
            _secure_directory(root, "run root")
            return root
        _error("run-id-collision", "could not allocate a unique run root")
    finally:
        try:
            if authenticated:
                os.fchmod(descriptor, 0o500)
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o500:
                    _error("invalid-run-root", "cannot seal .test-evidence")
        except OSError as error:
            _error("invalid-run-root", f"cannot seal .test-evidence: {error}")
        finally:
            os.close(descriptor)


def _inventory(repository: Path) -> list[str]:
    raw = _git(
        repository,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        binary=True,
    )
    assert isinstance(raw, bytes)
    try:
        paths = [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as error:
        _error("invalid-inventory", f"first-party path is not UTF-8: {error}")
    safe: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\n" in raw_path
            or "\x00" in raw_path
            or raw_path.startswith((".git/", ".test-evidence/"))
        ):
            continue
        safe.append(path.as_posix())
    return sorted(set(safe))


def _text_snapshot(repository: Path, paths: list[str]) -> list[tuple[str, str | None]]:
    records: list[tuple[str, str | None]] = []
    text_count = 0
    text_bytes = 0
    for raw_path in paths:
        if raw_path.startswith(SKILL_PREFIX):
            continue
        path = repository / raw_path
        try:
            metadata = path.lstat()
        except OSError:
            records.append((raw_path, None))
            continue
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            records.append((raw_path, None))
            continue
        try:
            raw = path.read_bytes()
            if b"\0" in raw:
                raise UnicodeDecodeError("utf-8", raw, 0, 1, "NUL byte")
            contents = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            records.append((raw_path, None))
            continue
        text_count += 1
        text_bytes += len(raw)
        if text_count > MAX_TEXT_FILES or text_bytes > MAX_TEXT_BYTES:
            return []
        records.append((raw_path, contents))
    return records


def _timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run() -> None:
    repository = _repository()
    now = datetime.now(timezone.utc)
    branch = str(_git(repository, "branch", "--show-current")).strip()
    if not branch:
        _error("unnamed-branch", "Test requires a named branch")
    root = _allocate_root(repository, now)
    payload = {
        "branch": branch,
        "cutoff_at": _timestamp(now + timedelta(seconds=USABLE_BUDGET_SECONDS)),
        "repository": str(repository),
        "root": str(root),
        "run_id": root.name,
        "started_at": _timestamp(now),
    }
    print("test_run_bootstrap=" + json.dumps(payload, sort_keys=True, separators=(",", ":")))

    paths = _inventory(repository)
    print("FIRST_PARTY_INVENTORY_BEGIN")
    for path in paths:
        print(path)
    print("FIRST_PARTY_INVENTORY_END")

    records = _text_snapshot(repository, paths)
    if not records and any(not path.startswith(SKILL_PREFIX) for path in paths):
        print("FIRST_PARTY_CONTENTS_SKIPPED=repository exceeds bounded bootstrap")
        return
    for path, contents in records:
        if contents is None:
            print(f"FIRST_PARTY_NON_TEXT_OR_SPECIAL={path}")
            continue
        print(f"FIRST_PARTY_FILE_BEGIN={path}")
        sys.stdout.write(contents)
        if contents and not contents.endswith("\n"):
            print()
        print(f"FIRST_PARTY_FILE_END={path}")


def main() -> int:
    try:
        _run()
        return 0
    except BootstrapError as error:
        print(f"test_run_bootstrap_error={error.code} {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"test_run_bootstrap_error=runtime-error {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
