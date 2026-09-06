#!/usr/bin/env python3
"""Private durable state for one Skill Builder run."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import fcntl
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunStateError(RuntimeError):
    """A durable-state contract or safety check failed."""


class RevisionConflict(RunStateError):
    """The expected receipt sequence is stale."""


RECEIPT_SCHEMA = "skill-builder-transition.v1"
INDEX_SCHEMA = "skill-builder-index.v1"
ENVELOPE_SCHEMA = "skill-builder-artifact-envelope.v1"
MANIFEST_SCHEMA = "skill-builder-raw-manifest.v1"
LEGACY_RESOLUTION_SCHEMA = "skill-builder-resolution.v1"
PREVIOUS_RESOLUTION_SCHEMA = "skill-builder-resolution.v2"
RESOLUTION_SCHEMA = "skill-builder-resolution.v3"
LEGACY_CANDIDATE_SCHEMA = "skill-builder-candidate.v1"
PREVIOUS_CANDIDATE_SCHEMA = "skill-builder-candidate.v2"
CANDIDATE_SCHEMA = "skill-builder-candidate.v3"
LEGACY_SCORECARD_SCHEMA = "skill-builder-scorecard.v1"
SCORECARD_SCHEMA = "skill-builder-scorecard.v2"
MAX_ARTIFACT_ITEMS = 256
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_CLI_JSON_BYTES = 4 * ((MAX_ARTIFACT_BYTES + 2) // 3) + 64 * 1024
MAX_TARGET_ITEMS = 10_000
MAX_TARGET_BYTES = 64 * 1024 * 1024
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_WORKFLOW_RE = re.compile(r"[0-9a-f]{32}\Z")
_ARTIFACT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ARTIFACT_TYPES = {
    "resolution-record",
    "baseline-report",
    "research-pack",
    "evidence-sieve",
    "design-record",
    "skill-contract",
    "user-confirmation-record",
    "evaluation-pack",
    "candidate-record",
    "trial-pack",
    "builder-run-conformance-ledger",
    "review-record",
    "target-scorecard",
    "verification-record",
    "release-record",
    "delivery-acceptance-record",
    "cleanup-authority-record",
    "final-run-manifest",
    "invalidation-record",
}
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_STAGES = (
    "resolved",
    "baseline",
    "research",
    "sieve",
    "design",
    "contract",
    "confirmed",
    "evaluation",
    "candidate",
    "trials",
    "reviewed",
    "scored",
    "verified",
    "finalized",
    "delivered",
    "abandoned",
)
_STAGE_TRANSITIONS = {
    "capture-baseline": ("resolved", "baseline"),
    "complete-research": ("baseline", "research"),
    "sieve-evidence": ("research", "sieve"),
    "accept-design": ("sieve", "design"),
    "accept-contract": ("design", "contract"),
    "confirm-contract": ("contract", "confirmed"),
    "freeze-evaluation": ("confirmed", "evaluation"),
    "accept-candidate": ("evaluation", "candidate"),
    "complete-trials": ("candidate", "trials"),
    "accept-review": ("trials", "reviewed"),
    "accept-scores": ("reviewed", "scored"),
    "accept-verification": ("scored", "verified"),
}
_EVENT_ARTIFACT_TYPES = {
    "capture-baseline": ("baseline-report",),
    "complete-research": ("research-pack",),
    "sieve-evidence": ("evidence-sieve",),
    "accept-design": ("design-record",),
    "accept-contract": ("skill-contract",),
    "confirm-contract": ("user-confirmation-record",),
    "freeze-evaluation": ("evaluation-pack",),
    "accept-candidate": ("candidate-record",),
    "complete-trials": ("trial-pack",),
    "accept-review": ("review-record",),
    "accept-scores": ("builder-run-conformance-ledger", "target-scorecard"),
    "accept-verification": ("verification-record",),
}

_PAYLOAD_SCHEMA_VERSIONS = {
    "baseline-report": "skill-builder-baseline.v1",
    "research-pack": "skill-builder-research-pack.v1",
    "evidence-sieve": "skill-builder-evidence-sieve.v1",
    "design-record": "skill-builder-design.v1",
    "skill-contract": "skill-builder-contract.v1",
    "user-confirmation-record": "skill-builder-user-confirmation.v1",
    "evaluation-pack": "skill-builder-evaluation-pack.v1",
    "candidate-record": {
        LEGACY_CANDIDATE_SCHEMA,
        PREVIOUS_CANDIDATE_SCHEMA,
        CANDIDATE_SCHEMA,
    },
    "trial-pack": "skill-builder-trial-pack.v1",
    "builder-run-conformance-ledger": "skill-builder-conformance.v1",
    "review-record": "skill-builder-review.v1",
    "target-scorecard": {LEGACY_SCORECARD_SCHEMA, SCORECARD_SCHEMA},
    "verification-record": "skill-builder-verification.v1",
    "release-record": "skill-builder-release.v1",
    "delivery-acceptance-record": "skill-builder-delivery-acceptance.v1",
    "cleanup-authority-record": "skill-builder-cleanup-authority.v1",
    "invalidation-record": "skill-builder-invalidation.v1",
}

_PAYLOAD_REQUIRED_FIELDS = {
    "baseline-report": {
        "schema_version",
        "mode",
        "target_snapshot_digest",
        "absent_target_proof",
        "host_conventions",
        "preserved_regressions",
        "raw_evidence_digests",
        "limitations",
    },
    "research-pack": {
        "schema_version",
        "target_snapshot_digest",
        "baseline_digest",
        "lanes",
        "limitations",
    },
    "evidence-sieve": {
        "schema_version",
        "research_digest",
        "decisions",
        "conflicts",
        "retained_dissent",
        "design_relevance",
        "limitations",
    },
    "design-record": {
        "schema_version",
        "research_digest",
        "sieve_digest",
        "alternatives",
        "mechanisms",
        "tradeoffs",
        "challenges",
        "evidence_links",
        "factual_conclusions",
        "user_owned_questions",
        "user_decisions",
        "rejected_alternatives",
        "unresolved_issues",
    },
    "skill-contract": {
        "schema_version",
        "design_digest",
        "purpose",
        "success_signal",
        "triggers",
        "non_triggers",
        "inputs_preconditions",
        "ordered_behavior",
        "allowed_actions",
        "forbidden_actions",
        "tools",
        "permissions",
        "delegation",
        "outputs_consumers",
        "failures",
        "changed_goals",
        "stopping_conditions",
        "resources",
        "non_goals",
        "evidence_bindings",
    },
    "user-confirmation-record": {
        "schema_version",
        "user_identity",
        "confirmed_at",
        "confirmation_text",
        "confirmation_event_digest",
        "target_identity",
        "contract_artifact_id",
        "contract_digest",
        "target_snapshot_digest",
        "accepted",
    },
    "evaluation-pack": {
        "schema_version",
        "frozen",
        "contract_digest",
        "confirmation_digest",
        "target_snapshot_digest",
        "rubric_digest",
        "scoring_parameters",
        "freeze_timestamp",
        "partitions",
    },
    "candidate-record": {
        "schema_version",
        "candidate_id",
        "isolated_locator",
        "base_snapshot_digest",
        "candidate_revision",
        "resulting_digest",
        "writable_role",
        "owned_paths",
        "diff_digest",
        "local_check_evidence",
        "contract_digest",
        "confirmation_digest",
        "evaluation_digest",
        "target_snapshot_digest",
    },
    "trial-pack": {
        "schema_version",
        "candidate_digest",
        "candidate_revision",
        "cases",
        "coverage",
        "isolation_evidence",
        "leakage_checks",
        "aggregate_manifest_digest",
        "status",
        "limitations",
    },
    "builder-run-conformance-ledger": {
        "schema_version",
        "candidate_digest",
        "gates",
    },
    "review-record": {
        "schema_version",
        "reviewer_identity",
        "independent",
        "read_only",
        "candidate_digest",
        "candidate_revision",
        "input_artifacts",
        "access_check_evidence",
        "fresh",
        "contamination_check",
        "valid",
        "findings",
        "verdict",
        "reviewed_at",
    },
    "target-scorecard": {
        "schema_version",
        "candidate_digest",
        "candidate_revision",
        "rubric_digest",
        "evaluation_digest",
        "review_digest",
        "categories",
    },
    "verification-record": {
        "schema_version",
        "verifier_identity",
        "independent",
        "read_only",
        "candidate_digest",
        "candidate_revision",
        "commands",
        "started_at",
        "ended_at",
        "before_manifest_digest",
        "after_manifest_digest",
        "fresh",
        "status",
        "conclusion",
    },
    "release-record": {
        "schema_version",
        "target_identity",
        "candidate_digest",
        "candidate_revision",
        "contract_digest",
        "confirmation_digest",
        "evaluation_digest",
        "conformance_digest",
        "scorecard_digest",
        "review_digest",
        "verification_digest",
        "retained_limitations",
        "release_evidence_manifest_digest",
        "authorized_delivery_scope",
    },
    "delivery-acceptance-record": {
        "schema_version",
        "action",
        "destination_identity",
        "finalized_revision",
        "resulting_destination_digest",
        "acceptance_evidence",
        "user_authority_event_digest",
        "actor",
        "accepted",
        "candidate_digest",
        "release_digest",
        "timestamp",
    },
    "cleanup-authority-record": {
        "schema_version",
        "workflow_id",
        "target_identity",
        "action",
        "authorized",
        "authority_event_digest",
        "actor",
        "timestamp",
        "accepted_delivery_digest",
    },
    "invalidation-record": {
        "schema_version",
        "change_kind",
        "changed_artifact_id",
        "changed_artifact_digest",
        "reason",
        "invalidated_artifact_ids",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _thread_lock(path: Path) -> threading.RLock:
    key = os.fspath(path)
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise RunStateError("structured records require finite integer JSON values")


def canonical_json_bytes(value: dict[str, Any], digest_field: str | None = None) -> bytes:
    """Return the artifact contract's exact canonical UTF-8 JSON bytes."""
    if not isinstance(value, dict):
        raise RunStateError("canonical durable records must be objects")
    unsigned = dict(value)
    if digest_field is not None:
        unsigned.pop(digest_field, None)
    _json_value(unsigned)
    try:
        encoded = json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise RunStateError("record is not canonical JSON-compatible") from error
    return encoded + b"\n"


def canonical_digest(value: dict[str, Any], digest_field: str | None = None) -> str:
    """Digest one structured record after top-level digest-field exclusion."""
    return hashlib.sha256(canonical_json_bytes(value, digest_field)).hexdigest()


def raw_digest(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise RunStateError("raw digests require exact bytes")
    return hashlib.sha256(payload).hexdigest()


def default_state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "codex-dev-flow" / "skill-builder"


def _absolute_root(state_root: Path | None) -> Path:
    candidate = Path(state_root) if state_root is not None else default_state_root()
    if not candidate.is_absolute() or ".." in candidate.parts or "\x00" in os.fspath(candidate):
        raise RunStateError("state root must be an absolute path without traversal")
    normalized = Path(os.path.normpath(os.fspath(candidate)))
    if normalized == Path(normalized.anchor):
        raise RunStateError("state root is overly broad")
    return normalized


def _validate_state_root_location(
    state_root: Path | None,
    target_locator: Path,
    git_identity: dict[str, Any],
) -> None:
    root = _absolute_root(state_root)
    _reject_symlink_components(root)
    boundaries = [target_locator]
    if git_identity["present"] is True:
        boundaries.append(Path(git_identity["repository"]))
    for boundary in boundaries:
        if root == boundary or root in boundary.parents or boundary in root.parents:
            raise RunStateError(
                "state root must be outside the target and target repository"
            )


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise RunStateError(f"symlink in trusted path: {current}")


def _validate_private_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise RunStateError(f"missing private {label}") from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RunStateError(f"{label} must be owned by the current user and mode 0700")


def _validate_regular(path: Path, label: str) -> os.stat_result:
    try:
        lexical = path.lstat()
    except OSError as error:
        raise RunStateError(f"missing {label}") from error
    if (
        not stat.S_ISREG(lexical.st_mode)
        or lexical.st_uid != os.geteuid()
        or stat.S_IMODE(lexical.st_mode) != 0o600
        or lexical.st_nlink != 1
    ):
        raise RunStateError(f"{label} must be a private, unlinked regular file")
    return lexical


def _ensure_private_directory(path: Path) -> None:
    _reject_symlink_components(path)
    if os.path.lexists(path):
        _validate_private_directory(path, path.name or "state root")
        return
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        os.chmod(path, 0o700, follow_symlinks=False)
    except OSError as error:
        raise RunStateError("cannot secure private directory") from error
    _validate_private_directory(path, path.name or "state root")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    temporary = path.parent / f".tmp-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: int | None = None
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, path, follow_symlinks=False)
        published = True
        _fsync_directory(path.parent)
        temporary.unlink()
        _fsync_directory(path.parent)
        _validate_regular(path, path.name)
    except FileExistsError as error:
        raise RunStateError(f"immutable entry already exists: {path.name}") from error
    except OSError as error:
        raise RunStateError(f"cannot publish immutable entry: {path.name}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        if published and not path.exists():
            raise RunStateError("immutable publication was lost")


def _exclusive_json(path: Path, value: dict[str, Any]) -> None:
    _exclusive_bytes(path, canonical_json_bytes(value))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = canonical_json_bytes(value)
    temporary = path.parent / f".current-{os.getpid()}-{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        _fsync_directory(path.parent)
        if _read_json(path) != value:
            raise RunStateError("atomic record read-back mismatch")
    except OSError as error:
        raise RunStateError(f"cannot atomically write {path.name}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _read_bytes(path: Path, maximum: int = MAX_JSON_BYTES) -> bytes:
    lexical = _validate_regular(path, path.name)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise RunStateError(f"cannot safely open {path.name}") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino):
            raise RunStateError(f"{path.name} was substituted")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > maximum:
            raise RunStateError(f"{path.name} is oversized")
        after = path.lstat()
        if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise RunStateError(f"{path.name} changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    payload = _read_bytes(path)
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeError, ValueError) as error:
        raise RunStateError(f"malformed JSON record: {path.name}") from error
    if not isinstance(value, dict):
        raise RunStateError(f"{path.name} must contain an object")
    _json_value(value)
    if payload != canonical_json_bytes(value):
        raise RunStateError(f"{path.name} is not exact canonical JSON encoding")
    return value


def _decode_canonical_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise RunStateError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise RunStateError(f"{label} must be a JSON object")
    _json_value(value)
    if payload != canonical_json_bytes(value):
        raise RunStateError(f"{label} is not exact canonical JSON encoding")
    return value


@contextmanager
def _locked_root(state_root: Path | None, *, create: bool) -> Any:
    root = _absolute_root(state_root)
    if create:
        _ensure_private_directory(root)
    else:
        _reject_symlink_components(root)
        _validate_private_directory(root, "state root")
    for name in ("live", "tombstones", "target-locks", "deleting"):
        child = root / name
        if create or (name == "deleting" and not os.path.lexists(child)):
            _ensure_private_directory(child)
        else:
            _validate_private_directory(child, name)
    lock_path = root / ".lock"
    flags = os.O_RDWR | os.O_NOFOLLOW
    created = False
    try:
        descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(lock_path, flags)
        except OSError as error:
            raise RunStateError("unsafe process lock") from error
    except OSError as error:
        raise RunStateError("unsafe process lock") from error
    try:
        if created:
            os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        lexical = _validate_regular(lock_path, "process lock")
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino)
        ):
            raise RunStateError("unsafe process lock")
        with _thread_lock(root):
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield root
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _nonempty_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or any(
        not isinstance(key, str) for key in value
    ):
        raise RunStateError(f"{label} must be a nonempty object")
    _json_value(value)
    return value


