#!/usr/bin/env python3
"""Freeze a Test charter with identity derived from the live repository.

The caller supplies only conceptual charter fields in
``charter-preparation.json``. This helper derives the canonical repository,
branch, HEAD, and run ID, then exclusively publishes ``charter.json`` and an
empty direct ledger before any product action runs. It makes no testing or
scope decision.
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


PREPARATION_SCHEMA_VERSION = "test-charter-preparation.v1"
CHARTER_SCHEMA_VERSION = "test-charter.v1"
LEDGER_SCHEMA_VERSION = "test-action-ledger.v2"
PREPARATION_FILENAME = "charter-preparation.json"
PREPARATION_FIELDS = {
    "schema_version",
    "workflow_id",
    "accepted_behavior",
    "scope",
    "material_oracles",
    "exemption_grounding_artifact_ids",
}
SCOPE_FIELDS = {"accepted_behavior", "inner_ring", "adjacent_ring", "broader_ring"}
ORACLE_FIELDS = {"oracle_id", "behavior", "consumer_surface", "required_action_ids"}
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
HEAD_RE = re.compile(r"[0-9a-f]{40,64}\Z")


class FreezeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _error(code: str, message: str) -> NoReturn:
    raise FreezeError(code, message)


def _reject_number(_: str) -> NoReturn:
    _error("invalid-preparation", "charter preparation forbids JSON numbers")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            _error("invalid-preparation", f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        _error("git-error", f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


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
        _error("wrong-working-directory", "run the freezer from the repository root")
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
        _error(
            "invalid-run-root",
            "root must be exactly <repository>/.test-evidence/<run-id>",
        )
    _harden_owned_directory(evidence, ".test-evidence")
    _harden_owned_directory(root, "run root")
    return root


def _read_preparation(path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except OSError as error:
        _error("missing-preparation", f"cannot inspect {path}: {error}")
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        _error("invalid-preparation-path", "preparation must be a regular non-symlink")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        _error(
            "invalid-preparation-permissions",
            "preparation must be private and owned",
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except FreezeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _error("invalid-preparation", f"cannot decode charter preparation: {error}")
    if not isinstance(value, dict):
        _error("invalid-preparation", "charter preparation must be an object")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _error("invalid-preparation", f"{label} must be a non-empty string")
    return value


def _string_array(
    value: object, label: str, *, minimum: int = 0
) -> list[str]:
    if not isinstance(value, list):
        _error("invalid-preparation", f"{label} must be an array")
    result = [_nonempty_string(item, f"{label}[]") for item in value]
    if len(result) < minimum:
        _error("invalid-preparation", f"{label} must contain at least {minimum} item")
    if len(result) != len(set(result)):
        _error("invalid-preparation", f"{label} must contain unique values")
    return result


def _validate_preparation(value: dict[str, object]) -> None:
    scaled = value.get("schema_version") == "test-charter-preparation.v2"
    expected = PREPARATION_FIELDS | {"execution_budget"} if scaled else PREPARATION_FIELDS
    if set(value) != expected:
        _error("invalid-preparation", "charter preparation fields are not exact")
    if value.get("schema_version") not in {PREPARATION_SCHEMA_VERSION, "test-charter-preparation.v2"}:
        _error("invalid-preparation", "unsupported charter preparation schema")
    workflow_id = value.get("workflow_id")
    if workflow_id is not None:
        _nonempty_string(workflow_id, "workflow_id")
    behavior = _nonempty_string(value.get("accepted_behavior"), "accepted_behavior")
    scope = value.get("scope")
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
        _error("invalid-preparation", "scope fields are not exact")
    if scope.get("accepted_behavior") != behavior:
        _error("invalid-preparation", "scope behavior must equal accepted behavior")
    _string_array(scope.get("inner_ring"), "scope.inner_ring", minimum=1)
    _string_array(scope.get("adjacent_ring"), "scope.adjacent_ring")
    _string_array(scope.get("broader_ring"), "scope.broader_ring")

    oracle_values = value.get("material_oracles")
    if not isinstance(oracle_values, list):
        _error("invalid-preparation", "material_oracles must be an array")
    oracle_ids: list[str] = []
    for index, oracle in enumerate(oracle_values):
        label = f"material_oracles[{index}]"
        if not isinstance(oracle, dict) or set(oracle) != ORACLE_FIELDS:
            _error("invalid-preparation", f"{label} fields are not exact")
        oracle_ids.append(_nonempty_string(oracle.get("oracle_id"), f"{label}.oracle_id"))
        _nonempty_string(oracle.get("behavior"), f"{label}.behavior")
        _nonempty_string(oracle.get("consumer_surface"), f"{label}.consumer_surface")
        _string_array(
            oracle.get("required_action_ids"),
            f"{label}.required_action_ids",
            minimum=1,
        )
    if len(oracle_ids) != len(set(oracle_ids)):
        _error("invalid-preparation", "material oracle IDs must be unique")

    exemption_ids = _string_array(
        value.get("exemption_grounding_artifact_ids"),
        "exemption_grounding_artifact_ids",
    )
    if bool(oracle_values) == bool(exemption_ids):
        _error(
            "invalid-preparation",
            "exactly one of material oracles or exemption grounding is required",
        )
    if scaled:
        try:
            execution_budget.validate(value["execution_budget"], oracle_values)
        except ValueError as error:
            _error("invalid-budget", str(error))


def _binding(repository: Path) -> tuple[str, str]:
    head = _git(repository, "rev-parse", "HEAD")
    branch = _git(repository, "branch", "--show-current")
    if HEAD_RE.fullmatch(head) is None or not branch:
        _error("invalid-revision", "repository must have a full HEAD and named branch")
    return head, branch


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


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _remove_owned(path: Path, expected: os.stat_result) -> bool:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return True
    if not stat.S_ISREG(observed.st_mode) or not _same_inode(observed, expected):
        return False
    path.unlink()
    return True


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _freeze(
    repository: Path,
    root: Path,
    preparation: dict[str, object],
) -> dict[str, str]:
    charter_path = root / "charter.json"
    ledger_path = root / "ledger.json"
    if os.path.lexists(charter_path) or os.path.lexists(ledger_path):
        _error("already-frozen", "charter.json or ledger.json already exists")

    head, branch = _binding(repository)
    charter = {
        "schema_version": CHARTER_SCHEMA_VERSION,
        "run_id": root.name,
        "workflow_id": preparation["workflow_id"],
        "repository": str(repository),
        "branch": branch,
        "head": head,
        "accepted_behavior": preparation["accepted_behavior"],
        "scope": preparation["scope"],
        "material_oracles": preparation["material_oracles"],
        "exemption_grounding_artifact_ids": preparation[
            "exemption_grounding_artifact_ids"
        ],
    }
    if preparation["schema_version"] == "test-charter-preparation.v2":
        charter["schema_version"] = "test-charter.v2"
        charter["execution_budget"] = preparation["execution_budget"]
    try:
        context = execution_budget.successor_context(charter, root)
        required = {action for oracle in charter["material_oracles"] for action in oracle["required_action_ids"]} | set(context["rerun_action_ids"])
        if int(context["actions_used"]) and int(context["actions_used"]) + len(required) + 1 > int(execution_budget.for_charter(charter)["semantic_actions_max"]):
            raise ValueError("remaining cumulative allowance cannot cover complete scope and final repetition")
        if (root / "bootstrap.json").exists() and execution_budget.remaining_seconds(charter, root) <= 0:
            raise ValueError("original cumulative deadline has expired")
    except (OSError, ValueError) as error:
        _error("invalid-successor", str(error))
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "run_id": root.name,
        "entries": [],
    }
    token = secrets.token_hex(12)
    charter_stage = root / f".charter-freeze-{token}"
    ledger_stage = root / f".ledger-freeze-{token}"
    staged: list[tuple[Path, os.stat_result]] = []
    published: list[tuple[Path, os.stat_result]] = []
    try:
        staged.append((charter_stage, _write_exclusive(charter_stage, _canonical_bytes(charter))))
        staged.append((ledger_stage, _write_exclusive(ledger_stage, _canonical_bytes(ledger))))
        if _binding(repository) != (head, branch):
            _error("revision-drift", "repository identity changed before charter freeze")
        for source, metadata, target in (
            (ledger_stage, staged[1][1], ledger_path),
            (charter_stage, staged[0][1], charter_path),
        ):
            try:
                os.link(source, target, follow_symlinks=False)
            except FileExistsError:
                _error("already-frozen", f"{target.name} already exists")
            observed = target.lstat()
            if not _same_inode(metadata, observed):
                _error("publication-race", f"{target.name} changed during publication")
            published.append((target, metadata))
        if _binding(repository) != (head, branch):
            _error("revision-drift", "repository identity changed during charter freeze")
    except Exception:
        cleanup_results = [
            _remove_owned(path, metadata)
            for path, metadata in reversed(published)
        ]
        cleanup_ok = all(cleanup_results)
        if not cleanup_ok:
            _error("cleanup-failed", "could not safely remove partial frozen output")
        raise
    finally:
        for path, metadata in staged:
            _remove_owned(path, metadata)

    return {
        "status": "FROZEN",
        "repository": str(repository),
        "branch": branch,
        "head": head,
        "run_id": root.name,
        "root": str(root),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        repository = _repository()
        root = _run_root(repository, parsed.root)
        preparation = _read_preparation(root / PREPARATION_FILENAME)
        _validate_preparation(preparation)
        print(json.dumps(_freeze(repository, root, preparation), sort_keys=True))
        return 0
    except FreezeError as error:
        print(f"charter_freeze_error={error.code} {error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as error:
        print(
            f"charter_freeze_error=runtime-error {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
