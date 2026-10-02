#!/usr/bin/env python3
"""Compile pre-recorded Test evidence into an atomic terminal handoff.

The public functions in this module deliberately implement only the
number-free I-JSON domain used by the Test evidence protocols.  They are not a
general-purpose JSON Canonicalization Scheme implementation.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import execution_budget
import record_final_action


SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
HEAD_RE = re.compile(r"[0-9a-f]{40,64}\Z")
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
STARTED_AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z\Z")
CHARTER_SCHEMA_VERSION = "test-charter.v1"
LEDGER_SCHEMA_VERSION = "test-action-ledger.v1"
DIRECT_LEDGER_SCHEMA_VERSION = "test-action-ledger.v2"
DRAFT_SCHEMA_VERSION = "test-evidence-draft.v1"
PREPARATION_SCHEMA_VERSION = "test-draft-preparation.v1"
COMPACT_PREPARATION_SCHEMA_VERSION = "test-draft-preparation.v2"
DIRECT_PREPARATION_SCHEMA_VERSION = "test-draft-preparation.v3"
DELTA_SCHEMA_VERSION = "test-draft-final-delta.v1"
EMPTY_DELTA_SCHEMA_VERSION = "test-draft-final-delta.v2"
TERMINAL_STATES = {"PASS", "FAIL", "BLOCKED", "EXEMPT"}
ACTION_ROLES = {"check", "journey"}
ACTION_STATES = {"pass", "fail", "blocked"}
RINGS = {"inner", "adjacent", "broader"}
RULE_STATUSES = {"active", "inactive", "unknown"}
RULE_OUTCOMES = {"satisfied", "unsatisfied", "not-applicable", "unknown"}
FINDING_KINDS = {
    "product-defect",
    "test-system-defect",
    "environment-blocker",
    "unresolved-cause",
}
FINDING_SEVERITIES = {"critical", "high", "medium", "low"}
TEARDOWN_STATES = {"pass", "fail", "not-required"}
ARTIFACT_KINDS = {
    "screenshot",
    "trace",
    "video",
    "log",
    "console",
    "network",
    "reproduction",
    "metadata",
}
RESERVED_ARTIFACT_IDS = {
    "test-charter",
    "test-action-ledger",
    "test-draft",
    "test-environment",
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
SCOPE_FIELDS = {"accepted_behavior", "inner_ring", "adjacent_ring", "broader_ring"}
ORACLE_FIELDS = {
    "oracle_id",
    "behavior",
    "consumer_surface",
    "required_action_ids",
}
LEDGER_FIELDS = {"schema_version", "run_id", "charter_digest", "entries"}
DIRECT_LEDGER_FIELDS = {"schema_version", "run_id", "entries"}
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
DRAFT_FIELDS = {
    "schema_version",
    "environment",
    "rule_applicability",
    "exploration",
    "findings",
    "artifacts",
    "teardown",
    "test_side_commits",
    "limitations",
    "started_at",
    "summary",
    "intended_result",
}
RULE_FIELDS = {"rule_id", "status", "outcome", "evidence"}
EXPLORATION_FIELDS = {
    "mission",
    "evidence_budget",
    "actions",
    "observations",
    "stop_condition",
    "teardown",
}
FINDING_FIELDS = {
    "kind",
    "severity",
    "ring",
    "journey",
    "expected",
    "actual",
    "reproduction",
    "violated_rule_ids",
    "artifacts",
}
ARTIFACT_FIELDS = {"artifact_id", "kind", "path"}
TEARDOWN_FIELDS = {"status", "actions", "artifact_ids"}
PREPARATION_FIELDS = {"schema_version", "draft_template"}
COMPACT_PREPARATION_FIELDS = {
    "schema_version",
    "draft_template",
    "rule_disposition",
    "active_rule_conditions",
    "rule_assessment_groups",
}
DIRECT_PREPARATION_FIELDS = {
    "schema_version",
    "draft_candidate",
    "rule_disposition",
    "active_rule_conditions",
    "rule_assessment_groups",
}
RULE_ASSESSMENT_GROUP_FIELDS = {
    "rule_ids",
    "outcome",
    "evidence_action_ids",
}
COMPACT_DRAFT_FIELDS = DRAFT_FIELDS - {"rule_applicability"}
DELTA_FIELDS = {"schema_version", "resolutions"}
RESOLUTION_FIELDS = {"resolution_id", "value"}
PENDING_KEY = "$test_pending"
PENDING_SPLICE_KEY = "$test_pending_splice"
PREPARATION_FILENAME = "draft-preparation.json"
DELTA_FILENAME = "draft-final-delta.json"
DRAFT_FILENAME = "draft.json"
NON_EVIDENCE_ARTIFACT_PATHS = {
    PREPARATION_FILENAME,
    DELTA_FILENAME,
}
REQUIRED_PENDING_PATHS = {
    "draft_template.exploration.observations[]",
    "draft_template.findings[]",
    "draft_template.artifacts[]",
    "draft_template.teardown.status",
    "draft_template.teardown.actions[]",
    "draft_template.teardown.artifact_ids[]",
    "draft_template.limitations[]",
    "draft_template.summary",
    "draft_template.intended_result",
}
OUTPUT_FILES = {
    "charter.json",
    "ledger.json",
    "draft.json",
    "environment.json",
    "bundle.json",
    "receipt.json",
}
MAX_SEMANTIC_ACTIONS = 8


@dataclass(frozen=True, order=True)
class Issue:
    """One stable validation failure suitable for deterministic reporting."""

    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


class FinalizerError(Exception):
    """A closed, deterministic finalizer validation failure."""

    def __init__(
        self,
        code: str,
        path: str,
        message: str,
        *,
        issues: Sequence[Issue] | None = None,
    ) -> None:
        issue_values = tuple(issues) if issues is not None else (
            Issue(code=code, path=path, message=message),
        )
        self.issues = tuple(sorted(set(issue_values)))
        super().__init__(", ".join(issue.message for issue in self.issues))


def _raise_json_error(code: str, path: str, message: str) -> NoReturn:
    raise FinalizerError(code, path, message)


def _reject_number(token: str) -> NoReturn:
    _raise_json_error(
        "number-not-allowed",
        "json",
        f"JSON numeric token {token!r} is outside the number-free protocol",
    )


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            _raise_json_error(
                "duplicate-key",
                key,
                f"decoded JSON object key {key!r} occurs more than once",
            )
        value[key] = child
    return value


def _is_noncharacter(codepoint: int) -> bool:
    return 0xFDD0 <= codepoint <= 0xFDEF or (codepoint & 0xFFFF) in {
        0xFFFE,
        0xFFFF,
    }


def _validate_string(value: str, path: str) -> None:
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF or _is_noncharacter(codepoint):
            _raise_json_error(
                "invalid-unicode",
                path,
                f"U+{codepoint:04X} is not allowed by the I-JSON protocol",
            )


def _validate_value(value: object, path: str = "$") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        _raise_json_error(
            "number-not-allowed",
            path,
            "numbers are outside the number-free evidence protocol",
        )
    if isinstance(value, str):
        _validate_string(value, path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_value(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _raise_json_error(
                    "invalid-json-type",
                    path,
                    "JSON object keys must be strings",
                )
            _validate_string(key, f"{path}.<key>")
            _validate_value(child, f"{path}.{key}")
        return
    _raise_json_error(
        "invalid-json-type",
        path,
        f"unsupported JSON value type {type(value).__name__}",
    )


def _decode_closed_json(raw: bytes, path: str) -> object:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FinalizerError(
            "invalid-unicode", path, "JSON input is not valid UTF-8"
        ) from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_closed_object,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except FinalizerError:
        raise
    except json.JSONDecodeError as error:
        raise FinalizerError(
            "invalid-json", path, f"invalid JSON at line {error.lineno} column {error.colno}",
        ) from error
    _validate_value(value)
    return value


def load_closed_json(path: Path) -> object:
    """Load one number-free I-JSON value without losing duplicate keys."""

    raw = _read_regular_nofollow(path, code="unreadable-json")
    return _decode_closed_json(raw, str(path))


def _load_closed_json_at(root_descriptor: int, relative: Path) -> object:
    raw = _read_regular_at(root_descriptor, relative, code="unreadable-json")
    return _decode_closed_json(raw, str(relative))


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_nofollow(path: Path, *, directory: bool) -> tuple[Path, int]:
    """Open an absolute path component-by-component without following links."""

    absolute = _absolute_without_resolution(path)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(absolute.anchor, directory_flags)
    except (OSError, ValueError) as error:
        raise FinalizerError(
            "unreadable-path", str(absolute), f"cannot open path anchor: {error}"
        ) from error
    try:
        components = absolute.parts[1:]
        if not components:
            raise FinalizerError(
                "invalid-path", str(absolute), "filesystem root is not an evidence path"
            )
        for index, component in enumerate(components):
            final = index == len(components) - 1
            flags = (
                directory_flags
                if not final or directory
                else os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            child = -1
            try:
                child = os.open(component, flags, dir_fd=descriptor)
                lexical = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False
                )
                observed = os.fstat(child)
            except (OSError, ValueError) as error:
                if child >= 0:
                    os.close(child)
                raise FinalizerError(
                    "symlinked-path",
                    str(absolute),
                    f"cannot open evidence path without following links: {error}",
                ) from error
            if not _same_inode(lexical, observed):
                os.close(child)
                raise FinalizerError(
                    "path-race",
                    str(absolute),
                    "evidence path component changed while it was opened",
                )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(metadata.st_mode):
            raise FinalizerError(
                "invalid-path-type",
                str(absolute),
                "evidence path has the wrong file type",
            )
        return absolute, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_beneath(
    root_descriptor: int, relative: Path, *, directory: bool
) -> int:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise FinalizerError(
            "invalid-path", str(relative), "path must remain beneath the run root"
        )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.dup(root_descriptor)
    try:
        for index, component in enumerate(relative.parts):
            final = index == len(relative.parts) - 1
            flags = (
                directory_flags
                if not final or directory
                else os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            child = -1
            try:
                child = os.open(component, flags, dir_fd=descriptor)
                lexical = os.stat(
                    component, dir_fd=descriptor, follow_symlinks=False
                )
                observed = os.fstat(child)
            except (OSError, ValueError) as error:
                if child >= 0:
                    os.close(child)
                raise FinalizerError(
                    "symlinked-path",
                    str(relative),
                    f"cannot open run-root path without following links: {error}",
                ) from error
            if not _same_inode(lexical, observed):
                os.close(child)
                raise FinalizerError(
                    "path-race",
                    str(relative),
                    "run-root path component changed while it was opened",
                )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(metadata.st_mode):
            raise FinalizerError(
                "invalid-path-type", str(relative), "run-root path has the wrong type"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_nofollow(path: Path, *, code: str) -> bytes:
    absolute, descriptor = _open_nofollow(path, directory=False)
    try:
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(descriptor, 1024 * 1024)
            except OSError as error:
                raise FinalizerError(
                    code, str(absolute), f"cannot read evidence file: {error}"
                ) from error
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        raw = b"".join(chunks)
        if (
            not _same_inode(before, after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or len(raw) != after.st_size
        ):
            raise FinalizerError(
                "path-race", str(absolute), "evidence file changed while it was read"
            )
        return raw
    finally:
        os.close(descriptor)


def _read_regular_snapshot_at(
    root_descriptor: int, relative: Path, *, code: str
) -> tuple[bytes, os.stat_result]:
    descriptor = _open_beneath(root_descriptor, relative, directory=False)
    try:
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(descriptor, 1024 * 1024)
            except OSError as error:
                raise FinalizerError(
                    code, str(relative), f"cannot read evidence file: {error}"
                ) from error
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        raw = b"".join(chunks)
        if (
            not _same_inode(before, after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or len(raw) != after.st_size
        ):
            raise FinalizerError(
                "path-race", str(relative), "evidence file changed while it was read"
            )
        return raw, after
    finally:
        os.close(descriptor)


def _read_regular_at(root_descriptor: int, relative: Path, *, code: str) -> bytes:
    raw, _ = _read_regular_snapshot_at(
        root_descriptor, relative, code=code
    )
    return raw


def _reject_symlink_components(path: Path) -> Path:
    absolute = _absolute_without_resolution(path)
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        except (OSError, ValueError) as error:
            raise FinalizerError(
                "unreadable-path", str(current), f"cannot inspect path: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise FinalizerError(
                "symlinked-path",
                str(current),
                "canonical evidence paths may not contain symbolic links",
            )
    return absolute


def _open_run_root(path: Path) -> tuple[Path, int]:
    absolute, descriptor = _open_nofollow(path, directory=True)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.geteuid():
            raise FinalizerError(
                "insecure-root",
                str(absolute),
                "canonical run root must be owned by the current user",
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise FinalizerError(
                "insecure-root",
                str(absolute),
                "canonical run root must be private (0700 or stricter)",
            )
        return absolute, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_run_root(path: Path) -> Path:
    absolute, descriptor = _open_run_root(path)
    os.close(descriptor)
    return absolute


def _revalidate_run_root(absolute: Path, descriptor: int) -> None:
    reopened_path, reopened = _open_run_root(absolute)
    try:
        if reopened_path != absolute or not _same_inode(
            os.fstat(descriptor), os.fstat(reopened)
        ):
            raise FinalizerError(
                "path-race",
                str(absolute),
                "canonical run root changed during finalization",
            )
    finally:
        os.close(reopened)


_SHORT_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


def _quote_string(value: str) -> str:
    _validate_string(value, "$")
    parts = ['"']
    for character in value:
        escaped = _SHORT_ESCAPES.get(character)
        if escaped is not None:
            parts.append(escaped)
        elif ord(character) <= 0x1F:
            parts.append(f"\\u{ord(character):04x}")
        else:
            parts.append(character)
    parts.append('"')
    return "".join(parts)


def canonical_json(value: object) -> bytes:
    """Return RFC 8785 bytes for the protocol's number-free I-JSON subset."""

    _validate_value(value)

    def serialize(child: object) -> str:
        if child is None:
            return "null"
        if child is True:
            return "true"
        if child is False:
            return "false"
        if isinstance(child, str):
            return _quote_string(child)
        if isinstance(child, list):
            return "[" + ",".join(serialize(item) for item in child) + "]"
        if isinstance(child, dict):
            names = sorted(child, key=lambda name: name.encode("utf-16-be"))
            return "{" + ",".join(
                f"{_quote_string(name)}:{serialize(child[name])}" for name in names
            ) + "}"
        raise AssertionError("value passed validation but cannot be serialized")

    return serialize(value).encode("utf-8")


