#!/usr/bin/env python3
"""Validate one non-authoritative Test ledger batch, derive HEAD, and append it.

The caller authors complete semantic entries without revision identity in the
fixed ``ledger-batch.json`` input. This helper validates the entire candidate
before publication, derives every entry HEAD from the frozen charter, and
atomically replaces only the ledger container. Existing entries are preserved
byte-for-byte as values and can never be edited through this interface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_budget


BATCH_SCHEMA_VERSION = "test-ledger-batch.v1"
BATCH_FILENAME = "ledger-batch.json"
CHARTER_SCHEMA_VERSION = "test-charter.v1"
LEDGER_SCHEMA_VERSION = "test-action-ledger.v2"
MAX_SEMANTIC_ACTIONS = 8
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
HEAD_RE = re.compile(r"[0-9a-f]{40,64}\Z")
ACTION_ROLES = {"check", "journey"}
ACTION_STATES = {"pass", "fail", "blocked"}
RINGS = {"inner", "adjacent", "broader"}
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
LEDGER_FIELDS = {"schema_version", "run_id", "entries"}
BATCH_FIELDS = {"schema_version", "entries"}
ENTRY_FIELDS = {
    "action_id",
    "role",
    "ring",
    "head",
    "action",
    "path",
    "expected",
    "actual",
    "status",
    "oracle_ids",
    "artifact_ids",
}
BATCH_ENTRY_FIELDS = ENTRY_FIELDS - {"head"}


class LedgerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str) -> NoReturn:
    raise LedgerError(code, message)


def _reject_number(_: str) -> NoReturn:
    _error("invalid-json-number", "ledger inputs forbid JSON numbers")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            _error("duplicate-json-key", f"duplicate key: {key}")
        value[key] = item
    return value


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
        _error("wrong-working-directory", "run the appender from the repository root")
    return repository


def _harden_owned_directory(path: Path, label: str) -> None:
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


def _run_root(repository: Path, supplied: str) -> Path:
    raw = Path(supplied)
    root = Path(os.path.abspath(repository / raw if not raw.is_absolute() else raw))
    evidence = repository / ".test-evidence"
    if root.parent != evidence or RUN_ID_RE.fullmatch(root.name) is None:
        _error("invalid-run-root", "root must be <repository>/.test-evidence/<run-id>")
    _harden_owned_directory(evidence, ".test-evidence")
    _harden_owned_directory(root, "run root")
    return root


def _read_private_json(
    path: Path,
    *,
    label: str,
    missing_code: str,
    permission_code: str,
) -> tuple[dict[str, object], bytes, os.stat_result]:
    try:
        metadata = path.lstat()
    except OSError as error:
        _error(missing_code, f"cannot inspect {label}: {error}")
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _error(missing_code, f"{label} must be a regular non-symlink")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        _error(permission_code, f"{label} must be private and owned")
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except LedgerError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _error(missing_code, f"cannot decode {label}: {error}")
    if not isinstance(value, dict):
        _error(missing_code, f"{label} must be an object")
    return value, raw, metadata


def _nonempty(value: object, label: str, code: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _error(code, f"{label} must be a non-empty NUL-free string")
    return value


def _string_array(
    value: object,
    label: str,
    code: str,
    *,
    minimum: int = 0,
    unique: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        _error(code, f"{label} must be an array")
    result = [_nonempty(item, f"{label}[]", code) for item in value]
    if len(result) < minimum or (unique and len(result) != len(set(result))):
        _error(code, f"{label} has invalid cardinality")
    return result


def _validate_entry(
    value: object,
    *,
    label: str,
    fields: set[str],
    code: str,
    head: str | None,
    oracle_ids: set[str],
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _error(code, f"{label} fields are not exact")
    action_id = _nonempty(value.get("action_id"), f"{label}.action_id", code)
    role = value.get("role")
    ring = value.get("ring")
    status_value = value.get("status")
    if role not in ACTION_ROLES:
        _error(code, f"{label}.role is invalid")
    if ring not in RINGS:
        _error(code, f"{label}.ring is invalid")
    if status_value not in ACTION_STATES:
        _error(code, f"{label}.status is invalid")
    _nonempty(value.get("action"), f"{label}.action", code)
    paths = _string_array(value.get("path"), f"{label}.path", code)
    if role == "journey" and not paths:
        _error(code, f"{label}.path must identify the journey")
    _nonempty(value.get("expected"), f"{label}.expected", code)
    _nonempty(value.get("actual"), f"{label}.actual", code)
    referenced_oracles = set(
        _string_array(
            value.get("oracle_ids"),
            f"{label}.oracle_ids",
            code,
            unique=True,
        )
    )
    if not referenced_oracles <= oracle_ids:
        _error(code, f"{label}.oracle_ids contains an unknown oracle")
    _string_array(
        value.get("artifact_ids"),
        f"{label}.artifact_ids",
        code,
        minimum=1,
        unique=True,
    )
    result = dict(value)
    if head is not None:
        result["head"] = head
    else:
        entry_head = value.get("head")
        if not isinstance(entry_head, str) or HEAD_RE.fullmatch(entry_head) is None:
            _error(code, f"{label}.head is invalid")
    assert action_id
    return result


def _git_identity(repository: Path) -> tuple[str, str]:
    values: list[str] = []
    for arguments in (("rev-parse", "HEAD"), ("branch", "--show-current")):
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            _error("git-error", f"git {' '.join(arguments)} failed")
        values.append(completed.stdout.strip())
    return values[0], values[1]


def _validate_inputs(
    repository: Path,
    root: Path,
    charter: dict[str, object],
    ledger: dict[str, object],
    batch: dict[str, object],
) -> tuple[dict[str, object], int]:
    expected_fields = CHARTER_FIELDS | {"execution_budget"} if charter.get("schema_version") == "test-charter.v2" else CHARTER_FIELDS
    if set(charter) != expected_fields or charter.get("schema_version") not in {CHARTER_SCHEMA_VERSION, "test-charter.v2"}:
        _error("invalid-charter", "frozen charter fields or schema are invalid")
    try:
        budget = execution_budget.for_charter(charter)
    except ValueError as error:
        _error("invalid-budget", str(error))
    head = charter.get("head")
    branch = charter.get("branch")
    if (
        charter.get("run_id") != root.name
        or charter.get("repository") != str(repository)
        or not isinstance(head, str)
        or HEAD_RE.fullmatch(head) is None
        or not isinstance(branch, str)
        or not branch
    ):
        _error("invalid-charter", "frozen charter identity is invalid")
    if _git_identity(repository) != (head, branch):
        _error("revision-mismatch", "repository identity differs from frozen charter")

    material_oracles = charter.get("material_oracles")
    if not isinstance(material_oracles, list):
        _error("invalid-charter", "material_oracles must be an array")
    oracle_ids = {
        oracle.get("oracle_id")
        for oracle in material_oracles
        if isinstance(oracle, dict) and isinstance(oracle.get("oracle_id"), str)
    }
    if len(oracle_ids) != len(material_oracles):
        _error("invalid-charter", "material oracle IDs are invalid or duplicated")

    if set(ledger) != LEDGER_FIELDS or ledger.get("schema_version") != LEDGER_SCHEMA_VERSION:
        _error("invalid-ledger", "ledger fields or schema are invalid")
    if ledger.get("run_id") != root.name or not isinstance(ledger.get("entries"), list):
        _error("invalid-ledger", "ledger run identity or entries are invalid")
    existing: list[dict[str, object]] = []
    for index, entry in enumerate(ledger["entries"]):
        validated = _validate_entry(
            entry,
            label=f"ledger.entries[{index}]",
            fields=ENTRY_FIELDS,
            code="invalid-ledger",
            head=None,
            oracle_ids=oracle_ids,
        )
        if validated.get("head") != head:
            _error("invalid-ledger", f"ledger.entries[{index}].head differs from charter")
        existing.append(validated)

    if set(batch) != BATCH_FIELDS or batch.get("schema_version") != BATCH_SCHEMA_VERSION:
        _error("invalid-batch", "ledger batch fields or schema are invalid")
    if not isinstance(batch.get("entries"), list) or not batch["entries"]:
        _error("invalid-batch", "ledger batch entries must be a non-empty array")
    appended = [
        _validate_entry(
            entry,
            label=f"batch.entries[{index}]",
            fields=BATCH_ENTRY_FIELDS,
            code="invalid-batch-entry",
            head=head,
            oracle_ids=oracle_ids,
        )
        for index, entry in enumerate(batch["entries"])
    ]
    action_ids = [entry["action_id"] for entry in (*existing, *appended)]
    if len(action_ids) != len(set(action_ids)):
        _error("duplicate-action-id", "combined ledger action IDs must be unique")
    if len(action_ids) > int(budget["semantic_actions_max"]):
        _error("action-budget-exceeded", "combined ledger exceeds the frozen action budget")
    try:
        execution_budget.validate_actions(budget, action_ids)
    except ValueError as error:
        _error("unplanned-action", str(error))
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "run_id": root.name,
        "entries": [*existing, *appended],
    }, len(appended)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _write_exclusive(path: Path, raw: bytes) -> os.stat_result:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def _publish(
    root: Path,
    ledger_raw: bytes,
    ledger_metadata: os.stat_result,
    batch_metadata: os.stat_result,
    value: dict[str, object],
) -> None:
    ledger_path = root / "ledger.json"
    batch_path = root / BATCH_FILENAME
    token = secrets.token_hex(12)
    stage = root / f".ledger-append-{token}"
    consumed = root / f".ledger-batch-consumed-{token}"
    stage_metadata = _write_exclusive(stage, _canonical_bytes(value))
    moved = False
    published = False
    try:
        if (
            ledger_path.read_bytes() != ledger_raw
            or not _same_inode(ledger_path.lstat(), ledger_metadata)
            or not _same_inode(batch_path.lstat(), batch_metadata)
        ):
            _error("input-race", "ledger or batch changed before publication")
        os.rename(batch_path, consumed)
        moved = True
        if ledger_path.read_bytes() != ledger_raw or not _same_inode(
            ledger_path.lstat(), ledger_metadata
        ):
            _error("input-race", "ledger changed during publication")
        os.replace(stage, ledger_path)
        published = True
        try:
            directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # Replacement already committed. Reporting a rejection would invite
            # a retry of the consumed batch and duplicate immutable evidence.
            pass
        try:
            if _same_inode(consumed.lstat(), batch_metadata):
                consumed.unlink()
        except OSError:
            # The canonical batch name is already consumed. Retain an ambiguous
            # private quarantine rather than falsely reporting non-publication.
            pass
    except Exception:
        if not published and moved and not batch_path.exists():
            try:
                os.rename(consumed, batch_path)
            except OSError:
                pass
        raise
    finally:
        try:
            if _same_inode(stage.lstat(), stage_metadata):
                stage.unlink()
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        repository = _repository()
        root = _run_root(repository, parsed.root)
        charter, _, _ = _read_private_json(
            root / "charter.json",
            label="charter.json",
            missing_code="invalid-charter",
            permission_code="invalid-charter-permissions",
        )
        ledger, ledger_raw, ledger_metadata = _read_private_json(
            root / "ledger.json",
            label="ledger.json",
            missing_code="invalid-ledger",
            permission_code="invalid-ledger-permissions",
        )
        batch, _, batch_metadata = _read_private_json(
            root / BATCH_FILENAME,
            label=BATCH_FILENAME,
            missing_code="invalid-batch",
            permission_code="invalid-batch-permissions",
        )
        value, appended = _validate_inputs(repository, root, charter, ledger, batch)
        _publish(root, ledger_raw, ledger_metadata, batch_metadata, value)
        print(
            "ledger_append=PASS "
            + json.dumps(
                {"appended": str(appended), "total": str(len(value["entries"]))},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except LedgerError as error:
        print(f"ledger_append_error={error.code} {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"ledger_append_error=runtime-error {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
