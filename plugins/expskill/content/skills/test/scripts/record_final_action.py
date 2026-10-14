#!/usr/bin/env python3
"""Run one predeclared final product command and retain its closed evidence.

This helper deliberately makes no testing decisions.  The caller selects the
command, expected observation, cleanup boundary, and source-integrity scope in
advance.  The helper only executes that literal argv without a shell and
records whether the already-declared predicate matched.

The ``handoff`` mode mechanically performs draft composition, binds both
declared recorder outputs to the composed draft, runs the same self-recording
action, and performs terminal finalization with one authenticated run root.  It
still makes no testing decision, it only removes repeated path copying from the
closed terminal transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_budget
import append_ledger


SCHEMA_VERSION = "test-final-action.v2"
HANDOFF_MODE = "compose-record-finalize with one root argument"
SPEC_FILENAME = "final-action.json"
CHARTER_FILENAME = "charter.json"
SPEC_FIELDS = {
    "schema_version",
    "observation_path",
    "metadata_path",
    "expected_exit_code",
    "output_predicate",
    "integrity_paths",
    "cleanup_absent_paths",
}
CHARTER_FIELDS = {
    "schema_version",
    "run_id",
    "workflow_id",
    "repository",
    "branch",
    "head",
    "accepted_behavior",
    "scope",
    "material_oracles",
    "exemption_grounding_artifact_ids",
}
PREDICATE_FIELDS = {"mode", "value"}
EXECUTION_RECORD_FIELDS = {
    "schema_version", "command", "exit_code", "output_sha256", "output_predicate",
    "teardown", "teardown_status", "head", "branch", "integrity_paths", "source_diff",
    "source_status", "integrity_status", "outcome", "run_id", "charter_sha256",
    "observation_path", "execution_spec", "entry",
}
OUTPUT_MODES = {"exact-text", "sha256", "json-fields", "exit-only"}
HEAD_RE = re.compile(r"[0-9a-f]{40,64}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SAFE_CLEANUP_PARTS = {".runtime", ".pytest_cache", "__pycache__"}
FORBIDDEN_OUTPUT_PREFIXES = {"terminal"}
FORBIDDEN_OUTPUT_FILES = {
    SPEC_FILENAME,
    "charter.json",
    "ledger.json",
    "draft-preparation.json",
    "draft-final-delta.json",
    "draft.json",
}


class RecorderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str) -> NoReturn:
    raise RecorderError(code, message)


def _reject_number(_: str) -> NoReturn:
    _error("invalid-json-number", "final-action.json forbids JSON numbers")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            _error("duplicate-json-key", f"duplicate key: {key}")
        value[key] = item
    return value


def _read_spec(path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except OSError as error:
        _error("missing-spec", f"cannot inspect {path}: {error}")
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _error("invalid-spec-path", "final-action.json must be a regular non-symlink")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        _error("invalid-spec-permissions", "final-action.json must be private and owned")
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except RecorderError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _error("invalid-spec-json", f"cannot decode final-action.json: {error}")
    if not isinstance(value, dict):
        _error("invalid-spec-shape", "final-action.json must be an object")
    return value


def _read_charter_identity(repository: Path, root: Path) -> tuple[str, str]:
    path = root / CHARTER_FILENAME
    try:
        metadata = path.lstat()
    except OSError as error:
        _error("invalid-charter", f"cannot inspect frozen charter: {error}")
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _error("invalid-charter", "frozen charter must be a regular non-symlink")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        _error("invalid-charter", "frozen charter must be private and owned")
    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except RecorderError as error:
        _error("invalid-charter", f"cannot trust frozen charter: {error.code}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _error("invalid-charter", f"cannot decode frozen charter: {error}")
    if (
        not isinstance(value, dict)
        or set(value) != (CHARTER_FIELDS | {"execution_budget"} if value.get("schema_version") == "test-charter.v2" else CHARTER_FIELDS)
        or value.get("schema_version") not in {"test-charter.v1", "test-charter.v2"}
        or value.get("run_id") != root.name
        or value.get("repository") != str(repository)
    ):
        _error("invalid-charter", "frozen charter shape or root binding is invalid")
    try:
        execution_budget.for_charter(value)
    except ValueError as error:
        _error("invalid-budget", str(error))
    head = value.get("head")
    branch = value.get("branch")
    if not isinstance(head, str) or HEAD_RE.fullmatch(head) is None:
        _error("invalid-charter", "frozen charter head is invalid")
    if not isinstance(branch, str) or not branch:
        _error("invalid-charter", "frozen charter branch is invalid")
    return head, branch


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
        _error("wrong-working-directory", "run the recorder from the repository root")
    return repository


def _harden_owned_directory(path: Path, label: str, *, code: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _error(code, f"cannot open {label} as a non-symlink directory: {error}")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            _error(code, f"{label} must be a non-symlink directory")
        if metadata.st_uid != os.getuid():
            _error(code, f"{label} must be owned by the current user")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o077:
            try:
                os.fchmod(descriptor, mode & ~0o077)
            except OSError as error:
                _error(code, f"cannot make {label} private: {error}")
            if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
                _error(code, f"{label} could not be made private")
    finally:
        os.close(descriptor)


def _private_directory(path: Path, label: str) -> None:
    _harden_owned_directory(path, label, code="invalid-run-root")


def _release_evidence_parent_for_cleanup(root: Path) -> None:
    """Best-effort release after terminal publication for worktree cleanup."""

    evidence = root.parent
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(evidence, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            return
        os.fchmod(descriptor, 0o700)
    except OSError:
        # Terminal evidence is already published. Cleanup access is secondary
        # and must not turn a committed truthful result into a reported error.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _run_root(repository: Path, supplied: str) -> Path:
    raw = Path(supplied)
    absolute = Path(os.path.abspath(repository / raw if not raw.is_absolute() else raw))
    evidence = repository / ".test-evidence"
    if absolute.parent != evidence or not RUN_ID_RE.fullmatch(absolute.name):
        _error(
            "invalid-run-root",
            "root must be exactly <repository>/.test-evidence/<run-id>",
        )
    _private_directory(evidence, ".test-evidence")
    _private_directory(absolute, "run root")
    return absolute


def _relative_path(raw: object, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\n" in raw:
        _error("invalid-path", f"{label} must be a non-empty relative path")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        _error("invalid-path", f"{label} must remain beneath its declared root")
    return path


def _output_path(root: Path, raw: object, *, label: str) -> Path:
    relative = _relative_path(raw, label=label)
    if (
        relative.parts[0] in FORBIDDEN_OUTPUT_PREFIXES
        or relative.parts[0].startswith(".terminal-")
        or relative.as_posix() in FORBIDDEN_OUTPUT_FILES
    ):
        _error("invalid-output-path", f"{label} occupies a reserved path")
    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            _error("invalid-output-path", f"cannot create {label} parent: {error}")
        _harden_owned_directory(
            parent,
            f"{label} parent",
            code="invalid-output-path",
        )
    path = root / relative
    if os.path.lexists(path):
        _error("output-already-exists", f"{label} already exists")
    return path


def _string_array(value: object, *, label: str, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        _error("invalid-spec-shape", f"{label} must be an array of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            _error("invalid-spec-shape", f"{label} must contain non-empty strings")
        result.append(item)
    if len(result) != len(set(result)):
        _error("invalid-spec-shape", f"{label} must contain unique paths")
    return result


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _validate_spec(
    repository: Path, root: Path, value: dict[str, object]
) -> dict[str, object]:
    if (root / "successor.json").exists():
        _error("closed-run", "product execution belongs to the allocated successor")
    if set(value) != SPEC_FIELDS:
        _error("invalid-spec-shape", "final-action.json fields are not exact")
    if value.get("schema_version") != SCHEMA_VERSION:
        _error("invalid-spec-version", "unsupported final-action schema")
    expected_head, expected_branch = _read_charter_identity(repository, root)
    observation_relative = _relative_path(
        value.get("observation_path"),
        label="observation_path",
    )
    metadata_relative = _relative_path(
        value.get("metadata_path"),
        label="metadata_path",
    )
    if (
        observation_relative == metadata_relative
        or observation_relative in metadata_relative.parents
        or metadata_relative in observation_relative.parents
    ):
        _error(
            "invalid-output-path",
            "observation and metadata paths must be distinct and non-overlapping",
        )
    observation = _output_path(
        root,
        observation_relative.as_posix(),
        label="observation_path",
    )
    metadata = _output_path(
        root,
        metadata_relative.as_posix(),
        label="metadata_path",
    )

    expected_exit = value.get("expected_exit_code")
    if not isinstance(expected_exit, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", expected_exit):
        _error("invalid-predicate", "expected_exit_code must be a decimal string")
    if int(expected_exit) > 255:
        _error("invalid-predicate", "expected_exit_code must be between 0 and 255")
    predicate = value.get("output_predicate")
    if not isinstance(predicate, dict) or set(predicate) != PREDICATE_FIELDS:
        _error("invalid-predicate", "output_predicate fields are not exact")
    mode = predicate.get("mode")
    expected_output = predicate.get("value")
    if not isinstance(mode, str) or mode not in OUTPUT_MODES:
        _error("invalid-predicate", "output predicate mode or value is invalid")
    if mode == "json-fields":
        _validate_json_assertions(expected_output)
    elif not isinstance(expected_output, str):
        _error("invalid-predicate", "text and exit predicates require a string value")
    if mode == "exit-only" and expected_output != "":
        _error("invalid-predicate", "exit-only value must be empty")
    if mode == "sha256" and not SHA256_RE.fullmatch(expected_output):
        _error("invalid-predicate", "sha256 predicate must be lowercase hexadecimal")

    integrity_paths = [
        _relative_path(path, label="integrity_paths[]").as_posix()
        for path in _string_array(value.get("integrity_paths"), label="integrity_paths", minimum=1)
    ]
    if any(path.split("/", 1)[0] in {".git", ".test-evidence"} for path in integrity_paths):
        _error("invalid-integrity-path", "integrity paths cannot include control data")

    cleanup_paths: list[str] = []
    for raw in _string_array(value.get("cleanup_absent_paths"), label="cleanup_absent_paths"):
        relative = _relative_path(raw, label="cleanup_absent_paths[]")
        if not any(
            part in SAFE_CLEANUP_PARTS or part.startswith((".test-", ".e2e-"))
            for part in relative.parts
        ):
            _error("unsafe-cleanup-path", f"cleanup path is not a narrow test-runtime path: {raw}")
        target = repository / relative
        if os.path.lexists(target):
            _error("cleanup-target-not-absent", f"cleanup target must initially be absent: {raw}")
        cleanup_paths.append(relative.as_posix())

    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    if head.returncode != 0 or head.stdout.strip() != expected_head:
        _error("revision-mismatch", "repository head differs from frozen charter")
    if branch.returncode != 0 or branch.stdout.strip() != expected_branch:
        _error("revision-mismatch", "repository branch differs from frozen charter")

    baseline_diff = _git(
        repository, "diff", "--exit-code", "HEAD", "--", *integrity_paths
    )
    baseline_worktree = _git(
        repository,
        "status",
        "--short",
        "--untracked-files=all",
        "--",
        *integrity_paths,
    )
    if (
        baseline_diff.returncode != 0
        or baseline_worktree.returncode != 0
        or baseline_worktree.stdout.strip()
    ):
        _error(
            "dirty-integrity-path",
            "integrity paths must be clean before the final product action",
        )

    return {
        "root": root,
        "observation": observation,
        "metadata": metadata,
        "expected_exit": int(expected_exit),
        "predicate_mode": mode,
        "predicate_value": expected_output,
        "expected_head": expected_head,
        "expected_branch": expected_branch,
        "integrity_paths": integrity_paths,
        "cleanup_paths": cleanup_paths,
    }


def _validate_command(command: list[str]) -> None:
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        _error("missing-command", "run requires a literal product command after --")
    if any(not value or "\x00" in value or "\n" in value or "<<" in value for value in command):
        _error("inline-command-forbidden", "command arguments must be literal single-line argv")
    for index, value in enumerate(command):
        executable = Path(value).name
        arguments = command[index + 1 :]
        if executable in {"sh", "bash", "zsh"} and any(
            argument in {"-c", "-lc"}
            or re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", argument) is not None
            for argument in arguments
        ):
            _error("inline-command-forbidden", "inline shells are forbidden")
        if executable.startswith("python") and any(
            argument == "-"
            or argument.startswith("-c")
            or re.fullmatch(r"-[bBdEhiIOPqRsSuvVx]*c", argument) is not None
            for argument in arguments
        ):
            _error("inline-command-forbidden", "inline Python execution is forbidden")
        if executable == "env" and any(
            argument in {"-S", "--split-string"}
            or argument.startswith("--split-string=")
            for argument in arguments
        ):
            _error("inline-command-forbidden", "inline environment commands are forbidden")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_output_parent(root: Path, path: Path, *, label: str) -> tuple[int, str]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        _error("invalid-output-path", f"{label} escaped the authenticated run root")
    if len(relative.parts) == 0:
        _error("invalid-output-path", f"{label} has no output filename")

    descriptor: int | None = None
    try:
        descriptor = os.open(root, _directory_flags())
        for part in relative.parts[:-1]:
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            _error("invalid-output-path", f"{label} parent is not an owned directory")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            _error("invalid-output-path", f"{label} parent is not private")
        return descriptor, relative.parts[-1]
    except RecorderError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        _error("invalid-output-path", f"cannot bind {label} parent: {error}")


def _read_private_entry(path: Path) -> tuple[object, ...]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        _error("run-root-changed", f"cannot open retained evidence entry: {error}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _error("run-root-changed", "retained evidence entry is not a regular file")
        if before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) & 0o077:
            _error("run-root-changed", "retained evidence entry is not private and owned")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _error("run-root-changed", "retained evidence changed while it was read")
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            _error("run-root-changed", "retained evidence path was replaced")
        return (
            "file",
            before.st_dev,
            before.st_ino,
            stat.S_IMODE(before.st_mode),
            before.st_uid,
            before.st_size,
            before.st_mtime_ns,
            digest.hexdigest(),
        )
    except OSError as error:
        _error("run-root-changed", f"cannot read retained evidence entry: {error}")
    finally:
        os.close(descriptor)


def _snapshot_run_root(root: Path) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    pending = [(".", root)]
    while pending:
        relative, path = pending.pop()
        try:
            metadata = path.lstat()
        except OSError as error:
            _error("run-root-changed", f"cannot inspect retained evidence tree: {error}")
        if stat.S_ISLNK(metadata.st_mode):
            _error("run-root-changed", "retained evidence tree contains a symbolic link")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            _error("run-root-changed", "retained evidence tree is not private and owned")
        if stat.S_ISDIR(metadata.st_mode):
            snapshot[relative] = (
                "directory",
                metadata.st_dev,
                metadata.st_ino,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_uid,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
            try:
                children = sorted(path.iterdir(), key=lambda child: child.name)
            except OSError as error:
                _error("run-root-changed", f"cannot traverse retained evidence tree: {error}")
            for child in reversed(children):
                child_relative = child.name if relative == "." else f"{relative}/{child.name}"
                pending.append((child_relative, child))
        elif stat.S_ISREG(metadata.st_mode):
            snapshot[relative] = _read_private_entry(path)
        else:
            _error("run-root-changed", "retained evidence tree contains a special file")
    return snapshot


def _verify_run_root_unchanged(
    root: Path,
    expected: dict[str, tuple[object, ...]],
) -> None:
    observed = _snapshot_run_root(root)
    if observed != expected:
        _error("run-root-changed", "retained evidence changed during the product action")


def _verify_run_root_with_outputs(
    root: Path,
    expected: dict[str, tuple[object, ...]],
    outputs: tuple[Path, Path],
) -> None:
    allowed: set[str] = set()
    for path in outputs:
        try:
            allowed.add(path.relative_to(root).as_posix())
        except ValueError:
            _error("run-root-changed", "recorder output escaped the run root")
    observed = _snapshot_run_root(root)
    if set(observed) != set(expected) | allowed:
        _error("run-root-changed", "retained evidence tree gained or lost an entry")
    for relative, original in expected.items():
        current = observed.get(relative)
        if original[0] == "directory":
            if current is None or current[:5] != original[:5]:
                _error("run-root-changed", "retained evidence directory was replaced")
        elif current != original:
            _error("run-root-changed", "retained evidence file changed after recording")
    for relative in allowed:
        current = observed.get(relative)
        if current is None or current[0] != "file":
            _error("run-root-changed", "recorder output is not a retained regular file")


def _write_exclusive_at(descriptor: int, name: str, raw: bytes) -> os.stat_result:
    output = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=descriptor,
    )
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(output, raw[offset:])
        os.fsync(output)
        return os.fstat(output)
    finally:
        os.close(output)


def _verify_output_binding(
    path: Path,
    parent: int,
    name: str,
    written: os.stat_result,
) -> None:
    try:
        through_parent = os.stat(name, dir_fd=parent, follow_symlinks=False)
        through_path = path.lstat()
    except OSError as error:
        _error("run-root-changed", f"cannot verify recorder output binding: {error}")
    identity = (written.st_dev, written.st_ino)
    if (through_parent.st_dev, through_parent.st_ino) != identity:
        _error("run-root-changed", "recorder output parent binding changed")
    if (through_path.st_dev, through_path.st_ino) != identity:
        _error("run-root-changed", "recorder output path escaped its bound parent")
    if not stat.S_ISREG(through_path.st_mode) or stat.S_ISLNK(through_path.st_mode):
        _error("run-root-changed", "recorder output is not a regular file")
    if through_path.st_uid != os.getuid() or stat.S_IMODE(through_path.st_mode) & 0o077:
        _error("run-root-changed", "recorder output is not private and owned")


def _discard_rejected_draft(root: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(root, _directory_flags())
        metadata = os.stat("draft.json", dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            _error("preflight-cleanup-failed", "rejected draft is not a regular file")
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            _error("preflight-cleanup-failed", "rejected draft is not private and owned")
        os.unlink("draft.json", dir_fd=descriptor)
        os.fsync(descriptor)
    except RecorderError:
        raise
    except OSError as error:
        _error("preflight-cleanup-failed", f"cannot remove rejected draft: {error}")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_created_path(path: Path) -> tuple[str, str]:
    if not os.path.lexists(path):
        return "pass", "remained absent"
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return "fail", "refused a symbolic link"
    if stat.S_ISDIR(metadata.st_mode):
        for descendant in path.rglob("*"):
            if descendant.is_symlink():
                return "fail", "refused a tree containing a symbolic link"
        shutil.rmtree(path)
    elif stat.S_ISREG(metadata.st_mode):
        path.unlink()
    else:
        return "fail", "refused a non-regular runtime object"
    return ("pass", "removed test-created state") if not os.path.lexists(path) else ("fail", "path remained after cleanup")


def _validate_json_assertions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        _error("invalid-predicate", "json-fields requires one to 64 assertions")
    for assertion in value:
        if not isinstance(assertion, dict) or set(assertion) != {"path", "operator", "value"}:
            _error("invalid-predicate", "JSON assertion fields are not exact")
        path = assertion["path"]
        if not isinstance(path, list) or not 1 <= len(path) <= 16 or any(not isinstance(part, str) or not part or "\x00" in part for part in path):
            _error("invalid-predicate", "JSON assertion path must contain one to sixteen literal keys")
        operator = assertion["operator"]
        expected = assertion["value"]
        if not isinstance(operator, str):
            _error("invalid-predicate", "JSON assertion operator must be a string")
        if operator == "equals":
            if expected is not None and not isinstance(expected, (str, bool)):
                _error("invalid-predicate", "equals accepts a string, boolean, or null")
        elif operator in {"integer-equals", "length-equals"}:
            pattern = r"-?(?:0|[1-9][0-9]{0,17})" if operator == "integer-equals" else r"(?:0|[1-9][0-9]{0,17})"
            if not isinstance(expected, str) or re.fullmatch(pattern, expected) is None:
                _error("invalid-predicate", "numeric comparisons require bounded decimal strings")
        elif operator == "type":
            if not isinstance(expected, str) or expected not in {"object", "array", "string", "number", "integer", "boolean", "null"}:
                _error("invalid-predicate", "JSON type assertion is invalid")
        elif operator == "exists":
            if not isinstance(expected, bool):
                _error("invalid-predicate", "exists requires a boolean")
        else:
            _error("invalid-predicate", "unsupported JSON assertion operator")
    return value


def _json_matches(raw: bytes, assertions: object) -> bool:
    try:
        assertions = _validate_json_assertions(assertions)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_number)
    except (RecorderError, ValueError, UnicodeDecodeError, RecursionError):
        return False
    missing = object()
    for assertion in assertions:
        actual = payload
        for part in assertion["path"]:
            if isinstance(actual, dict):
                actual = actual.get(part, missing)
            elif isinstance(actual, list) and part.isascii() and part.isdecimal() and len(part) <= 8 and int(part) < len(actual):
                actual = actual[int(part)]
            else:
                actual = missing
                break
        operator = assertion["operator"]
        expected = assertion["value"]
        if operator == "exists":
            matched = (actual is not missing) is expected
        elif actual is missing:
            matched = False
        elif operator == "equals":
            matched = type(actual) is type(expected) and actual == expected
        elif operator == "integer-equals":
            matched = type(actual) is int and actual == int(expected)
        elif operator == "length-equals":
            matched = isinstance(actual, (str, list, dict)) and len(actual) == int(expected)
        else:
            matched = {
                "object": type(actual) is dict,
                "array": type(actual) is list,
                "string": type(actual) is str,
                "number": type(actual) in {int, float},
                "integer": type(actual) is int,
                "boolean": type(actual) is bool,
                "null": actual is None,
            }[expected]
        if not matched:
            return False
    return True


def _output_matches(raw: bytes, mode: str, expected: object) -> bool:
    if mode == "exit-only":
        return expected == ""
    if mode == "json-fields":
        return _json_matches(raw, expected)
    if mode == "sha256":
        return hashlib.sha256(raw).hexdigest() == expected
    if mode != "exact-text":
        return False
    try:
        return raw.decode("utf-8") == expected
    except UnicodeDecodeError:
        return False


def _execution_actual(record: dict[str, object]) -> str:
    return (f"exit={record['exit_code']} output={record['output_predicate']} "
            f"teardown={record['teardown_status']} integrity={record['integrity_status']}")


def verify_execution_record(
    record: object, entry: dict[str, object], charter_raw: bytes,
    observation: bytes, observation_path: str,
) -> bool:
    if not isinstance(record, dict) or set(record) != EXECUTION_RECORD_FIELDS or record.get("schema_version") != "test-execution-record.v1":
        return False
    if record.get("entry") != entry or record.get("charter_sha256") != hashlib.sha256(charter_raw).hexdigest():
        return False
    try:
        charter = json.loads(charter_raw.decode("utf-8"), object_pairs_hook=_pairs)
        command = record["command"]
        if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
            return False
        _validate_command(list(command))
        spec = record["execution_spec"]
        if not isinstance(spec, dict) or set(spec) != SPEC_FIELDS or spec["schema_version"] != SCHEMA_VERSION:
            return False
        if spec["observation_path"] != observation_path or record["observation_path"] != observation_path:
            return False
        if record["run_id"] != charter["run_id"] or record["head"] != charter["head"] or record["branch"] != charter["branch"]:
            return False
        if record["output_sha256"] != hashlib.sha256(observation).hexdigest():
            return False
        predicate = spec["output_predicate"]
        if not isinstance(predicate, dict) or set(predicate) != PREDICATE_FIELDS:
            return False
        expected_exit = spec["expected_exit_code"]
        if not isinstance(expected_exit, str) or re.fullmatch(r"(?:0|[1-9][0-9]{0,2})", expected_exit) is None or int(expected_exit) > 255:
            return False
        matches = record["exit_code"] == expected_exit and _output_matches(observation, predicate["mode"], predicate["value"])
        if record["output_predicate"] != ("match" if matches else "mismatch"):
            return False
        if record["integrity_paths"] != spec["integrity_paths"] or not spec["integrity_paths"]:
            return False
        integrity_paths = _string_array(spec["integrity_paths"], label="integrity_paths", minimum=1)
        cleanup_paths = _string_array(spec["cleanup_absent_paths"], label="cleanup_absent_paths")
        for path in integrity_paths:
            relative = _relative_path(path, label="integrity_paths")
            if relative.parts[0] in {".git", ".test-evidence"}:
                return False
        for path in cleanup_paths:
            relative = _relative_path(path, label="cleanup_absent_paths")
            if not any(part in SAFE_CLEANUP_PARTS or part.startswith((".test-", ".e2e-")) for part in relative.parts):
                return False
        teardown = record["teardown"]
        if not isinstance(teardown, list) or any(not isinstance(item, dict) or set(item) != {"path", "status", "detail"} for item in teardown):
            return False
        if [item["path"] for item in teardown] != cleanup_paths:
            return False
        teardown_pass = all(item.get("status") == "pass" for item in teardown)
        if record["teardown_status"] != ("pass" if teardown_pass else "fail"):
            return False
        integrity_pass = record["integrity_status"] == "pass" and record["source_diff"] == "" and record["source_status"] == ""
        passed = matches and teardown_pass and integrity_pass
        return (record["outcome"] == ("match" if passed else "mismatch")
                and entry["status"] == ("pass" if passed else "fail")
                and entry["actual"] == _execution_actual(record))
    except (KeyError, TypeError, ValueError, RecorderError):
        return False


def _execute(repository: Path, command: list[str], timeout: float) -> tuple[str, bytes]:
    try:
        process = subprocess.Popen(
            command, cwd=repository, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
        )
    except OSError as error:
        return "launch-error", f"recorder_launch_error={type(error).__name__}: {error}\n".encode("utf-8")
    try:
        try:
            output, _ = process.communicate(timeout=timeout)
            return str(process.returncode), output
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b""
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                output, _ = process.communicate(timeout=2)
            except subprocess.TimeoutExpired as drain_error:
                output = drain_error.stdout or output
                if process.stdout is not None:
                    process.stdout.close()
                process.kill()
                process.wait(timeout=2)
            return "timeout", output
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _postflight(repository: Path, values: dict[str, object]) -> dict[str, object]:
    teardown: list[dict[str, str]] = []
    for raw in values["cleanup_paths"]:
        try:
            status_value, detail = _remove_created_path(repository / raw)
        except OSError as error:
            status_value, detail = "fail", f"{type(error).__name__}: {error}"
        teardown.append({"path": raw, "status": status_value, "detail": detail})

    def capture(*arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return _git(repository, *arguments)
        except OSError as error:
            return subprocess.CompletedProcess(["git", *arguments], 1, "", f"{type(error).__name__}: {error}")

    head = capture("rev-parse", "HEAD")
    branch = capture("branch", "--show-current")
    diff = capture("diff", "--exit-code", "HEAD", "--", *values["integrity_paths"])
    worktree = capture("status", "--short", "--untracked-files=all", "--", *values["integrity_paths"])
    integrity_status = "pass" if (
        head.returncode == 0 and head.stdout.strip() == values["expected_head"]
        and branch.returncode == 0 and branch.stdout.strip() == values["expected_branch"]
        and diff.returncode == 0 and worktree.returncode == 0 and not worktree.stdout.strip()
    ) else "fail"
    return {
        "teardown": teardown,
        "teardown_status": "pass" if all(item["status"] == "pass" for item in teardown) else "fail",
        "head": head.stdout.strip(), "branch": branch.stdout.strip(),
        "source_diff": diff.stdout + diff.stderr,
        "source_status": worktree.stdout + worktree.stderr,
        "integrity_status": integrity_status,
    }


def _run(repository: Path, values: dict[str, object], command: list[str]) -> bool:
    _validate_command(command)
    root = values.get("root")
    observation_path = values.get("observation")
    metadata_path = values.get("metadata")
    if not isinstance(root, Path) or not isinstance(observation_path, Path) or not isinstance(metadata_path, Path):
        _error("invalid-output-path", "validated run root and recorder outputs are unavailable")
    observation_parent, observation_name = _open_output_parent(
        root,
        observation_path,
        label="observation_path",
    )
    metadata_parent: int | None = None
    try:
        metadata_parent, metadata_name = _open_output_parent(
            root,
            metadata_path,
            label="metadata_path",
        )
        protected = _snapshot_run_root(root)
        charter = json.loads((root / CHARTER_FILENAME).read_text(encoding="utf-8"))
        remaining = execution_budget.remaining_seconds(charter, root)
        if remaining <= 0 and "execution_result" not in values:
            _error("deadline-exhausted", "frozen run deadline expired before execution")
        if "execution_result" in values:
            exit_code, output = values["execution_result"]
            postflight = values["postflight"]
        else:
            try:
                exit_code, output = _execute(repository, command, remaining)
            finally:
                postflight = _postflight(repository, values)
        _verify_run_root_unchanged(root, protected)
        observation_metadata = _write_exclusive_at(
            observation_parent,
            observation_name,
            output,
        )
        _verify_output_binding(
            observation_path,
            observation_parent,
            observation_name,
            observation_metadata,
        )

        teardown_status = postflight["teardown_status"]
        integrity_status = postflight["integrity_status"]
        predicate_match = exit_code == str(values["expected_exit"]) and _output_matches(
            output, str(values["predicate_mode"]), values["predicate_value"]
        )
        matched = predicate_match and teardown_status == "pass" and integrity_status == "pass"
        metadata = {
            "schema_version": "test-final-action-record.v1",
            "command": command,
            "exit_code": exit_code,
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_predicate": "match" if predicate_match else "mismatch",
            "integrity_paths": values["integrity_paths"],
            **postflight,
            "outcome": "match" if matched else "mismatch",
        }
        if "execution_entry" in values:
            entry = dict(values["execution_entry"])
            entry.update({"head": values["expected_head"], "status": "pass" if matched else "fail", "actual": _execution_actual(metadata)})
            metadata.update({
                "schema_version": "test-execution-record.v1",
                "run_id": root.name,
                "charter_sha256": hashlib.sha256((root / CHARTER_FILENAME).read_bytes()).hexdigest(),
                "observation_path": observation_path.relative_to(root).as_posix(),
                "execution_spec": values["execution_spec"],
                "entry": entry,
            })
            values["recorded_entry"] = entry
        raw_metadata = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        metadata_written = _write_exclusive_at(
            metadata_parent,
            metadata_name,
            raw_metadata,
        )
        _verify_output_binding(
            metadata_path,
            metadata_parent,
            metadata_name,
            metadata_written,
        )
        _verify_run_root_with_outputs(
            root,
            protected,
            (observation_path, metadata_path),
        )
        print(f"final_action_predicate={'MATCH' if matched else 'MISMATCH'}")
        print(
            "final_action_components="
            + json.dumps(
                {
                    "integrity": integrity_status,
                    "output_predicate": "match" if predicate_match else "mismatch",
                    "teardown": teardown_status,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        print(f"observation_path={values['observation']}")
        print(f"metadata_path={values['metadata']}")
        if "execution_entry" in values:
            print("action_output_preview=" + json.dumps(output[:4000].decode("utf-8", errors="replace"), ensure_ascii=False))
        return matched
    finally:
        os.close(observation_parent)
        if metadata_parent is not None:
            os.close(metadata_parent)


def _record_action(repository: Path, root: Path, supplied: str) -> int:
    relative = _relative_path(supplied, label="spec")
    spec_path = root / relative
    if any(parent.is_symlink() for parent in (spec_path, *spec_path.parents) if parent != root.parent):
        _error("invalid-spec-path", "action spec cannot contain a symbolic link")
    manifest = _read_spec(spec_path)
    if manifest.get("schema_version") == "test-recorded-wave.v1":
        if set(manifest) != {"schema_version", "actions"} or not isinstance(manifest["actions"], list) or not 1 <= len(manifest["actions"]) <= 8:
            _error("invalid-action-spec", "a recorded wave contains one to eight closed action manifests")
        manifests = manifest["actions"]
    else:
        manifests = [manifest]
    return _record_manifests(repository, root, manifests)


def _prepare_recorded_manifest(repository: Path, root: Path, manifest: object) -> tuple[dict[str, object], list[str], dict[str, object]]:
    if not isinstance(manifest, dict):
        _error("invalid-action-spec", "recorded action must be an object")
    if set(manifest) != {"schema_version", "entry", "command", "execution"} or manifest["schema_version"] != "test-recorded-action.v1":
        _error("invalid-action-spec", "recorded action manifest fields or schema are invalid")
    entry = manifest["entry"]
    fields = append_ledger.BATCH_ENTRY_FIELDS - {"actual", "status"}
    if not isinstance(entry, dict) or set(entry) != fields:
        _error("invalid-action-spec", "entry supplies only predeclared action fields, never actual or status")
    command = manifest["command"]
    if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
        _error("invalid-action-spec", "command must be literal argv")
    _validate_command(command)
    if len(entry.get("artifact_ids", [])) != 2:
        _error("invalid-action-spec", "artifact_ids must identify observation then metadata")
    execution = manifest["execution"]
    if not isinstance(execution, dict):
        _error("invalid-action-spec", "execution must be a closed recorder spec")
    values = _validate_spec(repository, root, execution)
    values.update({"execution_entry": entry, "execution_spec": execution})
    return values, command, dict(entry, actual="pending recorder observation", status="blocked")


def _record_manifests(repository: Path, root: Path, manifests: list[object]) -> int:
    prepared = [_prepare_recorded_manifest(repository, root, manifest) for manifest in manifests]
    outputs = [values[label] for values, _, _ in prepared for label in ("observation", "metadata")]
    if len(outputs) != len(set(outputs)) or any(left in right.parents for left in outputs for right in outputs if left != right):
        _error("invalid-output-path", "wave recorder outputs must be distinct and non-overlapping")
    cleanup = [Path(path) for values, _, _ in prepared for path in values["cleanup_paths"]]
    if len(cleanup) != len(set(cleanup)) or any(left in right.parents for left in cleanup for right in cleanup if left != right):
        _error("unsafe-cleanup-path", "parallel actions require disjoint cleanup ownership")
    charter, _, _ = append_ledger._read_private_json(root / CHARTER_FILENAME, label="charter", missing_code="invalid-charter", permission_code="invalid-charter")
    ledger, ledger_raw, ledger_metadata = append_ledger._read_private_json(root / "ledger.json", label="ledger", missing_code="invalid-ledger", permission_code="invalid-ledger")
    if os.path.lexists(root / append_ledger.BATCH_FILENAME):
        _error("invalid-batch", "consume or correct an existing ledger batch before recording")
    append_ledger._validate_inputs(repository, root, charter, ledger, {"schema_version": append_ledger.BATCH_SCHEMA_VERSION, "entries": [pending for _, _, pending in prepared]})
    budget = execution_budget.for_charter(charter)
    if budget["waves"]:
        indexes = [index for _, _, pending in prepared for index, wave in enumerate(budget["waves"]) if pending["action_id"] in wave]
        if len(set(indexes)) != 1:
            _error("unplanned-action", "one recorded batch cannot cross frozen dependency waves")
    if len(prepared) > 1:
        protected = _snapshot_run_root(root)
        remaining = execution_budget.remaining_seconds(charter, root)
        if remaining <= 0:
            _error("deadline-exhausted", "frozen run deadline expired before execution")
        try:
            with ThreadPoolExecutor(max_workers=len(prepared)) as executor:
                completed = list(executor.map(lambda command: _execute(repository, command, remaining), [command for _, command, _ in prepared]))
        finally:
            for values, _, _ in prepared:
                values["postflight"] = _postflight(repository, values)
        _verify_run_root_unchanged(root, protected)
        for (values, _, _), result in zip(prepared, completed):
            values["execution_result"] = result
    outcomes = [_run(repository, values, command) for values, command, _ in prepared]
    recorded_entries = [dict(values["recorded_entry"]) for values, _, _ in prepared]
    for recorded in recorded_entries:
        del recorded["head"]
    batch = {"schema_version": append_ledger.BATCH_SCHEMA_VERSION, "entries": recorded_entries}
    output, _ = append_ledger._validate_inputs(repository, root, charter, ledger, batch)
    if (root / "ledger.json").read_bytes() != ledger_raw or not append_ledger._same_inode((root / "ledger.json").lstat(), ledger_metadata):
        _error("input-race", "ledger changed during execution")
    batch_path = root / append_ledger.BATCH_FILENAME
    batch_metadata = append_ledger._write_exclusive(batch_path, append_ledger._canonical_bytes(batch))
    append_ledger._publish(root, ledger_raw, ledger_metadata, batch_metadata, output)
    for (values, _, _), recorded in zip(prepared, recorded_entries):
        entry = values["execution_entry"]
        execution = values["execution_spec"]
        print("recorded_action=" + json.dumps({"action_id": recorded["action_id"], "status": recorded["status"], "artifacts": [
            {"artifact_id": entry["artifact_ids"][0], "kind": "log", "path": execution["observation_path"]},
            {"artifact_id": entry["artifact_ids"][1], "kind": "log", "path": execution["metadata_path"]},
        ]}, sort_keys=True, separators=(",", ":")))
    return 0 if all(outcomes) else 1


def _relay_helper(arguments: list[str], repository: Path) -> int:
    completed = subprocess.run(
        arguments,
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def _bind_handoff_artifacts(root: Path, values: dict[str, object]) -> None:
    """Require both recorder outputs exactly once in the composed draft."""

    path = root / "draft.json"
    try:
        metadata = path.lstat()
    except OSError as error:
        _error("handoff-artifact-mismatch", f"cannot inspect composed draft: {error}")
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _error(
            "handoff-artifact-mismatch",
            "composed draft must be a regular non-symlink",
        )
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        _error("handoff-artifact-mismatch", "composed draft must be private and owned")
    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except RecorderError as error:
        _error(
            "handoff-artifact-mismatch",
            f"cannot trust composed draft: {error.code}",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _error("handoff-artifact-mismatch", f"cannot decode composed draft: {error}")
    if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
        _error("handoff-artifact-mismatch", "composed draft artifacts are invalid")

    paths: list[str] = []
    for artifact in value["artifacts"]:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            _error("handoff-artifact-mismatch", "composed draft artifact path is invalid")
        paths.append(artifact["path"])

    for label in ("observation", "metadata"):
        output = values.get(label)
        if not isinstance(output, Path):
            _error("handoff-artifact-mismatch", f"validated {label} path is unavailable")
        try:
            expected = output.relative_to(root).as_posix()
        except ValueError:
            _error("handoff-artifact-mismatch", f"validated {label} path escaped the run root")
        if paths.count(expected) != 1:
            _error(
                "handoff-artifact-mismatch",
                f"draft.artifacts must declare {label} path exactly once: {expected}",
            )
    ledger_path = root / "ledger.json"
    if ledger_path.exists():
        ledger = _read_spec(ledger_path)
        charter = _read_spec(root / CHARTER_FILENAME)
        if ledger.get("entries") == [] and charter.get("material_oracles") == []:
            return
        artifact_by_path = {item["path"]: item["artifact_id"] for item in value["artifacts"]}
        ids = {artifact_by_path[values[label].relative_to(root).as_posix()] for label in ("observation", "metadata")}
        candidates = [entry for entry in ledger.get("entries", []) if isinstance(entry, dict) and ids <= set(entry.get("artifact_ids", []))]
        if len(candidates) != 1:
            _error("handoff-artifact-mismatch", "one final ledger entry must bind both recorder artifacts")
        values["execution_entry"] = candidates[0]
        values["execution_spec"] = _read_spec(root / SPEC_FILENAME)


def _handoff(
    repository: Path,
    root: Path,
    values: dict[str, object],
    command: list[str],
) -> int:
    _validate_command(list(command))
    finalizer = Path(__file__).resolve().with_name("finalize_evidence.py")
    root_value = str(root)
    composed = _relay_helper(
        [
            sys.executable,
            str(finalizer),
            "compose-draft",
            "--root",
            root_value,
            "--preparation",
            str(root / "draft-preparation.json"),
            "--delta",
            str(root / "draft-final-delta.json"),
        ],
        repository,
    )
    if composed != 0:
        return composed
    try:
        _bind_handoff_artifacts(root, values)
    except RecorderError:
        _discard_rejected_draft(root)
        raise
    if not _run(repository, values, command):
        return 1
    finalized = _relay_helper(
        [
            sys.executable,
            str(finalizer),
            "--root",
            root_value,
            "--charter",
            str(root / "charter.json"),
            "--ledger",
            str(root / "ledger.json"),
            "--draft",
            str(root / "draft.json"),
        ],
        repository,
    )
    if finalized == 0:
        _release_evidence_parent_for_cleanup(root)
    return finalized


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--root", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--root", required=True)
    run.add_argument("command", nargs=argparse.REMAINDER)
    handoff = subparsers.add_parser("handoff")
    handoff.add_argument("--root", required=True)
    handoff.add_argument("command", nargs=argparse.REMAINDER)
    record = subparsers.add_parser("record")
    record.add_argument("--root", required=True)
    record.add_argument("--spec", required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        repository = _repository()
        root = _run_root(repository, parsed.root)
        if parsed.mode == "record":
            return _record_action(repository, root, parsed.spec)
        spec = _read_spec(root / SPEC_FILENAME)
        values = _validate_spec(repository, root, spec)
        if parsed.mode == "validate":
            print("final_action_validation=PASS")
            return 0
        if parsed.mode == "handoff":
            return _handoff(repository, root, values, list(parsed.command))
        return 0 if _run(repository, values, list(parsed.command)) else 1
    except RecorderError as error:
        print(f"final_action_error={error.code} {error}", file=sys.stderr)
        return 2
    except append_ledger.LedgerError as error:
        print(f"final_action_error={error.code} {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print(f"final_action_error=runtime-error {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
