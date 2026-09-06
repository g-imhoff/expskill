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
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


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
OUTPUT_MODES = {"exact-text", "sha256"}
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
        or set(value) != CHARTER_FIELDS
        or value.get("schema_version") != "test-charter.v1"
        or value.get("run_id") != root.name
        or value.get("repository") != str(repository)
    ):
        _error("invalid-charter", "frozen charter shape or root binding is invalid")
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
    if set(value) != SPEC_FIELDS:
        _error("invalid-spec-shape", "final-action.json fields are not exact")
    if value.get("schema_version") != SCHEMA_VERSION:
        _error("invalid-spec-version", "unsupported final-action schema")
    expected_head, expected_branch = _read_charter_identity(repository, root)
    observation = _output_path(root, value.get("observation_path"), label="observation_path")
    metadata = _output_path(root, value.get("metadata_path"), label="metadata_path")
    if observation == metadata:
        _error("invalid-output-path", "observation and metadata paths must differ")

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
    if mode not in OUTPUT_MODES or not isinstance(expected_output, str):
        _error("invalid-predicate", "output predicate mode or value is invalid")
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


def _output_matches(raw: bytes, mode: str, expected: str) -> bool:
    if mode == "sha256":
        return hashlib.sha256(raw).hexdigest() == expected
    try:
        return raw.decode("utf-8") == expected
    except UnicodeDecodeError:
        return False


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
        completed = subprocess.run(
            command,
            cwd=repository,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        output = completed.stdout
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

        teardown: list[dict[str, str]] = []
        for raw in values["cleanup_paths"]:
            status_value, detail = _remove_created_path(repository / raw)
            teardown.append({"path": raw, "status": status_value, "detail": detail})
        teardown_status = "pass" if all(item["status"] == "pass" for item in teardown) else "fail"

        head = _git(repository, "rev-parse", "HEAD")
        branch = _git(repository, "branch", "--show-current")
        diff = _git(repository, "diff", "--exit-code", "HEAD", "--", *values["integrity_paths"])
        worktree = _git(
            repository,
            "status",
            "--short",
            "--untracked-files=all",
            "--",
            *values["integrity_paths"],
        )
        integrity_status = "pass" if (
            head.returncode == 0
            and head.stdout.strip() == values["expected_head"]
            and branch.returncode == 0
            and branch.stdout.strip() == values["expected_branch"]
            and diff.returncode == 0
            and worktree.returncode == 0
            and not worktree.stdout.strip()
        ) else "fail"
        predicate_match = completed.returncode == values["expected_exit"] and _output_matches(
            output, str(values["predicate_mode"]), str(values["predicate_value"])
        )
        matched = predicate_match and teardown_status == "pass" and integrity_status == "pass"
        metadata = {
            "schema_version": "test-final-action-record.v1",
            "command": command,
            "exit_code": str(completed.returncode),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_predicate": "match" if predicate_match else "mismatch",
            "teardown": teardown,
            "teardown_status": teardown_status,
            "head": head.stdout.strip(),
            "branch": branch.stdout.strip(),
            "integrity_paths": values["integrity_paths"],
            "source_diff": diff.stdout + diff.stderr,
            "source_status": worktree.stdout + worktree.stderr,
            "integrity_status": integrity_status,
            "outcome": "match" if matched else "mismatch",
        }
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
        return matched
    finally:
        os.close(observation_parent)
        if metadata_parent is not None:
            os.close(metadata_parent)


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
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        repository = _repository()
        root = _run_root(repository, parsed.root)
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
    except (OSError, subprocess.SubprocessError) as error:
        print(f"final_action_error=runtime-error {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
