#!/usr/bin/env python3
"""Allocate one Test run root and print deterministic grounding context.

This helper makes no scope or testing decision. It replaces shell-dependent
timestamp and run-ID expansion with one private root, reports the current named
branch and deadline window, and emits a bounded first-party inventory/content
snapshot for the opening grounding call.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

import execution_budget


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
        if metadata.st_size > MAX_TEXT_BYTES:
            return []
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


def _continue(repository: Path, arguments: argparse.Namespace) -> None:
    root = Path(arguments.root)
    if not root.is_absolute() or root.parent != repository / ".test-evidence" or root.is_symlink():
        _error("invalid-run-root", "continuation requires the allocated absolute run root")
    _secure_directory(root, "run root")
    record = root / "bootstrap.json"
    if record.is_symlink() or not record.is_file() or record.stat().st_size > 2_000_000:
        _error("invalid-run-root", "continuation requires the retained bootstrap record")
    metadata = record.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        _error("invalid-run-root", "bootstrap record must be private and owned")
    payload = json.loads(record.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("root") != str(root) or payload.get("repository") != str(repository):
        _error("invalid-run-root", "bootstrap record does not bind this run root")
    if payload.get("branch") != str(_git(repository, "branch", "--show-current")).strip() or payload.get("head") != str(_git(repository, "rev-parse", "HEAD")).strip():
        _error("revision-drift", "repository revision differs from the opening bootstrap")
    if not arguments.read_path or len(arguments.read_path) > 32:
        _error("invalid-read-path", "select between one and 32 inventory paths")
    if not 1 <= arguments.max_lines <= 2_000 or arguments.start_line < 1:
        _error("invalid-read-path", "line window must be positive and at most 2000 lines")
    inventory = set(_inventory(repository))
    selected: list[tuple[str, list[str]]] = []
    total = 0
    for raw_path in arguments.read_path:
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts or path.as_posix() not in inventory or raw_path.startswith(SKILL_PREFIX):
            _error("invalid-read-path", "read paths must name first-party inventory files")
        target = repository / path
        if any(parent.is_symlink() for parent in (target, *target.parents)) or not target.is_file():
            _error("invalid-read-path", "read path must be a regular file without symlinks")
        lines: list[str] = []
        with target.open("rb") as source:
            for number in range(1, arguments.start_line + arguments.max_lines):
                raw = source.readline(MAX_TEXT_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_TEXT_BYTES or b"\0" in raw:
                    _error("invalid-read-path", "read window contains an oversized or binary line")
                if number >= arguments.start_line:
                    try:
                        line = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        _error("invalid-read-path", "read window is not UTF-8 text")
                    total += len(raw)
                    if total > MAX_TEXT_BYTES:
                        _error("read-budget-exceeded", "select a smaller targeted line window")
                    lines.append(line)
        selected.append((raw_path, lines))
    print("test_run_grounding=" + json.dumps(payload, sort_keys=True, separators=(",", ":")))
    for path, lines in selected:
        print(f"FIRST_PARTY_FILE_BEGIN={path}")
        print(f"FIRST_PARTY_FILE_LINES={arguments.start_line}:{arguments.start_line + len(lines) - 1}")
        sys.stdout.write("".join(lines))
        if lines and not lines[-1].endswith("\n"):
            print()
        print(f"FIRST_PARTY_FILE_END={path}")


def _run(arguments: argparse.Namespace) -> None:
    repository = _repository()
    if arguments.root is not None:
        _continue(repository, arguments)
        return
    if arguments.read_path:
        _error("invalid-run-root", "targeted reads require the existing --root")
    now = datetime.now(timezone.utc)
    branch = str(_git(repository, "branch", "--show-current")).strip()
    if not branch:
        _error("unnamed-branch", "Test requires a named branch")
    successor = None
    inherited_start = None
    if arguments.successor_of is not None:
        predecessor = Path(arguments.successor_of)
        if not predecessor.is_absolute() or predecessor.parent != repository / ".test-evidence" or predecessor.is_symlink():
            _error("invalid-successor", "use the exact allocated predecessor root in this worktree")
        _secure_directory(predecessor, "predecessor run root")
        if (predecessor / "successor.json").exists():
            _error("already-closed", "predecessor already has a successor")
        previous = execution_budget._private_json(predecessor / "charter.json")
        opening = execution_budget._private_json(predecessor / "bootstrap.json")
        ledger = execution_budget._private_json(predecessor / "ledger.json")
        if previous.get("repository") != str(repository) or previous.get("run_id") != predecessor.name or previous.get("branch") != branch or previous.get("head") != str(_git(repository, "rev-parse", "HEAD")).strip():
            _error("invalid-successor", "recovery requires the same repository, branch, and HEAD")
        if arguments.recovery_kind not in {"test-system-defect", "environment-blocker"} or not arguments.correction or not arguments.correction.strip():
            _error("invalid-successor", "classify the recovered cause and state its permitted correction")
        entries = ledger.get("entries")
        if not isinstance(entries, list) or not any(isinstance(entry, dict) and entry.get("status") == "fail" for entry in entries):
            _error("invalid-successor", "recovery requires a retained failed action")
        context = execution_budget.successor_context(previous, predecessor)
        if int(context["actions_used"]) + len(entries) >= int(execution_budget.for_charter(previous)["semantic_actions_max"]) or execution_budget.remaining_seconds(previous, predecessor) <= 0:
            _error("allowance-exhausted", "successor cannot reset the cumulative action or time allowance")
        inherited_start = opening["started_at"]
        successor = {"predecessor_root": str(predecessor), "reason": "recovery", "classification": arguments.recovery_kind,
                     "correction": arguments.correction, "source_files": execution_budget.retained_files(predecessor)}
    elif arguments.recovery_kind is not None or arguments.correction is not None:
        _error("invalid-successor", "recovery details require --successor-of")
    root = _allocate_root(repository, now)
    payload = {
        "branch": branch,
        "head": str(_git(repository, "rev-parse", "HEAD")).strip(),
        "cutoff_at": _timestamp(now + timedelta(seconds=USABLE_BUDGET_SECONDS)),
        "repository": str(repository),
        "root": str(root),
        "run_id": root.name,
        "started_at": _timestamp(now),
    }
    if successor is not None:
        payload["started_at"] = inherited_start
        payload["cutoff_at"] = _timestamp(datetime.fromisoformat(str(inherited_start).replace("Z", "+00:00")) + timedelta(seconds=int(execution_budget.for_charter(previous)["usable_budget_seconds"])))
        payload["successor_created_at"] = _timestamp(now)
        payload["successor"] = successor
        descriptor = os.open(predecessor / "successor.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"root": str(root), "run_id": root.name}, output, sort_keys=True)
    descriptor = os.open(root / "bootstrap.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
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


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root")
    parser.add_argument("--successor-of")
    parser.add_argument("--recovery-kind", choices=("test-system-defect", "environment-blocker"))
    parser.add_argument("--correction")
    parser.add_argument("--read-path", action="append", default=[])
    parser.add_argument("--start-line", type=int, default=1)
    parser.add_argument("--max-lines", type=int, default=200)
    parsed = parser.parse_args(arguments)
    try:
        _run(parsed)
        return 0
    except BootstrapError as error:
        print(f"test_run_bootstrap_error={error.code} {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError, UnicodeError) as error:
        print(
            f"test_run_bootstrap_error=runtime-error {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