def _exact_integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RunStateError(f"{label} must be an integer of at least {minimum}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RunStateError(f"{label} must be nonempty text")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise RunStateError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise RunStateError(f"{label} must be a list of nonempty text")
    return value


def _digest_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not _DIGEST_RE.fullmatch(item) for item in value
    ):
        raise RunStateError(f"{label} must be a list of SHA-256 digests")
    if len(value) != len(set(value)):
        raise RunStateError(f"{label} contains duplicate digests")
    return value


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RunStateError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise RunStateError(f"{label} must be an RFC 3339 UTC timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise RunStateError(f"{label} must use UTC")
    return value


def _mapping_list(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RunStateError(f"{label} must be a list of objects")
    return value


def _validate_artifact_payload(artifact_type: str, payload: dict[str, Any]) -> None:
    """Apply the normative, versioned payload schema before accepting an artifact."""
    expected_version = _PAYLOAD_SCHEMA_VERSIONS.get(artifact_type)
    expected_fields = _PAYLOAD_REQUIRED_FIELDS.get(artifact_type)
    if expected_version is None or expected_fields is None:
        return
    expected_versions = (
        {expected_version} if isinstance(expected_version, str) else expected_version
    )
    schema_version = payload.get("schema_version")
    versioned_fields = set(expected_fields)
    if artifact_type == "candidate-record" and schema_version == CANDIDATE_SCHEMA:
        versioned_fields.add("loaded_skill_digest")
    if set(payload) != versioned_fields or schema_version not in expected_versions:
        raise RunStateError(f"{artifact_type} payload schema is invalid")

    if artifact_type == "baseline-report":
        if payload["mode"] not in {"create", "improve"}:
            raise RunStateError("baseline mode is invalid")
        _digest(payload["target_snapshot_digest"], "baseline target snapshot")
        if payload["mode"] == "create":
            proof = payload["absent_target_proof"]
            if not isinstance(proof, dict) or set(proof) != {
                "absence_evidence_digest",
                "overlap_map_digest",
            }:
                raise RunStateError("create baseline lacks exact absent-target proof")
            _digest(proof["absence_evidence_digest"], "absence evidence")
            _digest(proof["overlap_map_digest"], "overlap map")
        elif payload["absent_target_proof"] is not None:
            raise RunStateError("improve baseline must not claim absent-target proof")
        _text_list(payload["host_conventions"], "baseline host conventions")
        _text_list(payload["preserved_regressions"], "baseline regressions")
        _digest_list(payload["raw_evidence_digests"], "baseline raw evidence")
        _text_list(payload["limitations"], "baseline limitations")
        return

    if artifact_type == "research-pack":
        _digest(payload["target_snapshot_digest"], "research target snapshot")
        _digest(payload["baseline_digest"], "research baseline")
        lanes = _mapping_list(payload["lanes"], "research lanes")
        if len(lanes) != 3:
            raise RunStateError("research pack must contain exactly three lanes")
        lane_ids: set[str] = set()
        for lane in lanes:
            if set(lane) != {
                "lane_id",
                "question",
                "model",
                "reasoning",
                "source_scope",
                "evidence_budget",
                "start_state",
                "end_state",
                "limitations",
                "evidence_cards",
            }:
                raise RunStateError("research lane schema is invalid")
            lane_id = _text(lane["lane_id"], "research lane identity")
            if lane_id in lane_ids:
                raise RunStateError("research lane identities must be unique")
            lane_ids.add(lane_id)
            _text(lane["question"], "research lane question")
            if lane["model"] != "GPT-5.6-Luna" or lane["reasoning"] != "max":
                raise RunStateError("research lane model or reasoning is invalid")
            _text_list(lane["source_scope"], "research source scope")
            _exact_integer(lane["evidence_budget"], "research evidence budget", minimum=1)
            _text(lane["start_state"], "research lane start state")
            _text(lane["end_state"], "research lane end state")
            _text_list(lane["limitations"], "research lane limitations")
            cards = _mapping_list(lane["evidence_cards"], "research evidence cards")
            for card in cards:
                if set(card) != {
                    "card_id",
                    "claim",
                    "technique",
                    "direct_source",
                    "locator",
                    "applicable_situation",
                    "limitation",
                    "experiment",
                    "lane_id",
                    "raw_source_digest",
                }:
                    raise RunStateError("research evidence-card schema is invalid")
                for field in set(card) - {"raw_source_digest"}:
                    _text(card[field], f"evidence card {field}")
                if card["lane_id"] != lane_id:
                    raise RunStateError("evidence card lane binding is invalid")
                _digest(card["raw_source_digest"], "evidence-card raw source")
        _text_list(payload["limitations"], "research limitations")
        return

    if artifact_type == "evidence-sieve":
        _digest(payload["research_digest"], "sieve research")
        decisions = _mapping_list(payload["decisions"], "sieve decisions")
        seen: set[str] = set()
        for decision in decisions:
            if set(decision) != {
                "card_id",
                "decision",
                "reason",
                "deduplication_links",
            }:
                raise RunStateError("evidence-sieve decision schema is invalid")
            card_id = _text(decision["card_id"], "sieve card identity")
            if card_id in seen or decision["decision"] not in {
                "adopt",
                "experiment",
                "reject",
            }:
                raise RunStateError("evidence-sieve decision is duplicate or invalid")
            seen.add(card_id)
            _text(decision["reason"], "sieve decision reason")
            _text_list(decision["deduplication_links"], "sieve deduplication links")
        for field in ("conflicts", "retained_dissent", "design_relevance", "limitations"):
            _text_list(payload[field], f"sieve {field}")
        return

    if artifact_type == "design-record":
        _digest(payload["research_digest"], "design research")
        _digest(payload["sieve_digest"], "design sieve")
        for field in _PAYLOAD_REQUIRED_FIELDS[artifact_type] - {
            "schema_version",
            "research_digest",
            "sieve_digest",
        }:
            _text_list(payload[field], f"design {field}")
        return

    if artifact_type == "skill-contract":
        _digest(payload["design_digest"], "contract design")
        _text(payload["purpose"], "contract purpose")
        _text(payload["success_signal"], "contract success signal")
        for field in _PAYLOAD_REQUIRED_FIELDS[artifact_type] - {
            "schema_version",
            "design_digest",
            "purpose",
            "success_signal",
            "evidence_bindings",
        }:
            _text_list(payload[field], f"contract {field}")
        bindings = _mapping_list(payload["evidence_bindings"], "contract evidence bindings")
        for binding in bindings:
            if set(binding) != {"statement", "artifact_id", "digest"}:
                raise RunStateError("contract evidence binding schema is invalid")
            _text(binding["statement"], "contract evidence statement")
            _text(binding["artifact_id"], "contract evidence artifact")
            _digest(binding["digest"], "contract evidence digest")
        return

    if artifact_type == "user-confirmation-record":
        _text(payload["user_identity"], "confirmation user identity")
        _timestamp(payload["confirmed_at"], "confirmation timestamp")
        _text(payload["confirmation_text"], "confirmation text")
        _digest(payload["confirmation_event_digest"], "confirmation event")
        _text(payload["target_identity"], "confirmation target")
        _text(payload["contract_artifact_id"], "confirmation contract artifact")
        _digest(payload["contract_digest"], "confirmation contract")
        _digest(payload["target_snapshot_digest"], "confirmation target snapshot")
        if payload["accepted"] is not True:
            raise RunStateError("user confirmation is not accepted")
        return

    if artifact_type == "evaluation-pack":
        if payload["frozen"] is not True:
            raise RunStateError("evaluation pack is not frozen")
        for field in (
            "contract_digest",
            "confirmation_digest",
            "target_snapshot_digest",
            "rubric_digest",
        ):
            _digest(payload[field], f"evaluation {field}")
        if not isinstance(payload["scoring_parameters"], dict) or not payload["scoring_parameters"]:
            raise RunStateError("evaluation scoring parameters are incomplete")
        _timestamp(payload["freeze_timestamp"], "evaluation freeze timestamp")
        partitions = payload["partitions"]
        if not isinstance(partitions, dict) or set(partitions) != {
            "visible_development",
            "frozen_validation",
            "hidden_release",
        }:
            raise RunStateError("evaluation case partitions are invalid")
        case_ids: set[str] = set()
        for partition, cases in partitions.items():
            for case in _mapping_list(cases, f"evaluation {partition} cases"):
                if set(case) != {
                    "case_id",
                    "partition",
                    "purpose",
                    "raw_request_digest",
                    "allowed_context",
                    "setup_manifest_digest",
                    "observable_assertions",
                    "forbidden_effects",
                    "evidence_requirements",
                    "pass_fail_rule",
                }:
                    raise RunStateError("evaluation case schema is invalid")
                case_id = _text(case["case_id"], "evaluation case identity")
                if case_id in case_ids or case["partition"] != partition:
                    raise RunStateError("evaluation case identity or partition is invalid")
                case_ids.add(case_id)
                _text(case["purpose"], "evaluation case purpose")
                _digest(case["raw_request_digest"], "evaluation raw request")
                _digest(case["setup_manifest_digest"], "evaluation setup manifest")
                for field in (
                    "allowed_context",
                    "observable_assertions",
                    "forbidden_effects",
                    "evidence_requirements",
                ):
                    _text_list(case[field], f"evaluation case {field}")
                _text(case["pass_fail_rule"], "evaluation pass/fail rule")
        if not case_ids:
            raise RunStateError("evaluation pack has no frozen cases")
        return

    if artifact_type == "candidate-record":
        for field in ("candidate_id", "isolated_locator", "candidate_revision", "writable_role"):
            _text(payload[field], f"candidate {field}")
        locator = Path(payload["isolated_locator"])
        if not locator.is_absolute() or ".." in locator.parts:
            raise RunStateError("candidate isolated locator must be absolute")
        for field in (
            "base_snapshot_digest",
            "resulting_digest",
            "diff_digest",
            "contract_digest",
            "confirmation_digest",
            "evaluation_digest",
            "target_snapshot_digest",
        ):
            _digest(payload[field], f"candidate {field}")
        if payload["schema_version"] == CANDIDATE_SCHEMA:
            _digest(payload["loaded_skill_digest"], "candidate loaded skill")
            if payload["loaded_skill_digest"] == payload["resulting_digest"]:
                raise RunStateError(
                    "candidate loaded-skill content cannot be its owned-result descriptor"
                )
        _text_list(payload["owned_paths"], "candidate owned paths")
        _digest_list(payload["local_check_evidence"], "candidate local checks")
        return

    if artifact_type == "trial-pack":
        _digest(payload["candidate_digest"], "trial candidate")
        _text(payload["candidate_revision"], "trial candidate revision")
        cases = _mapping_list(payload["cases"], "trial cases")
        if not cases:
            raise RunStateError("trial pack has no cases")
        case_fields = {
            "case_id",
            "case_digest",
            "request_digest",
            "raw_prompt_digest",
            "loaded_skill_digest",
            "fresh_context_identity",
            "tool_event_digest",
            "output_digest",
            "before_target_manifest_digest",
            "after_target_manifest_digest",
            "filesystem_result_digest",
            "verdict",
            "limitations",
        }
        case_ids: list[str] = []
        context_ids: set[str] = set()
        for case in cases:
            if set(case) != case_fields:
                raise RunStateError("trial case schema is invalid")
            case_id = _text(case["case_id"], "trial case identity")
            context_id = _text(
                case["fresh_context_identity"], "trial fresh context identity"
            )
            if case_id in case_ids or context_id in context_ids:
                raise RunStateError("trial case or fresh context identity is duplicated")
            case_ids.append(case_id)
            context_ids.add(context_id)
            for field in (
                "case_digest",
                "request_digest",
                "raw_prompt_digest",
                "loaded_skill_digest",
                "tool_event_digest",
                "output_digest",
                "before_target_manifest_digest",
                "after_target_manifest_digest",
                "filesystem_result_digest",
            ):
                _digest(case[field], f"trial case {field}")
            if case["verdict"] not in {"pass", "fail"}:
                raise RunStateError("trial case verdict is invalid")
            _text_list(case["limitations"], "trial case limitations")
        for field in ("coverage", "isolation_evidence", "leakage_checks"):
            _text_list(payload[field], f"trial {field}")
        if payload["coverage"] != case_ids:
            raise RunStateError("trial case coverage is incomplete or out of order")
        _digest(payload["aggregate_manifest_digest"], "trial aggregate manifest")
        if payload["status"] not in {"pass", "fail"}:
            raise RunStateError("trial status is invalid")
        expected_status = (
            "pass" if all(case["verdict"] == "pass" for case in cases) else "fail"
        )
        if payload["status"] != expected_status:
            raise RunStateError("trial aggregate status contradicts case verdicts")
        _text_list(payload["limitations"], "trial limitations")
        return

    if artifact_type == "builder-run-conformance-ledger":
        _digest(payload["candidate_digest"], "conformance candidate")
        gates = payload["gates"]
        if not isinstance(gates, dict) or not gates:
            raise RunStateError("conformance gates are incomplete")
        for gate_id, gate in gates.items():
            _text(gate_id, "conformance gate identity")
            if not isinstance(gate, dict) or set(gate) != {
                "status",
                "evidence_digests",
                "affected_stage",
                "repair_state",
                "release_blocking",
            }:
                raise RunStateError("conformance gate schema is invalid")
            if gate["status"] not in {"pass", "fail"} or not isinstance(gate["release_blocking"], bool):
                raise RunStateError("conformance gate result is invalid")
            _digest_list(gate["evidence_digests"], "conformance gate evidence")
            _text(gate["affected_stage"], "conformance affected stage")
            _text(gate["repair_state"], "conformance repair state")
        return

    if artifact_type == "review-record":
        _text(payload["reviewer_identity"], "reviewer identity")
        _digest(payload["candidate_digest"], "review candidate")
        _text(payload["candidate_revision"], "review candidate revision")
        if payload["independent"] is not True or payload["read_only"] is not True:
            raise RunStateError("review independence attestation is invalid")
        if not isinstance(payload["input_artifacts"], dict) or not payload["input_artifacts"]:
            raise RunStateError("review input artifacts are incomplete")
        for artifact_id, digest in payload["input_artifacts"].items():
            _text(artifact_id, "review input artifact")
            _digest(digest, "review input artifact digest")
        _digest_list(payload["access_check_evidence"], "review access checks")
        _text(payload["contamination_check"], "review contamination check")
        _timestamp(payload["reviewed_at"], "review timestamp")
        if not isinstance(payload["fresh"], bool) or not isinstance(payload["valid"], bool):
            raise RunStateError("review freshness or validity is malformed")
        if payload["verdict"] not in {"ready", "not ready", None}:
            raise RunStateError("review verdict is invalid")
        if payload["valid"] is False and payload["verdict"] is not None:
            raise RunStateError("invalid review cannot carry a scoring verdict")
        for finding in _mapping_list(payload["findings"], "review findings"):
            if set(finding) != {
                "severity",
                "release_blocking",
                "evidence",
                "impact",
                "correction",
                "affected_target_criteria",
            }:
                raise RunStateError("review finding schema is invalid")
            if finding["severity"] not in {
                "critical",
                "important",
                "high",
                "medium",
                "low",
                "advisory",
            } or not isinstance(finding["release_blocking"], bool):
                raise RunStateError("review finding severity is invalid")
            _digest_list(finding["evidence"], "review finding evidence")
            _text(finding["impact"], "review finding impact")
            _text(finding["correction"], "review finding correction")
            _text_list(finding["affected_target_criteria"], "review finding criteria")
        return

    if artifact_type == "target-scorecard":
        for field in (
            "candidate_digest",
            "rubric_digest",
            "evaluation_digest",
            "review_digest",
        ):
            _digest(payload[field], f"scorecard {field}")
        _text(payload["candidate_revision"], "scorecard candidate revision")
        for category in _mapping_list(payload["categories"], "scorecard categories"):
            if payload["schema_version"] == LEGACY_SCORECARD_SCHEMA:
                if set(category) != {
                    "name",
                    "score",
                    "criteria",
                    "frozen_parameter_identifiers",
                    "evidence_identifiers",
                    "related_findings",
                    "repair_history",
                }:
                    raise RunStateError("scorecard category schema is invalid")
            elif set(category) != {"name", "score", "criteria", "repair_history"}:
                raise RunStateError("scorecard category schema is invalid")
            _text(category["name"], "scorecard category name")
            _exact_integer(category["score"], "scorecard score")
            criteria = category["criteria"]
            if not isinstance(criteria, dict) or not criteria:
                raise RunStateError("scorecard criteria are invalid")
            if payload["schema_version"] == LEGACY_SCORECARD_SCHEMA:
                if any(
                    not isinstance(key, str)
                    or not key
                    or not isinstance(value, bool)
                    for key, value in criteria.items()
                ):
                    raise RunStateError("scorecard criteria are invalid")
                for field in (
                    "frozen_parameter_identifiers",
                    "evidence_identifiers",
                    "related_findings",
                ):
                    _text_list(category[field], f"scorecard category {field}")
            else:
                criterion_fields = {
                    "passed",
                    "frozen_parameter_identifiers",
                    "case_ids",
                    "raw_artifact_digests",
                    "trial_receipt_ids",
                    "review_finding_ids",
                    "candidate_revision",
                    "review_artifact_id",
                    "review_digest",
                }
                for criterion_id, result in criteria.items():
                    _text(criterion_id, "scorecard criterion identity")
                    if not isinstance(result, dict) or set(result) != criterion_fields:
                        raise RunStateError("scorecard criterion-result schema is invalid")
                    if not isinstance(result["passed"], bool):
                        raise RunStateError("scorecard criterion result is invalid")
                    for field in ("frozen_parameter_identifiers", "case_ids"):
                        values = _text_list(
                            result[field], f"scorecard criterion {field}"
                        )
                        if values != sorted(values) or len(values) != len(set(values)):
                            raise RunStateError(
                                f"scorecard criterion {field} is not deterministic"
                            )
                    for field in (
                        "raw_artifact_digests",
                        "trial_receipt_ids",
                        "review_finding_ids",
                    ):
                        values = _digest_list(
                            result[field], f"scorecard criterion {field}"
                        )
                        if values != sorted(values):
                            raise RunStateError(
                                f"scorecard criterion {field} is not deterministic"
                            )
                    _text(result["candidate_revision"], "scorecard criterion candidate")
                    _text(result["review_artifact_id"], "scorecard criterion review")
                    _digest(result["review_digest"], "scorecard criterion review")
            _text_list(category["repair_history"], "scorecard category repair history")
        return

    if artifact_type == "verification-record":
        _text(payload["verifier_identity"], "verifier identity")
        _digest(payload["candidate_digest"], "verification candidate")
        _text(payload["candidate_revision"], "verification candidate revision")
        if payload["independent"] is not True or payload["read_only"] is not True:
            raise RunStateError("verification independence attestation is invalid")
        _timestamp(payload["started_at"], "verification start")
        _timestamp(payload["ended_at"], "verification end")
        _digest(payload["before_manifest_digest"], "verification before manifest")
        _digest(payload["after_manifest_digest"], "verification after manifest")
        if not isinstance(payload["fresh"], bool) or payload["status"] not in {"pass", "fail"}:
            raise RunStateError("verification freshness or status is invalid")
        _text(payload["conclusion"], "verification conclusion")
        commands = _mapping_list(payload["commands"], "verification commands")
        if not commands:
            raise RunStateError("verification commands are missing")
        for command in commands:
            if set(command) != {"command", "exit_status", "output_digest"}:
                raise RunStateError("verification command schema is invalid")
            _text(command["command"], "verification command")
            _exact_integer(command["exit_status"], "verification exit status")
            _digest(command["output_digest"], "verification output")
        return

    if artifact_type == "release-record":
        _text(payload["target_identity"], "release target identity")
        _text(payload["candidate_revision"], "release candidate revision")
        for field in (
            "candidate_digest",
            "contract_digest",
            "confirmation_digest",
            "evaluation_digest",
            "conformance_digest",
            "scorecard_digest",
            "review_digest",
            "verification_digest",
            "release_evidence_manifest_digest",
        ):
            _digest(payload[field], f"release {field}")
        _text_list(payload["retained_limitations"], "release limitations")
        scopes = _text_list(payload["authorized_delivery_scope"], "release delivery scope")
        if len(scopes) != len(set(scopes)):
            raise RunStateError("release delivery scope contains duplicates")
        return

    if artifact_type == "delivery-acceptance-record":
        if payload["action"] not in {"installation", "integration"} or payload["accepted"] is not True:
            raise RunStateError("delivery acceptance result is invalid")
        for field in ("destination_identity", "finalized_revision", "actor"):
            _text(payload[field], f"delivery {field}")
        for field in (
            "resulting_destination_digest",
            "user_authority_event_digest",
            "candidate_digest",
            "release_digest",
        ):
            _digest(payload[field], f"delivery {field}")
        _digest_list(payload["acceptance_evidence"], "delivery acceptance evidence")
        _timestamp(payload["timestamp"], "delivery timestamp")
        return

    if artifact_type == "cleanup-authority-record":
        _text(payload["workflow_id"], "cleanup workflow")
        _text(payload["target_identity"], "cleanup target")
        if payload["action"] != "cleanup" or payload["authorized"] is not True:
            raise RunStateError("cleanup authority result is invalid")
        _digest(payload["authority_event_digest"], "cleanup authority event")
        _text(payload["actor"], "cleanup authority actor")
        _timestamp(payload["timestamp"], "cleanup authority timestamp")
        _digest(payload["accepted_delivery_digest"], "cleanup accepted delivery")
        return

    if artifact_type == "invalidation-record":
        _text(payload["change_kind"], "invalidation change kind")
        _text(payload["changed_artifact_id"], "invalidation changed artifact")
        _digest(payload["changed_artifact_digest"], "invalidation changed artifact")
        _text(payload["reason"], "invalidation reason")
        identifiers = _text_list(payload["invalidated_artifact_ids"], "invalidated artifacts")
        if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
            raise RunStateError("invalidated artifact identifiers are not deterministic")


def _identity_records(
    host_identity: object, target_identity: object, authority: object
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    host = _nonempty_mapping(host_identity, "host identity")
    target = _nonempty_mapping(target_identity, "target identity")
    granted = _nonempty_mapping(authority, "authority")
    if set(host) != {"kind", "canonical_id", "locator", "discovery_evidence"}:
        raise RunStateError("host identity schema is incomplete")
    if set(target) != {
        "requested",
        "canonical",
        "name",
        "invocation_token",
        "locator",
    }:
        raise RunStateError("target identity schema is incomplete")
    if set(granted) != {
        "reads",
        "writes",
        "delegation",
        "candidate_effects",
        "delivery_effects",
    }:
        raise RunStateError("authority schema is incomplete")
    if any(not isinstance(host[field], str) or not host[field] for field in ("kind", "canonical_id", "locator")):
        raise RunStateError("host identity contains empty text")
    if not isinstance(host["discovery_evidence"], list) or not host["discovery_evidence"]:
        raise RunStateError("host identity lacks discovery evidence")
    if any(not isinstance(target[field], str) or not target[field] for field in target):
        raise RunStateError("target identity contains empty text")
    for field in granted:
        if not isinstance(granted[field], list) or any(
            not isinstance(item, str) or not item for item in granted[field]
        ):
            raise RunStateError("authority entries must be lists of text")
    locator = Path(target["locator"])
    if not locator.is_absolute() or ".." in locator.parts or "\x00" in target["locator"]:
        raise RunStateError("target locator must be canonical and absolute")
    _reject_symlink_components(locator)
    return dict(host), dict(target), dict(granted)


def _validate_git_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("present") not in {True, False}:
        raise RunStateError("Git identity must declare presence")
    if value["present"] is False:
        if set(value) != {"present"}:
            raise RunStateError("non-Git identity must not invent Git values")
        return {"present": False}
    required = {
        "present",
        "repository",
        "branch",
        "commit",
        "dirty_state_digest",
    }
    if set(value) != required:
        raise RunStateError("Git identity schema is incomplete")
    if any(
        not isinstance(value[field], str) or not value[field]
        for field in {"repository", "commit", "dirty_state_digest"}
    ):
        raise RunStateError("Git identity contains empty values")
    if value["branch"] is not None and (
        not isinstance(value["branch"], str) or not value["branch"]
    ):
        raise RunStateError("Git branch identity must be nonempty text or null")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value["commit"]):
        raise RunStateError("Git commit identity is not full length")
    if not _DIGEST_RE.fullmatch(value["dirty_state_digest"]):
        raise RunStateError("Git dirty-state digest is invalid")
    repository = Path(value["repository"])
    if not repository.is_absolute():
        raise RunStateError("Git repository identity must be absolute")
    return dict(value)


def _absent_snapshot(
    target: dict[str, Any], absence_evidence: object, overlap_map: object
) -> dict[str, Any]:
    absence = _nonempty_mapping(absence_evidence, "absence evidence")
    overlap = _nonempty_mapping(overlap_map, "overlap map")
    locator = Path(target["locator"])
    if os.path.lexists(locator):
        raise RunStateError("create mode requires an absent exact target")
    snapshot: dict[str, Any] = {
        "exists": False,
        "snapshot_kind": "absent-target.v1",
        "manifest_digest": None,
        "content_digest": raw_digest(b"absent\n"),
        "capture_sequence": 0,
        "searched_identity_scope": [target["canonical"], target["locator"]],
        "overlap_map_digest": canonical_digest(overlap),
        "absence_evidence_digest": canonical_digest(absence),
    }
    snapshot["snapshot_digest"] = canonical_digest(snapshot, "snapshot_digest")
    return snapshot


def snapshot_target(target: Path) -> dict[str, Any]:
    """Capture a bounded, symlink-free manifest for one exact existing target."""
    locator = Path(target)
    if not locator.is_absolute():
        raise RunStateError("target snapshot locator must be absolute")
    _reject_symlink_components(locator)
    try:
        root_info = locator.lstat()
    except OSError as error:
        raise RunStateError("improve mode requires an existing exact target") from error
    if stat.S_ISLNK(root_info.st_mode) or not (
        stat.S_ISDIR(root_info.st_mode) or stat.S_ISREG(root_info.st_mode)
    ):
        raise RunStateError("target must be a regular file or directory")
    entries: list[dict[str, Any]] = []
    total = 0

    def same_entry(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            stat.S_IFMT(first.st_mode),
        ) == (
            second.st_dev,
            second.st_ino,
            stat.S_IFMT(second.st_mode),
        )

    def read_file(
        descriptor: int, before: os.stat_result, relative: str
    ) -> tuple[bytes, os.stat_result]:
        nonlocal total
        opened = os.fstat(descriptor)
        if not same_entry(before, opened) or not stat.S_ISREG(opened.st_mode):
            raise RunStateError("target snapshot entry was substituted")
        if opened.st_size < 0 or opened.st_size > MAX_TARGET_BYTES - total:
            raise RunStateError("target snapshot is oversized")
        chunks: list[bytes] = []
        remaining = MAX_TARGET_BYTES - total + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        total += len(payload)
        if total > MAX_TARGET_BYTES:
            raise RunStateError("target snapshot is oversized")
        after = os.fstat(descriptor)
        if (
            not same_entry(opened, after)
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or opened.st_ctime_ns != after.st_ctime_ns
            or len(payload) != after.st_size
        ):
            raise RunStateError(f"target snapshot file changed while reading: {relative}")
        return payload, after

    def add_entry() -> None:
        if len(entries) >= MAX_TARGET_ITEMS:
            raise RunStateError("target snapshot has too many items")

    def walk_directory(descriptor: int, prefix: str = "") -> None:
        try:
            names = sorted(entry.name for entry in os.scandir(descriptor))
        except OSError as error:
            raise RunStateError("cannot enumerate target snapshot") from error
        for name in names:
            if name in {"", ".", ".."} or "/" in name or "\x00" in name:
                raise RunStateError("target snapshot contains an unsafe name")
            relative = f"{prefix}/{name}" if prefix else name
            try:
                before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise RunStateError("target snapshot entry disappeared") from error
            add_entry()
            if stat.S_ISLNK(before.st_mode):
                raise RunStateError("target snapshot contains a symlink")
            if stat.S_ISDIR(before.st_mode):
                try:
                    child = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                except OSError as error:
                    raise RunStateError("target snapshot directory was substituted") from error
                try:
                    if not same_entry(before, os.fstat(child)):
                        raise RunStateError("target snapshot directory was substituted")
                    entries.append(
                        {
                            "path": relative,
                            "kind": "directory",
                            "mode": stat.S_IMODE(before.st_mode),
                            "byte_count": 0,
                            "digest": None,
                        }
                    )
                    walk_directory(child, relative)
                    after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if not same_entry(before, after):
                        raise RunStateError("target snapshot directory changed")
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(before.st_mode):
                raise RunStateError("target snapshot contains a non-regular entry")
            if before.st_size < 0 or before.st_size > MAX_TARGET_BYTES - total:
                raise RunStateError("target snapshot is oversized")
            try:
                child = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except OSError as error:
                raise RunStateError("target snapshot file was substituted") from error
            try:
                payload, opened = read_file(child, before, relative)
            finally:
                os.close(child)
            after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not same_entry(opened, after):
                raise RunStateError("target snapshot file changed")
            entries.append(
                {
                    "path": relative,
                    "kind": "file",
                    "mode": stat.S_IMODE(opened.st_mode),
                    "byte_count": len(payload),
                    "digest": raw_digest(payload),
                }
            )

    if stat.S_ISDIR(root_info.st_mode):
        try:
            descriptor = os.open(locator, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise RunStateError("target snapshot root was substituted") from error
        try:
            if not same_entry(root_info, os.fstat(descriptor)):
                raise RunStateError("target snapshot root was substituted")
            walk_directory(descriptor)
            if not same_entry(root_info, locator.lstat()):
                raise RunStateError("target snapshot root changed")
        finally:
            os.close(descriptor)
    else:
        if root_info.st_size < 0 or root_info.st_size > MAX_TARGET_BYTES:
            raise RunStateError("target snapshot is oversized")
        try:
            descriptor = os.open(locator, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as error:
            raise RunStateError("target snapshot root was substituted") from error
        try:
            add_entry()
            payload, opened = read_file(descriptor, root_info, ".")
        finally:
            os.close(descriptor)
        if not same_entry(opened, locator.lstat()):
            raise RunStateError("target snapshot root changed")
        entries.append(
            {
                "path": ".",
                "kind": "file",
                "mode": stat.S_IMODE(opened.st_mode),
                "byte_count": len(payload),
                "digest": raw_digest(payload),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": "skill-builder-target-manifest.v1",
        "target_locator": os.fspath(locator),
        "target_kind": "directory" if stat.S_ISDIR(root_info.st_mode) else "file",
        "target_mode": stat.S_IMODE(root_info.st_mode),
        "declared_bounds": {
            "max_items": MAX_TARGET_ITEMS,
            "max_bytes": MAX_TARGET_BYTES,
        },
        "observed_item_count": len(entries),
        "observed_byte_count": total,
        "entries": entries,
    }
    manifest["manifest_digest"] = canonical_digest(manifest, "manifest_digest")
    return manifest


def _existing_snapshot(
    target: dict[str, Any], supplied_manifest: object
) -> dict[str, Any]:
    if not isinstance(supplied_manifest, dict):
        raise RunStateError("improve mode requires an exact target manifest")
    actual = snapshot_target(Path(target["locator"]))
    if supplied_manifest != actual:
        raise RunStateError("supplied target manifest does not match the exact target")
    snapshot: dict[str, Any] = {
        "exists": True,
        "snapshot_kind": "existing-target.v1",
        "manifest_digest": actual["manifest_digest"],
        "content_digest": canonical_digest(actual, "manifest_digest"),
        "capture_sequence": 0,
        "manifest": actual,
    }
    snapshot["snapshot_digest"] = canonical_digest(snapshot, "snapshot_digest")
    return snapshot


def _git_output(path: Path, *arguments: str, binary: bool = False) -> str | bytes:
    process = subprocess.run(
        ["git", "-C", os.fspath(path), *arguments],
        capture_output=True,
        text=not binary,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode:
        message = process.stderr.decode(errors="replace") if binary else process.stderr
        raise RunStateError((message or "Git identity command failed").strip())
    return process.stdout


def _detect_git_identity(target: Path) -> dict[str, Any]:
    probe = target if target.is_dir() else target.parent
    process = subprocess.run(
        ["git", "-C", os.fspath(probe), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode:
        return {"present": False}
    repository = Path(process.stdout.strip()).resolve(strict=True)
    branch_process = subprocess.run(
        ["git", "-C", os.fspath(repository), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if branch_process.returncode not in {0, 1}:
        raise RunStateError(
            (branch_process.stderr or "Git branch identity command failed").strip()
        )
    branch = branch_process.stdout.strip() if branch_process.returncode == 0 else None
    commit = str(_git_output(repository, "rev-parse", "--verify", "HEAD")).strip()
    status_bytes = _git_output(
        repository, "status", "--porcelain=v1", "-z", "--untracked-files=all", binary=True
    )
    assert isinstance(status_bytes, bytes)
    return _validate_git_identity(
        {
            "present": True,
            "repository": os.fspath(repository),
            "branch": branch,
            "commit": commit,
            "dirty_state_digest": raw_digest(status_bytes),
        }
    )


def _artifact_directory(run: Path, artifact_id: str) -> Path:
    if not isinstance(artifact_id, str) or not _ARTIFACT_RE.fullmatch(artifact_id):
        raise RunStateError("invalid artifact identifier")
    return run / "artifacts" / artifact_id


def _create_resolution_artifact(
    run: Path,
    workflow_id: str,
    host: dict[str, Any],
    target: dict[str, Any],
    mode: dict[str, Any],
    authority: dict[str, Any],
    git_identity: dict[str, Any],
    target_snapshot: dict[str, Any],
    queue: dict[str, Any],
    active_lock: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    artifact = _artifact_directory(run, "resolution")
    _ensure_private_directory(artifact)
    raw = artifact / "raw"
    _ensure_private_directory(raw)
    payload_record = {
        "schema_version": RESOLUTION_SCHEMA,
        "workflow_id": workflow_id,
        "candidate_record_schema": CANDIDATE_SCHEMA,
        "scorecard_schema": SCORECARD_SCHEMA,
        "host_identity": host,
        "target_identity": target,
        "mode": mode,
        "authority": authority,
        "git_identity": git_identity,
        "target_snapshot": target_snapshot,
        "queue": queue,
        "active_target_lock": active_lock,
    }
    payload = canonical_json_bytes(payload_record)
    payload_path = raw / "payload.json"
    _exclusive_bytes(payload_path, payload)
    relative_payload = "artifacts/resolution/raw/payload.json"
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "workflow_id": workflow_id,
        "target_identity": target["canonical"],
        "collection_type": "resolution",
        "declared_bounds": {
            "max_items": MAX_ARTIFACT_ITEMS,
            "max_bytes": MAX_ARTIFACT_BYTES,
        },
        "observed_item_count": 1,
        "observed_byte_count": len(payload),
        "entries": [
            {
                "path": relative_payload,
                "media_kind": "application/json",
                "byte_count": len(payload),
                "digest": raw_digest(payload),
                "source_role": "main-agent",
                "retention_class": "terminal",
            }
        ],
        "overflow": {"truncated": False, "reason": None},
    }
    manifest["manifest_digest"] = canonical_digest(manifest, "manifest_digest")
    _exclusive_json(artifact / "manifest.json", manifest)
    envelope: dict[str, Any] = {
        "schema_version": ENVELOPE_SCHEMA,
        "artifact_id": "resolution",
        "artifact_type": "resolution-record",
        "workflow_id": workflow_id,
        "target_identity": target["canonical"],
        "mode": mode["name"],
        "created_stage": "resolved",
        "created_sequence": 0,
        "producer": "main-agent",
        "created_at": created_at,
        "input_bindings": [],
        "payload_path": relative_payload,
        "payload_digest": raw_digest(payload),
        "manifest_digest": manifest["manifest_digest"],
        "limitations": [],
    }
    envelope["envelope_digest"] = canonical_digest(envelope, "envelope_digest")
    _exclusive_json(artifact / "envelope.json", envelope)
    _fsync_directory(artifact)
    return envelope


def _safe_artifact_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise RunStateError("artifact path must be a safe relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RunStateError("artifact path escapes its collection")
    return value


def _create_artifact(
    *,
    run: Path,
    workflow_id: str,
    target_identity: str,
    mode: str,
    stage: str,
    sequence: int,
    artifact_id: str,
    artifact_type: str,
    files: dict[str, bytes],
    primary_path: str,
    producer: str,
    input_bindings: list[dict[str, str]],
    limitations: list[str],
) -> dict[str, Any]:
    if artifact_type not in _ARTIFACT_TYPES:
        raise RunStateError("unsupported artifact type")
    artifact = _artifact_directory(run, artifact_id)
    if os.path.lexists(artifact):
        raise RunStateError("immutable artifact already exists")
    if not isinstance(files, dict) or not files or len(files) > MAX_ARTIFACT_ITEMS:
        raise RunStateError("artifact collection has an invalid item count")
    normalized: list[tuple[str, bytes]] = []
    casefolded: set[str] = set()
    total = 0
    for name, payload in files.items():
        safe_name = _safe_artifact_path(name)
        if safe_name.casefold() in casefolded:
            raise RunStateError("artifact collection has an ambiguous duplicate path")
        if not isinstance(payload, bytes):
            raise RunStateError("artifact payloads must be exact bytes")
        casefolded.add(safe_name.casefold())
        total += len(payload)
        normalized.append((safe_name, payload))
    if total > MAX_ARTIFACT_BYTES:
        raise RunStateError("artifact collection exceeds its byte bound")
    primary = _safe_artifact_path(primary_path)
    if primary not in files:
        raise RunStateError("primary artifact payload is not retained")
    if artifact_type in _PAYLOAD_SCHEMA_VERSIONS:
        primary_record = _decode_canonical_object(
            files[primary], f"{artifact_type} primary payload"
        )
        _validate_artifact_payload(artifact_type, primary_record)
        if artifact_type == "candidate-record":
            resolution, _ = _resolution_payload(run, workflow_id)
            required_schema = resolution.get(
                "candidate_record_schema", LEGACY_CANDIDATE_SCHEMA
            )
            if primary_record["schema_version"] != required_schema:
                raise RunStateError(
                    "candidate schema does not match the run's versioned semantics"
                )
        if artifact_type == "target-scorecard":
            resolution, _ = _resolution_payload(run, workflow_id)
            required_schema = resolution.get(
                "scorecard_schema", LEGACY_SCORECARD_SCHEMA
            )
            if primary_record["schema_version"] != required_schema:
                raise RunStateError(
                    "scorecard schema does not match the run's versioned semantics"
                )
    if not isinstance(producer, str) or not producer.strip():
        raise RunStateError("artifact producer identity is required")
    if not isinstance(limitations, list) or any(
        not isinstance(item, str) or not item.strip() for item in limitations
    ):
        raise RunStateError("artifact limitations must be bounded text entries")
    if not isinstance(input_bindings, list):
        raise RunStateError("artifact input bindings must be a list")
    normalized_bindings: list[dict[str, str]] = []
    for binding in input_bindings:
        if not isinstance(binding, dict) or set(binding) != {"artifact_id", "digest"}:
            raise RunStateError("artifact input binding schema is invalid")
        if not _ARTIFACT_RE.fullmatch(binding.get("artifact_id", "")) or not _DIGEST_RE.fullmatch(binding.get("digest", "")):
            raise RunStateError("artifact input binding identity or digest is invalid")
        normalized_bindings.append(dict(binding))
    normalized_bindings.sort(key=lambda item: item["artifact_id"])
    if len({item["artifact_id"] for item in normalized_bindings}) != len(normalized_bindings):
        raise RunStateError("artifact input bindings contain duplicates")

    _ensure_private_directory(artifact)
    raw = artifact / "raw"
    _ensure_private_directory(raw)
    entries: list[dict[str, Any]] = []
    for relative, payload in sorted(normalized):
        destination = raw.joinpath(*relative.split("/"))
        _ensure_private_directory(destination.parent)
        _exclusive_bytes(destination, payload)
        entries.append(
            {
                "path": f"artifacts/{artifact_id}/raw/{relative}",
                "media_kind": "application/octet-stream",
                "byte_count": len(payload),
                "digest": raw_digest(payload),
                "source_role": producer,
                "retention_class": "bounded-evidence",
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "workflow_id": workflow_id,
        "target_identity": target_identity,
        "collection_type": artifact_type,
        "declared_bounds": {
            "max_items": MAX_ARTIFACT_ITEMS,
            "max_bytes": MAX_ARTIFACT_BYTES,
        },
        "observed_item_count": len(entries),
        "observed_byte_count": total,
        "entries": entries,
        "overflow": {"truncated": False, "reason": None},
    }
    manifest["manifest_digest"] = canonical_digest(manifest, "manifest_digest")
    _exclusive_json(artifact / "manifest.json", manifest)
    primary_relative = f"artifacts/{artifact_id}/raw/{primary}"
    envelope: dict[str, Any] = {
        "schema_version": ENVELOPE_SCHEMA,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "workflow_id": workflow_id,
        "target_identity": target_identity,
        "mode": mode,
        "created_stage": stage,
        "created_sequence": sequence,
        "producer": producer,
        "created_at": _now(),
        "input_bindings": normalized_bindings,
        "payload_path": primary_relative,
        "payload_digest": next(
            entry["digest"] for entry in entries if entry["path"] == primary_relative
        ),
        "manifest_digest": manifest["manifest_digest"],
        "limitations": list(limitations),
    }
    envelope["envelope_digest"] = canonical_digest(envelope, "envelope_digest")
    _exclusive_json(artifact / "envelope.json", envelope)
    _fsync_directory(artifact)
    validated, _ = _validate_envelope(run, artifact_id)
    if validated != envelope:
        raise RunStateError("artifact read-back mismatch")
    return envelope


_RECEIPT_FIELDS = {
    "schema_version",
    "workflow_id",
    "target_identity",
    "sequence",
    "prior_receipt_digest",
    "event",
    "source_stage",
    "destination_stage",
    "relevant_artifact_digests",
    "target_snapshot_digest",
    "authority_event_digest",
    "created_at",
    "receipt_digest",
}


def _new_receipt(
    *,
    workflow_id: str,
    target_identity: str,
    sequence: int,
    prior_receipt_digest: str | None,
    event: str,
    source_stage: str | None,
    destination_stage: str,
    relevant_artifact_digests: list[dict[str, str]],
    target_snapshot_digest: str,
    authority_event_digest: str | None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "workflow_id": workflow_id,
        "target_identity": target_identity,
        "sequence": sequence,
        "prior_receipt_digest": prior_receipt_digest,
        "event": event,
        "source_stage": source_stage,
        "destination_stage": destination_stage,
        "relevant_artifact_digests": sorted(
            relevant_artifact_digests, key=lambda item: (item["artifact_id"], item["status"])
        ),
        "target_snapshot_digest": target_snapshot_digest,
        "authority_event_digest": authority_event_digest,
        "created_at": _now(),
    }
    receipt["receipt_digest"] = canonical_digest(receipt, "receipt_digest")
    return receipt


def _write_receipt(run: Path, receipt: dict[str, Any]) -> Path:
    path = run / "receipts" / f"{receipt['sequence']:08d}.json"
    _exclusive_json(path, receipt)
    if _read_json(path) != receipt:
        raise RunStateError("immutable receipt read-back mismatch")
    return path


def _append_transaction(
    *,
    run: Path,
    current: dict[str, Any],
    event: str,
    destination_stage: str,
    authority_event_digest: str | None,
    existing_bindings: list[dict[str, str]],
    artifact_requests: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Publish artifacts and one receipt with the receipt as the commit point."""
    sequence = current["head_sequence"] + 1
    transaction_path = run / "transactions" / f"{sequence:08d}.json"
    artifact_ids = [request["artifact_id"] for request in artifact_requests]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise RunStateError("append transaction artifact identifiers are duplicated")
    transaction: dict[str, Any] = {
        "schema_version": "skill-builder-append-transaction.v1",
        "workflow_id": current["workflow_id"],
        "sequence": sequence,
        "prior_receipt_digest": current["head_transition_digest"],
        "event": event,
        "source_stage": current["stage"],
        "destination_stage": destination_stage,
        "artifact_ids": sorted(artifact_ids),
        "receipt": None,
    }
    _exclusive_json(transaction_path, transaction)
    created: dict[str, dict[str, Any]] = {}
    receipt_path = run / "receipts" / f"{sequence:08d}.json"
    try:
        for request in artifact_requests:
            parameters = dict(request)
            artifact_id = parameters.pop("artifact_id")
            envelope = _create_artifact(
                run=run,
                workflow_id=current["workflow_id"],
                target_identity=current["target_identity"]["canonical"],
                mode=current["mode"]["name"],
                stage=current["stage"],
                sequence=sequence,
                artifact_id=artifact_id,
                **parameters,
            )
            created[artifact_id] = envelope
        bindings = list(existing_bindings) + [
            {
                "artifact_id": artifact_id,
                "envelope_digest": envelope["envelope_digest"],
                "status": "accepted",
            }
            for artifact_id, envelope in created.items()
        ]
        receipt = _new_receipt(
            workflow_id=current["workflow_id"],
            target_identity=current["target_identity"]["canonical"],
            sequence=sequence,
            prior_receipt_digest=current["head_transition_digest"],
            event=event,
            source_stage=current["stage"],
            destination_stage=destination_stage,
            relevant_artifact_digests=bindings,
            target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_event_digest=authority_event_digest,
        )
        transaction["receipt"] = receipt
        _atomic_json(transaction_path, transaction)
        _write_receipt(run, receipt)
    except BaseException:
        if not os.path.lexists(receipt_path):
            for artifact_id in artifact_ids:
                artifact = _artifact_directory(run, artifact_id)
                if os.path.lexists(artifact) and not artifact.is_symlink():
                    _remove_owned_tree(artifact)
            if os.path.lexists(transaction_path) and not transaction_path.is_symlink():
                transaction_path.unlink()
                _fsync_directory(transaction_path.parent)
        raise
    # A durable receipt is append-only truth.  Index failure leaves the journal
    # in place for recovery; neither the receipt nor accepted artifacts roll back.
    derived = _derive_index(run)
    _atomic_json(run / "current.json", derived)
    transaction_path.unlink()
    _fsync_directory(transaction_path.parent)
    return derived, created, receipt


def _validate_envelope(run: Path, artifact_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _artifact_directory(run, artifact_id)
    _validate_private_directory(artifact, f"artifact {artifact_id}")
    _validate_private_directory(artifact / "raw", f"artifact {artifact_id} raw directory")
    if set(path.name for path in artifact.iterdir()) != {"raw", "manifest.json", "envelope.json"}:
        raise RunStateError("artifact contains unmanifested control files")
    envelope = _read_json(artifact / "envelope.json")
    expected_envelope = {
        "schema_version",
        "artifact_id",
        "artifact_type",
        "workflow_id",
        "target_identity",
        "mode",
        "created_stage",
        "created_sequence",
        "producer",
        "created_at",
        "input_bindings",
        "payload_path",
        "payload_digest",
        "manifest_digest",
        "limitations",
        "envelope_digest",
    }
    if set(envelope) != expected_envelope or envelope.get("schema_version") != ENVELOPE_SCHEMA:
        raise RunStateError("artifact envelope schema is invalid")
    if (
        envelope.get("artifact_id") != artifact_id
        or not _ARTIFACT_RE.fullmatch(artifact_id)
        or envelope.get("artifact_type") not in _ARTIFACT_TYPES
        or envelope.get("workflow_id") != run.name
        or envelope.get("mode") not in {"create", "improve"}
        or isinstance(envelope.get("created_sequence"), bool)
        or not isinstance(envelope.get("created_sequence"), int)
        or envelope["created_sequence"] < 0
        or envelope.get("created_stage") not in {*_STAGES, "paused"}
        or not isinstance(envelope.get("producer"), str)
        or not envelope["producer"].strip()
        or not isinstance(envelope.get("target_identity"), str)
        or not envelope["target_identity"].strip()
        or not isinstance(envelope.get("limitations"), list)
        or any(not isinstance(item, str) or not item.strip() for item in envelope["limitations"])
        or not isinstance(envelope.get("created_at"), str)
        or envelope.get("envelope_digest") != canonical_digest(envelope, "envelope_digest")
    ):
        raise RunStateError("artifact envelope digest or identity mismatch")
    _timestamp(envelope["created_at"], "artifact creation timestamp")
    input_bindings = envelope.get("input_bindings")
    if not isinstance(input_bindings, list) or input_bindings != sorted(
        input_bindings,
        key=lambda item: item.get("artifact_id", "") if isinstance(item, dict) else "",
    ):
        raise RunStateError("artifact envelope input bindings are invalid")
    for binding in input_bindings:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"artifact_id", "digest"}
            or not _ARTIFACT_RE.fullmatch(binding.get("artifact_id", ""))
            or not _DIGEST_RE.fullmatch(binding.get("digest", ""))
        ):
            raise RunStateError("artifact envelope input binding is malformed")
    if len({binding["artifact_id"] for binding in input_bindings}) != len(input_bindings):
        raise RunStateError("artifact envelope input bindings contain duplicates")
    manifest = _read_json(artifact / "manifest.json")
    manifest_fields = {
        "schema_version",
        "workflow_id",
        "target_identity",
        "collection_type",
        "declared_bounds",
        "observed_item_count",
        "observed_byte_count",
        "entries",
        "overflow",
        "manifest_digest",
    }
    if (
        set(manifest) != manifest_fields
        or manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("workflow_id") != envelope["workflow_id"]
        or manifest.get("target_identity") != envelope["target_identity"]
        or manifest.get("collection_type")
        not in {envelope["artifact_type"], "resolution"}
        or manifest.get("declared_bounds")
        != {"max_items": MAX_ARTIFACT_ITEMS, "max_bytes": MAX_ARTIFACT_BYTES}
        or manifest.get("overflow") != {"truncated": False, "reason": None}
        or manifest.get("manifest_digest")
        != canonical_digest(manifest, "manifest_digest")
    ):
        raise RunStateError("raw-artifact manifest digest mismatch")
    if manifest.get("manifest_digest") != envelope.get("manifest_digest"):
        raise RunStateError("artifact envelope manifest binding mismatch")
    entries = manifest.get("entries")
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_ARTIFACT_ITEMS
        or isinstance(manifest.get("observed_item_count"), bool)
        or not isinstance(manifest.get("observed_item_count"), int)
        or manifest["observed_item_count"] < 1
        or isinstance(manifest.get("observed_byte_count"), bool)
        or not isinstance(manifest.get("observed_byte_count"), int)
        or manifest["observed_byte_count"] < 0
    ):
        raise RunStateError("raw-artifact manifest has an invalid item count")
    listed: set[str] = set()
    casefolded: set[str] = set()
    total = 0
    prior_path: str | None = None
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "media_kind",
            "byte_count",
            "digest",
            "source_role",
            "retention_class",
        }:
            raise RunStateError("raw-artifact manifest entry is invalid")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative.startswith("/") or "\\" in relative:
            raise RunStateError("raw-artifact path is unsafe")
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise RunStateError("raw-artifact path escapes its collection")
        expected_prefix = f"artifacts/{artifact_id}/raw/"
        if not relative.startswith(expected_prefix):
            raise RunStateError("raw-artifact path is outside its collection")
        local_relative = relative.removeprefix(expected_prefix)
        if (
            relative == prior_path
            or local_relative.casefold() in casefolded
            or not isinstance(entry.get("media_kind"), str)
            or not entry["media_kind"].strip()
            or isinstance(entry.get("byte_count"), bool)
            or not isinstance(entry.get("byte_count"), int)
            or entry["byte_count"] < 0
            or not isinstance(entry.get("digest"), str)
            or not _DIGEST_RE.fullmatch(entry["digest"])
            or not isinstance(entry.get("source_role"), str)
            or not entry["source_role"].strip()
            or not isinstance(entry.get("retention_class"), str)
            or not entry["retention_class"].strip()
        ):
            raise RunStateError("raw-artifact manifest entry fields are invalid")
        if prior_path is not None and relative < prior_path:
            raise RunStateError("raw-artifact manifest entries are not deterministic")
        prior_path = relative
        casefolded.add(local_relative.casefold())
        local = run.joinpath(*parts)
        payload = _read_bytes(local, MAX_ARTIFACT_BYTES)
        if entry.get("byte_count") != len(payload) or entry.get("digest") != raw_digest(payload):
            raise RunStateError("raw-artifact payload digest mismatch")
        listed.add(local_relative)
        total += len(payload)
    actual: set[str] = set()
    for path in (artifact / "raw").rglob("*"):
        info = path.lstat()
        relative = path.relative_to(artifact / "raw").as_posix()
        if stat.S_ISDIR(info.st_mode):
            _validate_private_directory(path, f"raw directory {relative}")
        elif stat.S_ISREG(info.st_mode):
            actual.add(relative)
        else:
            raise RunStateError("raw-artifact collection contains an unsafe entry")
    if actual != listed or total != manifest.get("observed_byte_count") or total > MAX_ARTIFACT_BYTES:
        raise RunStateError("raw-artifact inventory is incomplete or oversized")
    if len(listed) != manifest.get("observed_item_count"):
        raise RunStateError("raw-artifact item count mismatch")
    payload_path = envelope.get("payload_path")
    if not isinstance(payload_path, str) or payload_path not in {entry["path"] for entry in entries}:
        raise RunStateError("artifact payload is not manifested")
    payload = _read_bytes(run.joinpath(*payload_path.split("/")), MAX_ARTIFACT_BYTES)
    if raw_digest(payload) != envelope.get("payload_digest"):
        raise RunStateError("artifact payload binding mismatch")
    return envelope, manifest


def _load_receipt_chain(run: Path) -> list[dict[str, Any]]:
    receipts_dir = run / "receipts"
    _validate_private_directory(receipts_dir, "receipt directory")
    names = sorted(path.name for path in receipts_dir.iterdir())
    if not names:
        raise RunStateError("run lacks a genesis receipt")
    expected_names = [f"{index:08d}.json" for index in range(len(names))]
    if names != expected_names:
        raise RunStateError("receipt chain contains a gap, fork, or unknown entry")
    chain: list[dict[str, Any]] = []
    prior: dict[str, Any] | None = None
    for sequence, name in enumerate(names):
        receipt = _read_json(receipts_dir / name)
        if set(receipt) != _RECEIPT_FIELDS or receipt.get("schema_version") != RECEIPT_SCHEMA:
            raise RunStateError("transition receipt schema is invalid")
        if (
            isinstance(receipt.get("sequence"), bool)
            or not isinstance(receipt.get("sequence"), int)
            or receipt["sequence"] != sequence
            or not isinstance(receipt.get("workflow_id"), str)
            or not _WORKFLOW_RE.fullmatch(receipt["workflow_id"])
            or not isinstance(receipt.get("target_identity"), str)
            or not receipt["target_identity"].strip()
            or not isinstance(receipt.get("event"), str)
            or not receipt["event"].strip()
            or not isinstance(receipt.get("destination_stage"), str)
            or not isinstance(receipt.get("target_snapshot_digest"), str)
            or not _DIGEST_RE.fullmatch(receipt["target_snapshot_digest"])
            or not isinstance(receipt.get("receipt_digest"), str)
            or not _DIGEST_RE.fullmatch(receipt["receipt_digest"])
            or receipt["receipt_digest"]
            != canonical_digest(receipt, "receipt_digest")
        ):
            raise RunStateError("transition receipt sequence or digest mismatch")
        _timestamp(receipt.get("created_at"), "transition receipt timestamp")
        if receipt.get("prior_receipt_digest") is not None:
            _digest(receipt["prior_receipt_digest"], "prior receipt")
        if receipt.get("authority_event_digest") is not None:
            _digest(receipt["authority_event_digest"], "receipt authority event")
        relevant = receipt.get("relevant_artifact_digests")
        if not isinstance(relevant, list):
            raise RunStateError("receipt artifact bindings must be a list")
        identities: set[str] = set()
        for item in relevant:
            if (
                not isinstance(item, dict)
                or set(item) != {"artifact_id", "envelope_digest", "status"}
                or not isinstance(item.get("artifact_id"), str)
                or not _ARTIFACT_RE.fullmatch(item["artifact_id"])
                or not isinstance(item.get("envelope_digest"), str)
                or not _DIGEST_RE.fullmatch(item["envelope_digest"])
                or item.get("status")
                not in {"accepted", "superseded", "invalidated"}
                or item["artifact_id"] in identities
            ):
                raise RunStateError("receipt artifact binding schema is invalid")
            identities.add(item["artifact_id"])
        if relevant != sorted(
            relevant, key=lambda item: (item["artifact_id"], item["status"])
        ):
            raise RunStateError("receipt artifact bindings are not deterministic")
        if sequence == 0:
            if (
                receipt.get("prior_receipt_digest") is not None
                or receipt.get("source_stage") is not None
                or receipt.get("event") != "initialize"
                or receipt.get("destination_stage") != "resolved"
                or receipt.get("authority_event_digest") is not None
                or len(relevant) != 1
                or relevant[0].get("artifact_id") != "resolution"
                or relevant[0].get("status") != "accepted"
            ):
                raise RunStateError("genesis receipt is invalid")
        else:
            assert prior is not None
            if receipt.get("workflow_id") != prior.get("workflow_id") or receipt.get("target_identity") != prior.get("target_identity"):
                raise RunStateError("receipt identity substitution")
            if receipt.get("prior_receipt_digest") != prior.get("receipt_digest") or receipt.get("source_stage") != prior.get("destination_stage"):
                raise RunStateError("receipt chain link or stage mismatch")
            event = receipt.get("event")
            source = receipt.get("source_stage")
            destination = receipt.get("destination_stage")
            if event == "retain-artifact":
                if source != destination or source not in _STAGES:
                    raise RunStateError("artifact retention changed the run stage")
            elif event in _STAGE_TRANSITIONS:
                if (source, destination) != _STAGE_TRANSITIONS[event]:
                    raise RunStateError("receipt contains an unsupported stage transition")
            elif event == "pause":
                if source not in _STAGES[:-3] or destination != "paused":
                    raise RunStateError("pause receipt is not valid from its source stage")
            elif event == "resume":
                if source != "paused" or destination != prior.get("source_stage"):
                    raise RunStateError("resume receipt does not restore the paused stage")
            elif event == "invalidate":
                if source not in _STAGES[:-3] or destination not in _STAGES:
                    raise RunStateError("invalidation receipt has an invalid stage")
                if destination != "abandoned" and _STAGES.index(destination) > _STAGES.index(source):
                    raise RunStateError("invalidation advanced rather than rewound state")
                statuses = [item["status"] for item in relevant]
                if statuses.count("accepted") != 1 or statuses.count("superseded") != 1:
                    raise RunStateError("invalidation receipt lacks causal artifact status")
            elif event == "finalize":
                if source != "verified" or destination != "finalized":
                    raise RunStateError("finalization receipt has an invalid stage")
            elif event == "record-delivery":
                if source != "finalized" or destination != "delivered" or not isinstance(receipt.get("authority_event_digest"), str):
                    raise RunStateError("delivery receipt has an invalid stage or authority")
            elif event == "record-cleanup-authority":
                if source != "delivered" or destination != "delivered" or not isinstance(receipt.get("authority_event_digest"), str):
                    raise RunStateError("cleanup-authority receipt is invalid")
            else:
                raise RunStateError("receipt event is unsupported")
        chain.append(receipt)
        prior = receipt
    return chain


def _resolution_payload(run: Path, workflow_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope, _ = _validate_envelope(run, "resolution")
    if envelope.get("workflow_id") != workflow_id:
        raise RunStateError("resolution artifact workflow mismatch")
    payload = _read_json(run / "artifacts" / "resolution" / "raw" / "payload.json")
    common_fields = {
        "schema_version",
        "workflow_id",
        "host_identity",
        "target_identity",
        "mode",
        "authority",
        "git_identity",
        "target_snapshot",
        "queue",
        "active_target_lock",
    }
    schema_version = payload.get("schema_version")
    if schema_version == LEGACY_RESOLUTION_SCHEMA:
        expected_fields = common_fields
        version_markers_valid = True
    elif schema_version == PREVIOUS_RESOLUTION_SCHEMA:
        expected_fields = common_fields | {"candidate_record_schema"}
        version_markers_valid = (
            payload.get("candidate_record_schema") == PREVIOUS_CANDIDATE_SCHEMA
        )
    else:
        expected_fields = common_fields | {
            "candidate_record_schema",
            "scorecard_schema",
        }
        version_markers_valid = (
            payload.get("candidate_record_schema") == CANDIDATE_SCHEMA
            and payload.get("scorecard_schema") == SCORECARD_SCHEMA
        )
    if (
        schema_version
        not in {
            LEGACY_RESOLUTION_SCHEMA,
            PREVIOUS_RESOLUTION_SCHEMA,
            RESOLUTION_SCHEMA,
        }
        or set(payload) != expected_fields
        or payload.get("workflow_id") != workflow_id
        or not version_markers_valid
    ):
        raise RunStateError("resolution payload identity mismatch")
    return payload, envelope


def _artifact_payload_json(run: Path, artifact_id: str) -> dict[str, Any]:
    envelope, _ = _validate_envelope(run, artifact_id)
    payload_path = envelope["payload_path"]
    payload = _read_json(run.joinpath(*payload_path.split("/")))
    _validate_artifact_payload(envelope["artifact_type"], payload)
    return payload


def _require_input_bindings(
    envelope: dict[str, Any],
    required: dict[str, str],
    label: str,
) -> None:
    observed = {
        binding["artifact_id"]: binding["digest"]
        for binding in envelope["input_bindings"]
    }
    if any(observed.get(artifact_id) != digest for artifact_id, digest in required.items()):
        raise RunStateError(f"{label} input binding is missing or stale")


def _validate_baseline_binding(
    run: Path,
    current: dict[str, Any],
    baseline_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    baseline_envelope, _ = _validate_envelope(run, baseline_id)
    baseline = _artifact_payload_json(run, baseline_id)
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    snapshot = current["target_snapshot"]
    if (
        baseline.get("mode") != current["mode"]["name"]
        or baseline.get("target_snapshot_digest") != snapshot["snapshot_digest"]
    ):
        raise RunStateError("baseline mode or target-snapshot binding is stale")
    if current["mode"]["name"] == "create":
        expected_proof = {
            "absence_evidence_digest": snapshot["absence_evidence_digest"],
            "overlap_map_digest": snapshot["overlap_map_digest"],
        }
        if baseline.get("absent_target_proof") != expected_proof:
            raise RunStateError("baseline absent-target proof binding is stale")
    elif baseline.get("absent_target_proof") is not None:
        raise RunStateError("improve baseline contains an absent-target proof")
    _require_input_bindings(
        baseline_envelope,
        {resolution_id: resolution_envelope["envelope_digest"]},
        "baseline",
    )
    _require_retained_raw_digests(
        run,
        baseline_id,
        list(baseline["raw_evidence_digests"]),
        "baseline",
    )


def _artifact_raw_digests(
    run: Path, artifact_id: str
) -> tuple[dict[str, Any], dict[str, str]]:
    envelope, manifest = _validate_envelope(run, artifact_id)
    prefix = f"artifacts/{artifact_id}/raw/"
    return envelope, {
        entry["path"].removeprefix(prefix): entry["digest"]
        for entry in manifest["entries"]
    }


def _validate_trial_binding(
    run: Path,
    current: dict[str, Any],
    trial_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    trial_envelope, raw_digests = _artifact_raw_digests(run, trial_id)
    trials = _artifact_payload_json(run, trial_id)
    candidate_id, candidate_envelope = _event_artifact(
        run, "accept-candidate", "candidate-record", event_artifacts
    )
    candidate = _artifact_payload_json(run, candidate_id)
    evaluation_id, evaluation_envelope = _event_artifact(
        run, "freeze-evaluation", "evaluation-pack", event_artifacts
    )
    evaluation = _artifact_payload_json(run, evaluation_id)
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    if (
        trials["candidate_digest"] != candidate_envelope["envelope_digest"]
        or trials["candidate_revision"] != candidate["candidate_revision"]
    ):
        raise RunStateError("trial candidate binding is stale or substituted")
    _require_input_bindings(
        trial_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            candidate_id: candidate_envelope["envelope_digest"],
            evaluation_id: evaluation_envelope["envelope_digest"],
        },
        "trial pack",
    )

    frozen_cases: list[dict[str, Any]] = []
    for partition in (
        "visible_development",
        "frozen_validation",
        "hidden_release",
    ):
        frozen_cases.extend(evaluation["partitions"][partition])
    trial_cases = trials["cases"]
    if [case["case_id"] for case in trial_cases] != [
        case["case_id"] for case in frozen_cases
    ]:
        raise RunStateError("trial coverage does not match the frozen evaluation cases")

    raw_fields = {
        "request_digest": "request",
        "raw_prompt_digest": "prompt",
        "loaded_skill_digest": "loaded-skill",
        "tool_event_digest": "tool-events",
        "output_digest": "output",
        "before_target_manifest_digest": "before-manifest",
        "after_target_manifest_digest": "after-manifest",
        "filesystem_result_digest": "filesystem-result",
    }
    expected_loaded_skill_digest = (
        candidate["loaded_skill_digest"]
        if candidate["schema_version"] == CANDIDATE_SCHEMA
        else candidate["resulting_digest"]
    )
    for trial_case, frozen_case in zip(trial_cases, frozen_cases, strict=True):
        case_id = trial_case["case_id"]
        if not _ARTIFACT_RE.fullmatch(case_id):
            raise RunStateError("trial case identity is unsafe")
        expected_case_digest = raw_digest(canonical_json_bytes(frozen_case))
        if (
            trial_case["case_digest"] != expected_case_digest
            or raw_digests.get(f"evidence/{case_id}/case.json")
            != expected_case_digest
            or trial_case["request_digest"] != frozen_case["raw_request_digest"]
            or trial_case["before_target_manifest_digest"]
            != frozen_case["setup_manifest_digest"]
        ):
            raise RunStateError("trial case binding is stale or substituted")
        if trial_case["loaded_skill_digest"] != expected_loaded_skill_digest:
            raise RunStateError(
                "trial loaded-skill content identity is stale or substituted"
            )
        for field, file_stem in raw_fields.items():
            if (
                raw_digests.get(f"evidence/{case_id}/{file_stem}.bin")
                != trial_case[field]
            ):
                raise RunStateError("trial raw evidence binding is missing or stale")
    if (
        raw_digests.get("evidence/aggregate-manifest.json")
        != trials["aggregate_manifest_digest"]
    ):
        raise RunStateError("trial aggregate manifest binding is missing or stale")


def _event_artifact(
    run: Path,
    event: str,
    artifact_type: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    if event_artifacts is not None:
        artifact_id = event_artifacts.get(event, {}).get(artifact_type)
        if artifact_id is None:
            raise RunStateError(f"current {artifact_type} binding is missing")
        envelope, _ = _validate_envelope(run, artifact_id)
        if envelope["artifact_type"] != artifact_type:
            raise RunStateError(f"current {artifact_type} binding is invalid")
        return artifact_id, envelope
    for receipt in reversed(_load_receipt_chain(run)):
        if receipt["event"] != event:
            continue
        for binding in receipt["relevant_artifact_digests"]:
            envelope, _ = _validate_envelope(run, binding["artifact_id"])
            if envelope["artifact_type"] == artifact_type and binding["status"] == "accepted":
                return binding["artifact_id"], envelope
    raise RunStateError(f"current {artifact_type} binding is missing")


def _require_retained_raw_digests(
    run: Path,
    artifact_id: str,
    claims: list[str],
    label: str,
) -> None:
    _, raw_digests = _artifact_raw_digests(run, artifact_id)
    retained = {
        digest for path, digest in raw_digests.items() if path != "record.json"
    }
    if any(digest not in retained for digest in claims):
        raise RunStateError(f"{label} lacks retained raw evidence")


def _validate_research_binding(
    run: Path,
    current: dict[str, Any],
    research_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    research_envelope, _ = _validate_envelope(run, research_id)
    research = _artifact_payload_json(run, research_id)
    baseline_id, baseline_envelope = _event_artifact(
        run, "capture-baseline", "baseline-report", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    if (
        research["target_snapshot_digest"]
        != current["target_snapshot"]["snapshot_digest"]
        or research["baseline_digest"] != baseline_envelope["envelope_digest"]
    ):
        raise RunStateError("research baseline or target-snapshot binding is stale")
    _require_input_bindings(
        research_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            baseline_id: baseline_envelope["envelope_digest"],
        },
        "research pack",
    )
    card_ids: set[str] = set()
    raw_sources: list[str] = []
    for lane in research["lanes"]:
        for card in lane["evidence_cards"]:
            if card["card_id"] in card_ids:
                raise RunStateError("research evidence-card identity is duplicated")
            card_ids.add(card["card_id"])
            raw_sources.append(card["raw_source_digest"])
    _require_retained_raw_digests(
        run, research_id, raw_sources, "research pack"
    )


def _validate_sieve_binding(
    run: Path,
    current: dict[str, Any],
    sieve_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    sieve_envelope, _ = _validate_envelope(run, sieve_id)
    sieve = _artifact_payload_json(run, sieve_id)
    research_id, research_envelope = _event_artifact(
        run, "complete-research", "research-pack", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    research = _artifact_payload_json(run, research_id)
    if sieve["research_digest"] != research_envelope["envelope_digest"]:
        raise RunStateError("evidence sieve research binding is stale")
    card_ids = [
        card["card_id"]
        for lane in research["lanes"]
        for card in lane["evidence_cards"]
    ]
    decision_ids = [decision["card_id"] for decision in sieve["decisions"]]
    if len(card_ids) != len(set(card_ids)) or sorted(decision_ids) != sorted(card_ids):
        raise RunStateError("evidence sieve does not decide every research card")
    _require_input_bindings(
        sieve_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            research_id: research_envelope["envelope_digest"],
        },
        "evidence sieve",
    )


def _validate_design_binding(
    run: Path,
    current: dict[str, Any],
    design_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    design_envelope, _ = _validate_envelope(run, design_id)
    design = _artifact_payload_json(run, design_id)
    research_id, research_envelope = _event_artifact(
        run, "complete-research", "research-pack", event_artifacts
    )
    sieve_id, sieve_envelope = _event_artifact(
        run, "sieve-evidence", "evidence-sieve", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    if (
        design["research_digest"] != research_envelope["envelope_digest"]
        or design["sieve_digest"] != sieve_envelope["envelope_digest"]
    ):
        raise RunStateError("design research or sieve binding is stale")
    _require_input_bindings(
        design_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            research_id: research_envelope["envelope_digest"],
            sieve_id: sieve_envelope["envelope_digest"],
        },
        "design record",
    )


def _validate_contract_binding(
    run: Path,
    current: dict[str, Any],
    contract_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    contract_envelope, _ = _validate_envelope(run, contract_id)
    contract = _artifact_payload_json(run, contract_id)
    design_id, design_envelope = _event_artifact(
        run, "accept-design", "design-record", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    if contract["design_digest"] != design_envelope["envelope_digest"]:
        raise RunStateError("skill contract design binding is stale")
    required = {
        resolution_id: resolution_envelope["envelope_digest"],
        design_id: design_envelope["envelope_digest"],
    }
    for binding in contract["evidence_bindings"]:
        record = current["artifact_index"].get(binding["artifact_id"])
        if (
            record is None
            or record["derived_status"] != "accepted"
            or record["digest"] != binding["digest"]
        ):
            raise RunStateError("skill contract evidence binding is stale")
        required[binding["artifact_id"]] = binding["digest"]
    _require_input_bindings(contract_envelope, required, "skill contract")


def _validate_review_binding(
    run: Path,
    current: dict[str, Any],
    review_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    review_envelope, _ = _validate_envelope(run, review_id)
    review = _artifact_payload_json(run, review_id)
    candidate_id, candidate_envelope = _event_artifact(
        run, "accept-candidate", "candidate-record", event_artifacts
    )
    trials_id, trials_envelope = _event_artifact(
        run, "complete-trials", "trial-pack", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    candidate = _artifact_payload_json(run, candidate_id)
    if (
        review["candidate_digest"] != candidate_envelope["envelope_digest"]
        or review["candidate_revision"] != candidate["candidate_revision"]
        or review["input_artifacts"].get(candidate_id)
        != candidate_envelope["envelope_digest"]
    ):
        raise RunStateError("review candidate or input binding is stale")
    for artifact_id, digest in review["input_artifacts"].items():
        record = current["artifact_index"].get(artifact_id)
        if (
            record is None
            or record["derived_status"] != "accepted"
            or record["digest"] != digest
        ):
            raise RunStateError("review input artifact binding is stale")
    _require_input_bindings(
        review_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            candidate_id: candidate_envelope["envelope_digest"],
            trials_id: trials_envelope["envelope_digest"],
        },
        "review record",
    )
    claims = list(review["access_check_evidence"])
    claims.extend(
        digest
        for finding in review["findings"]
        for digest in finding["evidence"]
    )
    _require_retained_raw_digests(run, review_id, claims, "review record")


def _validate_score_bindings(
    run: Path,
    current: dict[str, Any],
    conformance_id: str,
    scorecard_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    conformance_envelope, _ = _validate_envelope(run, conformance_id)
    conformance = _artifact_payload_json(run, conformance_id)
    scorecard_envelope, _ = _validate_envelope(run, scorecard_id)
    scorecard = _artifact_payload_json(run, scorecard_id)
    candidate_id, candidate_envelope = _event_artifact(
        run, "accept-candidate", "candidate-record", event_artifacts
    )
    trials_id, trials_envelope = _event_artifact(
        run, "complete-trials", "trial-pack", event_artifacts
    )
    review_id, review_envelope = _event_artifact(
        run, "accept-review", "review-record", event_artifacts
    )
    evaluation_id, evaluation_envelope = _event_artifact(
        run, "freeze-evaluation", "evaluation-pack", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    candidate = _artifact_payload_json(run, candidate_id)
    trials = _artifact_payload_json(run, trials_id)
    review = _artifact_payload_json(run, review_id)
    evaluation = _artifact_payload_json(run, evaluation_id)
    resolution, _ = _resolution_payload(run, current["workflow_id"])
    required_scorecard_schema = resolution.get(
        "scorecard_schema", LEGACY_SCORECARD_SCHEMA
    )
    if scorecard["schema_version"] != required_scorecard_schema:
        raise RunStateError(
            "scorecard schema does not match the run's versioned semantics"
        )
    if conformance["candidate_digest"] != candidate_envelope["envelope_digest"]:
        raise RunStateError("conformance candidate binding is stale")
    _require_input_bindings(
        conformance_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            candidate_id: candidate_envelope["envelope_digest"],
            trials_id: trials_envelope["envelope_digest"],
            review_id: review_envelope["envelope_digest"],
        },
        "conformance ledger",
    )
    conformance_claims = [
        digest
        for gate in conformance["gates"].values()
        for digest in gate["evidence_digests"]
    ]
    _require_retained_raw_digests(
        run, conformance_id, conformance_claims, "conformance ledger"
    )
    if (
        scorecard["candidate_digest"] != candidate_envelope["envelope_digest"]
        or scorecard["candidate_revision"] != candidate["candidate_revision"]
        or scorecard["evaluation_digest"] != evaluation_envelope["envelope_digest"]
        or scorecard["rubric_digest"] != evaluation["rubric_digest"]
        or scorecard["review_digest"] != review_envelope["envelope_digest"]
    ):
        raise RunStateError("scorecard candidate or evidence binding is stale")
    _require_input_bindings(
        scorecard_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            candidate_id: candidate_envelope["envelope_digest"],
            evaluation_id: evaluation_envelope["envelope_digest"],
            review_id: review_envelope["envelope_digest"],
            conformance_id: conformance_envelope["envelope_digest"],
        },
        "target scorecard",
    )
    if scorecard["schema_version"] == LEGACY_SCORECARD_SCHEMA:
        return

    frozen_parameter_ids = sorted(evaluation["scoring_parameters"])
    if not frozen_parameter_ids:
        raise RunStateError("scorecard lacks frozen scoring-parameter evidence")
    for parameter_id in frozen_parameter_ids:
        _text(parameter_id, "evaluation scoring parameter identity")
    cases_by_id = {case["case_id"]: case for case in trials["cases"]}
    frozen_case_ids = sorted(cases_by_id)
    if not frozen_case_ids:
        raise RunStateError("scorecard lacks frozen case evidence")
    review_finding_ids: dict[str, list[str]] = {}
    for finding in review["findings"]:
        finding_id = raw_digest(canonical_json_bytes(finding))
        for criterion_id in finding["affected_target_criteria"]:
            review_finding_ids.setdefault(criterion_id, []).append(finding_id)

    raw_claims: list[str] = []
    for category in scorecard["categories"]:
        for criterion_id, result in category["criteria"].items():
            selected_cases = [cases_by_id[case_id] for case_id in frozen_case_ids]
            expected_raw_digests = sorted(
                {case["output_digest"] for case in selected_cases}
            )
            expected_trial_receipts = sorted(
                raw_digest(canonical_json_bytes(case)) for case in selected_cases
            )
            expected_finding_ids = sorted(set(review_finding_ids.get(criterion_id, [])))
            if (
                result["frozen_parameter_identifiers"] != frozen_parameter_ids
                or result["case_ids"] != frozen_case_ids
                or result["raw_artifact_digests"] != expected_raw_digests
                or result["trial_receipt_ids"] != expected_trial_receipts
                or result["review_finding_ids"] != expected_finding_ids
                or result["candidate_revision"] != candidate["candidate_revision"]
                or result["review_artifact_id"] != review_id
                or result["review_digest"] != review_envelope["envelope_digest"]
            ):
                raise RunStateError(
                    "scorecard criterion evidence is incomplete, stale, or substituted"
                )
            raw_claims.extend(result["raw_artifact_digests"])
    _require_retained_raw_digests(
        run, trials_id, sorted(set(raw_claims)), "scorecard criterion evidence"
    )


def _validate_verification_binding(
    run: Path,
    current: dict[str, Any],
    verification_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    verification_envelope, _ = _validate_envelope(run, verification_id)
    verification = _artifact_payload_json(run, verification_id)
    candidate_id, candidate_envelope = _event_artifact(
        run, "accept-candidate", "candidate-record", event_artifacts
    )
    trials_id, trials_envelope = _event_artifact(
        run, "complete-trials", "trial-pack", event_artifacts
    )
    review_id, review_envelope = _event_artifact(
        run, "accept-review", "review-record", event_artifacts
    )
    conformance_id, conformance_envelope = _event_artifact(
        run, "accept-scores", "builder-run-conformance-ledger", event_artifacts
    )
    scorecard_id, scorecard_envelope = _event_artifact(
        run, "accept-scores", "target-scorecard", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    candidate = _artifact_payload_json(run, candidate_id)
    if (
        verification["candidate_digest"] != candidate_envelope["envelope_digest"]
        or verification["candidate_revision"] != candidate["candidate_revision"]
    ):
        raise RunStateError("verification candidate binding is stale")
    _require_input_bindings(
        verification_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            candidate_id: candidate_envelope["envelope_digest"],
            trials_id: trials_envelope["envelope_digest"],
            review_id: review_envelope["envelope_digest"],
            conformance_id: conformance_envelope["envelope_digest"],
            scorecard_id: scorecard_envelope["envelope_digest"],
        },
        "verification record",
    )
    claims = [
        verification["before_manifest_digest"],
        verification["after_manifest_digest"],
        *[command["output_digest"] for command in verification["commands"]],
    ]
    _require_retained_raw_digests(
        run, verification_id, claims, "verification record"
    )


def _validate_confirmation_binding(
    run: Path,
    current: dict[str, Any],
    confirmation_id: str,
    authority_event_digest: str | None,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    confirmation_envelope, _ = _validate_envelope(run, confirmation_id)
    confirmation = _artifact_payload_json(run, confirmation_id)
    contract_id, contract_envelope = _event_artifact(
        run, "accept-contract", "skill-contract", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    required = {
        "accepted",
        "contract_digest",
        "target_identity",
        "target_snapshot_digest",
        "confirmation_event_digest",
    }
    if not required <= set(confirmation) or confirmation.get("accepted") is not True:
        raise RunStateError("user confirmation is not an accepted explicit record")
    if (
        confirmation.get("contract_digest") != contract_envelope["envelope_digest"]
        or confirmation.get("contract_artifact_id") != contract_id
        or confirmation.get("target_identity") != current["target_identity"]["canonical"]
        or confirmation.get("target_snapshot_digest")
        != current["target_snapshot"]["snapshot_digest"]
        or confirmation.get("confirmation_event_digest") != authority_event_digest
        or not isinstance(authority_event_digest, str)
        or not _DIGEST_RE.fullmatch(authority_event_digest)
    ):
        raise RunStateError("user confirmation binding is missing or mismatched")
    _require_input_bindings(
        confirmation_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            contract_id: contract_envelope["envelope_digest"],
        },
        "user confirmation",
    )
    return confirmation, confirmation_envelope


def _validate_evaluation_binding(
    run: Path,
    current: dict[str, Any],
    evaluation_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evaluation_envelope, _ = _validate_envelope(run, evaluation_id)
    evaluation = _artifact_payload_json(run, evaluation_id)
    contract_id, contract_envelope = _event_artifact(
        run, "accept-contract", "skill-contract", event_artifacts
    )
    confirmation_id, confirmation_envelope = _event_artifact(
        run, "confirm-contract", "user-confirmation-record", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    confirmation_authority = (
        event_artifacts.get("confirm-contract", {}).get("authority_event_digest")
        if event_artifacts is not None
        else next(
            receipt["authority_event_digest"]
            for receipt in reversed(_load_receipt_chain(run))
            if receipt["event"] == "confirm-contract"
        )
    )
    _validate_confirmation_binding(
        run,
        current,
        confirmation_id,
        confirmation_authority,
        event_artifacts,
    )
    required = {
        "frozen",
        "contract_digest",
        "confirmation_digest",
        "target_snapshot_digest",
    }
    if not required <= set(evaluation) or evaluation.get("frozen") is not True:
        raise RunStateError("evaluation pack is not frozen")
    if (
        evaluation.get("contract_digest") != contract_envelope["envelope_digest"]
        or evaluation.get("confirmation_digest")
        != confirmation_envelope["envelope_digest"]
        or evaluation.get("target_snapshot_digest")
        != current["target_snapshot"]["snapshot_digest"]
    ):
        raise RunStateError("evaluation pack binding is missing or mismatched")
    _require_input_bindings(
        evaluation_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            contract_id: contract_envelope["envelope_digest"],
            confirmation_id: confirmation_envelope["envelope_digest"],
        },
        "evaluation pack",
    )
    return evaluation, evaluation_envelope


def _validate_candidate_entry(
    run: Path,
    current: dict[str, Any],
    candidate_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    candidate_envelope, _ = _validate_envelope(run, candidate_id)
    candidate = _artifact_payload_json(run, candidate_id)
    contract_id, contract_envelope = _event_artifact(
        run, "accept-contract", "skill-contract", event_artifacts
    )
    confirmation_id, confirmation_envelope = _event_artifact(
        run, "confirm-contract", "user-confirmation-record", event_artifacts
    )
    evaluation_id, evaluation_envelope = _event_artifact(
        run, "freeze-evaluation", "evaluation-pack", event_artifacts
    )
    resolution_id, resolution_envelope = _event_artifact(
        run, "initialize", "resolution-record", event_artifacts
    )
    resolution, _ = _resolution_payload(run, current["workflow_id"])
    required_candidate_schema = resolution.get(
        "candidate_record_schema", LEGACY_CANDIDATE_SCHEMA
    )
    if candidate["schema_version"] != required_candidate_schema:
        raise RunStateError(
            "candidate schema does not match the run's versioned semantics"
        )
    confirmation_authority = (
        event_artifacts.get("confirm-contract", {}).get("authority_event_digest")
        if event_artifacts is not None
        else next(
            receipt["authority_event_digest"]
            for receipt in reversed(_load_receipt_chain(run))
            if receipt["event"] == "confirm-contract"
        )
    )
    _validate_confirmation_binding(
        run,
        current,
        confirmation_id,
        confirmation_authority,
        event_artifacts,
    )
    _validate_evaluation_binding(run, current, evaluation_id, event_artifacts)
    required = {
        "contract_digest",
        "confirmation_digest",
        "evaluation_digest",
        "target_snapshot_digest",
        "candidate_revision",
    }
    if not required <= set(candidate) or not isinstance(candidate.get("candidate_revision"), str) or not candidate["candidate_revision"]:
        raise RunStateError("candidate record is incomplete")
    if (
        candidate.get("contract_digest") != contract_envelope["envelope_digest"]
        or candidate.get("confirmation_digest")
        != confirmation_envelope["envelope_digest"]
        or candidate.get("evaluation_digest") != evaluation_envelope["envelope_digest"]
        or candidate.get("target_snapshot_digest")
        != current["target_snapshot"]["snapshot_digest"]
        or candidate.get("base_snapshot_digest")
        != current["target_snapshot"]["snapshot_digest"]
    ):
        raise RunStateError("candidate entry binding is missing or mismatched")
    _require_input_bindings(
        candidate_envelope,
        {
            resolution_id: resolution_envelope["envelope_digest"],
            contract_id: contract_envelope["envelope_digest"],
            confirmation_id: confirmation_envelope["envelope_digest"],
            evaluation_id: evaluation_envelope["envelope_digest"],
        },
        "candidate entry",
    )


def _artifact_id_of_type(
    run: Path, artifact_ids: list[str], artifact_type: str
) -> str:
    matches = [
        artifact_id
        for artifact_id in artifact_ids
        if _validate_envelope(run, artifact_id)[0]["artifact_type"] == artifact_type
    ]
    if len(matches) != 1:
        raise RunStateError(
            f"transition requires exactly one {artifact_type} artifact"
        )
    return matches[0]


def _validate_transition_semantics(
    run: Path,
    current: dict[str, Any],
    event: str,
    artifact_ids: list[str],
    authority_event_digest: str | None,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Apply the same evidence gate to a live transition and receipt replay."""
    if event != "confirm-contract" and authority_event_digest is not None:
        raise RunStateError("transition carries uncontracted authority evidence")
    if event == "capture-baseline":
        _validate_baseline_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "baseline-report"),
            event_artifacts,
        )
    elif event == "complete-research":
        _validate_research_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "research-pack"),
            event_artifacts,
        )
    elif event == "sieve-evidence":
        _validate_sieve_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "evidence-sieve"),
            event_artifacts,
        )
    elif event == "accept-design":
        _validate_design_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "design-record"),
            event_artifacts,
        )
    elif event == "accept-contract":
        _validate_contract_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "skill-contract"),
            event_artifacts,
        )
    elif event == "confirm-contract":
        _validate_confirmation_binding(
            run,
            current,
            _artifact_id_of_type(
                run, artifact_ids, "user-confirmation-record"
            ),
            authority_event_digest,
            event_artifacts,
        )
    elif event == "freeze-evaluation":
        _validate_evaluation_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "evaluation-pack"),
            event_artifacts,
        )
    elif event == "accept-candidate":
        _validate_candidate_entry(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "candidate-record"),
            event_artifacts,
        )
    elif event == "complete-trials":
        _validate_trial_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "trial-pack"),
            event_artifacts,
        )
    elif event == "accept-review":
        _validate_review_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "review-record"),
            event_artifacts,
        )
    elif event == "accept-scores":
        _validate_score_bindings(
            run,
            current,
            _artifact_id_of_type(
                run, artifact_ids, "builder-run-conformance-ledger"
            ),
            _artifact_id_of_type(run, artifact_ids, "target-scorecard"),
            event_artifacts,
        )
    elif event == "accept-verification":
        _validate_verification_binding(
            run,
            current,
            _artifact_id_of_type(run, artifact_ids, "verification-record"),
            event_artifacts,
        )