def _sha256_value(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _resource_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate resource key {key!r}")
        value[key] = child
    return value


def _load_resource(name: str) -> dict[str, object]:
    path = Path(__file__).resolve().parent.parent / "references" / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_resource_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise FinalizerError(
            "invalid-resource", str(path), f"cannot load packaged resource: {error}"
        ) from error
    if not isinstance(value, dict):
        raise FinalizerError(
            "invalid-resource", str(path), "packaged resource must be a JSON object"
        )
    return value


def _add(issues: list[Issue], code: str, path: str, message: str) -> None:
    issues.append(Issue(code=code, path=path, message=message))


def _closed_object_fields(
    value: object, expected: set[str], path: str, issues: list[Issue]
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        _add(issues, "invalid-type", path, "must be an object")
        return None
    for key in sorted(set(value) - expected):
        _add(issues, "unexpected-key", f"{path}.{key}", "key is not allowed")
    for key in sorted(expected - set(value)):
        _add(issues, "missing-key", f"{path}.{key}", "required key is missing")
    return value


def _nonempty_string(
    value: object, path: str, issues: list[Issue]
) -> str | None:
    if not isinstance(value, str) or not value:
        _add(issues, "invalid-type", path, "must be a non-empty string")
        return None
    return value


def _string_array(
    value: object,
    path: str,
    issues: list[Issue],
    *,
    minimum: int = 0,
    unique: bool = False,
) -> list[str] | None:
    if not isinstance(value, list):
        _add(issues, "invalid-type", path, "must be an array")
        return None
    if len(value) < minimum:
        _add(issues, "missing-value", path, f"must contain at least {minimum} item(s)")
    strings: list[str] = []
    for index, child in enumerate(value):
        parsed = _nonempty_string(child, f"{path}[{index}]", issues)
        if parsed is not None:
            strings.append(parsed)
    if unique and len(strings) != len(set(strings)):
        _add(issues, "duplicate-reference", path, "references must be unique")
    return strings


def _validate_scope(value: object, path: str, issues: list[Issue]) -> dict[str, object] | None:
    scope = _closed_object_fields(value, SCOPE_FIELDS, path, issues)
    if scope is None:
        return None
    _nonempty_string(scope.get("accepted_behavior"), f"{path}.accepted_behavior", issues)
    _string_array(scope.get("inner_ring"), f"{path}.inner_ring", issues, minimum=1, unique=True)
    _string_array(scope.get("adjacent_ring"), f"{path}.adjacent_ring", issues, unique=True)
    _string_array(scope.get("broader_ring"), f"{path}.broader_ring", issues, unique=True)
    return scope


def _validate_input_location(path: Path, expected: Path) -> None:
    absolute = _absolute_without_resolution(path)
    if absolute != expected:
        raise FinalizerError(
            "invalid-input-path",
            str(absolute),
            f"input must be exactly {expected}",
        )
    _, descriptor = _open_nofollow(absolute, directory=False)
    os.close(descriptor)


def _validate_artifact_path(
    root: Path,
    relative: object,
    *,
    root_descriptor: int | None = None,
    must_exist: bool = True,
) -> tuple[Path | None, Issue | None]:
    if not isinstance(relative, str) or not relative:
        return None, Issue("invalid-artifact-path", "draft.artifacts", "artifact path must be a non-empty string")
    candidate_relative = Path(relative)
    if (
        not candidate_relative.parts
        or "\x00" in relative
        or candidate_relative.is_absolute()
        or ".." in candidate_relative.parts
    ):
        return None, Issue("invalid-artifact-path", relative, "artifact path must remain beneath the canonical root")
    candidate = _absolute_without_resolution(root / candidate_relative)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, Issue("invalid-artifact-path", relative, "artifact path escapes the canonical root")
    if candidate_relative.parts and (
        candidate_relative.parts[0] == "terminal"
        or candidate_relative.parts[0].startswith(".terminal-")
    ):
        return None, Issue("invalid-artifact-path", relative, "raw artifact cannot occupy terminal staging")
    if candidate_relative.as_posix() in NON_EVIDENCE_ARTIFACT_PATHS:
        return None, Issue(
            "invalid-artifact-path",
            relative,
            "draft composition inputs are not raw evidence artifacts",
        )
    if must_exist:
        try:
            descriptor = (
                _open_beneath(root_descriptor, candidate_relative, directory=False)
                if root_descriptor is not None
                else _open_nofollow(candidate, directory=False)[1]
            )
        except FinalizerError:
            return None, Issue(
                "invalid-artifact-path",
                relative,
                "artifact path is absent, invalid, or contains a symbolic link",
            )
        else:
            os.close(descriptor)
    return candidate, None


def validate_inputs(
    root: Path,
    charter_value: object,
    ledger_value: object,
    draft_value: object,
    *,
    catalog: dict[str, object] | None = None,
    contract: dict[str, object] | None = None,
    root_descriptor: int | None = None,
    artifacts_must_exist: bool = True,
) -> list[Issue]:
    """Validate supplied facts without selecting scope, findings, or a result."""

    issues: list[Issue] = []
    catalog = catalog if catalog is not None else _load_resource("quality-rules.json")
    contract = contract if contract is not None else _load_resource("evidence-contract.json")
    contract_finding_kinds = contract.get("finding_kinds")
    if (
        not isinstance(contract_finding_kinds, list)
        or len(contract_finding_kinds) != len(FINDING_KINDS)
        or not all(isinstance(kind, str) for kind in contract_finding_kinds)
        or set(contract_finding_kinds) != FINDING_KINDS
    ):
        _add(
            issues,
            "invalid-resource",
            "contract.finding_kinds",
            "packaged finding kinds differ from the fixed authoring protocol",
        )
    charter_fields = CHARTER_FIELDS | {"execution_budget"} if isinstance(charter_value, dict) and charter_value.get("schema_version") == "test-charter.v2" else CHARTER_FIELDS
    charter = _closed_object_fields(charter_value, charter_fields, "charter", issues)
    ledger_fields = (
        DIRECT_LEDGER_FIELDS
        if isinstance(ledger_value, dict)
        and ledger_value.get("schema_version") == DIRECT_LEDGER_SCHEMA_VERSION
        else LEDGER_FIELDS
    )
    ledger = _closed_object_fields(ledger_value, ledger_fields, "ledger", issues)
    draft = _closed_object_fields(draft_value, DRAFT_FIELDS, "draft", issues)
    if charter is None or ledger is None or draft is None:
        return sorted(set(issues))

    if charter.get("schema_version") not in {CHARTER_SCHEMA_VERSION, "test-charter.v2"}:
        _add(issues, "unknown-state", "charter.schema_version", "unsupported charter schema version")
    action_limit = MAX_SEMANTIC_ACTIONS
    try:
        budget = execution_budget.for_charter(charter)
        action_limit = int(budget["semantic_actions_max"])
    except ValueError as error:
        _add(issues, "invalid-budget", "charter.execution_budget", str(error))
    run_id = _nonempty_string(charter.get("run_id"), "charter.run_id", issues)
    if run_id is not None and RUN_ID_RE.fullmatch(run_id) is None:
        _add(issues, "invalid-run-id", "charter.run_id", "run ID contains unsafe characters")
    workflow_id = charter.get("workflow_id")
    if workflow_id is not None:
        _nonempty_string(workflow_id, "charter.workflow_id", issues)
    repository = _nonempty_string(charter.get("repository"), "charter.repository", issues)
    branch = _nonempty_string(charter.get("branch"), "charter.branch", issues)
    del branch
    head = _nonempty_string(charter.get("head"), "charter.head", issues)
    if head is not None and HEAD_RE.fullmatch(head) is None:
        _add(issues, "wrong-head", "charter.head", "head must be 40-64 lowercase hexadecimal characters")
    behavior = _nonempty_string(charter.get("accepted_behavior"), "charter.accepted_behavior", issues)
    scope = _validate_scope(charter.get("scope"), "charter.scope", issues)
    if behavior is not None and scope is not None and scope.get("accepted_behavior") != behavior:
        _add(issues, "scope-mismatch", "charter.scope.accepted_behavior", "scope behavior must equal charter behavior")
    if run_id is not None and root.name != run_id:
        _add(issues, "run-id-mismatch", "charter.run_id", "run ID must equal canonical root basename")
    if repository is not None:
        repository_path = Path(repository)
        if not repository_path.is_absolute():
            _add(issues, "invalid-repository", "charter.repository", "repository must be absolute")
        else:
            expected_root = _absolute_without_resolution(repository_path / ".test-evidence" / (run_id or root.name))
            if expected_root != root:
                _add(issues, "invalid-root", "charter.repository", "root is not the repository's canonical evidence path")

    oracle_values = charter.get("material_oracles")
    oracles: list[dict[str, object]] = []
    oracle_ids: list[str] = []
    if not isinstance(oracle_values, list):
        _add(issues, "invalid-type", "charter.material_oracles", "must be an array")
    else:
        for index, value in enumerate(oracle_values):
            path = f"charter.material_oracles[{index}]"
            oracle = _closed_object_fields(value, ORACLE_FIELDS, path, issues)
            if oracle is None:
                continue
            oracle_id = _nonempty_string(oracle.get("oracle_id"), f"{path}.oracle_id", issues)
            _nonempty_string(oracle.get("behavior"), f"{path}.behavior", issues)
            _nonempty_string(oracle.get("consumer_surface"), f"{path}.consumer_surface", issues)
            _string_array(oracle.get("required_action_ids"), f"{path}.required_action_ids", issues, minimum=1, unique=True)
            oracles.append(oracle)
            if oracle_id is not None:
                oracle_ids.append(oracle_id)
        if len(oracle_ids) != len(set(oracle_ids)):
            _add(issues, "duplicate-id", "charter.material_oracles", "oracle IDs must be unique")
    exemption_ids = _string_array(
        charter.get("exemption_grounding_artifact_ids"),
        "charter.exemption_grounding_artifact_ids",
        issues,
        unique=True,
    ) or []

    ledger_schema = ledger.get("schema_version")
    if ledger_schema not in {LEDGER_SCHEMA_VERSION, DIRECT_LEDGER_SCHEMA_VERSION}:
        _add(issues, "unknown-state", "ledger.schema_version", "unsupported ledger schema version")
    if ledger.get("run_id") != run_id:
        _add(issues, "run-id-mismatch", "ledger.run_id", "ledger run ID must equal charter run ID")
    if ledger_schema == LEDGER_SCHEMA_VERSION:
        digest = ledger.get("charter_digest")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None or digest != _sha256_value(charter):
            _add(issues, "stale-charter-digest", "ledger.charter_digest", "ledger is not bound to the frozen charter")
    entry_values = ledger.get("entries")
    entries: list[dict[str, object]] = []
    action_ids: list[str] = []
    if not isinstance(entry_values, list):
        _add(issues, "invalid-type", "ledger.entries", "must be an array")
    else:
        for index, value in enumerate(entry_values):
            path = f"ledger.entries[{index}]"
            entry = _closed_object_fields(value, ENTRY_FIELDS, path, issues)
            if entry is None:
                continue
            action_id = _nonempty_string(entry.get("action_id"), f"{path}.action_id", issues)
            role = entry.get("role")
            if not isinstance(role, str) or role not in ACTION_ROLES:
                _add(issues, "unknown-state", f"{path}.role", "role must be check or journey")
            ring = entry.get("ring")
            if not isinstance(ring, str) or ring not in RINGS:
                _add(issues, "unknown-state", f"{path}.ring", "unknown scope ring")
            entry_head = entry.get("head")
            if entry_head != head or not isinstance(entry_head, str) or HEAD_RE.fullmatch(entry_head) is None:
                _add(issues, "wrong-head", f"{path}.head", "action head must equal charter head")
            _nonempty_string(entry.get("action"), f"{path}.action", issues)
            path_values = _string_array(entry.get("path"), f"{path}.path", issues) or []
            if role == "journey" and not path_values:
                _add(issues, "invalid-action", f"{path}.path", "journey path must be non-empty")
            _nonempty_string(entry.get("expected"), f"{path}.expected", issues)
            _nonempty_string(entry.get("actual"), f"{path}.actual", issues)
            status_value = entry.get("status")
            if not isinstance(status_value, str) or status_value not in ACTION_STATES:
                _add(issues, "unknown-state", f"{path}.status", "unknown action status")
            _string_array(entry.get("oracle_ids"), f"{path}.oracle_ids", issues, unique=True)
            _string_array(entry.get("artifact_ids"), f"{path}.artifact_ids", issues, minimum=1, unique=True)
            entries.append(entry)
            if action_id is not None:
                action_ids.append(action_id)
        if len(action_ids) != len(set(action_ids)):
            _add(issues, "duplicate-id", "ledger.entries", "action IDs must be unique")
        if len(entries) > action_limit:
            _add(
                issues,
                "action-budget-exceeded",
                "ledger.entries",
                f"at most {action_limit} semantic actions are allowed",
            )
        if "budget" in locals():
            try:
                execution_budget.validate_actions(budget, action_ids, root=root, charter=charter)
                context = execution_budget.successor_context(charter, root)
                missing = set(context["rerun_action_ids"]) - set(action_ids)
                if missing:
                    raise ValueError("successor has not rerun the complete previously selected action scope")
            except (OSError, ValueError) as error:
                _add(issues, "invalid-budget", "ledger.entries", str(error))

    if draft.get("schema_version") != DRAFT_SCHEMA_VERSION:
        _add(issues, "unknown-state", "draft.schema_version", "unsupported draft schema version")
    environment = draft.get("environment")
    if not isinstance(environment, dict) or not environment:
        _add(issues, "invalid-type", "draft.environment", "environment must be a non-empty object")
    rule_values = draft.get("rule_applicability")
    rules: list[dict[str, object]] = []
    rule_ids: list[str] = []
    if not isinstance(rule_values, list):
        _add(issues, "invalid-type", "draft.rule_applicability", "must be an array")
    else:
        for index, value in enumerate(rule_values):
            path = f"draft.rule_applicability[{index}]"
            rule = _closed_object_fields(value, RULE_FIELDS, path, issues)
            if rule is None:
                continue
            rule_id = _nonempty_string(rule.get("rule_id"), f"{path}.rule_id", issues)
            status_value = rule.get("status")
            outcome = rule.get("outcome")
            if not isinstance(status_value, str) or status_value not in RULE_STATUSES:
                _add(issues, "unknown-state", f"{path}.status", "unknown rule applicability")
            if not isinstance(outcome, str) or outcome not in RULE_OUTCOMES:
                _add(issues, "unknown-state", f"{path}.outcome", "unknown rule outcome")
            allowed_outcomes = {
                "active": {"satisfied", "unsatisfied"},
                "inactive": {"not-applicable"},
                "unknown": {"unknown"},
            }
            if (
                isinstance(status_value, str)
                and status_value in allowed_outcomes
                and (
                    not isinstance(outcome, str)
                    or outcome not in allowed_outcomes[status_value]
                )
            ):
                _add(issues, "unsupported-rule-outcome", f"{path}.outcome", "rule status and outcome contradict")
            _string_array(rule.get("evidence"), f"{path}.evidence", issues, minimum=1)
            rules.append(rule)
            if rule_id is not None:
                rule_ids.append(rule_id)
        if len(rule_ids) != len(set(rule_ids)):
            _add(issues, "duplicate-id", "draft.rule_applicability", "rule IDs must be unique")

    exploration = _closed_object_fields(draft.get("exploration"), EXPLORATION_FIELDS, "draft.exploration", issues)
    if exploration is not None:
        for key in ("mission", "evidence_budget", "stop_condition"):
            _nonempty_string(exploration.get(key), f"draft.exploration.{key}", issues)
        for key in ("actions", "observations", "teardown"):
            _string_array(exploration.get(key), f"draft.exploration.{key}", issues)

    artifact_values = draft.get("artifacts")
    artifacts: list[dict[str, object]] = []
    artifact_ids: list[str] = []
    if not isinstance(artifact_values, list):
        _add(issues, "invalid-type", "draft.artifacts", "must be an array")
    else:
        for index, value in enumerate(artifact_values):
            path = f"draft.artifacts[{index}]"
            artifact = _closed_object_fields(value, ARTIFACT_FIELDS, path, issues)
            if artifact is None:
                continue
            artifact_id = _nonempty_string(artifact.get("artifact_id"), f"{path}.artifact_id", issues)
            if artifact_id in RESERVED_ARTIFACT_IDS:
                _add(issues, "duplicate-id", f"{path}.artifact_id", "artifact ID is reserved")
            artifact_kind = artifact.get("kind")
            if not isinstance(artifact_kind, str) or artifact_kind not in ARTIFACT_KINDS:
                _add(issues, "unknown-state", f"{path}.kind", "unknown artifact kind")
            _, artifact_issue = _validate_artifact_path(
                root,
                artifact.get("path"),
                root_descriptor=root_descriptor,
                must_exist=artifacts_must_exist,
            )
            if artifact_issue is not None:
                _add(issues, artifact_issue.code, f"{path}.path", artifact_issue.message)
            artifacts.append(artifact)
            if artifact_id is not None:
                artifact_ids.append(artifact_id)
        if len(artifact_ids) != len(set(artifact_ids)):
            _add(issues, "duplicate-id", "draft.artifacts", "artifact IDs must be unique")

    finding_values = draft.get("findings")
    findings: list[dict[str, object]] = []
    if not isinstance(finding_values, list):
        _add(issues, "invalid-type", "draft.findings", "must be an array")
    else:
        for index, value in enumerate(finding_values):
            path = f"draft.findings[{index}]"
            finding = _closed_object_fields(value, FINDING_FIELDS, path, issues)
            if finding is None:
                continue
            finding_kind = finding.get("kind")
            if not isinstance(finding_kind, str) or finding_kind not in FINDING_KINDS:
                _add(issues, "unknown-state", f"{path}.kind", "unknown finding kind")
            severity = finding.get("severity")
            if not isinstance(severity, str) or severity not in FINDING_SEVERITIES:
                _add(issues, "unknown-state", f"{path}.severity", "unknown finding severity")
            ring = finding.get("ring")
            if not isinstance(ring, str) or ring not in RINGS:
                _add(issues, "unknown-state", f"{path}.ring", "unknown finding ring")
            for key in ("journey", "expected", "actual"):
                _nonempty_string(finding.get(key), f"{path}.{key}", issues)
            _string_array(finding.get("reproduction"), f"{path}.reproduction", issues, minimum=1)
            _string_array(finding.get("violated_rule_ids"), f"{path}.violated_rule_ids", issues, unique=True)
            _string_array(finding.get("artifacts"), f"{path}.artifacts", issues, unique=True)
            findings.append(finding)

    teardown = _closed_object_fields(draft.get("teardown"), TEARDOWN_FIELDS, "draft.teardown", issues)
    if teardown is not None:
        teardown_status_value = teardown.get("status")
        if (
            not isinstance(teardown_status_value, str)
            or teardown_status_value not in TEARDOWN_STATES
        ):
            _add(issues, "unknown-state", "draft.teardown.status", "unknown teardown state")
        _string_array(teardown.get("actions"), "draft.teardown.actions", issues)
        _string_array(teardown.get("artifact_ids"), "draft.teardown.artifact_ids", issues, unique=True)
    commits = _string_array(draft.get("test_side_commits"), "draft.test_side_commits", issues, unique=True) or []
    for index, commit in enumerate(commits):
        if HEAD_RE.fullmatch(commit) is None:
            _add(issues, "wrong-head", f"draft.test_side_commits[{index}]", "commit must be lowercase hexadecimal")
    _string_array(draft.get("limitations"), "draft.limitations", issues)
    started_at = _nonempty_string(draft.get("started_at"), "draft.started_at", issues)
    if started_at is not None and STARTED_AT_RE.fullmatch(started_at) is None:
        _add(issues, "invalid-timestamp", "draft.started_at", "timestamp must be UTC RFC3339 ending in Z")
    elif started_at is not None:
        try:
            parsed_started_at = datetime.fromisoformat(started_at[:-1] + "+00:00")
        except ValueError:
            _add(issues, "invalid-timestamp", "draft.started_at", "timestamp is not a real calendar instant")
        else:
            if parsed_started_at.utcoffset() != timezone.utc.utcoffset(parsed_started_at):
                _add(issues, "invalid-timestamp", "draft.started_at", "timestamp must identify UTC")
    _nonempty_string(draft.get("summary"), "draft.summary", issues)
    result = draft.get("intended_result")
    if not isinstance(result, str) or result not in TERMINAL_STATES:
        _add(issues, "unknown-state", "draft.intended_result", "unknown terminal state")

    known_oracles = set(oracle_ids)
    known_artifacts = set(artifact_ids)
    if result == "PASS" and artifacts_must_exist:
        artifact_by_id = {item.get("artifact_id"): item for item in artifacts}
        charter_raw = _read_regular_at(root_descriptor, Path("charter.json"), code="unreadable-json") if root_descriptor is not None else _read_regular_nofollow(root / "charter.json", code="unreadable-json")
        for index, entry in enumerate(entries):
            verified = 0
            referenced = [artifact_by_id.get(key) for key in entry.get("artifact_ids", []) if isinstance(key, str)] if isinstance(entry.get("artifact_ids"), list) else []
            for artifact in referenced:
                if not isinstance(artifact, dict):
                    continue
                path, issue = _validate_artifact_path(root, artifact.get("path"), root_descriptor=root_descriptor)
                if issue is not None or path is None:
                    continue
                try:
                    raw = _read_regular_at(root_descriptor, path.relative_to(root), code="unreadable-json") if root_descriptor is not None else _read_regular_nofollow(path, code="unreadable-json")
                    record = json.loads(raw.decode("utf-8"), object_pairs_hook=_closed_object, parse_int=_reject_number, parse_float=_reject_number, parse_constant=_reject_number)
                except (FinalizerError, ValueError, UnicodeDecodeError, OSError):
                    continue
                if not isinstance(record, dict) or record.get("schema_version") != "test-execution-record.v1":
                    continue
                observation_path = record.get("observation_path")
                observations = [item for item in referenced if isinstance(item, dict) and item.get("path") == observation_path]
                spec = record.get("execution_spec")
                if len(observations) != 1 or not isinstance(spec, dict) or spec.get("metadata_path") != artifact.get("path"):
                    continue
                observation, issue = _validate_artifact_path(root, observation_path, root_descriptor=root_descriptor)
                if issue is None and observation is not None:
                    raw = _read_regular_at(root_descriptor, observation.relative_to(root), code="unreadable-artifact") if root_descriptor is not None else _read_regular_nofollow(observation, code="unreadable-artifact")
                    if record_final_action.verify_execution_record(record, entry, charter_raw, raw, observation_path):
                        verified += 1
            if verified != 1:
                _add(issues, "unrecorded-execution", f"ledger.entries[{index}]", "PASS requires one recorder receipt with matching raw bytes, predicate, frozen charter and exact action bindings")
    for index, entry in enumerate(entries):
        for oracle_id in entry.get("oracle_ids", []) if isinstance(entry.get("oracle_ids"), list) else []:
            if not isinstance(oracle_id, str):
                continue
            if oracle_id not in known_oracles:
                _add(issues, "unknown-reference", f"ledger.entries[{index}].oracle_ids", "action references unknown oracle")
        for artifact_id in entry.get("artifact_ids", []) if isinstance(entry.get("artifact_ids"), list) else []:
            if not isinstance(artifact_id, str):
                continue
            if artifact_id not in known_artifacts:
                _add(issues, "unknown-reference", f"ledger.entries[{index}].artifact_ids", "action references unknown artifact")
    for artifact_id in exemption_ids:
        if artifact_id not in known_artifacts:
            _add(issues, "unknown-reference", "charter.exemption_grounding_artifact_ids", "exemption references unknown artifact")
    for index, finding in enumerate(findings):
        for rule_id in finding.get("violated_rule_ids", []) if isinstance(finding.get("violated_rule_ids"), list) else []:
            if not isinstance(rule_id, str):
                continue
            if rule_id not in set(rule_ids):
                _add(issues, "unknown-reference", f"draft.findings[{index}].violated_rule_ids", "finding references unknown rule")
        for artifact_id in finding.get("artifacts", []) if isinstance(finding.get("artifacts"), list) else []:
            if not isinstance(artifact_id, str):
                continue
            if artifact_id not in known_artifacts:
                _add(issues, "unknown-reference", f"draft.findings[{index}].artifacts", "finding references unknown artifact")
    if teardown is not None:
        for artifact_id in teardown.get("artifact_ids", []) if isinstance(teardown.get("artifact_ids"), list) else []:
            if not isinstance(artifact_id, str):
                continue
            if artifact_id not in known_artifacts:
                _add(issues, "unknown-reference", "draft.teardown.artifact_ids", "teardown references unknown artifact")

    catalog_rules = catalog.get("rules")
    expected_rule_ids = [
        rule.get("id") for rule in catalog_rules if isinstance(rule, dict)
    ] if isinstance(catalog_rules, list) else []
    if result != "EXEMPT" and rule_ids != expected_rule_ids:
        _add(issues, "incomplete-rule-set", "draft.rule_applicability", "rules must match the complete catalog in order")

    entry_by_id = {
        entry.get("action_id"): entry
        for entry in entries
        if isinstance(entry.get("action_id"), str)
    }
    rule_by_id = {
        rule.get("rule_id"): rule
        for rule in rules
        if isinstance(rule.get("rule_id"), str)
    }
    for index, rule in enumerate(rules):
        evidence = rule.get("evidence")
        if not isinstance(evidence, list):
            continue
        for value in evidence:
            if not isinstance(value, str) or not value.startswith("ledger:"):
                continue
            if value.removeprefix("ledger:") not in entry_by_id:
                _add(
                    issues,
                    "unknown-reference",
                    f"draft.rule_applicability[{index}].evidence",
                    "rule evidence references an unknown ledger action",
                )
    violated_rule_ids: set[str] = set()
    for finding in findings:
        values = finding.get("violated_rule_ids")
        if isinstance(values, list):
            violated_rule_ids.update(value for value in values if isinstance(value, str))
    unsatisfied_rule_ids = {
        rule_id
        for rule_id, rule in rule_by_id.items()
        if rule.get("status") == "active" and rule.get("outcome") == "unsatisfied"
    }
    for rule_id in sorted(violated_rule_ids | unsatisfied_rule_ids):
        rule = rule_by_id.get(rule_id)
        if rule is None or rule.get("status") != "active" or rule.get("outcome") != "unsatisfied":
            _add(
                issues,
                "unsupported-rule-outcome",
                "draft.rule_applicability",
                f"rule {rule_id!r} and finding evidence do not agree",
            )
    for rule_id in sorted(unsatisfied_rule_ids - violated_rule_ids):
        _add(
            issues,
            "unsupported-rule-outcome",
            "draft.rule_applicability",
            f"unsatisfied rule {rule_id!r} has no matching finding",
        )

    oracle_statuses: dict[str, set[str]] = {oracle_id: set() for oracle_id in oracle_ids}
    required_entries: list[dict[str, object]] = []
    for oracle_index, oracle in enumerate(oracles):
        oracle_id = oracle.get("oracle_id")
        required = oracle.get("required_action_ids")
        if not isinstance(oracle_id, str) or not isinstance(required, list):
            continue
        for action_id in required:
            if not isinstance(action_id, str):
                continue
            entry = entry_by_id.get(action_id)
            if entry is None:
                continue
            required_entries.append(entry)
            entry_oracles = entry.get("oracle_ids")
            if not isinstance(entry_oracles, list) or oracle_id not in entry_oracles:
                _add(
                    issues,
                    "unknown-reference",
                    f"charter.material_oracles[{oracle_index}].required_action_ids",
                    f"required action {action_id!r} is not bound back to oracle {oracle_id!r}",
                )
        for entry in entries:
            entry_oracles = entry.get("oracle_ids")
            status_value = entry.get("status")
            if isinstance(entry_oracles, list) and oracle_id in entry_oracles and isinstance(status_value, str):
                oracle_statuses[oracle_id].add(status_value)
    contradictory = False
    for oracle_id, statuses in oracle_statuses.items():
        if "fail" in statuses and "pass" in statuses:
            contradictory = True

    has_required_fail = any(
        entry.get("status") == "fail" for entry in required_entries
    )
    has_blocked = any(entry.get("status") == "blocked" for entry in required_entries)
    missing_required = any(
        action_id not in entry_by_id
        for oracle in oracles
        for action_id in (
            oracle.get("required_action_ids", [])
            if isinstance(oracle.get("required_action_ids"), list)
            else []
        )
        if isinstance(action_id, str)
    )
    environment_blocker = any(
        finding.get("kind") == "environment-blocker" for finding in findings
    )
    raw_teardown_status = teardown.get("status") if teardown is not None else None
    teardown_status = (
        raw_teardown_status if isinstance(raw_teardown_status, str) else None
    )
    unknown_rules = any(
        rule.get("status") == "unknown" or rule.get("outcome") == "unknown"
        for rule in rules
    )

    if result == "EXEMPT":
        exempt_valid = (
            not oracles
            and not entries
            and not rules
            and not findings
            and bool(exemption_ids)
            and teardown_status != "fail"
        )
        if not exempt_valid:
            _add(
                issues,
                "invalid-exemption",
                "draft.intended_result",
                "EXEMPT requires no product actions, rules, or findings and retained grounding evidence",
            )
    else:
        if not oracles:
            _add(
                issues,
                "missing-material-oracle",
                "charter.material_oracles",
                "non-exempt evidence requires at least one material consumer oracle",
            )
        if unknown_rules:
            _add(
                issues,
                "unknown-state",
                "draft.rule_applicability",
                "unknown rules cannot be terminalized",
            )

    if result == "PASS":
        if contradictory:
            _add(
                issues,
                "contradictory-oracle",
                "ledger.entries",
                "material oracle contains both fail and pass evidence",
            )
        pass_supported = (
            bool(oracles)
            and not missing_required
            and all(entry.get("status") == "pass" for entry in entries)
            and all(entry.get("status") == "pass" for entry in required_entries)
            and not findings
            and not unsatisfied_rule_ids
            and not unknown_rules
            and not contradictory
            and teardown_status in {"pass", "not-required"}
        )
        if not pass_supported:
            _add(
                issues,
                "unsupported-result",
                "draft.intended_result",
                "supplied evidence does not support PASS",
            )
    elif result == "FAIL":
        defect_findings = any(
            finding.get("kind") != "environment-blocker" for finding in findings
        )
        if not (
            has_required_fail
            or contradictory
            or unsatisfied_rule_ids
            or defect_findings
        ):
            _add(
                issues,
                "unsupported-result",
                "draft.intended_result",
                "supplied evidence does not support FAIL",
            )
    elif result == "BLOCKED":
        blocker_supported = has_blocked or missing_required or environment_blocker
        proven_defect = has_required_fail or contradictory or bool(
            unsatisfied_rule_ids
        ) or any(
            finding.get("kind") != "environment-blocker" for finding in findings
        )
        if not blocker_supported or proven_defect:
            _add(
                issues,
                "unsupported-result",
                "draft.intended_result",
                "supplied evidence does not support BLOCKED",
            )
    return sorted(set(issues))


def _artifact_records(
    root: Path,
    charter: dict[str, object],
    ledger: dict[str, object],
    draft: dict[str, object],
    *,
    root_descriptor: int | None = None,
) -> tuple[list[dict[str, object]], dict[str, bytes]]:
    snapshots = {
        "charter.json": canonical_json(charter),
        "ledger.json": canonical_json(ledger),
        "draft.json": canonical_json(draft),
        "environment.json": canonical_json(draft["environment"]),
    }
    records: list[dict[str, object]] = []
    for declaration in draft["artifacts"]:
        relative = str(declaration["path"])
        path = _absolute_without_resolution(root / relative)
        records.append(
            {
                "artifact_id": declaration["artifact_id"],
                "kind": declaration["kind"],
                "path": relative,
                "sha256": hashlib.sha256(
                    _read_regular_at(
                        root_descriptor,
                        Path(relative),
                        code="invalid-artifact-path",
                    )
                    if root_descriptor is not None
                    else _read_regular_nofollow(
                        path, code="invalid-artifact-path"
                    )
                ).hexdigest(),
            }
        )
    for artifact_id, filename in (
        ("test-charter", "charter.json"),
        ("test-action-ledger", "ledger.json"),
        ("test-draft", "draft.json"),
        ("test-environment", "environment.json"),
    ):
        records.append(
            {
                "artifact_id": artifact_id,
                "kind": "metadata",
                "path": f"terminal/{filename}",
                "sha256": hashlib.sha256(snapshots[filename]).hexdigest(),
            }
        )
    return records, snapshots


def compile_bundle(
    root: Path,
    charter: dict[str, object],
    ledger: dict[str, object],
    draft: dict[str, object],
    completed_at: str,
    *,
    root_descriptor: int | None = None,
) -> tuple[dict[str, object], dict[str, bytes]]:
    """Derive the accepted bundle without changing supplied judgments."""

    artifact_records, snapshots = _artifact_records(
        root,
        charter,
        ledger,
        draft,
        root_descriptor=root_descriptor,
    )
    environment_digest = _sha256_value(draft["environment"])
    checks: list[dict[str, object]] = []
    journeys: list[dict[str, object]] = []
    for entry in ledger["entries"]:
        if entry["role"] == "check":
            checks.append(
                {
                    "check_id": entry["action_id"],
                    "ring": entry["ring"],
                    "action": entry["action"],
                    "expected": entry["expected"],
                    "actual": entry["actual"],
                    "status": entry["status"],
                    "artifact_ids": entry["artifact_ids"],
                }
            )
        else:
            journeys.append(
                {
                    "journey_id": entry["action_id"],
                    "ring": entry["ring"],
                    "path": entry["path"],
                    "expected": entry["expected"],
                    "actual": entry["actual"],
                    "status": entry["status"],
                    "artifact_ids": entry["artifact_ids"],
                }
            )
    rules = [
        {
            "rule_id": rule["rule_id"],
            "status": rule["status"],
            "evidence": rule["evidence"],
        }
        for rule in draft["rule_applicability"]
    ]
    findings = [
        {
            "kind": finding["kind"],
            "severity": finding["severity"],
            "repository": charter["repository"],
            "head": charter["head"],
            "environment_digest": environment_digest,
            "ring": finding["ring"],
            "journey": finding["journey"],
            "expected": finding["expected"],
            "actual": finding["actual"],
            "reproduction": finding["reproduction"],
            "violated_rule_ids": finding["violated_rule_ids"],
            "artifacts": finding["artifacts"],
        }
        for finding in draft["findings"]
    ]
    bundle: dict[str, object] = {
        "schema_version": "test-evidence-bundle.v1",
        "run_id": charter["run_id"],
        "workflow_id": charter["workflow_id"],
        "repository": charter["repository"],
        "branch": charter["branch"],
        "head": charter["head"],
        "environment_digest": environment_digest,
        "scope": charter["scope"],
        "rule_applicability": rules,
        "checks": checks,
        "journeys": journeys,
        "exploration": draft["exploration"],
        "findings": findings,
        "artifacts": artifact_records,
        "teardown": draft["teardown"],
        "test_side_commits": draft["test_side_commits"],
        "limitations": draft["limitations"],
        "result": draft["intended_result"],
        "started_at": draft["started_at"],
        "completed_at": completed_at,
    }
    bundle["bundle_digest"] = _sha256_value(bundle)
    return bundle, snapshots


def compile_receipt(bundle: dict[str, object]) -> dict[str, object]:
    """Derive the compact receipt exclusively from the final bundle."""

    return {
        "schema_version": "test-evidence-receipt.v1",
        "run_id": bundle["run_id"],
        "workflow_id": bundle["workflow_id"],
        "repository": bundle["repository"],
        "branch": bundle["branch"],
        "head": bundle["head"],
        "environment_digest": bundle["environment_digest"],
        "selected_scope_digest": _sha256_value(bundle["scope"]),
        "bundle_digest": bundle["bundle_digest"],
        "test_side_commits": bundle["test_side_commits"],
        "result": bundle["result"],
    }


def _matches_declared_type(value: object, declared: object) -> bool:
    declarations = declared if isinstance(declared, list) else [declared]
    for candidate in declarations:
        if candidate == "null" and value is None:
            return True
        if candidate == "string" and isinstance(value, str):
            return True
        if candidate == "array" and isinstance(value, list):
            return True
        if candidate == "object" and isinstance(value, dict):
            return True
    return False


def _validate_contract_value(
    value: object, schema: object, path: str, issues: list[Issue]
) -> None:
    if not isinstance(schema, dict):
        _add(issues, "invalid-resource", path, "contract schema node must be an object")
        return
    declared = schema.get("type")
    if not _matches_declared_type(value, declared):
        _add(issues, "contract-violation", path, f"value does not match declared type {declared!r}")
        return
    if value is None:
        return
    if isinstance(value, str):
        minimum = schema.get("min_length")
        if isinstance(minimum, int) and len(value) < minimum:
            _add(issues, "contract-violation", path, "string is shorter than the contract minimum")
        if "const" in schema and value != schema["const"]:
            _add(issues, "contract-violation", path, "string does not match contract constant")
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            _add(issues, "contract-violation", path, "string is outside the contract enum")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            _add(issues, "contract-violation", path, "string does not match contract pattern")
        return
    if isinstance(value, list):
        minimum = schema.get("min_items")
        if isinstance(minimum, int) and len(value) < minimum:
            _add(issues, "contract-violation", path, "array is shorter than the contract minimum")
        if schema.get("unique_items") is True:
            serialized = [canonical_json(item) for item in value]
            if len(serialized) != len(set(serialized)):
                _add(issues, "contract-violation", path, "array items must be unique")
        item_schema = schema.get("items")
        for index, child in enumerate(value):
            _validate_contract_value(child, item_schema, f"{path}[{index}]", issues)
        return
    if isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            _add(issues, "invalid-resource", path, "object schema has no properties")
            return
        required = schema.get("required")
        required_names = set(required) if isinstance(required, list) else set()
        for name in sorted(required_names - set(value)):
            _add(issues, "contract-violation", f"{path}.{name}", "required field is missing")
        if schema.get("additional_properties") is False:
            for name in sorted(set(value) - set(properties)):
                _add(issues, "contract-violation", f"{path}.{name}", "field is not allowed")
        for name, child in value.items():
            child_schema = properties.get(name)
            if child_schema is not None:
                _validate_contract_value(child, child_schema, f"{path}.{name}", issues)


def validate_compiled_contract(
    bundle: dict[str, object],
    receipt: dict[str, object],
    contract: dict[str, object],
) -> list[Issue]:
    issues: list[Issue] = []
    _validate_contract_value(bundle, contract.get("bundle"), "bundle", issues)
    _validate_contract_value(receipt, contract.get("receipt"), "receipt", issues)
    return sorted(set(issues))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _write_private_file(path: Path, contents: bytes) -> None:
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
        while offset < len(contents):
            written = os.write(descriptor, contents[offset:])
            if written <= 0:
                raise OSError("private evidence write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_file_at(
    directory_descriptor: int, filename: str, contents: bytes
) -> None:
    if filename not in OUTPUT_FILES:
        raise FinalizerError(
            "publication-failed", filename, "unexpected terminal output filename"
        )
    descriptor = os.open(
        filename,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_descriptor,
    )
    try:
        offset = 0
        while offset < len(contents):
            written = os.write(descriptor, contents[offset:])
            if written <= 0:
                raise OSError("private evidence write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_noreplace_raw(
    source: str | Path,
    target: str | Path,
    *,
    source_dir_fd: int = -100,
    target_dir_fd: int = -100,
) -> None:
    """Atomically rename without replacing an existing target on Linux."""

    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as error:
        raise FinalizerError(
            "unsupported-publication",
            "terminal",
            "atomic no-replace publication is unavailable on this platform",
        ) from error
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    try:
        source_bytes = os.fsencode(os.fspath(source))
        target_bytes = os.fsencode(os.fspath(target))
    except (TypeError, ValueError) as error:
        raise FinalizerError(
            "invalid-path", "terminal", "publication path is invalid"
        ) from error
    if renameat2(
        source_dir_fd,
        source_bytes,
        target_dir_fd,
        target_bytes,
        1,  # RENAME_NOREPLACE
    ) == 0:
        return
    observed_errno = ctypes.get_errno()
    if observed_errno in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FinalizerError(
            "terminal-exists",
            "terminal",
            "terminal evidence is immutable and cannot be overwritten",
        )
    if observed_errno in {
        errno.ENOSYS,
        errno.EINVAL,
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise FinalizerError(
            "unsupported-publication",
            "terminal",
            "the filesystem does not support atomic no-replace publication",
        )
    raise FinalizerError(
        "publication-failed",
        "terminal",
        f"atomic no-replace publication failed with errno {observed_errno}",
    )


def _rename_noreplace(
    source: str | Path,
    target: str | Path,
    *,
    source_dir_fd: int = -100,
    target_dir_fd: int = -100,
) -> None:
    """Patchable publication seam kept separate from failure cleanup."""

    _rename_noreplace_raw(
        source,
        target,
        source_dir_fd=source_dir_fd,
        target_dir_fd=target_dir_fd,
    )


def _create_staging_directory(root_descriptor: int) -> tuple[str, int, os.stat_result]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    for _ in range(32):
        name = f".terminal-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=root_descriptor)
        except FileExistsError:
            continue
        descriptor = -1
        created_metadata: os.stat_result | None = None
        metadata: os.stat_result | None = None
        precheck_error: Exception | None = None
        try:
            try:
                created_metadata = os.stat(
                    name, dir_fd=root_descriptor, follow_symlinks=False
                )
            except Exception as error:
                precheck_error = error
            descriptor = os.open(name, flags, dir_fd=root_descriptor)
            metadata = os.fstat(descriptor)
            lexical = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            if (
                not _same_inode(metadata, lexical)
                or (
                    created_metadata is not None
                    and not _same_inode(metadata, created_metadata)
                )
            ):
                raise FinalizerError(
                    "path-race", name, "staging directory changed while it was opened"
                )
            if precheck_error is not None:
                raise FinalizerError(
                    "path-race",
                    name,
                    "staging directory could not be continuously verified",
                )
            return name, descriptor, metadata
        except Exception as error:
            if metadata is None and descriptor >= 0:
                try:
                    metadata = os.fstat(descriptor)
                except OSError:
                    pass
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            cleanup_issues: list[Issue] = []
            # The inode observed immediately after our successful mkdir is the
            # only ownership anchor. A later open may already refer to a
            # same-name substitute and must never override that anchor.
            owned_metadata = (
                created_metadata if created_metadata is not None else metadata
            )
            if owned_metadata is not None and stat.S_ISDIR(owned_metadata.st_mode):
                cleanup_issues.extend(
                    _attempt_owned_cleanup(root_descriptor, name, owned_metadata)
                )
            else:
                cleanup_issues.append(
                    Issue(
                        "cleanup-failed",
                        name,
                        "staging ownership was unavailable, no pathname was deleted",
                    )
                )
            primary = (
                error.issues
                if isinstance(error, FinalizerError)
                else (
                    Issue(
                        "publication-failed",
                        name,
                        "could not validate the private staging directory",
                    ),
                )
            )
            raise FinalizerError(
                "publication-failed",
                name,
                "could not validate the private staging directory",
                issues=(*primary, *cleanup_issues),
            ) from error
    raise FinalizerError(
        "publication-failed", "staging", "could not allocate a private staging directory"
    )


def _cleanup_owned_directory(
    root_descriptor: int, name: str, owned: os.stat_result
) -> list[Issue]:
    """Remove canonical authority without any post-authentication path deletion."""

    issues: list[Issue] = []
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    quarantine = f".terminal-cleanup-{secrets.token_hex(12)}"
    try:
        _rename_noreplace_raw(
            name,
            quarantine,
            source_dir_fd=root_descriptor,
            target_dir_fd=root_descriptor,
        )
    except FinalizerError:
        return [
            Issue(
                "cleanup-failed",
                name,
                "could not prove the owned terminal output entered private quarantine",
            )
        ]
    try:
        os.fsync(root_descriptor)
    except OSError:
        issues.append(
            Issue(
                "cleanup-failed",
                quarantine,
                "terminal quarantine rename could not be durably synchronized",
            )
        )
    try:
        descriptor = os.open(quarantine, flags, dir_fd=root_descriptor)
    except OSError:
        issues.append(
            Issue(
                "cleanup-failed",
                quarantine,
                "could not open quarantined publication output",
            )
        )
        return sorted(set(issues))
    try:
        if not _same_inode(os.fstat(descriptor), owned):
            issues.append(
                Issue(
                    "cleanup-failed",
                    quarantine,
                    "refused to touch a substituted terminal quarantine",
                )
            )
            return sorted(set(issues))
        try:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
        except OSError:
            issues.append(
                Issue(
                    "cleanup-failed",
                    quarantine,
                    "private terminal quarantine could not be durably synchronized",
                )
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    issues.append(
        Issue(
            "cleanup-failed",
            quarantine,
            "owned terminal output remains privately quarantined until explicit quiescent cleanup",
        )
    )
    return sorted(set(issues))


def _attempt_owned_cleanup(
    root_descriptor: int, name: str, owned: os.stat_result
) -> list[Issue]:
    try:
        return _cleanup_owned_directory(root_descriptor, name, owned)
    except Exception:
        return [
            Issue(
                "cleanup-failed",
                name,
                "owned publication cleanup raised an internal error",
            )
        ]


def _raise_issues(issues: Sequence[Issue], path: str, message: str) -> None:
    if issues:
        raise FinalizerError("validation-failed", path, message, issues=issues)


def _verify_output_at(
    root: Path,
    root_descriptor: int,
    output_descriptor: int,
    bundle: dict[str, object],
    receipt: dict[str, object],
    snapshots: dict[str, bytes],
    contract: dict[str, object],
) -> None:
    """Reopen and fully validate a staged or published output by descriptor."""

    directory_metadata = os.fstat(output_descriptor)
    if not stat.S_ISDIR(directory_metadata.st_mode):
        raise FinalizerError(
            "publication-failed", "terminal", "terminal output is not a directory"
        )
    if stat.S_IMODE(directory_metadata.st_mode) & 0o077:
        raise FinalizerError(
            "insecure-output", "terminal", "terminal directory is not private"
        )
    try:
        observed = set(os.listdir(output_descriptor))
    except OSError as error:
        raise FinalizerError(
            "publication-failed", "terminal", "cannot enumerate terminal output"
        ) from error
    if observed != OUTPUT_FILES:
        raise FinalizerError(
            "publication-failed",
            "terminal",
            f"terminal file set differs: {sorted(observed ^ OUTPUT_FILES)!r}",
        )

    raw: dict[str, bytes] = {}
    for filename in sorted(OUTPUT_FILES):
        try:
            metadata = os.stat(
                filename, dir_fd=output_descriptor, follow_symlinks=False
            )
        except OSError as error:
            raise FinalizerError(
                "publication-failed",
                f"terminal/{filename}",
                "cannot inspect terminal output file",
            ) from error
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise FinalizerError(
                "publication-failed",
                f"terminal/{filename}",
                "terminal output must be a regular file",
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise FinalizerError(
                "insecure-output",
                f"terminal/{filename}",
                "terminal file is not private",
            )
        raw[filename] = _read_regular_at(
            output_descriptor, Path(filename), code="publication-failed"
        )

    expected = dict(snapshots)
    expected["bundle.json"] = canonical_json(bundle)
    expected["receipt.json"] = canonical_json(receipt)
    for filename in sorted(OUTPUT_FILES):
        if raw[filename] != expected[filename]:
            raise FinalizerError(
                "publication-failed",
                f"terminal/{filename}",
                "terminal bytes differ from the validated compilation",
            )

    decoded = {
        filename: _decode_closed_json(contents, f"terminal/{filename}")
        for filename, contents in raw.items()
    }
    if any(not isinstance(value, dict) for value in decoded.values()):
        raise FinalizerError(
            "publication-failed", "terminal", "terminal values must be JSON objects"
        )
    charter = dict(decoded["charter.json"])
    ledger = dict(decoded["ledger.json"])
    draft = dict(decoded["draft.json"])
    observed_bundle = dict(decoded["bundle.json"])
    observed_receipt = dict(decoded["receipt.json"])
    if decoded["environment.json"] != draft.get("environment"):
        raise FinalizerError(
            "digest-mismatch",
            "terminal/environment.json",
            "environment snapshot differs from the frozen draft",
        )
    _raise_issues(
        validate_inputs(
            root,
            charter,
            ledger,
            draft,
            catalog=_load_resource("quality-rules.json"),
            contract=contract,
            root_descriptor=root_descriptor,
        ),
        "terminal.inputs",
        "published input snapshots are invalid",
    )
    _raise_issues(
        validate_compiled_contract(observed_bundle, observed_receipt, contract),
        "terminal.contract",
        "published bundle or receipt violates the contract",
    )
    reconstructed, _ = compile_bundle(
        root,
        charter,
        ledger,
        draft,
        str(observed_bundle.get("completed_at", "")),
        root_descriptor=root_descriptor,
    )
    reconstructed_receipt = compile_receipt(reconstructed)
    if canonical_json(observed_bundle) != canonical_json(reconstructed):
        raise FinalizerError(
            "digest-mismatch",
            "terminal/bundle.json",
            "bundle is not the deterministic compilation of retained inputs",
        )
    if canonical_json(observed_receipt) != canonical_json(reconstructed_receipt):
        raise FinalizerError(
            "digest-mismatch",
            "terminal/receipt.json",
            "receipt is not derived from the retained bundle",
        )


def _verify_file_set(terminal: Path) -> None:
    expected = {
        "charter.json",
        "ledger.json",
        "draft.json",
        "environment.json",
        "bundle.json",
        "receipt.json",
    }
    try:
        observed = {path.name for path in terminal.iterdir()}
    except OSError as error:
        raise FinalizerError(
            "publication-failed", str(terminal), f"cannot inspect terminal output: {error}"
        ) from error
    if observed != expected:
        raise FinalizerError(
            "publication-failed",
            str(terminal),
            f"terminal file set differs: {sorted(observed ^ expected)!r}",
        )
    if stat.S_IMODE(terminal.stat().st_mode) & 0o077:
        raise FinalizerError(
            "insecure-output", str(terminal), "terminal directory is not private"
        )
    for filename in sorted(expected):
        path = terminal / filename
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise FinalizerError(
                "publication-failed", str(path), "terminal output must be a regular file"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise FinalizerError(
                "insecure-output", str(path), "terminal file is not private"
            )


def verify_published(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    """Reopen and independently reconstruct a published bundle and receipt."""

    root = _validate_run_root(root)
    terminal = root / "terminal"
    _reject_symlink_components(terminal)
    _verify_file_set(terminal)
    charter_value = load_closed_json(terminal / "charter.json")
    ledger_value = load_closed_json(terminal / "ledger.json")
    draft_value = load_closed_json(terminal / "draft.json")
    environment = load_closed_json(terminal / "environment.json")
    bundle_value = load_closed_json(terminal / "bundle.json")
    receipt_value = load_closed_json(terminal / "receipt.json")
    for label, value in (
        ("charter", charter_value),
        ("ledger", ledger_value),
        ("draft", draft_value),
        ("environment", environment),
        ("bundle", bundle_value),
        ("receipt", receipt_value),
    ):
        if not isinstance(value, dict):
            raise FinalizerError(
                "publication-failed", f"terminal.{label}", "published value must be an object"
            )
    charter = dict(charter_value)
    ledger = dict(ledger_value)
    draft = dict(draft_value)
    bundle = dict(bundle_value)
    receipt = dict(receipt_value)
    if environment != draft.get("environment"):
        raise FinalizerError(
            "digest-mismatch",
            "terminal.environment.json",
            "environment snapshot differs from the frozen draft",
        )
    catalog = _load_resource("quality-rules.json")
    contract = _load_resource("evidence-contract.json")
    _raise_issues(
        validate_inputs(root, charter, ledger, draft, catalog=catalog, contract=contract),
        "terminal.inputs",
        "published input snapshots are invalid",
    )
    _raise_issues(
        validate_compiled_contract(bundle, receipt, contract),
        "terminal.contract",
        "published bundle or receipt violates the contract",
    )
    digest_input = dict(bundle)
    stored_digest = digest_input.pop("bundle_digest", None)
    if stored_digest != _sha256_value(digest_input):
        raise FinalizerError(
            "digest-mismatch", "terminal.bundle.bundle_digest", "bundle digest does not match retained bytes"
        )
    expected_bundle, _ = compile_bundle(
        root, charter, ledger, draft, str(bundle.get("completed_at", ""))
    )
    expected_receipt = compile_receipt(expected_bundle)
    if canonical_json(bundle) != canonical_json(expected_bundle):
        raise FinalizerError(
            "digest-mismatch", "terminal.bundle", "bundle is not the deterministic compilation of snapshots"
        )
    if canonical_json(receipt) != canonical_json(expected_receipt):
        raise FinalizerError(
            "digest-mismatch", "terminal.receipt", "receipt is not derived from the retained bundle"
        )
    for index, artifact in enumerate(bundle["artifacts"]):
        relative = Path(str(artifact["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise FinalizerError(
                "invalid-artifact-path", f"terminal.bundle.artifacts[{index}]", "published artifact path escapes"
            )
        path = _absolute_without_resolution(root / relative)
        _reject_symlink_components(path)
        observed_digest = hashlib.sha256(
            _read_regular_nofollow(path, code="invalid-artifact-path")
        ).hexdigest()
        if observed_digest != artifact["sha256"]:
            raise FinalizerError(
                "digest-mismatch", str(path), "published artifact digest does not match retained bytes"
            )
    return bundle, receipt


def publish_atomically(
    root: Path,
    bundle: dict[str, object],
    receipt: dict[str, object],
    snapshots: dict[str, bytes],
    *,
    root_descriptor: int | None = None,
    contract: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    """Publish one immutable terminal directory without replacing a predecessor."""

    owned_root = root_descriptor is None
    if root_descriptor is None:
        root, root_descriptor = _open_run_root(root)
    assert root_descriptor is not None
    contract = contract if contract is not None else _load_resource(
        "evidence-contract.json"
    )
    staging_name: str | None = None
    staging_descriptor: int | None = None
    staging_metadata: os.stat_result | None = None
    published = False
    failure: FinalizerError | None = None
    cleanup_issues: list[Issue] = []
    try:
        staging_name, staging_descriptor, staging_metadata = _create_staging_directory(
            root_descriptor
        )
        for filename in (
            "charter.json",
            "ledger.json",
            "draft.json",
            "environment.json",
        ):
            _write_private_file_at(
                staging_descriptor, filename, snapshots[filename]
            )
        _write_private_file_at(
            staging_descriptor, "bundle.json", canonical_json(bundle)
        )
        _write_private_file_at(
            staging_descriptor, "receipt.json", canonical_json(receipt)
        )
        os.fsync(staging_descriptor)
        _verify_output_at(
            root,
            root_descriptor,
            staging_descriptor,
            bundle,
            receipt,
            snapshots,
            contract,
        )
        _revalidate_run_root(root, root_descriptor)
        _rename_noreplace(
            staging_name,
            "terminal",
            source_dir_fd=root_descriptor,
            target_dir_fd=root_descriptor,
        )
        published = True
        os.fsync(root_descriptor)
        terminal_descriptor = _open_beneath(
            root_descriptor, Path("terminal"), directory=True
        )
        try:
            if not _same_inode(os.fstat(terminal_descriptor), staging_metadata):
                raise FinalizerError(
                    "path-race",
                    "terminal",
                    "published terminal no longer denotes the staged output",
                )
            _verify_output_at(
                root,
                root_descriptor,
                terminal_descriptor,
                bundle,
                receipt,
                snapshots,
                contract,
            )
            _revalidate_run_root(root, root_descriptor)
        finally:
            os.close(terminal_descriptor)
    except FinalizerError as error:
        failure = error
    except (OSError, ValueError) as error:
        failure = FinalizerError(
            "publication-failed",
            "terminal",
            f"atomic publication failed with {type(error).__name__}",
        )
    finally:
        if failure is not None and staging_name is not None and staging_metadata is not None:
            cleanup_name = "terminal" if published else staging_name
            cleanup_issues.extend(
                _attempt_owned_cleanup(
                    root_descriptor, cleanup_name, staging_metadata
                )
            )
        if staging_descriptor is not None:
            os.close(staging_descriptor)
        if owned_root:
            os.close(root_descriptor)
    if failure is not None:
        if cleanup_issues:
            raise FinalizerError(
                "publication-failed",
                "terminal",
                "publication and cleanup failed",
                issues=(*failure.issues, *cleanup_issues),
            )
        raise failure
    return root / "terminal" / "bundle.json", root / "terminal" / "receipt.json"


def _composition_object(
    value: object,
    fields: set[str],
    path: str,
    *,
    code: str,
) -> dict[str, object]:
    issues: list[Issue] = []
    parsed = _closed_object_fields(value, fields, path, issues)
    if parsed is None or issues:
        raise FinalizerError(
            code,
            path,
            "draft composition input has the wrong closed object shape",
            issues=issues or (Issue(code, path, "must be an object"),),
        )
    return parsed


def _pending_marker_id(value: object, key: str) -> str | None:
    if not isinstance(value, dict) or set(value) != {key}:
        return None
    marker_id = value.get(key)
    return marker_id if isinstance(marker_id, str) and marker_id else None


def _contains_composition_marker(value: object) -> bool:
    if isinstance(value, list):
        return any(_contains_composition_marker(child) for child in value)
    if isinstance(value, dict):
        return (
            PENDING_KEY in value
            or PENDING_SPLICE_KEY in value
            or any(_contains_composition_marker(child) for child in value.values())
        )
    return False


def _require_pending_judgments(template: dict[str, object]) -> None:
    scalar_paths = {
        "draft_template.summary": template.get("summary"),
        "draft_template.intended_result": template.get("intended_result"),
    }
    exploration = template.get("exploration")
    teardown = template.get("teardown")
    splice_paths = {
        "draft_template.exploration.observations[]": (
            exploration.get("observations") if isinstance(exploration, dict) else None
        ),
        "draft_template.findings[]": template.get("findings"),
        "draft_template.artifacts[]": template.get("artifacts"),
        "draft_template.teardown.actions[]": (
            teardown.get("actions") if isinstance(teardown, dict) else None
        ),
        "draft_template.teardown.artifact_ids[]": (
            teardown.get("artifact_ids") if isinstance(teardown, dict) else None
        ),
        "draft_template.limitations[]": template.get("limitations"),
    }
    for path, value in scalar_paths.items():
        if _pending_marker_id(value, PENDING_KEY) is None:
            raise FinalizerError(
                "required-pending-judgment",
                path,
                "conditional summary and result must use explicit preparation markers",
            )
    if not isinstance(teardown, dict) or _pending_marker_id(
        teardown.get("status"), PENDING_KEY
    ) is None:
        raise FinalizerError(
            "required-pending-judgment",
            "draft_template.teardown.status",
            "conditional teardown status must use an explicit preparation marker",
        )
    for path, value in splice_paths.items():
        if not isinstance(value, list) or not any(
            _pending_marker_id(child, PENDING_SPLICE_KEY) is not None
            for child in value
        ):
            raise FinalizerError(
                "required-pending-judgment",
                path,
                "conditional list values must include a pending splice marker",
            )


def _compose_value(
    value: object,
    resolutions: dict[str, object],
    used: set[str],
    path: str,
) -> object:
    if isinstance(value, list):
        composed: list[object] = []
        for index, child in enumerate(value):
            marker_id = _pending_marker_id(child, PENDING_SPLICE_KEY)
            if marker_id is not None:
                if marker_id in used:
                    raise FinalizerError(
                        "duplicate-marker",
                        f"{path}[{index}]",
                        "pending marker IDs must be unique",
                    )
                if marker_id not in resolutions:
                    raise FinalizerError(
                        "unresolved-marker",
                        f"{path}[{index}]",
                        f"pending marker {marker_id!r} has no final resolution",
                    )
                replacement = resolutions[marker_id]
                if not isinstance(replacement, list):
                    raise FinalizerError(
                        "invalid-resolution",
                        f"delta.resolutions.{marker_id}",
                        "a splice marker requires an array resolution",
                    )
                used.add(marker_id)
                composed.extend(replacement)
                continue
            composed.append(
                _compose_value(child, resolutions, used, f"{path}[{index}]")
            )
        return composed
    if isinstance(value, dict):
        marker_id = _pending_marker_id(value, PENDING_KEY)
        if marker_id is not None:
            if marker_id in used:
                raise FinalizerError(
                    "duplicate-marker", path, "pending marker IDs must be unique"
                )
            if marker_id not in resolutions:
                raise FinalizerError(
                    "unresolved-marker",
                    path,
                    f"pending marker {marker_id!r} has no final resolution",
                )
            used.add(marker_id)
            return resolutions[marker_id]
        if PENDING_KEY in value or PENDING_SPLICE_KEY in value:
            raise FinalizerError(
                "invalid-preparation",
                path,
                "pending markers must be exact one-key objects in the correct container",
            )
        return {
            key: _compose_value(child, resolutions, used, f"{path}.{key}")
            for key, child in value.items()
        }
    return value


def _compact_string_array(
    value: object, path: str, *, minimum: int = 0
) -> list[str]:
    issues: list[Issue] = []
    strings = _string_array(value, path, issues, minimum=minimum, unique=True)
    if strings is None or issues:
        raise FinalizerError(
            "invalid-preparation",
            path,
            "compact rule assessment has an invalid string array",
            issues=issues or (Issue("invalid-preparation", path, "must be an array"),),
        )
    return strings


def _expand_compact_rule_assessments(
    preparation: dict[str, object], catalog: dict[str, object]
) -> list[dict[str, object]]:
    disposition = preparation.get("rule_disposition")
    if disposition not in {"evaluate", "exempt"}:
        raise FinalizerError(
            "invalid-preparation",
            "preparation.rule_disposition",
            "rule disposition must be evaluate or exempt",
        )
    active_conditions = _compact_string_array(
        preparation.get("active_rule_conditions"),
        "preparation.active_rule_conditions",
    )
    active_conditions = [condition for condition in active_conditions if condition != "always"]
    group_values = preparation.get("rule_assessment_groups")
    if not isinstance(group_values, list):
        raise FinalizerError(
            "invalid-preparation",
            "preparation.rule_assessment_groups",
            "rule assessment groups must be an array",
        )
    if disposition == "exempt":
        if active_conditions or group_values:
            raise FinalizerError(
                "invalid-exempt-assessment",
                "preparation.rule_assessment_groups",
                "an exempt run has no rule conditions or assessments",
            )
        return []

    catalog_values = catalog.get("rules")
    if not isinstance(catalog_values, list):
        raise FinalizerError(
            "invalid-resource", "catalog.rules", "quality catalog rules must be an array"
        )
    catalog_rules: list[tuple[str, list[str]]] = []
    known_conditions: set[str] = set()
    for index, value in enumerate(catalog_values):
        if not isinstance(value, dict):
            raise FinalizerError(
                "invalid-resource",
                f"catalog.rules[{index}]",
                "quality catalog rule must be an object",
            )
        rule_id = value.get("id")
        conditions = value.get("applies_when")
        if (
            not isinstance(rule_id, str)
            or not rule_id
            or not isinstance(conditions, list)
            or not conditions
            or not all(isinstance(item, str) and item for item in conditions)
        ):
            raise FinalizerError(
                "invalid-resource",
                f"catalog.rules[{index}]",
                "quality catalog rule identity and conditions are invalid",
            )
        typed_conditions = list(conditions)
        catalog_rules.append((rule_id, typed_conditions))
        known_conditions.update(item for item in typed_conditions if item != "always")

    unknown_conditions = sorted(set(active_conditions) - known_conditions)
    if unknown_conditions:
        raise FinalizerError(
            "unknown-rule-condition",
            "preparation.active_rule_conditions",
            f"unknown active rule conditions: {unknown_conditions!r}",
        )

    assessments: dict[str, tuple[str, list[str]]] = {}
    for index, value in enumerate(group_values):
        path = f"preparation.rule_assessment_groups[{index}]"
        group = _composition_object(
            value,
            RULE_ASSESSMENT_GROUP_FIELDS,
            path,
            code="invalid-preparation",
        )
        rule_ids = _compact_string_array(
            group.get("rule_ids"), f"{path}.rule_ids", minimum=1
        )
        evidence_ids = _compact_string_array(
            group.get("evidence_action_ids"),
            f"{path}.evidence_action_ids",
            minimum=1,
        )
        outcome = group.get("outcome")
        if outcome not in {"satisfied", "unsatisfied", "unknown"}:
            raise FinalizerError(
                "invalid-preparation",
                f"{path}.outcome",
                "active rule outcome must be satisfied, unsatisfied, or unknown",
            )
        for rule_id in rule_ids:
            if rule_id in assessments:
                raise FinalizerError(
                    "duplicate-rule-assessment",
                    f"{path}.rule_ids",
                    f"rule {rule_id!r} is assessed more than once",
                )
            assessments[rule_id] = (outcome, evidence_ids)

    catalog_ids = {rule_id for rule_id, _ in catalog_rules}
    unknown_rule_ids = sorted(set(assessments) - catalog_ids)
    if unknown_rule_ids:
        raise FinalizerError(
            "unknown-rule-assessment",
            "preparation.rule_assessment_groups",
            f"unknown assessed rules: {unknown_rule_ids!r}",
        )
    active_condition_set = set(active_conditions)
    active_rule_ids = {
        rule_id
        for rule_id, conditions in catalog_rules
        if "always" in conditions or active_condition_set.intersection(conditions)
    }
    inactive_assessments = sorted(set(assessments) - active_rule_ids)
    if inactive_assessments:
        raise FinalizerError(
            "inactive-rule-assessment",
            "preparation.rule_assessment_groups",
            f"inactive rules cannot be assessed: {inactive_assessments!r}",
        )
    missing_assessments = sorted(active_rule_ids - set(assessments))
    if missing_assessments:
        raise FinalizerError(
            "missing-rule-assessment",
            "preparation.rule_assessment_groups",
            f"active rules lack an assessment: {missing_assessments!r}",
        )

    expanded: list[dict[str, object]] = []
    for rule_id, conditions in catalog_rules:
        assessment = assessments.get(rule_id)
        if assessment is None:
            inactive = [condition for condition in conditions if condition != "always"]
            expanded.append(
                {
                    "rule_id": rule_id,
                    "status": "inactive",
                    "outcome": "not-applicable",
                    "evidence": [
                        "inactive rule context: " + ", ".join(inactive)
                    ],
                }
            )
            continue
        outcome, evidence_ids = assessment
        expanded.append(
            {
                "rule_id": rule_id,
                "status": "unknown" if outcome == "unknown" else "active",
                "outcome": outcome,
                "evidence": [f"ledger:{action_id}" for action_id in evidence_ids],
            }
        )
    return expanded


def compose_draft(
    preparation_value: object,
    delta_value: object,
    *,
    catalog: dict[str, object] | None = None,
) -> dict[str, object]:
    """Mechanically resolve supplied values without deriving semantic judgments."""

    preparation_version = (
        preparation_value.get("schema_version")
        if isinstance(preparation_value, dict)
        else None
    )
    compact = preparation_version == COMPACT_PREPARATION_SCHEMA_VERSION
    direct = preparation_version == DIRECT_PREPARATION_SCHEMA_VERSION
    preparation = _composition_object(
        preparation_value,
        (
            DIRECT_PREPARATION_FIELDS
            if direct
            else COMPACT_PREPARATION_FIELDS
            if compact
            else PREPARATION_FIELDS
        ),
        "preparation",
        code="invalid-preparation",
    )
    supported_version = (
        DIRECT_PREPARATION_SCHEMA_VERSION
        if direct
        else COMPACT_PREPARATION_SCHEMA_VERSION
        if compact
        else PREPARATION_SCHEMA_VERSION
    )
    if preparation.get("schema_version") != supported_version:
        raise FinalizerError(
            "invalid-preparation",
            "preparation.schema_version",
            "unsupported draft preparation schema version",
        )
    template = _composition_object(
        preparation.get("draft_candidate" if direct else "draft_template"),
        COMPACT_DRAFT_FIELDS if compact or direct else DRAFT_FIELDS,
        "preparation.draft_candidate" if direct else "preparation.draft_template",
        code="invalid-preparation",
    )
    if template.get("schema_version") != DRAFT_SCHEMA_VERSION:
        raise FinalizerError(
            "invalid-preparation",
            (
                "preparation.draft_candidate.schema_version"
                if direct
                else "preparation.draft_template.schema_version"
            ),
            "draft candidate must carry the exact draft schema version",
        )
    if direct:
        if _contains_composition_marker(template):
            raise FinalizerError(
                "invalid-preparation",
                "preparation.draft_candidate",
                "a direct conditional candidate cannot contain pending markers",
            )
    else:
        _require_pending_judgments(template)

    delta = _composition_object(
        delta_value,
        DELTA_FIELDS,
        "delta",
        code="invalid-delta",
    )
    expected_delta_version = (
        EMPTY_DELTA_SCHEMA_VERSION if direct else DELTA_SCHEMA_VERSION
    )
    if delta.get("schema_version") != expected_delta_version:
        raise FinalizerError(
            "invalid-delta",
            "delta.schema_version",
            "unsupported final delta schema version",
        )
    values = delta.get("resolutions")
    if direct:
        if values != []:
            raise FinalizerError(
                "invalid-delta",
                "delta.resolutions",
                "a direct conditional candidate requires an empty resolutions array",
            )
        composed: object = dict(template)
    elif not isinstance(values, list) or not values:
        raise FinalizerError(
            "invalid-delta", "delta.resolutions", "resolutions must be a non-empty array"
        )
    else:
        resolutions: dict[str, object] = {}
        for index, value in enumerate(values):
            path = f"delta.resolutions[{index}]"
            resolution = _composition_object(
                value, RESOLUTION_FIELDS, path, code="invalid-delta"
            )
            resolution_id = resolution.get("resolution_id")
            if not isinstance(resolution_id, str) or not resolution_id:
                raise FinalizerError(
                    "invalid-delta", f"{path}.resolution_id", "must be a non-empty string"
                )
            if resolution_id in resolutions:
                raise FinalizerError(
                    "duplicate-resolution",
                    f"{path}.resolution_id",
                    "resolution IDs must be unique",
                )
            replacement = resolution.get("value")
            if _contains_composition_marker(replacement):
                raise FinalizerError(
                    "invalid-resolution",
                    f"{path}.value",
                    "a final resolution cannot contain pending markers",
                )
            resolutions[resolution_id] = replacement

        used: set[str] = set()
        composed = _compose_value(template, resolutions, used, "draft_template")
        unused = sorted(set(resolutions) - used)
        if unused:
            raise FinalizerError(
                "unused-resolution",
                "delta.resolutions",
                f"resolutions are not referenced by the preparation: {unused!r}",
            )
        if _contains_composition_marker(composed):
            raise FinalizerError(
                "unresolved-marker",
                "draft",
                "composed draft still contains a pending marker",
            )
    assert isinstance(composed, dict)
    if compact or direct:
        composed["rule_applicability"] = _expand_compact_rule_assessments(
            preparation,
            catalog if catalog is not None else _load_resource("quality-rules.json"),
        )
    return composed


def _load_composition_charter_binding(
    root: Path, root_descriptor: int
) -> tuple[bytes, os.stat_result]:
    """Authenticate only the frozen charter fields that bind a composition root."""

    raw, metadata = _read_regular_snapshot_at(
        root_descriptor, Path("charter.json"), code="invalid-charter-binding"
    )
    value = _decode_closed_json(raw, "charter.json")
    if not isinstance(value, dict):
        raise FinalizerError(
            "invalid-charter-binding",
            "charter.json",
            "composition charter must be a JSON object",
        )
    if value.get("schema_version") not in {CHARTER_SCHEMA_VERSION, "test-charter.v2"}:
        raise FinalizerError(
            "invalid-charter-binding",
            "charter.schema_version",
            "composition charter has an unsupported schema version",
        )
    run_id = value.get("run_id")
    if (
        not isinstance(run_id, str)
        or RUN_ID_RE.fullmatch(run_id) is None
        or run_id != root.name
    ):
        raise FinalizerError(
            "run-id-mismatch",
            "charter.run_id",
            "composition root basename must equal the frozen charter run ID",
        )
    repository = value.get("repository")
    if not isinstance(repository, str) or not repository:
        raise FinalizerError(
            "invalid-charter-binding",
            "charter.repository",
            "composition charter repository must be a non-empty absolute path",
        )
    repository_path = Path(repository)
    if not repository_path.is_absolute():
        raise FinalizerError(
            "invalid-charter-binding",
            "charter.repository",
            "composition charter repository must be absolute",
        )
    expected_root = _absolute_without_resolution(
        repository_path / ".test-evidence" / run_id
    )
    if expected_root != root:
        raise FinalizerError(
            "invalid-root",
            "charter.repository",
            "composition root is not the repository's canonical evidence path",
        )
    return raw, metadata


def _revalidate_composition_charter_binding(
    root_descriptor: int,
    expected_raw: bytes,
    expected_metadata: os.stat_result,
) -> None:
    raw, metadata = _read_regular_snapshot_at(
        root_descriptor, Path("charter.json"), code="invalid-charter-binding"
    )
    if (
        not _same_inode(metadata, expected_metadata)
        or metadata.st_size != expected_metadata.st_size
        or metadata.st_mtime_ns != expected_metadata.st_mtime_ns
        or metadata.st_ctime_ns != expected_metadata.st_ctime_ns
        or raw != expected_raw
    ):
        raise FinalizerError(
            "path-race",
            "charter.json",
            "frozen composition charter changed after root authentication",
        )


def _revalidate_composition_ledger_binding(
    root_descriptor: int,
    expected_raw: bytes,
    expected_metadata: os.stat_result,
) -> None:
    raw, metadata = _read_regular_snapshot_at(
        root_descriptor, Path("ledger.json"), code="invalid-ledger"
    )
    if (
        not _same_inode(metadata, expected_metadata)
        or metadata.st_size != expected_metadata.st_size
        or metadata.st_mtime_ns != expected_metadata.st_mtime_ns
        or metadata.st_ctime_ns != expected_metadata.st_ctime_ns
        or raw != expected_raw
    ):
        raise FinalizerError(
            "path-race",
            "ledger.json",
            "composition ledger changed after semantic preflight",
        )


def _cleanup_owned_draft_file(
    root_descriptor: int, name: str, owned: os.stat_result
) -> list[Issue]:
    """Remove authority, but never path-unlink after inode continuity is lost."""

    quarantine = f".draft-cleanup-{secrets.token_hex(12)}"
    try:
        _rename_noreplace_raw(
            name,
            quarantine,
            source_dir_fd=root_descriptor,
            target_dir_fd=root_descriptor,
        )
    except FinalizerError:
        return [
            Issue(
                "cleanup-failed",
                name,
                "could not prove the owned draft entered private quarantine",
            )
        ]
    descriptor = -1
    try:
        descriptor = _open_beneath(
            root_descriptor, Path(quarantine), directory=False
        )
        if not _same_inode(os.fstat(descriptor), owned):
            return [
                Issue(
                    "cleanup-failed",
                    quarantine,
                    "refused to touch a substituted draft quarantine",
                )
            ]
        try:
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.fsync(root_descriptor)
        except OSError:
            return [
                Issue(
                    "cleanup-failed",
                    quarantine,
                    "private draft quarantine could not be durably synchronized",
                )
            ]
        return [
            Issue(
                "cleanup-failed",
                quarantine,
                "owned draft remains privately quarantined until explicit quiescent cleanup",
            )
        ]
    except (FinalizerError, OSError):
        return [
            Issue(
                "cleanup-failed",
                quarantine,
                "could not safely remove owned draft publication",
            )
        ]
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_composed_draft(
    root: Path,
    root_descriptor: int,
    contents: bytes,
    charter_binding: tuple[bytes, os.stat_result],
    ledger_binding: tuple[bytes, os.stat_result],
) -> None:
    staging_name = f".draft-{secrets.token_hex(12)}"
    staging_metadata: os.stat_result | None = None
    staging_descriptor = -1
    published = False
    failure: FinalizerError | None = None
    cleanup_issues: list[Issue] = []
    try:
        _revalidate_composition_charter_binding(
            root_descriptor, *charter_binding
        )
        _revalidate_composition_ledger_binding(
            root_descriptor, *ledger_binding
        )
        staging_descriptor = os.open(
            staging_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_descriptor,
        )
        staging_metadata = os.fstat(staging_descriptor)
        offset = 0
        while offset < len(contents):
            written = os.write(staging_descriptor, contents[offset:])
            if written <= 0:
                raise OSError("draft staging write made no progress")
            offset += written
        os.fsync(staging_descriptor)
        os.close(staging_descriptor)
        staging_descriptor = -1
        _revalidate_run_root(root, root_descriptor)
        _revalidate_composition_charter_binding(
            root_descriptor, *charter_binding
        )
        _revalidate_composition_ledger_binding(
            root_descriptor, *ledger_binding
        )
        try:
            _rename_noreplace(
                staging_name,
                DRAFT_FILENAME,
                source_dir_fd=root_descriptor,
                target_dir_fd=root_descriptor,
            )
        except FinalizerError as error:
            if any(issue.code == "terminal-exists" for issue in error.issues):
                raise FinalizerError(
                    "draft-exists",
                    str(root / DRAFT_FILENAME),
                    "authoritative draft is immutable and cannot be overwritten",
                ) from error
            raise
        published = True
        os.fsync(root_descriptor)
        draft_descriptor = _open_beneath(
            root_descriptor, Path(DRAFT_FILENAME), directory=False
        )
        try:
            if not _same_inode(os.fstat(draft_descriptor), staging_metadata):
                raise FinalizerError(
                    "path-race",
                    str(root / DRAFT_FILENAME),
                    "published draft no longer denotes the staged file",
                )
        finally:
            os.close(draft_descriptor)
        if _read_regular_at(
            root_descriptor, Path(DRAFT_FILENAME), code="publication-failed"
        ) != contents:
            raise FinalizerError(
                "publication-failed",
                str(root / DRAFT_FILENAME),
                "published draft bytes differ from the composition",
            )
        _revalidate_composition_charter_binding(
            root_descriptor, *charter_binding
        )
        _revalidate_composition_ledger_binding(
            root_descriptor, *ledger_binding
        )
        _revalidate_run_root(root, root_descriptor)
    except FinalizerError as error:
        failure = error
    except (OSError, ValueError) as error:
        failure = FinalizerError(
            "publication-failed",
            str(root / DRAFT_FILENAME),
            f"durable draft publication failed with {type(error).__name__}",
        )
    finally:
        if staging_descriptor >= 0:
            os.close(staging_descriptor)
        if failure is not None and staging_metadata is not None:
            cleanup_issues.extend(
                _cleanup_owned_draft_file(
                    root_descriptor,
                    DRAFT_FILENAME if published else staging_name,
                    staging_metadata,
                )
            )
    if failure is not None:
        if cleanup_issues:
            raise FinalizerError(
                "publication-failed",
                str(root / DRAFT_FILENAME),
                "draft publication and cleanup failed",
                issues=(*failure.issues, *cleanup_issues),
            )
        raise failure


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise FinalizerError("invalid-arguments", "arguments", message)


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--charter", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--draft", required=True, type=Path)
    return parser.parse_args(argv)


def _parse_compose_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = _ArgumentParser(
        description="Mechanically compose a final draft from frozen preparation and delta"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--preparation", required=True, type=Path)
    parser.add_argument("--delta", required=True, type=Path)
    return parser.parse_args(argv)


def _write_error(error: FinalizerError) -> None:
    payload = {
        "errors": [issue.as_dict() for issue in error.issues],
        "status": "ERROR",
    }
    sys.stdout.write(canonical_json(payload).decode("utf-8") + "\n")


def _compose_draft_main(argv: Sequence[str]) -> int:
    root_descriptor: int | None = None
    try:
        arguments = _parse_compose_arguments(argv)
        root, root_descriptor = _open_run_root(arguments.root)
        charter_binding = _load_composition_charter_binding(
            root, root_descriptor
        )
        _validate_input_location(
            arguments.preparation, root / PREPARATION_FILENAME
        )
        _validate_input_location(arguments.delta, root / DELTA_FILENAME)
        try:
            os.stat(DRAFT_FILENAME, dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FinalizerError(
                "draft-exists",
                str(root / DRAFT_FILENAME),
                "authoritative draft is immutable and cannot be overwritten",
            )
        preparation = _load_closed_json_at(
            root_descriptor, Path(PREPARATION_FILENAME)
        )
        delta = _load_closed_json_at(root_descriptor, Path(DELTA_FILENAME))
        catalog = _load_resource("quality-rules.json")
        draft = compose_draft(preparation, delta, catalog=catalog)
        charter = _decode_closed_json(charter_binding[0], "charter.json")
        ledger_binding = _read_regular_snapshot_at(
            root_descriptor, Path("ledger.json"), code="invalid-ledger"
        )
        ledger = _decode_closed_json(ledger_binding[0], "ledger.json")
        issues = validate_inputs(
            root,
            charter,
            ledger,
            draft,
            catalog=catalog,
            contract=_load_resource("evidence-contract.json"),
            root_descriptor=root_descriptor,
            artifacts_must_exist=False,
        )
        if issues:
            raise FinalizerError(
                "invalid-input",
                "inputs",
                "composed evidence inputs are semantically invalid",
                issues=issues,
            )
        _publish_composed_draft(
            root,
            root_descriptor,
            canonical_json(draft),
            charter_binding,
            ledger_binding,
        )
        payload = {"draft_path": str(root / DRAFT_FILENAME)}
        sys.stdout.write(
            "draft_composition=PASS\n"
            + canonical_json(payload).decode("utf-8")
            + "\n"
        )
        return 0
    except FinalizerError as error:
        _write_error(error)
        return 1
    except (OSError, ValueError) as error:
        _write_error(
            FinalizerError(
                "filesystem-error",
                "composer",
                f"filesystem operation failed with {type(error).__name__}",
            )
        )
        return 1
    except Exception:
        _write_error(
            FinalizerError(
                "internal-error",
                "composer",
                "draft composition failed without exposing an unsafe traceback",
            )
        )
        return 1
    finally:
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    effective_arguments = list(sys.argv[1:] if argv is None else argv)
    if effective_arguments[:1] == ["compose-draft"]:
        return _compose_draft_main(effective_arguments[1:])
    root_descriptor: int | None = None
    try:
        arguments = _parse_arguments(effective_arguments)
        root, root_descriptor = _open_run_root(arguments.root)
        _validate_input_location(arguments.charter, root / "charter.json")
        _validate_input_location(arguments.ledger, root / "ledger.json")
        _validate_input_location(arguments.draft, root / "draft.json")
        charter = _load_closed_json_at(root_descriptor, Path("charter.json"))
        ledger = _load_closed_json_at(root_descriptor, Path("ledger.json"))
        draft = _load_closed_json_at(root_descriptor, Path("draft.json"))
        catalog = _load_resource("quality-rules.json")
        contract = _load_resource("evidence-contract.json")
        issues = validate_inputs(
            root,
            charter,
            ledger,
            draft,
            catalog=catalog,
            contract=contract,
            root_descriptor=root_descriptor,
        )
        if issues:
            raise FinalizerError(
                "invalid-input",
                "inputs",
                "evidence inputs are invalid",
                issues=issues,
            )
        try:
            os.stat("terminal", dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FinalizerError(
                "terminal-exists",
                str(root / "terminal"),
                "terminal evidence is immutable and cannot be overwritten",
            )
        bundle, snapshots = compile_bundle(
            root,
            charter,
            ledger,
            draft,
            _utc_now(),
            root_descriptor=root_descriptor,
        )
        receipt = compile_receipt(bundle)
        _raise_issues(
            validate_compiled_contract(bundle, receipt, contract),
            "compiled.contract",
            "compiled evidence violates its accepted contract",
        )
        bundle_path, receipt_path = publish_atomically(
            root,
            bundle,
            receipt,
            snapshots,
            root_descriptor=root_descriptor,
            contract=contract,
        )
        payload = {
            "bundle_digest": bundle["bundle_digest"],
            "bundle_path": str(bundle_path),
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "result": bundle["result"],
            "summary": draft["summary"],
        }
        sys.stdout.write(
            "terminal_preflight=PASS\n"
            + canonical_json(payload).decode("utf-8")
            + "\n"
        )
        return 0
    except FinalizerError as error:
        _write_error(error)
        return 1
    except (OSError, ValueError) as error:
        _write_error(
            FinalizerError(
                "filesystem-error",
                "finalizer",
                f"filesystem operation failed with {type(error).__name__}",
            )
        )
        return 1
    except Exception:
        _write_error(
            FinalizerError(
                "internal-error",
                "finalizer",
                "finalizer failed without exposing an unsafe traceback",
            )
        )
        return 1
    finally:
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