def _derive_index(run: Path) -> dict[str, Any]:
    if not _WORKFLOW_RE.fullmatch(run.name):
        raise RunStateError("invalid run directory identity")
    chain = _load_receipt_chain(run)
    genesis = chain[0]
    workflow_id = run.name
    if genesis.get("workflow_id") != workflow_id:
        raise RunStateError("run directory and workflow identity differ")
    resolution, resolution_envelope = _resolution_payload(run, workflow_id)
    binding = genesis.get("relevant_artifact_digests")
    if binding != [
        {
            "artifact_id": "resolution",
            "envelope_digest": resolution_envelope["envelope_digest"],
            "status": "accepted",
        }
    ]:
        raise RunStateError("genesis does not bind the resolution artifact")
    snapshot = resolution.get("target_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("snapshot_digest") != canonical_digest(snapshot, "snapshot_digest"):
        raise RunStateError("target snapshot digest mismatch")
    if any(receipt.get("target_snapshot_digest") != snapshot["snapshot_digest"] for receipt in chain):
        raise RunStateError("receipt target snapshot binding mismatch")
    artifact_status: dict[str, dict[str, Any]] = {}
    event_artifacts: dict[str, dict[str, Any]] = {}
    for receipt in chain:
        prior_artifact_status = dict(artifact_status)
        validated_bindings: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in receipt["relevant_artifact_digests"]:
            if not isinstance(item, dict) or set(item) != {"artifact_id", "envelope_digest", "status"}:
                raise RunStateError("receipt artifact binding schema is invalid")
            artifact_id = item["artifact_id"]
            envelope, _ = _validate_envelope(run, artifact_id)
            if envelope["artifact_type"] in _PAYLOAD_SCHEMA_VERSIONS:
                _artifact_payload_json(run, artifact_id)
            if (
                envelope["workflow_id"] != workflow_id
                or envelope["target_identity"]
                != resolution["target_identity"]["canonical"]
                or envelope["mode"] != resolution["mode"]["name"]
            ):
                raise RunStateError("artifact workflow, target, or mode identity mismatch")
            if item["envelope_digest"] != envelope["envelope_digest"]:
                raise RunStateError("receipt artifact digest mismatch")
            if artifact_id not in prior_artifact_status:
                if envelope["created_sequence"] != receipt["sequence"]:
                    raise RunStateError("artifact producing sequence is mismatched")
                for dependency in envelope["input_bindings"]:
                    accepted = prior_artifact_status.get(dependency["artifact_id"])
                    if (
                        accepted is None
                        or accepted["digest"] != dependency["digest"]
                        or accepted["derived_status"] != "accepted"
                    ):
                        raise RunStateError("artifact dependency is missing or stale")
            validated_bindings.append((item, envelope))
        event = receipt["event"]
        accepted_types = sorted(
            envelope["artifact_type"]
            for item, envelope in validated_bindings
            if item["status"] == "accepted"
        )
        if event in _EVENT_ARTIFACT_TYPES:
            if accepted_types != sorted(_EVENT_ARTIFACT_TYPES[event]) or any(
                item["status"] != "accepted" for item, _ in validated_bindings
            ):
                raise RunStateError(
                    "receipt event artifact type cardinality is invalid"
                )
        elif event == "retain-artifact":
            if len(validated_bindings) != 1 or validated_bindings[0][0]["status"] != "accepted":
                raise RunStateError("artifact retention receipt is malformed")
        elif event in {"pause", "resume"}:
            if validated_bindings:
                raise RunStateError("lifecycle receipt unexpectedly binds artifacts")
        elif event == "record-delivery":
            if accepted_types != ["delivery-acceptance-record"] or len(validated_bindings) != 1:
                raise RunStateError("delivery receipt artifact binding is invalid")
        elif event == "record-cleanup-authority":
            if accepted_types != ["cleanup-authority-record"] or len(validated_bindings) != 1:
                raise RunStateError("cleanup-authority receipt artifact binding is invalid")
        elif event == "finalize":
            expected_final_types = sorted(
                (
                    "candidate-record",
                    "trial-pack",
                    "review-record",
                    "builder-run-conformance-ledger",
                    "target-scorecard",
                    "verification-record",
                    "release-record",
                    "skill-contract",
                    "user-confirmation-record",
                    "evaluation-pack",
                )
            )
            if accepted_types != expected_final_types or len(validated_bindings) != len(
                expected_final_types
            ):
                raise RunStateError("finalization receipt evidence binding is incomplete")
        elif event == "initialize":
            if accepted_types != ["resolution-record"] or len(validated_bindings) != 1:
                raise RunStateError("genesis artifact binding is invalid")
        elif event != "invalidate":
            raise RunStateError("receipt event semantics are unsupported")
        replay_current = {
            "workflow_id": workflow_id,
            "stage": receipt["source_stage"],
            "target_identity": resolution["target_identity"],
            "mode": resolution["mode"],
            "authority": resolution["authority"],
            "target_snapshot": snapshot,
            "artifact_index": prior_artifact_status,
        }
        accepted_ids = [
            item["artifact_id"]
            for item, _ in validated_bindings
            if item["status"] == "accepted"
        ]
        if event in _STAGE_TRANSITIONS:
            _validate_transition_semantics(
                run,
                replay_current,
                event,
                accepted_ids,
                receipt["authority_event_digest"],
                event_artifacts,
            )
        elif event == "finalize":
            release_id = _artifact_id_of_type(run, accepted_ids, "release-record")
            if set(_validate_final_evidence(
                run,
                replay_current,
                release_id,
                event_artifacts,
            )) != set(accepted_ids):
                raise RunStateError("finalization receipt semantic binding is invalid")
        elif event == "record-delivery":
            _validate_delivery_record_binding(
                run,
                replay_current,
                _artifact_id_of_type(
                    run, accepted_ids, "delivery-acceptance-record"
                ),
                receipt["authority_event_digest"],
                event_artifacts,
            )
        elif event == "record-cleanup-authority":
            _validate_cleanup_authority_binding(
                run,
                replay_current,
                _artifact_id_of_type(
                    run, accepted_ids, "cleanup-authority-record"
                ),
                receipt["authority_event_digest"],
                event_artifacts,
            )
        elif event == "invalidate":
            _validate_invalidation_binding(
                run,
                replay_current,
                receipt,
                validated_bindings,
                event_artifacts,
            )
        for item, envelope in validated_bindings:
            artifact_id = item["artifact_id"]
            artifact_status[artifact_id] = {
                "artifact_id": artifact_id,
                "type": envelope["artifact_type"],
                "path": f"artifacts/{artifact_id}",
                "digest": envelope["envelope_digest"],
                "derived_status": item["status"],
                "producing_sequence": envelope["created_sequence"],
                "dependency_identifiers": [entry["artifact_id"] for entry in envelope["input_bindings"]],
            }
            if item["status"] != "accepted":
                for event_binding in event_artifacts.values():
                    for key, value in list(event_binding.items()):
                        if key != "authority_event_digest" and value == artifact_id:
                            event_binding.pop(key)
        if event == "initialize" or event in _STAGE_TRANSITIONS or event in {
            "finalize",
            "record-delivery",
            "record-cleanup-authority",
        }:
            event_artifacts[event] = {
                envelope["artifact_type"]: item["artifact_id"]
                for item, envelope in validated_bindings
                if item["status"] == "accepted"
            }
            event_artifacts[event]["authority_event_digest"] = receipt[
                "authority_event_digest"
            ]
    actual_artifacts: set[str] = set()
    artifacts_directory = run / "artifacts"
    for path in artifacts_directory.iterdir():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RunStateError("artifact namespace contains an unsafe entry")
        if not _ARTIFACT_RE.fullmatch(path.name):
            raise RunStateError("artifact namespace contains an unknown entry")
        actual_artifacts.add(path.name)
    if actual_artifacts != set(artifact_status):
        raise RunStateError("artifact namespace contains an orphan or missing artifact")
    head = chain[-1]
    stage = head["destination_stage"]
    active_lock = resolution["active_target_lock"] if stage not in {"finalized", "delivered", "abandoned"} else None
    stored_queue = resolution["queue"]
    if (
        not isinstance(stored_queue, dict)
        or set(stored_queue) != {"order", "states", "targets"}
        or not isinstance(stored_queue["order"], list)
        or not isinstance(stored_queue["states"], dict)
        or not isinstance(stored_queue["targets"], dict)
        or len(stored_queue["order"]) != len(set(stored_queue["order"]))
        or set(stored_queue["order"]) != set(stored_queue["states"])
        or set(stored_queue["order"]) != set(stored_queue["targets"])
    ):
        raise RunStateError("resolution queue schema is invalid")
    for queued_identity, queued_target in stored_queue["targets"].items():
        if (
            not isinstance(queued_identity, str)
            or not isinstance(queued_target, dict)
            or queued_target.get("canonical") != queued_identity
        ):
            raise RunStateError("resolution queue target binding is invalid")
    queue = {
        "order": list(stored_queue["order"]),
        "states": dict(stored_queue["states"]),
    }
    canonical_target = resolution["target_identity"]["canonical"]
    if stage == "paused":
        queue["states"][canonical_target] = "paused"
    elif stage in {"finalized", "delivered", "abandoned"}:
        queue["states"][canonical_target] = stage
    else:
        queue["states"][canonical_target] = "active"
    return {
        "schema_version": INDEX_SCHEMA,
        "workflow_id": workflow_id,
        "head_sequence": head["sequence"],
        "head_transition_digest": head["receipt_digest"],
        "stage": stage,
        "host_identity": resolution["host_identity"],
        "target_identity": resolution["target_identity"],
        "mode": resolution["mode"],
        "authority": resolution["authority"],
        "git_identity": resolution["git_identity"],
        "target_snapshot": snapshot,
        "queue": queue,
        "active_target_lock": active_lock,
        "artifact_index": artifact_status,
        "created_at": chain[0]["created_at"],
        "updated_at": head["created_at"],
    }


def _run_directory(root: Path, workflow_id: str) -> Path:
    if not isinstance(workflow_id, str) or not _WORKFLOW_RE.fullmatch(workflow_id):
        raise RunStateError("invalid workflow identifier")
    run = root / "live" / workflow_id
    _validate_private_directory(run, "live run")
    allowed = {
        "receipts",
        "artifacts",
        "transactions",
        "current.json",
        "final-run-manifest.json",
    }
    if set(path.name for path in run.iterdir()) - allowed:
        raise RunStateError("live run contains an unknown entry")
    _validate_private_directory(run / "receipts", "receipt directory")
    _validate_private_directory(run / "artifacts", "artifact directory")
    _validate_private_directory(run / "transactions", "transaction directory")
    return run


def _target_lock_name(canonical_target: str) -> str:
    return f"{raw_digest(canonical_target.encode('utf-8'))}.json"


def _owned_result_digest(
    index: dict[str, Any], manifest: dict[str, Any], owned_paths: list[str]
) -> str:
    if not owned_paths or len(owned_paths) != len(set(owned_paths)):
        raise RunStateError("candidate owned paths are empty or duplicated")
    entries = manifest["entries"]
    baseline_manifest = (
        index["target_snapshot"]["manifest"]
        if index["mode"]["name"] == "improve"
        else None
    )
    baseline_entries = (
        {entry["path"]: entry for entry in baseline_manifest["entries"]}
        if baseline_manifest is not None
        else {}
    )
    current_entries = {entry["path"]: entry for entry in entries}
    selected: list[dict[str, Any]] = []
    selected_paths: set[str] = set()

    def path_is_owned(path: str) -> bool:
        return any(
            owned == "."
            or path == owned
            or path.startswith(f"{owned}/")
            for owned in owned_paths
        )

    for owned in owned_paths:
        if (
            not isinstance(owned, str)
            or not owned
            or owned.startswith("/")
            or "\\" in owned
            or any(part in {"", ".."} for part in owned.split("/"))
        ):
            raise RunStateError("candidate owned path is unsafe")
        known_paths = set(current_entries) | set(baseline_entries)
        if not any(
            owned == "." or path == owned or path.startswith(f"{owned}/")
            for path in known_paths
        ):
            raise RunStateError("candidate owned path is absent from the target history")
        matching = [entry for entry in entries if path_is_owned(entry["path"])]
        for entry in matching:
            if entry["path"] in selected_paths:
                continue
            selected_paths.add(entry["path"])
            selected.append(
                {
                    key: entry[key]
                    for key in (
                        "path",
                        "kind",
                        "mode",
                        "byte_count",
                        "digest",
                    )
                }
            )
    selected.sort(key=lambda entry: entry["path"])
    all_paths = {entry["path"] for entry in entries}
    if index["mode"]["name"] == "create" and selected_paths != all_paths:
        raise RunStateError("create delivery contains content outside candidate ownership")
    if index["mode"]["name"] == "improve":
        unowned_paths = {
            path
            for path in set(baseline_entries) | set(current_entries)
            if not path_is_owned(path)
        }
        if any(
            baseline_entries.get(path) != current_entries.get(path)
            for path in unowned_paths
        ):
            raise RunStateError("delivery changed content outside candidate ownership")
        assert baseline_manifest is not None
        if "." not in owned_paths and any(
            baseline_manifest[field] != manifest[field]
            for field in ("target_kind", "target_mode")
        ):
            raise RunStateError(
                "delivery changed target metadata outside candidate ownership"
            )
    record = {
        "schema_version": "skill-builder-owned-result.v2",
        "target_kind": manifest["target_kind"],
        "target_mode": manifest["target_mode"],
        "entries": selected,
    }
    return raw_digest(canonical_json_bytes(record))


def _validate_delivery_destination(
    index: dict[str, Any],
    delivery: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    """Verify the exact post-delivery target named by the accepted record."""
    if delivery.get("destination_identity") != index["target_identity"]["canonical"]:
        raise RunStateError("delivery destination is not the canonical target")
    locator = Path(index["target_identity"]["locator"])
    if not os.path.lexists(locator):
        raise RunStateError("accepted delivery destination does not exist")
    manifest = snapshot_target(locator)
    if delivery.get("resulting_destination_digest") != manifest["manifest_digest"]:
        raise RunStateError("delivery destination digest does not match the exact target")
    if (
        candidate.get("schema_version")
        in {PREVIOUS_CANDIDATE_SCHEMA, CANDIDATE_SCHEMA}
        and candidate.get("resulting_digest")
        != _owned_result_digest(index, manifest, candidate.get("owned_paths"))
    ):
        raise RunStateError("delivery content does not match the finalized candidate result")


def _validate_target_unchanged(index: dict[str, Any], root: Path) -> None:
    snapshot = index["target_snapshot"]
    locator = Path(index["target_identity"]["locator"])
    _reject_symlink_components(locator)
    if index["stage"] == "delivered":
        run = _run_directory(root, index["workflow_id"])
        _, _, delivery = _current_event_artifact(
            run,
            index,
            "record-delivery",
            "delivery-acceptance-record",
        )
        _, _, candidate = _current_event_artifact(
            run,
            index,
            "accept-candidate",
            "candidate-record",
        )
        _validate_delivery_destination(index, delivery, candidate)
        return
    if snapshot["exists"] is False and os.path.lexists(locator):
        raise RunStateError("target changed after its absent snapshot")
    if snapshot["exists"] is True and snapshot_target(locator) != snapshot.get("manifest"):
        raise RunStateError("target changed after its exact snapshot")
    detected_git = _detect_git_identity(locator)
    if detected_git != index["git_identity"]:
        raise RunStateError("Git identity changed after initialization")
    active_lock = index["active_target_lock"]
    if active_lock is not None:
        lock = _read_json(
            root
            / "target-locks"
            / _target_lock_name(index["target_identity"]["canonical"])
        )
        expected = {
            "schema_version": "skill-builder-target-lock.v1",
            **active_lock,
            "workflow_id": index["workflow_id"],
        }
        if lock != expected:
            raise RunStateError("active-target lock binding mismatch")


def initialize_run(
    *,
    host_identity: dict[str, Any],
    target_identity: dict[str, Any],
    mode: str,
    authority: dict[str, Any],
    absence_evidence: dict[str, Any] | None = None,
    overlap_map: dict[str, Any] | None = None,
    target_manifest: dict[str, Any] | None = None,
    git_identity: dict[str, Any] | None = None,
    queue: list[dict[str, Any]] | None = None,
    owner_identity: str = "main-agent",
    state_root: Path | None = None,
    _queue_states: dict[str, str] | None = None,
) -> dict[str, Any]:
    host, target, granted = _identity_records(host_identity, target_identity, authority)
    if mode not in {"create", "improve"}:
        raise RunStateError("mode must be create or improve")
    if mode == "create":
        snapshot = _absent_snapshot(target, absence_evidence, overlap_map)
    else:
        snapshot = _existing_snapshot(target, target_manifest)
    detected_git = _detect_git_identity(Path(target["locator"]))
    if git_identity is None:
        git = detected_git
    else:
        git = _validate_git_identity(git_identity)
        if git != detected_git:
            raise RunStateError("supplied Git identity does not match detected identity")
    _validate_state_root_location(state_root, Path(target["locator"]), git)
    if not isinstance(owner_identity, str) or not owner_identity.strip():
        raise RunStateError("active-lock owner identity is required")
    requested_queue = queue if queue is not None else [target]
    if not isinstance(requested_queue, list) or not requested_queue:
        raise RunStateError("queue must contain at least one target")
    canonical_queue: list[str] = []
    queue_targets: dict[str, dict[str, Any]] = {}
    for item in requested_queue:
        _, queue_target, _ = _identity_records(host, item, granted)
        canonical_queue.append(queue_target["canonical"])
        queue_targets[queue_target["canonical"]] = queue_target
    if len(canonical_queue) != len(set(canonical_queue)) or target["canonical"] not in canonical_queue:
        raise RunStateError("queue target identities must be unique and include the active target")
    workflow_id = secrets.token_hex(16)
    created_at = _now()
    active_lock = {
        "target_identity": target["canonical"],
        "owner_identity": owner_identity,
        "acquired_at": created_at,
        "lock_nonce": secrets.token_hex(16),
    }
    queue_states = (
        dict(_queue_states)
        if _queue_states is not None
        else {
            item: ("active" if item == target["canonical"] else "pending")
            for item in canonical_queue
        }
    )
    if (
        set(queue_states) != set(canonical_queue)
        or queue_states.get(target["canonical"]) != "active"
        or list(queue_states.values()).count("active") != 1
        or any(
            value
            not in {"pending", "active", "paused", "finalized", "delivered", "abandoned"}
            for value in queue_states.values()
        )
    ):
        raise RunStateError("queue states must bind exactly one active target")
    queue_record = {
        "order": canonical_queue,
        "states": queue_states,
        "targets": queue_targets,
    }
    mode_record = {
        "name": mode,
        "evidence": (
            {
                "absence_evidence_digest": snapshot["absence_evidence_digest"],
                "overlap_map_digest": snapshot["overlap_map_digest"],
            }
            if mode == "create"
            else {"target_manifest_digest": snapshot["manifest_digest"]}
        ),
    }
    with _locked_root(state_root, create=True) as root:
        live = root / "live"
        for entry in live.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                raise RunStateError("live-run namespace contains an unsafe entry")
            existing_run = _run_directory(root, entry.name)
            current = _derive_index(existing_run)
            if _read_json(existing_run / "current.json") != current:
                raise RunStateError("existing live run has a stale derived index")
            unfinished = current["stage"] not in {
                "finalized",
                "delivered",
                "abandoned",
            }
            queue_overlap = set(canonical_queue) & set(current["queue"]["order"])
            if unfinished and queue_overlap:
                raise RunStateError(
                    "unfinished queued work already owns the active target lock"
                )
        run = live / workflow_id
        _ensure_private_directory(run)
        _ensure_private_directory(run / "receipts")
        _ensure_private_directory(run / "artifacts")
        _ensure_private_directory(run / "transactions")
        lock_path = root / "target-locks" / _target_lock_name(target["canonical"])
        lock_record = {
            "schema_version": "skill-builder-target-lock.v1",
            **active_lock,
            "workflow_id": workflow_id,
        }
        lock_published = False
        try:
            envelope = _create_resolution_artifact(
                run,
                workflow_id,
                host,
                target,
                mode_record,
                granted,
                git,
                snapshot,
                queue_record,
                active_lock,
                created_at,
            )
            genesis = _new_receipt(
                workflow_id=workflow_id,
                target_identity=target["canonical"],
                sequence=0,
                prior_receipt_digest=None,
                event="initialize",
                source_stage=None,
                destination_stage="resolved",
                relevant_artifact_digests=[
                    {
                        "artifact_id": "resolution",
                        "envelope_digest": envelope["envelope_digest"],
                        "status": "accepted",
                    }
                ],
                target_snapshot_digest=snapshot["snapshot_digest"],
                authority_event_digest=None,
            )
            _write_receipt(run, genesis)
            index = _derive_index(run)
            _atomic_json(run / "current.json", index)
            _exclusive_json(lock_path, lock_record)
            lock_published = True
            _fsync_directory(live)
        except BaseException:
            # No caller-visible workflow exists until the genesis chain and index
            # are complete.  Best-effort rollback is confined to the fresh ID.
            if lock_published and os.path.lexists(lock_path):
                if lock_path.is_symlink() or _read_json(lock_path) != lock_record:
                    raise RunStateError(
                        "fresh target lock changed during initialization rollback"
                    )
                lock_path.unlink()
                _fsync_directory(lock_path.parent)
            if run.exists() and not run.is_symlink():
                _remove_owned_tree(run)
                _fsync_directory(live)
            raise
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "initialize",
            "workflow_id": workflow_id,
            "sequence": 0,
            "stage": "resolved",
            "receipt_digest": genesis["receipt_digest"],
            "target_snapshot_digest": snapshot["snapshot_digest"],
        }


def activate_next_target(
    *,
    workflow_id: str,
    expected_sequence: int,
    mode: str,
    absence_evidence: dict[str, Any] | None = None,
    overlap_map: dict[str, Any] | None = None,
    target_manifest: dict[str, Any] | None = None,
    git_identity: dict[str, Any] | None = None,
    owner_identity: str = "main-agent",
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Activate the next pending identity from a terminal run's durable queue."""
    if (
        isinstance(expected_sequence, bool)
        or not isinstance(expected_sequence, int)
        or expected_sequence < 0
    ):
        raise RunStateError("expected sequence must be a nonnegative integer")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] not in {"finalized", "delivered", "abandoned"}:
            raise RunStateError("unfinished queued work blocks target activation")
        resolution, _ = _resolution_payload(run, workflow_id)
        durable_queue = resolution["queue"]
        pending = [
            identity
            for identity in durable_queue["order"]
            if current["queue"]["states"].get(identity) == "pending"
        ]
        if not pending:
            raise RunStateError("queue has no pending target to activate")
        next_identity = pending[0]
        next_target = dict(durable_queue["targets"][next_identity])
        queue_targets = [
            dict(durable_queue["targets"][identity])
            for identity in durable_queue["order"]
        ]
        queue_states = dict(current["queue"]["states"])
        queue_states[next_identity] = "active"
        host = dict(current["host_identity"])
        granted = dict(current["authority"])

    activated = initialize_run(
        host_identity=host,
        target_identity=next_target,
        mode=mode,
        authority=granted,
        absence_evidence=absence_evidence,
        overlap_map=overlap_map,
        target_manifest=target_manifest,
        git_identity=git_identity,
        queue=queue_targets,
        owner_identity=owner_identity,
        state_root=state_root,
        _queue_states=queue_states,
    )
    activated["operation"] = "activate-next"
    activated["previous_workflow_id"] = workflow_id
    return activated


def load_run(*, workflow_id: str, state_root: Path | None = None) -> dict[str, Any]:
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        derived = _derive_index(run)
        stored = _read_json(run / "current.json")
        if stored != derived:
            raise RunStateError("derived current index is stale or corrupt, recover it")
        _validate_target_unchanged(derived, root)
        return json.loads(json.dumps(derived, ensure_ascii=False))


def discover_run(
    *,
    host_identity: dict[str, Any],
    target_identity: dict[str, Any],
    state_root: Path | None = None,
) -> dict[str, Any]:
    empty_authority = {
        "reads": [],
        "writes": [],
        "delegation": [],
        "candidate_effects": [],
        "delivery_effects": [],
    }
    host, target, _ = _identity_records(
        host_identity, target_identity, empty_authority
    )
    matches: list[dict[str, Any]] = []
    with _locked_root(state_root, create=False) as root:
        for entry in sorted((root / "live").iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                raise RunStateError("live-run namespace contains an unsafe entry")
            run = _run_directory(root, entry.name)
            derived = _derive_index(run)
            if _read_json(run / "current.json") != derived:
                raise RunStateError("derived current index is stale or corrupt")
            _validate_target_unchanged(derived, root)
            if (
                derived["host_identity"] == host
                and derived["target_identity"] == target
            ):
                matches.append(derived)
        if len(matches) != 1:
            raise RunStateError(
                "run not found" if not matches else "canonical discovery is ambiguous"
            )
        match = matches[0]
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "discover",
            "workflow_id": match["workflow_id"],
            "sequence": match["head_sequence"],
            "stage": match["stage"],
            "receipt_digest": match["head_transition_digest"],
            "target_snapshot_digest": match["target_snapshot"]["snapshot_digest"],
        }


def retain_artifact(
    *,
    workflow_id: str,
    expected_sequence: int,
    artifact_id: str,
    artifact_type: str,
    files: dict[str, bytes],
    primary_path: str,
    producer: str,
    input_bindings: list[dict[str, str]],
    limitations: list[str],
    state_root: Path | None = None,
) -> dict[str, Any]:
    if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int) or expected_sequence < 0:
        raise RunStateError("expected sequence must be a nonnegative integer")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] in {"paused", "finalized", "delivered", "abandoned"}:
            raise RunStateError("terminal run artifacts are immutable")
        _validate_target_unchanged(current, root)
        for binding in input_bindings:
            if not isinstance(binding, dict):
                raise RunStateError("artifact input binding schema is invalid")
            existing = current["artifact_index"].get(binding.get("artifact_id"))
            if existing is None or existing["digest"] != binding.get("digest") or existing["derived_status"] != "accepted":
                raise RunStateError("artifact input binding is missing, stale, or invalidated")
        artifact = _artifact_directory(run, artifact_id)
        if os.path.lexists(artifact):
            raise RunStateError("immutable artifact already exists")
        derived, created, receipt = _append_transaction(
            run=run,
            current=current,
            event="retain-artifact",
            destination_stage=current["stage"],
            authority_event_digest=None,
            existing_bindings=[],
            artifact_requests=[
                {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "files": files,
                    "primary_path": primary_path,
                    "producer": producer,
                    "input_bindings": input_bindings,
                    "limitations": limitations,
                }
            ],
        )
        envelope = created[artifact_id]
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "retain-artifact",
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
            "artifact_id": artifact_id,
            "artifact_digest": envelope["envelope_digest"],
        }


def transition_run(
    *,
    workflow_id: str,
    expected_sequence: int,
    event: str,
    destination_stage: str,
    artifact_ids: list[str],
    authority_event_digest: str | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    expected_transition = _STAGE_TRANSITIONS.get(event)
    if expected_transition is None or expected_transition[1] != destination_stage:
        raise RunStateError("unsupported stage transition")
    if not isinstance(artifact_ids, list) or not artifact_ids or len(artifact_ids) != len(set(artifact_ids)):
        raise RunStateError("stage transition requires unique artifact identifiers")
    if authority_event_digest is not None and not _DIGEST_RE.fullmatch(authority_event_digest):
        raise RunStateError("authority-event digest is invalid")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] != expected_transition[0]:
            raise RunStateError("event is not allowed from the current stage")
        _validate_target_unchanged(current, root)
        bindings: list[dict[str, str]] = []
        observed_types: list[str] = []
        for artifact_id in artifact_ids:
            record = current["artifact_index"].get(artifact_id)
            if record is None or record["derived_status"] != "accepted":
                raise RunStateError("transition artifact is missing or invalidated")
            observed_types.append(record["type"])
            bindings.append(
                {
                    "artifact_id": artifact_id,
                    "envelope_digest": record["digest"],
                    "status": "accepted",
                }
            )
        if sorted(observed_types) != sorted(_EVENT_ARTIFACT_TYPES[event]):
            raise RunStateError(
                "transition requires exact singular artifact type cardinality"
            )
        for artifact_id in artifact_ids:
            _artifact_payload_json(run, artifact_id)
        _validate_transition_semantics(
            run,
            current,
            event,
            artifact_ids,
            authority_event_digest,
        )
        derived, _, _ = _append_transaction(
            run=run,
            current=current,
            event=event,
            destination_stage=destination_stage,
            authority_event_digest=authority_event_digest,
            existing_bindings=bindings,
            artifact_requests=[],
        )
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": event,
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
        }


def recover_run(
    *, workflow_id: str, state_root: Path | None = None
) -> dict[str, Any]:
    with _locked_root(state_root, create=False) as root:
        if not isinstance(workflow_id, str) or not _WORKFLOW_RE.fullmatch(workflow_id):
            raise RunStateError("invalid workflow identifier")
        candidate = root / "live" / workflow_id
        if not os.path.lexists(candidate):
            tombstone = _validate_tombstone(
                root / "tombstones" / f"{workflow_id}.json", workflow_id
            )
            deleting = root / "deleting" / workflow_id
            if os.path.lexists(deleting):
                raise RunStateError("cleanup is incomplete and must be retried")
            return {
                "schema_version": "skill-builder-operation.v1",
                "operation": "recover",
                "workflow_id": workflow_id,
                "sequence": None,
                "stage": "cleaned",
                "receipt_digest": tombstone["final_transition_receipt_digest"],
                "tombstone_digest": tombstone["tombstone_digest"],
            }
        run = _run_directory(root, workflow_id)
        derived = _derive_index(run)
        _validate_target_unchanged(derived, root)
        _atomic_json(run / "current.json", derived)
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "recover",
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
        }


def _lifecycle_transition(
    *,
    workflow_id: str,
    expected_sequence: int,
    operation: str,
    state_root: Path | None,
) -> dict[str, Any]:
    if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int) or expected_sequence < 0:
        raise RunStateError("expected sequence must be a nonnegative integer")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        chain = _load_receipt_chain(run)
        if operation == "pause":
            if current["stage"] not in _STAGES[:-3]:
                raise RunStateError("run cannot be paused from its current stage")
            destination = "paused"
        elif operation == "resume":
            if current["stage"] != "paused" or chain[-1]["event"] != "pause":
                raise RunStateError("run is not directly resumable")
            destination = chain[-1]["source_stage"]
        else:
            raise RunStateError("unsupported lifecycle transition")
        _validate_target_unchanged(current, root)
        derived, _, _ = _append_transaction(
            run=run,
            current=current,
            event=operation,
            destination_stage=destination,
            authority_event_digest=None,
            existing_bindings=[],
            artifact_requests=[],
        )
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": operation,
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
        }


def pause_run(
    *, workflow_id: str, expected_sequence: int, state_root: Path | None = None
) -> dict[str, Any]:
    return _lifecycle_transition(
        workflow_id=workflow_id,
        expected_sequence=expected_sequence,
        operation="pause",
        state_root=state_root,
    )


def resume_run(
    *, workflow_id: str, expected_sequence: int, state_root: Path | None = None
) -> dict[str, Any]:
    return _lifecycle_transition(
        workflow_id=workflow_id,
        expected_sequence=expected_sequence,
        operation="resume",
        state_root=state_root,
    )


_AFTER_BASELINE = {
    "research-pack",
    "evidence-sieve",
    "design-record",
    "skill-contract",
    "user-confirmation-record",
    "evaluation-pack",
    "candidate-record",
    "trial-pack",
    "review-record",
    "builder-run-conformance-ledger",
    "target-scorecard",
    "verification-record",
    "release-record",
}
_AFTER_CONTRACT = {
    "user-confirmation-record",
    "evaluation-pack",
    "candidate-record",
    "trial-pack",
    "review-record",
    "builder-run-conformance-ledger",
    "target-scorecard",
    "verification-record",
    "release-record",
}
_AFTER_CANDIDATE = {
    "trial-pack",
    "review-record",
    "builder-run-conformance-ledger",
    "target-scorecard",
    "verification-record",
    "release-record",
}
_INVALIDATION_RULES = {
    "host-target": {
        "changed_types": {"resolution-record"},
        "destination": "abandoned",
        "invalidates": {"baseline-report", *_AFTER_BASELINE},
    },
    "host-identity": {
        "changed_types": {"resolution-record"},
        "destination": "abandoned",
        "invalidates": {"baseline-report", *_AFTER_BASELINE},
    },
    "target-identity": {
        "changed_types": {"resolution-record"},
        "destination": "abandoned",
        "invalidates": {"baseline-report", *_AFTER_BASELINE},
    },
    "mode": {
        "changed_types": {"resolution-record"},
        "destination": "abandoned",
        "invalidates": {"baseline-report", *_AFTER_BASELINE},
    },
    "target-snapshot": {
        "changed_types": {"resolution-record"},
        "destination": "abandoned",
        "invalidates": {"baseline-report", *_AFTER_BASELINE},
    },
    "research": {
        "changed_types": {"research-pack"},
        "destination": "baseline",
        "invalidates": _AFTER_BASELINE - {"research-pack"},
    },
    "sieve": {
        "changed_types": {"evidence-sieve"},
        "destination": "research",
        "invalidates": _AFTER_BASELINE - {"research-pack", "evidence-sieve"},
    },
    "design": {
        "changed_types": {"design-record"},
        "destination": "sieve",
        "invalidates": _AFTER_CONTRACT | {"skill-contract"},
    },
    "contract": {
        "changed_types": {"skill-contract"},
        "destination": "design",
        "invalidates": _AFTER_CONTRACT,
    },
    "confirmation": {
        "changed_types": {"user-confirmation-record"},
        "destination": "contract",
        "invalidates": _AFTER_CONTRACT - {"user-confirmation-record"},
    },
    "evaluation": {
        "changed_types": {"evaluation-pack"},
        "destination": "confirmed",
        "invalidates": {"candidate-record", *_AFTER_CANDIDATE},
    },
    "target-parameters": {
        "changed_types": {"evaluation-pack"},
        "destination": "confirmed",
        "invalidates": {"candidate-record", *_AFTER_CANDIDATE},
    },
    "rubric": {
        "changed_types": {"evaluation-pack"},
        "destination": "confirmed",
        "invalidates": {"candidate-record", *_AFTER_CANDIDATE},
    },
    "candidate": {
        "changed_types": {"candidate-record"},
        "destination": "evaluation",
        "invalidates": _AFTER_CANDIDATE,
    },
    "trial": {
        "changed_types": {"trial-pack"},
        "destination": "candidate",
        "invalidates": _AFTER_CANDIDATE - {"trial-pack"},
    },
    "review": {
        "changed_types": {"review-record"},
        "destination": "trials",
        "invalidates": {
            "builder-run-conformance-ledger",
            "target-scorecard",
            "verification-record",
            "release-record",
        },
    },
    "verification-input": {
        "changed_types": {"verification-record"},
        "destination": "scored",
        "invalidates": {"release-record"},
    },
    "verification-result": {
        "changed_types": {"verification-record"},
        "destination": "scored",
        "invalidates": {"release-record"},
    },
    "verification": {
        "changed_types": {"verification-record"},
        "destination": "scored",
        "invalidates": {"release-record"},
    },
    "delivery-intent": {
        "changed_types": {"release-record"},
        "destination": "verified",
        "invalidates": set(),
    },
}

_CURRENT_EVENT_BY_ARTIFACT_TYPE = {
    "resolution-record": "initialize",
    "research-pack": "complete-research",
    "evidence-sieve": "sieve-evidence",
    "design-record": "accept-design",
    "skill-contract": "accept-contract",
    "user-confirmation-record": "confirm-contract",
    "evaluation-pack": "freeze-evaluation",
    "candidate-record": "accept-candidate",
    "trial-pack": "complete-trials",
    "review-record": "accept-review",
    "verification-record": "accept-verification",
}


def _require_selected_change_artifact(
    run: Path,
    current: dict[str, Any],
    artifact_id: str,
    artifact_type: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    event = _CURRENT_EVENT_BY_ARTIFACT_TYPE.get(artifact_type)
    if event is not None:
        selected_id, selected_envelope = _event_artifact(
            run, event, artifact_type, event_artifacts
        )
        selected = current["artifact_index"].get(selected_id)
        if (
            artifact_id != selected_id
            or selected is None
            or selected["derived_status"] != "accepted"
            or selected["digest"] != selected_envelope["envelope_digest"]
        ):
            raise RunStateError(
                "material change does not name the current selected artifact"
            )
        return
    if artifact_type == "release-record":
        releases = sorted(
            record["artifact_id"]
            for record in current["artifact_index"].values()
            if record["type"] == "release-record"
            and record["derived_status"] == "accepted"
        )
        if releases != [artifact_id]:
            raise RunStateError(
                "material change does not identify one unambiguous release"
            )
        return
    raise RunStateError("material change artifact has no selecting workflow event")


def _validate_invalidation_binding(
    run: Path,
    current: dict[str, Any],
    receipt: dict[str, Any],
    validated_bindings: list[tuple[dict[str, Any], dict[str, Any]]],
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Bind one invalidation receipt to its exact material-change rule."""
    accepted = [
        (item, envelope)
        for item, envelope in validated_bindings
        if item["status"] == "accepted"
    ]
    superseded = [
        (item, envelope)
        for item, envelope in validated_bindings
        if item["status"] == "superseded"
    ]
    invalidated = [
        (item, envelope)
        for item, envelope in validated_bindings
        if item["status"] == "invalidated"
    ]
    if (
        len(accepted) != 1
        or accepted[0][1]["artifact_type"] != "invalidation-record"
        or len(superseded) != 1
        or receipt["authority_event_digest"] is not None
    ):
        raise RunStateError("invalidation receipt has invalid causal cardinality")

    invalidation_item, invalidation_envelope = accepted[0]
    changed_item, changed_envelope = superseded[0]
    invalidation = _artifact_payload_json(
        run, invalidation_item["artifact_id"]
    )
    rule = _INVALIDATION_RULES.get(invalidation["change_kind"])
    if rule is None:
        raise RunStateError("invalidation change kind is unsupported")
    changed = current["artifact_index"].get(changed_item["artifact_id"])
    if (
        changed is None
        or changed["derived_status"] != "accepted"
        or changed["digest"] != changed_item["envelope_digest"]
        or changed_envelope["artifact_type"] not in rule["changed_types"]
        or invalidation["changed_artifact_id"] != changed_item["artifact_id"]
        or invalidation["changed_artifact_digest"] != changed_item["envelope_digest"]
        or receipt["destination_stage"] != rule["destination"]
        or invalidation_envelope["created_sequence"] != receipt["sequence"]
    ):
        raise RunStateError("invalidation change, artifact, or destination is mismatched")
    _require_selected_change_artifact(
        run,
        current,
        changed_item["artifact_id"],
        changed_envelope["artifact_type"],
        event_artifacts,
    )

    expected_invalidated = sorted(
        record["artifact_id"]
        for record in current["artifact_index"].values()
        if record["type"] in rule["invalidates"]
        and record["derived_status"] == "accepted"
        and record["artifact_id"] != changed_item["artifact_id"]
    )
    observed_invalidated = sorted(item["artifact_id"] for item, _ in invalidated)
    if (
        observed_invalidated != expected_invalidated
        or invalidation["invalidated_artifact_ids"] != expected_invalidated
    ):
        raise RunStateError("invalidation downstream status set is incomplete")
    for item, envelope in invalidated:
        prior = current["artifact_index"].get(item["artifact_id"])
        if (
            prior is None
            or prior["derived_status"] != "accepted"
            or prior["digest"] != item["envelope_digest"]
            or envelope["artifact_type"] not in rule["invalidates"]
        ):
            raise RunStateError("invalidation downstream binding is stale")
    if invalidation_envelope["input_bindings"] != [
        {
            "artifact_id": changed_item["artifact_id"],
            "digest": changed_item["envelope_digest"],
        }
    ]:
        raise RunStateError("invalidation causal input binding is invalid")


def invalidate_run(
    *,
    workflow_id: str,
    expected_sequence: int,
    change_kind: str,
    changed_artifact_id: str,
    reason: str,
    state_root: Path | None = None,
) -> dict[str, Any]:
    rule = _INVALIDATION_RULES.get(change_kind)
    if rule is None:
        raise RunStateError("unsupported material-change kind")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
        raise RunStateError("invalidation reason must be nonempty bounded text")
    if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int) or expected_sequence < 0:
        raise RunStateError("expected sequence must be a nonnegative integer")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] not in _STAGES[:-3]:
            raise RunStateError("terminal or paused runs cannot be invalidated")
        destination = rule["destination"]
        invalidated_types = rule["invalidates"]
        if destination != "abandoned" and _STAGES.index(destination) > _STAGES.index(current["stage"]):
            raise RunStateError("material change does not apply at the current stage")
        changed = current["artifact_index"].get(changed_artifact_id)
        if changed is None or changed["derived_status"] != "accepted":
            raise RunStateError("changed artifact is missing or already invalidated")
        if changed["type"] not in rule["changed_types"]:
            raise RunStateError("material change kind does not match changed artifact type")
        _require_selected_change_artifact(
            run,
            current,
            changed_artifact_id,
            changed["type"],
        )
        if change_kind != "target-snapshot":
            _validate_target_unchanged(current, root)
        invalidated_records = [
            record
            for record in current["artifact_index"].values()
            if record["type"] in invalidated_types
            and record["derived_status"] == "accepted"
            and record["artifact_id"] != changed_artifact_id
        ]
        invalidation_id = f"invalidation-{expected_sequence + 1:08d}"
        payload = {
            "schema_version": "skill-builder-invalidation.v1",
            "change_kind": change_kind,
            "changed_artifact_id": changed_artifact_id,
            "changed_artifact_digest": changed["digest"],
            "reason": reason,
            "invalidated_artifact_ids": sorted(
                record["artifact_id"] for record in invalidated_records
            ),
        }
        existing_bindings = [
            {
                "artifact_id": changed_artifact_id,
                "envelope_digest": changed["digest"],
                "status": "superseded",
            },
            *[
                {
                    "artifact_id": record["artifact_id"],
                    "envelope_digest": record["digest"],
                    "status": "invalidated",
                }
                for record in invalidated_records
            ],
        ]
        derived, _, _ = _append_transaction(
            run=run,
            current=current,
            event="invalidate",
            destination_stage=destination,
            authority_event_digest=None,
            existing_bindings=existing_bindings,
            artifact_requests=[
                {
                    "artifact_id": invalidation_id,
                    "artifact_type": "invalidation-record",
                    "files": {"record.json": canonical_json_bytes(payload)},
                    "primary_path": "record.json",
                    "producer": "main-agent",
                    "input_bindings": [
                        {
                            "artifact_id": changed_artifact_id,
                            "digest": changed["digest"],
                        }
                    ],
                    "limitations": [],
                }
            ],
        )
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "invalidate",
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
            "invalidated_artifact_ids": sorted(
                record["artifact_id"] for record in invalidated_records
            ),
        }


_SCORE_CATEGORIES = (
    "triggering",
    "scope discipline",
    "workflow quality",
    "collaboration",
    "output contract",
    "safety",
    "recovery",
    "composability",
    "context efficiency",
    "testability",
)
_CONFORMANCE_GATES = frozenset(f"BR{index}" for index in range(1, 11))
_SCORE_CRITERIA = {
    "triggering": frozenset(f"TR{index}" for index in range(1, 11)),
    "scope discipline": frozenset(f"SC{index}" for index in range(1, 11)),
    "workflow quality": frozenset(f"WF{index}" for index in range(1, 11)),
    "collaboration": frozenset(f"CO{index}" for index in range(1, 11)),
    "output contract": frozenset(f"OU{index}" for index in range(1, 11)),
    "safety": frozenset(f"SA{index}" for index in range(1, 11)),
    "recovery": frozenset(f"RE{index}" for index in range(1, 11)),
    "composability": frozenset(f"CP{index}" for index in range(1, 11)),
    "context efficiency": frozenset(f"CE{index}" for index in range(1, 11)),
    "testability": frozenset(f"TE{index}" for index in range(1, 11)),
}
_MATERIAL_REVIEW_SEVERITIES = frozenset(
    {"critical", "important", "high", "medium"}
)


def _current_event_artifact(
    run: Path,
    current: dict[str, Any],
    event: str,
    artifact_type: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    artifact_id, envelope = _event_artifact(
        run, event, artifact_type, event_artifacts
    )
    record = current["artifact_index"].get(artifact_id)
    if record is None or record["derived_status"] != "accepted" or record["digest"] != envelope["envelope_digest"]:
        raise RunStateError(f"current {artifact_type} evidence is invalidated or stale")
    return artifact_id, envelope, _artifact_payload_json(run, artifact_id)


def _validate_final_evidence(
    run: Path,
    current: dict[str, Any],
    release_artifact_id: str,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    candidate_id, candidate_envelope, candidate = _current_event_artifact(
        run, current, "accept-candidate", "candidate-record", event_artifacts
    )
    candidate_digest = candidate_envelope["envelope_digest"]
    candidate_revision = candidate.get("candidate_revision")
    if not isinstance(candidate_revision, str) or not candidate_revision:
        raise RunStateError("candidate revision is missing")
    trials_id, _, trials = _current_event_artifact(
        run, current, "complete-trials", "trial-pack", event_artifacts
    )
    if (
        trials.get("candidate_digest") != candidate_digest
        or trials.get("candidate_revision") != candidate_revision
        or trials.get("status") != "pass"
    ):
        raise RunStateError("trial evidence is stale or failing")
    review_id, review_envelope, review = _current_event_artifact(
        run, current, "accept-review", "review-record", event_artifacts
    )
    _validate_review_binding(run, current, review_id, event_artifacts)
    findings = review.get("findings")
    if (
        review.get("candidate_digest") != candidate_digest
        or review.get("candidate_revision") != candidate_revision
        or review.get("independent") is not True
        or review.get("read_only") is not True
        or review.get("valid") is not True
        or review.get("fresh") is not True
        or review.get("verdict") != "ready"
        or not isinstance(findings, list)
        or any(
            isinstance(item, dict)
            and (
                item.get("release_blocking") is True
                or item.get("severity") in _MATERIAL_REVIEW_SEVERITIES
            )
            for item in findings
        )
    ):
        raise RunStateError("final review is stale, invalid, or not ready")
    conformance_id, conformance_envelope, conformance = _current_event_artifact(
        run,
        current,
        "accept-scores",
        "builder-run-conformance-ledger",
        event_artifacts,
    )
    gates = conformance.get("gates")
    if (
        conformance.get("candidate_digest") != candidate_digest
        or not isinstance(gates, dict)
        or set(gates) != _CONFORMANCE_GATES
        or any(
            not isinstance(gate, dict)
            or gate.get("status") != "pass"
            or not isinstance(gate.get("evidence_digests"), list)
            or not gate["evidence_digests"]
            or any(not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest) for digest in gate["evidence_digests"])
            for gate in gates.values()
        )
    ):
        raise RunStateError("builder-run conformance is incomplete or failing")
    scorecard_id, scorecard_envelope, scorecard = _current_event_artifact(
        run, current, "accept-scores", "target-scorecard", event_artifacts
    )
    _validate_score_bindings(
        run,
        current,
        conformance_id,
        scorecard_id,
        event_artifacts,
    )
    categories = scorecard.get("categories")
    if (
        scorecard.get("candidate_digest") != candidate_digest
        or scorecard.get("candidate_revision") != candidate_revision
        or scorecard.get("review_digest") != review_envelope["envelope_digest"]
        or not isinstance(categories, list)
        or [item.get("name") if isinstance(item, dict) else None for item in categories]
        != list(_SCORE_CATEGORIES)
    ):
        raise RunStateError("target scorecard binding or categories are invalid")
    for category in categories:
        criteria = category.get("criteria")
        criterion_results_pass = (
            all(value is True for value in criteria.values())
            if scorecard.get("schema_version") == LEGACY_SCORECARD_SCHEMA
            else all(
                isinstance(value, dict) and value.get("passed") is True
                for value in criteria.values()
            )
            if isinstance(criteria, dict)
            else False
        )
        if (
            category.get("score") != 10
            or isinstance(category.get("score"), bool)
            or not isinstance(criteria, dict)
            or set(criteria) != _SCORE_CRITERIA[category["name"]]
            or not criterion_results_pass
        ):
            raise RunStateError("every target score and binary criterion must pass at 10")
    verification_id, verification_envelope, verification = _current_event_artifact(
        run, current, "accept-verification", "verification-record", event_artifacts
    )
    _validate_verification_binding(
        run, current, verification_id, event_artifacts
    )
    commands = verification.get("commands")
    if (
        verification.get("candidate_digest") != candidate_digest
        or verification.get("candidate_revision") != candidate_revision
        or verification.get("independent") is not True
        or verification.get("read_only") is not True
        or verification.get("fresh") is not True
        or verification.get("status") != "pass"
        or not isinstance(commands, list)
        or not commands
        or any(
            not isinstance(command, dict)
            or not isinstance(command.get("command"), str)
            or not command["command"]
            or command.get("exit_status") != 0
            or not isinstance(command.get("output_digest"), str)
            or not _DIGEST_RE.fullmatch(command["output_digest"])
            for command in commands
        )
    ):
        raise RunStateError("verification is stale, non-independent, or failing")
    release_record = current["artifact_index"].get(release_artifact_id)
    if (
        release_record is None
        or release_record["type"] != "release-record"
        or release_record["derived_status"] != "accepted"
    ):
        raise RunStateError("current release record is missing")
    release_envelope, _ = _validate_envelope(run, release_artifact_id)
    release = _artifact_payload_json(run, release_artifact_id)
    _require_retained_raw_digests(
        run,
        release_artifact_id,
        [release["release_evidence_manifest_digest"]],
        "release record",
    )
    contract_id, contract_envelope = _event_artifact(
        run, "accept-contract", "skill-contract", event_artifacts
    )
    confirmation_id, confirmation_envelope = _event_artifact(
        run, "confirm-contract", "user-confirmation-record", event_artifacts
    )
    evaluation_id, evaluation_envelope = _event_artifact(
        run, "freeze-evaluation", "evaluation-pack", event_artifacts
    )
    evaluation = _artifact_payload_json(run, evaluation_id)
    if (
        scorecard.get("evaluation_digest") != evaluation_envelope["envelope_digest"]
        or scorecard.get("rubric_digest") != evaluation.get("rubric_digest")
    ):
        raise RunStateError("target scorecard is not bound to the frozen evaluation")
    expected_release = {
        "target_identity": current["target_identity"]["canonical"],
        "candidate_digest": candidate_digest,
        "candidate_revision": candidate_revision,
        "contract_digest": contract_envelope["envelope_digest"],
        "confirmation_digest": confirmation_envelope["envelope_digest"],
        "evaluation_digest": evaluation_envelope["envelope_digest"],
        "conformance_digest": conformance_envelope["envelope_digest"],
        "scorecard_digest": scorecard_envelope["envelope_digest"],
        "review_digest": review_envelope["envelope_digest"],
        "verification_digest": verification_envelope["envelope_digest"],
    }
    if any(release.get(key) != value for key, value in expected_release.items()):
        raise RunStateError("release record has a stale or substituted evidence binding")
    if not isinstance(release.get("retained_limitations"), list) or not isinstance(release.get("authorized_delivery_scope"), list):
        raise RunStateError("release limitations or delivery scope are malformed")
    return [
        candidate_id,
        trials_id,
        review_id,
        conformance_id,
        scorecard_id,
        verification_id,
        release_artifact_id,
        contract_id,
        confirmation_id,
        evaluation_id,
    ]


def _validate_delivery_record_binding(
    run: Path,
    current: dict[str, Any],
    delivery_id: str,
    authority_event_digest: str | None,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    delivery_envelope, raw_digests = _artifact_raw_digests(run, delivery_id)
    delivery = _artifact_payload_json(run, delivery_id)
    candidate_id, candidate_envelope, candidate = _current_event_artifact(
        run,
        current,
        "accept-candidate",
        "candidate-record",
        event_artifacts,
    )
    release_id, release_envelope, release = _current_event_artifact(
        run, current, "finalize", "release-record", event_artifacts
    )
    delivery_scope = f"{delivery['action']}:{delivery['destination_identity']}"
    if (
        delivery["user_authority_event_digest"] != authority_event_digest
        or raw_digests.get("authority-event.bin") != authority_event_digest
        or delivery["finalized_revision"] != candidate["candidate_revision"]
        or delivery["candidate_digest"] != candidate_envelope["envelope_digest"]
        or delivery["release_digest"] != release_envelope["envelope_digest"]
        or delivery_scope not in current["authority"]["delivery_effects"]
        or delivery_scope not in release["authorized_delivery_scope"]
    ):
        raise RunStateError("delivery authority, revision, or release binding is invalid")
    retained_evidence = sorted(
        digest
        for path, digest in raw_digests.items()
        if path.startswith("evidence/")
    )
    if retained_evidence != sorted(delivery["acceptance_evidence"]):
        raise RunStateError("delivery acceptance evidence lacks retained raw bytes")
    _validate_delivery_destination(current, delivery, candidate)
    _require_input_bindings(
        delivery_envelope,
        {
            candidate_id: candidate_envelope["envelope_digest"],
            release_id: release_envelope["envelope_digest"],
        },
        "delivery acceptance",
    )


def _validate_cleanup_authority_binding(
    run: Path,
    current: dict[str, Any],
    authority_id: str,
    authority_event_digest: str | None,
    event_artifacts: dict[str, dict[str, Any]] | None = None,
) -> None:
    authority_envelope, raw_digests = _artifact_raw_digests(run, authority_id)
    cleanup_authority = _artifact_payload_json(run, authority_id)
    delivery_id, delivery_envelope, delivery = _current_event_artifact(
        run,
        current,
        "record-delivery",
        "delivery-acceptance-record",
        event_artifacts,
    )
    if (
        cleanup_authority["workflow_id"] != current["workflow_id"]
        or cleanup_authority["target_identity"]
        != current["target_identity"]["canonical"]
        or cleanup_authority["authority_event_digest"] != authority_event_digest
        or raw_digests.get("authority-event.bin") != authority_event_digest
        or cleanup_authority["accepted_delivery_digest"]
        != delivery_envelope["envelope_digest"]
        or delivery["accepted"] is not True
    ):
        raise RunStateError("cleanup authority or accepted-delivery binding is invalid")
    _require_input_bindings(
        authority_envelope,
        {delivery_id: delivery_envelope["envelope_digest"]},
        "cleanup authority",
    )


def finalize_run(
    *,
    workflow_id: str,
    expected_sequence: int,
    release_artifact_id: str,
    state_root: Path | None = None,
) -> dict[str, Any]:
    if isinstance(expected_sequence, bool) or not isinstance(expected_sequence, int) or expected_sequence < 0:
        raise RunStateError("expected sequence must be a nonnegative integer")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] != "verified":
            raise RunStateError("finalization requires the verified stage")
        _validate_target_unchanged(current, root)
        artifact_ids = _validate_final_evidence(run, current, release_artifact_id)
        bindings = [
            {
                "artifact_id": artifact_id,
                "envelope_digest": current["artifact_index"][artifact_id]["digest"],
                "status": "accepted",
            }
            for artifact_id in artifact_ids
        ]
        derived, _, _ = _append_transaction(
            run=run,
            current=current,
            event="finalize",
            destination_stage="finalized",
            authority_event_digest=None,
            existing_bindings=bindings,
            artifact_requests=[],
        )
        lock_path = root / "target-locks" / _target_lock_name(
            current["target_identity"]["canonical"]
        )
        _validate_regular(lock_path, "active-target lock")
        expected_lock = {
            "schema_version": "skill-builder-target-lock.v1",
            **current["active_target_lock"],
            "workflow_id": workflow_id,
        }
        if _read_json(lock_path) != expected_lock:
            raise RunStateError("active-target lock changed before finalization")
        lock_path.unlink()
        _fsync_directory(lock_path.parent)
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "finalize",
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
            "release_artifact_id": release_artifact_id,
            "release_artifact_digest": current["artifact_index"][release_artifact_id]["digest"],
        }


def record_delivery(
    *,
    workflow_id: str,
    expected_sequence: int,
    delivery: dict[str, Any],
    authority_event_digest: str,
    authority_event: bytes | None = None,
    evidence_files: dict[str, bytes] | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(delivery, dict) or set(delivery) != {
        "action",
        "destination_identity",
        "finalized_revision",
        "resulting_destination_digest",
        "acceptance_evidence",
        "user_authority_event_digest",
        "actor",
        "accepted",
    }:
        raise RunStateError("delivery-acceptance schema is incomplete")
    if delivery.get("action") not in {"installation", "integration"} or delivery.get("accepted") is not True:
        raise RunStateError("delivery is not an accepted installation or integration")
    if delivery.get("user_authority_event_digest") != authority_event_digest or not isinstance(authority_event_digest, str) or not _DIGEST_RE.fullmatch(authority_event_digest):
        raise RunStateError("delivery authority binding is invalid")
    for field in ("destination_identity", "finalized_revision", "actor"):
        if not isinstance(delivery.get(field), str) or not delivery[field].strip():
            raise RunStateError("delivery identity fields must be nonempty text")
    if not isinstance(delivery.get("resulting_destination_digest"), str) or not _DIGEST_RE.fullmatch(delivery["resulting_destination_digest"]):
        raise RunStateError("delivery destination digest is invalid")
    evidence = delivery.get("acceptance_evidence")
    if not isinstance(evidence, list) or not evidence or any(
        not isinstance(item, str) or not _DIGEST_RE.fullmatch(item) for item in evidence
    ):
        raise RunStateError("delivery acceptance evidence is incomplete")
    if not isinstance(authority_event, bytes):
        raise RunStateError("delivery authority event must be retained as raw bytes")
    if raw_digest(authority_event) != authority_event_digest:
        raise RunStateError("raw delivery authority does not match its digest")
    if not isinstance(evidence_files, dict) or not evidence_files:
        raise RunStateError("delivery acceptance evidence files are required")
    raw_evidence_digests: list[str] = []
    for name, content in evidence_files.items():
        _safe_artifact_path(name)
        if not isinstance(content, bytes):
            raise RunStateError("delivery evidence values must be raw bytes")
        raw_evidence_digests.append(raw_digest(content))
    if sorted(raw_evidence_digests) != sorted(evidence):
        raise RunStateError("raw delivery evidence does not match declared digests")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] != "finalized":
            raise RunStateError("delivery may be recorded only for a finalized run")
        candidate_id, candidate_envelope, candidate = _current_event_artifact(
            run, current, "accept-candidate", "candidate-record"
        )
        _validate_delivery_destination(current, delivery, candidate)
        if delivery["finalized_revision"] != candidate.get("candidate_revision"):
            raise RunStateError("delivery revision does not match the finalized candidate")
        release_id, release_envelope, release = _current_event_artifact(
            run, current, "finalize", "release-record"
        )
        delivery_scope = f"{delivery['action']}:{delivery['destination_identity']}"
        if (
            delivery_scope not in current["authority"]["delivery_effects"]
            or delivery_scope not in release["authorized_delivery_scope"]
        ):
            raise RunStateError("delivery action or destination exceeds recorded authority scope")
        artifact_id = f"delivery-{expected_sequence + 1:08d}"
        payload = {
            "schema_version": "skill-builder-delivery-acceptance.v1",
            **delivery,
            "candidate_digest": candidate_envelope["envelope_digest"],
            "release_digest": release_envelope["envelope_digest"],
            "timestamp": _now(),
        }
        files = {
            "record.json": canonical_json_bytes(payload),
            "authority-event.bin": authority_event,
            **{
                f"evidence/{name}": content
                for name, content in evidence_files.items()
            },
        }
        derived, created, _ = _append_transaction(
            run=run,
            current=current,
            event="record-delivery",
            destination_stage="delivered",
            authority_event_digest=authority_event_digest,
            existing_bindings=[],
            artifact_requests=[
                {
                    "artifact_id": artifact_id,
                    "artifact_type": "delivery-acceptance-record",
                    "files": files,
                    "primary_path": "record.json",
                    "producer": delivery["actor"],
                    "input_bindings": [
                        {
                            "artifact_id": candidate_id,
                            "digest": candidate_envelope["envelope_digest"],
                        },
                        {
                            "artifact_id": release_id,
                            "digest": release_envelope["envelope_digest"],
                        },
                    ],
                    "limitations": [],
                }
            ],
        )
        envelope = created[artifact_id]
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "record-delivery",
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
            "delivery_artifact_id": artifact_id,
            "delivery_artifact_digest": envelope["envelope_digest"],
        }


def record_cleanup_authority(
    *,
    workflow_id: str,
    expected_sequence: int,
    authority_event_digest: str,
    authority_event: bytes | None = None,
    actor: str,
    state_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(authority_event_digest, str) or not _DIGEST_RE.fullmatch(authority_event_digest):
        raise RunStateError("cleanup-authority event digest is invalid")
    if not isinstance(authority_event, bytes):
        raise RunStateError("cleanup-authority event must be retained as raw bytes")
    if raw_digest(authority_event) != authority_event_digest:
        raise RunStateError("cleanup-authority raw event digest does not match")
    if not isinstance(actor, str) or not actor.strip():
        raise RunStateError("cleanup-authority actor is required")
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] != "delivered":
            raise RunStateError("cleanup authority requires accepted delivery")
        delivery_id, delivery_envelope, _ = _current_event_artifact(
            run, current, "record-delivery", "delivery-acceptance-record"
        )
        artifact_id = f"cleanup-authority-{expected_sequence + 1:08d}"
        payload = {
            "schema_version": "skill-builder-cleanup-authority.v1",
            "workflow_id": workflow_id,
            "target_identity": current["target_identity"]["canonical"],
            "action": "cleanup",
            "authorized": True,
            "authority_event_digest": authority_event_digest,
            "actor": actor,
            "timestamp": _now(),
            "accepted_delivery_digest": delivery_envelope["envelope_digest"],
        }
        derived, created, _ = _append_transaction(
            run=run,
            current=current,
            event="record-cleanup-authority",
            destination_stage="delivered",
            authority_event_digest=authority_event_digest,
            existing_bindings=[],
            artifact_requests=[
                {
                    "artifact_id": artifact_id,
                    "artifact_type": "cleanup-authority-record",
                    "files": {
                        "record.json": canonical_json_bytes(payload),
                        "authority-event.bin": authority_event,
                    },
                    "primary_path": "record.json",
                    "producer": actor,
                    "input_bindings": [
                        {
                            "artifact_id": delivery_id,
                            "digest": delivery_envelope["envelope_digest"],
                        }
                    ],
                    "limitations": [],
                }
            ],
        )
        envelope = created[artifact_id]
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "record-cleanup-authority",
            "workflow_id": workflow_id,
            "sequence": derived["head_sequence"],
            "stage": derived["stage"],
            "receipt_digest": derived["head_transition_digest"],
            "authority_artifact_id": artifact_id,
            "authority_artifact_digest": envelope["envelope_digest"],
        }


_TOMBSTONE_FIELDS = {
    "schema_version",
    "workflow_id",
    "target_identity",
    "run_directory_identity",
    "final_transition_receipt_digest",
    "final_run_manifest_digest",
    "accepted_delivery_record_digest",
    "cleanup_authority_event_digest",
    "created_at",
    "tombstone_digest",
}


def _validate_tombstone(path: Path, workflow_id: str) -> dict[str, Any]:
    tombstone = _read_json(path)
    if (
        set(tombstone) != _TOMBSTONE_FIELDS
        or tombstone.get("schema_version") != "skill-builder-cleanup-tombstone.v1"
        or tombstone.get("workflow_id") != workflow_id
        or tombstone.get("tombstone_digest")
        != canonical_digest(tombstone, "tombstone_digest")
    ):
        raise RunStateError("cleanup tombstone is invalid")
    for field in (
        "final_transition_receipt_digest",
        "final_run_manifest_digest",
        "accepted_delivery_record_digest",
        "cleanup_authority_event_digest",
        "tombstone_digest",
    ):
        if not isinstance(tombstone.get(field), str) or not _DIGEST_RE.fullmatch(tombstone[field]):
            raise RunStateError("cleanup tombstone contains an invalid digest")
    _text(tombstone.get("target_identity"), "cleanup tombstone target identity")
    _timestamp(tombstone.get("created_at"), "cleanup tombstone creation time")
    identity = tombstone.get("run_directory_identity")
    if not isinstance(identity, dict) or set(identity) != {"name", "device", "inode"} or identity.get("name") != workflow_id:
        raise RunStateError("cleanup tombstone run-directory identity is invalid")
    _exact_integer(identity.get("device"), "cleanup tombstone device", minimum=1)
    _exact_integer(identity.get("inode"), "cleanup tombstone inode", minimum=1)
    return tombstone


def _final_run_manifest(run: Path, current: dict[str, Any]) -> dict[str, Any]:
    chain = _load_receipt_chain(run)
    finalization_receipt = next(
        (receipt for receipt in reversed(chain) if receipt["event"] == "finalize"),
        None,
    )
    if finalization_receipt is None:
        raise RunStateError("final run manifest lacks a finalization receipt")
    _, release_envelope = _event_artifact(run, "finalize", "release-record")
    _, delivery_envelope = _event_artifact(
        run, "record-delivery", "delivery-acceptance-record"
    )
    entries: list[dict[str, Any]] = []
    for path in sorted(run.rglob("*")):
        relative = path.relative_to(run).as_posix()
        if relative in {"current.json", "final-run-manifest.json"}:
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise RunStateError("run manifest encountered a symlink")
        if stat.S_ISDIR(info.st_mode):
            _validate_private_directory(path, relative)
            continue
        payload = _read_bytes(path, MAX_ARTIFACT_BYTES)
        entries.append(
            {
                "path": relative,
                "byte_count": len(payload),
                "digest": raw_digest(payload),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": "skill-builder-final-run-manifest.v1",
        "workflow_id": current["workflow_id"],
        "target_identity": current["target_identity"]["canonical"],
        "finalization_receipt_digest": finalization_receipt["receipt_digest"],
        "release_record_digest": release_envelope["envelope_digest"],
        "accepted_delivery_record_digest": delivery_envelope["envelope_digest"],
        "head_transition_digest": current["head_transition_digest"],
        "entries": entries,
        "observed_item_count": len(entries),
        "observed_byte_count": sum(item["byte_count"] for item in entries),
    }
    manifest["manifest_digest"] = canonical_digest(manifest, "manifest_digest")
    return manifest


def _remove_owned_tree(path: Path) -> None:
    """Remove one owned tree without reopening validated descendants by pathname."""

    def directory_descriptor(descriptor: int, label: str) -> os.stat_result:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise RunStateError(f"{label} must be owned and mode 0700")
        return info

    def same_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            stat.S_IFMT(first.st_mode),
        ) == (
            second.st_dev,
            second.st_ino,
            stat.S_IFMT(second.st_mode),
        )

    def remove_contents(descriptor: int, display: Path) -> None:
        directory_descriptor(descriptor, "cleanup directory")
        with os.scandir(descriptor) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            child_path = display / name
            try:
                observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise RunStateError("cleanup entry changed during deletion") from error
            if stat.S_ISLNK(observed.st_mode):
                raise RunStateError("cleanup boundary contains a symlink")
            if stat.S_ISDIR(observed.st_mode):
                # The pathname validation preserves the public safety contract;
                # the subsequent openat + identity check closes its race window.
                _validate_private_directory(child_path, "cleanup directory")
                child_descriptor: int | None = None
                try:
                    child_descriptor = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    opened = directory_descriptor(
                        child_descriptor, "cleanup directory"
                    )
                    if not same_identity(observed, opened):
                        raise RunStateError("cleanup directory identity changed")
                    remove_contents(child_descriptor, child_path)
                except OSError as error:
                    raise RunStateError(
                        "cleanup directory changed during deletion"
                    ) from error
                finally:
                    if child_descriptor is not None:
                        os.close(child_descriptor)
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not same_identity(opened, current):
                    raise RunStateError("cleanup directory identity changed")
                os.rmdir(name, dir_fd=descriptor)
            elif stat.S_ISREG(observed.st_mode):
                file_descriptor: int | None = None
                try:
                    file_descriptor = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                    opened = os.fstat(file_descriptor)
                    if (
                        not same_identity(observed, opened)
                        or opened.st_uid != os.geteuid()
                        or stat.S_IMODE(opened.st_mode) != 0o600
                        or opened.st_nlink != 1
                    ):
                        raise RunStateError("cleanup file ownership is invalid")
                except OSError as error:
                    raise RunStateError("cleanup file changed during deletion") from error
                finally:
                    if file_descriptor is not None:
                        os.close(file_descriptor)
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if not same_identity(opened, current):
                    raise RunStateError("cleanup file identity changed")
                os.unlink(name, dir_fd=descriptor)
            else:
                raise RunStateError("cleanup boundary contains an unowned file type")

    _validate_private_directory(path, "cleanup directory")
    parent_descriptor: int | None = None
    root_descriptor: int | None = None
    try:
        parent_descriptor = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        observed = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        root_descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        opened = directory_descriptor(root_descriptor, "cleanup boundary")
        if not same_identity(observed, opened):
            raise RunStateError("cleanup boundary identity changed")
        remove_contents(root_descriptor, path)
        current = os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not same_identity(opened, current):
            raise RunStateError("cleanup boundary identity changed")
        os.rmdir(path.name, dir_fd=parent_descriptor)
    except OSError as error:
        raise RunStateError("cleanup boundary changed during deletion") from error
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _move_to_deleting(run: Path, deleting: Path) -> None:
    """Atomically move one validated run into its retryable deletion boundary."""
    _validate_private_directory(run, "live run")
    _validate_private_directory(run.parent, "live")
    _validate_private_directory(deleting.parent, "deleting")
    if os.path.lexists(deleting):
        raise RunStateError("cleanup deletion boundary already exists")
    before = run.lstat()
    try:
        os.rename(run, deleting)
    except OSError as error:
        raise RunStateError("cleanup boundary move failed") from error
    _fsync_directory(run.parent)
    _fsync_directory(deleting.parent)
    _validate_private_directory(deleting, "deleting run")
    after = deleting.lstat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise RunStateError("cleanup boundary identity changed during move")


def cleanup_run(
    *,
    workflow_id: str,
    expected_sequence: int,
    acknowledge_cleanup: bool,
    state_root: Path | None = None,
) -> dict[str, Any]:
    if acknowledge_cleanup is not True:
        raise RunStateError("cleanup requires explicit acknowledgement")
    if (
        isinstance(expected_sequence, bool)
        or not isinstance(expected_sequence, int)
        or expected_sequence < 0
    ):
        raise RunStateError("expected sequence must be a nonnegative integer")
    with _locked_root(state_root, create=False) as root:
        tombstone_path = root / "tombstones" / f"{workflow_id}.json"
        run_candidate = root / "live" / workflow_id
        deleting_candidate = root / "deleting" / workflow_id
        if os.path.lexists(tombstone_path):
            tombstone = _validate_tombstone(tombstone_path, workflow_id)
            live_exists = os.path.lexists(run_candidate)
            deleting_exists = os.path.lexists(deleting_candidate)
            if live_exists and deleting_exists:
                raise RunStateError("cleanup has conflicting deletion boundaries")
            if not live_exists and not deleting_exists:
                return {
                    "schema_version": "skill-builder-operation.v1",
                    "operation": "cleanup",
                    "workflow_id": workflow_id,
                    "sequence": expected_sequence,
                    "stage": "cleaned",
                    "receipt_digest": tombstone[
                        "final_transition_receipt_digest"
                    ],
                    "tombstone_digest": tombstone["tombstone_digest"],
                }
            expected_identity = tombstone["run_directory_identity"]
            if deleting_exists:
                _validate_private_directory(deleting_candidate, "deleting run")
                deleting_info = deleting_candidate.lstat()
                if (
                    deleting_info.st_dev != expected_identity["device"]
                    or deleting_info.st_ino != expected_identity["inode"]
                ):
                    raise RunStateError(
                        "cleanup retry deletion-boundary identity changed"
                    )
                _remove_owned_tree(deleting_candidate)
                _fsync_directory(root / "deleting")
                return {
                    "schema_version": "skill-builder-operation.v1",
                    "operation": "cleanup",
                    "workflow_id": workflow_id,
                    "sequence": expected_sequence,
                    "stage": "cleaned",
                    "receipt_digest": tombstone[
                        "final_transition_receipt_digest"
                    ],
                    "tombstone_digest": tombstone["tombstone_digest"],
                }
            _validate_private_directory(run_candidate, "live run")
            current = _derive_index(run_candidate)
            current_path = run_candidate / "current.json"
            if os.path.lexists(current_path) and _read_json(current_path) != current:
                raise RunStateError("cleanup retry current index is stale or corrupt")
            if current["head_sequence"] != expected_sequence:
                raise RevisionConflict("cleanup retry receipt sequence conflict")
            if current["stage"] != "delivered":
                raise RunStateError("cleanup retry requires accepted delivery")
            _validate_target_unchanged(current, root)
            _, delivery_envelope, delivery = _current_event_artifact(
                run_candidate,
                current,
                "record-delivery",
                "delivery-acceptance-record",
            )
            _, _, cleanup_authority = _current_event_artifact(
                run_candidate,
                current,
                "record-cleanup-authority",
                "cleanup-authority-record",
            )
            final_manifest = _final_run_manifest(run_candidate, current)
            final_manifest_path = run_candidate / "final-run-manifest.json"
            if (
                not os.path.lexists(final_manifest_path)
                or _read_json(final_manifest_path) != final_manifest
                or delivery.get("accepted") is not True
                or cleanup_authority.get("authorized") is not True
                or tombstone["target_identity"]
                != current["target_identity"]["canonical"]
                or tombstone["final_transition_receipt_digest"]
                != current["head_transition_digest"]
                or tombstone["final_run_manifest_digest"]
                != final_manifest["manifest_digest"]
                or tombstone["accepted_delivery_record_digest"]
                != delivery_envelope["envelope_digest"]
                or tombstone["cleanup_authority_event_digest"]
                != cleanup_authority["authority_event_digest"]
            ):
                raise RunStateError("cleanup retry tombstone bindings are invalid")
            run_info = run_candidate.lstat()
            if (
                run_info.st_dev != expected_identity["device"]
                or run_info.st_ino != expected_identity["inode"]
            ):
                raise RunStateError("cleanup retry run-directory identity changed")
            _move_to_deleting(run_candidate, deleting_candidate)
            _remove_owned_tree(deleting_candidate)
            _fsync_directory(root / "deleting")
            return {
                "schema_version": "skill-builder-operation.v1",
                "operation": "cleanup",
                "workflow_id": workflow_id,
                "sequence": expected_sequence,
                "stage": "cleaned",
                "receipt_digest": tombstone["final_transition_receipt_digest"],
                "tombstone_digest": tombstone["tombstone_digest"],
            }
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] != "delivered":
            raise RunStateError("cleanup requires accepted delivery")
        _validate_target_unchanged(current, root)
        delivery_id, delivery_envelope, delivery = _current_event_artifact(
            run, current, "record-delivery", "delivery-acceptance-record"
        )
        authority_id, _, cleanup_authority = _current_event_artifact(
            run, current, "record-cleanup-authority", "cleanup-authority-record"
        )
        authority_receipt = next(
            receipt
            for receipt in reversed(_load_receipt_chain(run))
            if receipt["event"] == "record-cleanup-authority"
        )
        if (
            delivery.get("accepted") is not True
            or cleanup_authority.get("authorized") is not True
            or cleanup_authority.get("accepted_delivery_digest")
            != delivery_envelope["envelope_digest"]
            or cleanup_authority.get("authority_event_digest")
            != authority_receipt["authority_event_digest"]
        ):
            raise RunStateError("cleanup authority or accepted delivery is stale")
        final_manifest = _final_run_manifest(run, current)
        final_manifest_path = run / "final-run-manifest.json"
        if os.path.lexists(final_manifest_path):
            if _read_json(final_manifest_path) != final_manifest:
                raise RunStateError("existing final run manifest is stale")
        else:
            _exclusive_json(final_manifest_path, final_manifest)
        run_info = run.lstat()
        tombstone: dict[str, Any] = {
            "schema_version": "skill-builder-cleanup-tombstone.v1",
            "workflow_id": workflow_id,
            "target_identity": current["target_identity"]["canonical"],
            "run_directory_identity": {
                "name": workflow_id,
                "device": run_info.st_dev,
                "inode": run_info.st_ino,
            },
            "final_transition_receipt_digest": current["head_transition_digest"],
            "final_run_manifest_digest": final_manifest["manifest_digest"],
            "accepted_delivery_record_digest": delivery_envelope["envelope_digest"],
            "cleanup_authority_event_digest": cleanup_authority[
                "authority_event_digest"
            ],
            "created_at": _now(),
        }
        tombstone["tombstone_digest"] = canonical_digest(
            tombstone, "tombstone_digest"
        )
        if os.path.lexists(tombstone_path):
            stored = _validate_tombstone(tombstone_path, workflow_id)
            comparable = dict(tombstone)
            comparable["created_at"] = stored["created_at"]
            comparable["tombstone_digest"] = canonical_digest(
                comparable, "tombstone_digest"
            )
            if stored != comparable:
                raise RunStateError("existing cleanup tombstone has different bindings")
            tombstone = stored
        else:
            _exclusive_json(tombstone_path, tombstone)
            if _validate_tombstone(tombstone_path, workflow_id) != tombstone:
                raise RunStateError("cleanup tombstone read-back mismatch")
        # The durable, read-back-validated tombstone is the sole authority for
        # this exact deletion boundary.  No target or external path is visited.
        _move_to_deleting(run, deleting_candidate)
        _remove_owned_tree(deleting_candidate)
        _fsync_directory(root / "deleting")
        return {
            "schema_version": "skill-builder-operation.v1",
            "operation": "cleanup",
            "workflow_id": workflow_id,
            "sequence": expected_sequence,
            "stage": "cleaned",
            "receipt_digest": current["head_transition_digest"],
            "tombstone_digest": tombstone["tombstone_digest"],
            "delivery_artifact_id": delivery_id,
            "authority_artifact_id": authority_id,
        }


def _cli_json() -> dict[str, Any]:
    payload = sys.stdin.buffer.read(MAX_CLI_JSON_BYTES + 1)
    if len(payload) > MAX_CLI_JSON_BYTES:
        raise RunStateError("CLI JSON input is oversized")
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeError, ValueError) as error:
        raise RunStateError("CLI input is not strict JSON") from error
    if not isinstance(value, dict):
        raise RunStateError("CLI input must be one JSON object")
    _json_value(value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Operate private Skill Builder run state. JSON payloads are read from "
            "stdin. --state-root is for isolated tests only. Raw retain payloads "
            f"are bounded to {MAX_ARTIFACT_ITEMS} items and {MAX_ARTIFACT_BYTES} bytes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "request schemas (one strict JSON object on stdin):\n"
            "  initialize request: host_identity, target_identity, mode, authority, "
            "and mode evidence; optional queue\n"
            "  retain request: workflow_id, expected_sequence, artifact_id, "
            "artifact_type, files_base64, primary_path, producer, input_bindings, limitations\n"
            "  transition request: workflow_id, expected_sequence, event, "
            "destination_stage, artifact_ids; optional authority_event_digest\n"
            "  deliver request: workflow_id, expected_sequence, delivery, "
            "authority_event_digest, authority_event_base64, evidence_files_base64\n"
            "  cleanup-authority request: workflow_id, expected_sequence, "
            "authority_event_digest, authority_event_base64, actor\n"
            "  cleanup request: workflow_id and expected_sequence (also requires --yes)\n"
            "  activate-next request: workflow_id, expected_sequence, mode, and mode evidence"
        ),
    )
    parser.add_argument(
        "command",
        choices=(
            "initialize",
            "discover",
            "load",
            "snapshot",
            "retain",
            "transition",
            "invalidate",
            "pause",
            "resume",
            "recover",
            "finalize",
            "deliver",
            "cleanup-authority",
            "cleanup",
            "activate-next",
        ),
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        help="explicit absolute isolated state root (tests only)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="explicitly acknowledge destructive cleanup of the exact live run",
    )
    args = parser.parse_args(argv)
    if args.command == "cleanup" and not args.yes:
        parser.error("cleanup requires --yes")
    try:
        payload = _cli_json()
        state_root = args.state_root
        if args.command == "initialize":
            result = initialize_run(state_root=state_root, **payload)
        elif args.command == "discover":
            result = discover_run(state_root=state_root, **payload)
        elif args.command == "load":
            result = load_run(state_root=state_root, **payload)
        elif args.command == "snapshot":
            if set(payload) != {"target"} or not isinstance(payload["target"], str):
                raise RunStateError("snapshot requires one target path")
            result = snapshot_target(Path(payload["target"]))
        elif args.command == "retain":
            encoded = payload.pop("files_base64", None)
            if not isinstance(encoded, dict) or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in encoded.items()
            ):
                raise RunStateError("retain requires a files_base64 object")
            files: dict[str, bytes] = {}
            for name, value in encoded.items():
                try:
                    files[name] = base64.b64decode(value, validate=True)
                except (ValueError, binascii.Error) as error:
                    raise RunStateError("retained artifact contains invalid base64") from error
            result = retain_artifact(state_root=state_root, files=files, **payload)
        elif args.command == "transition":
            result = transition_run(state_root=state_root, **payload)
        elif args.command == "invalidate":
            result = invalidate_run(state_root=state_root, **payload)
        elif args.command == "pause":
            result = pause_run(state_root=state_root, **payload)
        elif args.command == "resume":
            result = resume_run(state_root=state_root, **payload)
        elif args.command == "recover":
            result = recover_run(state_root=state_root, **payload)
        elif args.command == "finalize":
            result = finalize_run(state_root=state_root, **payload)
        elif args.command == "deliver":
            encoded_authority = payload.pop("authority_event_base64", None)
            encoded_evidence = payload.pop("evidence_files_base64", None)
            if not isinstance(encoded_authority, str) or not isinstance(
                encoded_evidence, dict
            ) or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in encoded_evidence.items()
            ):
                raise RunStateError(
                    "deliver requires authority_event_base64 and an "
                    "evidence_files_base64 object"
                )
            try:
                authority_event = base64.b64decode(
                    encoded_authority, validate=True
                )
                evidence_files = {
                    name: base64.b64decode(value, validate=True)
                    for name, value in encoded_evidence.items()
                }
            except (ValueError, binascii.Error) as error:
                raise RunStateError("delivery evidence contains invalid base64") from error
            result = record_delivery(
                state_root=state_root,
                authority_event=authority_event,
                evidence_files=evidence_files,
                **payload,
            )
        elif args.command == "cleanup-authority":
            encoded_authority = payload.pop("authority_event_base64", None)
            if not isinstance(encoded_authority, str):
                raise RunStateError(
                    "cleanup-authority requires authority_event_base64"
                )
            try:
                authority_event = base64.b64decode(
                    encoded_authority, validate=True
                )
            except (ValueError, binascii.Error) as error:
                raise RunStateError(
                    "cleanup-authority event contains invalid base64"
                ) from error
            result = record_cleanup_authority(
                state_root=state_root,
                authority_event=authority_event,
                **payload,
            )
        elif args.command == "activate-next":
            result = activate_next_target(state_root=state_root, **payload)
        else:
            result = cleanup_run(
                state_root=state_root,
                acknowledge_cleanup=True,
                **payload,
            )
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 0
    except (RunStateError, TypeError, KeyError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
