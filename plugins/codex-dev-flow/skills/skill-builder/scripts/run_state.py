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
MAX_ARTIFACT_ITEMS = 256
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
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
    "capture-baseline": {"baseline-report"},
    "complete-research": {"research-pack"},
    "sieve-evidence": {"evidence-sieve"},
    "accept-design": {"design-record"},
    "accept-contract": {"skill-contract"},
    "confirm-contract": {"user-confirmation-record"},
    "freeze-evaluation": {"evaluation-pack"},
    "accept-candidate": {"candidate-record"},
    "complete-trials": {"trial-pack"},
    "accept-review": {"review-record"},
    "accept-scores": {"builder-run-conformance-ledger", "target-scorecard"},
    "accept-verification": {"verification-record"},
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
    try:
        value = json.loads(
            _read_bytes(path).decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeError, ValueError) as error:
        raise RunStateError(f"malformed JSON record: {path.name}") from error
    if not isinstance(value, dict):
        raise RunStateError(f"{path.name} must contain an object")
    _json_value(value)
    return value


@contextmanager
def _locked_root(state_root: Path | None, *, create: bool) -> Any:
    root = _absolute_root(state_root)
    if create:
        _ensure_private_directory(root)
    else:
        _reject_symlink_components(root)
        _validate_private_directory(root, "state root")
    for name in ("live", "tombstones", "target-locks"):
        child = root / name
        if create:
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
    if any(not isinstance(value[field], str) or not value[field] for field in required - {"present"}):
        raise RunStateError("Git identity contains empty values")
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
    candidates = [locator] if locator.is_file() else sorted(locator.rglob("*"))
    if len(candidates) > MAX_TARGET_ITEMS:
        raise RunStateError("target snapshot has too many items")
    for path in candidates:
        info = path.lstat()
        relative = "." if path == locator else path.relative_to(locator).as_posix()
        if stat.S_ISLNK(info.st_mode):
            raise RunStateError("target snapshot contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            entries.append(
                {
                    "path": relative,
                    "kind": "directory",
                    "mode": stat.S_IMODE(info.st_mode),
                    "byte_count": 0,
                    "digest": None,
                }
            )
            continue
        if not stat.S_ISREG(info.st_mode):
            raise RunStateError("target snapshot contains a non-regular entry")
        payload = path.read_bytes()
        total += len(payload)
        if total > MAX_TARGET_BYTES:
            raise RunStateError("target snapshot is oversized")
        entries.append(
            {
                "path": relative,
                "kind": "file",
                "mode": stat.S_IMODE(info.st_mode),
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
    branch = str(_git_output(repository, "symbolic-ref", "--quiet", "--short", "HEAD")).strip()
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
        "schema_version": "skill-builder-resolution.v1",
        "workflow_id": workflow_id,
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
        or envelope.get("artifact_type") not in _ARTIFACT_TYPES
        or envelope.get("workflow_id") != run.name
        or envelope.get("mode") not in {"create", "improve"}
        or isinstance(envelope.get("created_sequence"), bool)
        or not isinstance(envelope.get("created_sequence"), int)
        or envelope["created_sequence"] < 0
        or not isinstance(envelope.get("created_stage"), str)
        or not isinstance(envelope.get("producer"), str)
        or not isinstance(envelope.get("target_identity"), str)
        or not isinstance(envelope.get("limitations"), list)
        or any(not isinstance(item, str) for item in envelope["limitations"])
        or envelope.get("envelope_digest") != canonical_digest(envelope, "envelope_digest")
    ):
        raise RunStateError("artifact envelope digest or identity mismatch")
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
    if not isinstance(entries, list) or not entries or len(entries) > MAX_ARTIFACT_ITEMS:
        raise RunStateError("raw-artifact manifest has an invalid item count")
    listed: set[str] = set()
    total = 0
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
        local = run.joinpath(*parts)
        payload = _read_bytes(local, MAX_ARTIFACT_BYTES)
        if entry.get("byte_count") != len(payload) or entry.get("digest") != raw_digest(payload):
            raise RunStateError("raw-artifact payload digest mismatch")
        listed.add(relative.removeprefix(expected_prefix))
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
        if receipt.get("sequence") != sequence or receipt.get("receipt_digest") != canonical_digest(receipt, "receipt_digest"):
            raise RunStateError("transition receipt sequence or digest mismatch")
        if sequence == 0:
            if receipt.get("prior_receipt_digest") is not None or receipt.get("source_stage") is not None or receipt.get("event") != "initialize":
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
                if _STAGES.index(destination) > _STAGES.index(source):
                    raise RunStateError("invalidation advanced rather than rewound state")
                statuses = {
                    item.get("status")
                    for item in receipt.get("relevant_artifact_digests", [])
                    if isinstance(item, dict)
                }
                if "invalidated" not in statuses or not statuses <= {"accepted", "superseded", "invalidated"}:
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
        relevant = receipt.get("relevant_artifact_digests")
        if not isinstance(relevant, list) or relevant != sorted(relevant, key=lambda item: (item.get("artifact_id", ""), item.get("status", "")) if isinstance(item, dict) else ("", "")):
            raise RunStateError("receipt artifact bindings are not deterministic")
        chain.append(receipt)
        prior = receipt
    return chain


def _resolution_payload(run: Path, workflow_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope, _ = _validate_envelope(run, "resolution")
    if envelope.get("workflow_id") != workflow_id:
        raise RunStateError("resolution artifact workflow mismatch")
    payload = _read_json(run / "artifacts" / "resolution" / "raw" / "payload.json")
    if payload.get("schema_version") != "skill-builder-resolution.v1" or payload.get("workflow_id") != workflow_id:
        raise RunStateError("resolution payload identity mismatch")
    return payload, envelope


def _artifact_payload_json(run: Path, artifact_id: str) -> dict[str, Any]:
    envelope, _ = _validate_envelope(run, artifact_id)
    payload_path = envelope["payload_path"]
    return _read_json(run.joinpath(*payload_path.split("/")))


def _event_artifact(
    run: Path, event: str, artifact_type: str
) -> tuple[str, dict[str, Any]]:
    for receipt in reversed(_load_receipt_chain(run)):
        if receipt["event"] != event:
            continue
        for binding in receipt["relevant_artifact_digests"]:
            envelope, _ = _validate_envelope(run, binding["artifact_id"])
            if envelope["artifact_type"] == artifact_type and binding["status"] == "accepted":
                return binding["artifact_id"], envelope
    raise RunStateError(f"current {artifact_type} binding is missing")


def _validate_confirmation_binding(
    run: Path,
    current: dict[str, Any],
    confirmation_id: str,
    authority_event_digest: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    confirmation_envelope, _ = _validate_envelope(run, confirmation_id)
    confirmation = _artifact_payload_json(run, confirmation_id)
    _, contract_envelope = _event_artifact(run, "accept-contract", "skill-contract")
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
        or confirmation.get("target_identity") != current["target_identity"]["canonical"]
        or confirmation.get("target_snapshot_digest")
        != current["target_snapshot"]["snapshot_digest"]
        or confirmation.get("confirmation_event_digest") != authority_event_digest
        or not isinstance(authority_event_digest, str)
        or not _DIGEST_RE.fullmatch(authority_event_digest)
    ):
        raise RunStateError("user confirmation binding is missing or mismatched")
    return confirmation, confirmation_envelope


def _validate_evaluation_binding(
    run: Path,
    current: dict[str, Any],
    evaluation_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evaluation_envelope, _ = _validate_envelope(run, evaluation_id)
    evaluation = _artifact_payload_json(run, evaluation_id)
    _, contract_envelope = _event_artifact(run, "accept-contract", "skill-contract")
    confirmation_id, confirmation_envelope = _event_artifact(
        run, "confirm-contract", "user-confirmation-record"
    )
    confirmation_receipt = next(
        receipt
        for receipt in reversed(_load_receipt_chain(run))
        if receipt["event"] == "confirm-contract"
    )
    _validate_confirmation_binding(
        run,
        current,
        confirmation_id,
        confirmation_receipt["authority_event_digest"],
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
    return evaluation, evaluation_envelope


def _validate_candidate_entry(
    run: Path, current: dict[str, Any], candidate_id: str
) -> None:
    candidate = _artifact_payload_json(run, candidate_id)
    _, contract_envelope = _event_artifact(run, "accept-contract", "skill-contract")
    confirmation_id, confirmation_envelope = _event_artifact(
        run, "confirm-contract", "user-confirmation-record"
    )
    evaluation_id, evaluation_envelope = _event_artifact(
        run, "freeze-evaluation", "evaluation-pack"
    )
    confirmation_receipt = next(
        receipt
        for receipt in reversed(_load_receipt_chain(run))
        if receipt["event"] == "confirm-contract"
    )
    _validate_confirmation_binding(
        run,
        current,
        confirmation_id,
        confirmation_receipt["authority_event_digest"],
    )
    _validate_evaluation_binding(run, current, evaluation_id)
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
    ):
        raise RunStateError("candidate entry binding is missing or mismatched")


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
    for receipt in chain:
        prior_artifact_status = dict(artifact_status)
        validated_bindings: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in receipt["relevant_artifact_digests"]:
            if not isinstance(item, dict) or set(item) != {"artifact_id", "envelope_digest", "status"}:
                raise RunStateError("receipt artifact binding schema is invalid")
            artifact_id = item["artifact_id"]
            envelope, _ = _validate_envelope(run, artifact_id)
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
    head = chain[-1]
    stage = head["destination_stage"]
    active_lock = resolution["active_target_lock"] if stage not in {"finalized", "delivered", "abandoned"} else None
    queue = json.loads(json.dumps(resolution["queue"], ensure_ascii=False))
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
    allowed = {"receipts", "artifacts", "current.json", "final-run-manifest.json"}
    if set(path.name for path in run.iterdir()) - allowed:
        raise RunStateError("live run contains an unknown entry")
    _validate_private_directory(run / "receipts", "receipt directory")
    _validate_private_directory(run / "artifacts", "artifact directory")
    return run


def _target_lock_name(canonical_target: str) -> str:
    return f"{raw_digest(canonical_target.encode('utf-8'))}.json"


def _validate_target_unchanged(index: dict[str, Any], root: Path) -> None:
    snapshot = index["target_snapshot"]
    locator = Path(index["target_identity"]["locator"])
    _reject_symlink_components(locator)
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
    for item in requested_queue:
        _, queue_target, _ = _identity_records(host, item, granted)
        canonical_queue.append(queue_target["canonical"])
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
    queue_record = {
        "order": canonical_queue,
        "states": {
            item: ("active" if item == target["canonical"] else "pending")
            for item in canonical_queue
        },
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
            lock_path = root / "target-locks" / _target_lock_name(target["canonical"])
            _exclusive_json(lock_path, {"schema_version": "skill-builder-target-lock.v1", **active_lock, "workflow_id": workflow_id})
            _fsync_directory(live)
        except BaseException:
            # No caller-visible workflow exists until the genesis chain and index
            # are complete.  Best-effort rollback is confined to the fresh ID.
            if run.exists() and not run.is_symlink():
                import shutil

                shutil.rmtree(run)
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
        receipt_path: Path | None = None
        try:
            envelope = _create_artifact(
                run=run,
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                mode=current["mode"]["name"],
                stage=current["stage"],
                sequence=expected_sequence + 1,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                files=files,
                primary_path=primary_path,
                producer=producer,
                input_bindings=input_bindings,
                limitations=limitations,
            )
            receipt = _new_receipt(
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                sequence=expected_sequence + 1,
                prior_receipt_digest=current["head_transition_digest"],
                event="retain-artifact",
                source_stage=current["stage"],
                destination_stage=current["stage"],
                relevant_artifact_digests=[
                    {
                        "artifact_id": artifact_id,
                        "envelope_digest": envelope["envelope_digest"],
                        "status": "accepted",
                    }
                ],
                target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
                authority_event_digest=None,
            )
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            if artifact.exists() and not artifact.is_symlink():
                shutil.rmtree(artifact)
                _fsync_directory(artifact.parent)
            _atomic_json(run / "current.json", current)
            raise
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
        observed_types: set[str] = set()
        for artifact_id in artifact_ids:
            record = current["artifact_index"].get(artifact_id)
            if record is None or record["derived_status"] != "accepted":
                raise RunStateError("transition artifact is missing or invalidated")
            observed_types.add(record["type"])
            bindings.append(
                {
                    "artifact_id": artifact_id,
                    "envelope_digest": record["digest"],
                    "status": "accepted",
                }
            )
        if not _EVENT_ARTIFACT_TYPES[event] <= observed_types:
            raise RunStateError("transition lacks its required artifact types")
        if event == "confirm-contract":
            confirmation_id = next(
                artifact_id
                for artifact_id in artifact_ids
                if current["artifact_index"][artifact_id]["type"]
                == "user-confirmation-record"
            )
            _validate_confirmation_binding(
                run, current, confirmation_id, authority_event_digest
            )
        elif event == "freeze-evaluation":
            evaluation_id = next(
                artifact_id
                for artifact_id in artifact_ids
                if current["artifact_index"][artifact_id]["type"]
                == "evaluation-pack"
            )
            _validate_evaluation_binding(run, current, evaluation_id)
        elif event == "accept-candidate":
            candidate_id = next(
                artifact_id
                for artifact_id in artifact_ids
                if current["artifact_index"][artifact_id]["type"]
                == "candidate-record"
            )
            _validate_candidate_entry(run, current, candidate_id)
        receipt = _new_receipt(
            workflow_id=workflow_id,
            target_identity=current["target_identity"]["canonical"],
            sequence=expected_sequence + 1,
            prior_receipt_digest=current["head_transition_digest"],
            event=event,
            source_stage=current["stage"],
            destination_stage=destination_stage,
            relevant_artifact_digests=bindings,
            target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_event_digest=authority_event_digest,
        )
        receipt_path: Path | None = None
        try:
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            _atomic_json(run / "current.json", current)
            raise
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
        receipt = _new_receipt(
            workflow_id=workflow_id,
            target_identity=current["target_identity"]["canonical"],
            sequence=expected_sequence + 1,
            prior_receipt_digest=current["head_transition_digest"],
            event=operation,
            source_stage=current["stage"],
            destination_stage=destination,
            relevant_artifact_digests=[],
            target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_event_digest=None,
        )
        receipt_path: Path | None = None
        try:
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            _atomic_json(run / "current.json", current)
            raise
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


_INVALIDATION_RULES = {
    "host-target": (
        "resolved",
        {
            "baseline-report",
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
        },
    ),
    "contract": (
        "contract",
        {
            "user-confirmation-record",
            "evaluation-pack",
            "candidate-record",
            "trial-pack",
            "review-record",
            "builder-run-conformance-ledger",
            "target-scorecard",
            "verification-record",
            "release-record",
        },
    ),
    "confirmation": (
        "confirmed",
        {
            "evaluation-pack",
            "candidate-record",
            "trial-pack",
            "review-record",
            "builder-run-conformance-ledger",
            "target-scorecard",
            "verification-record",
            "release-record",
        },
    ),
    "evaluation": (
        "confirmed",
        {
            "candidate-record",
            "trial-pack",
            "review-record",
            "builder-run-conformance-ledger",
            "target-scorecard",
            "verification-record",
            "release-record",
        },
    ),
    "candidate": (
        "evaluation",
        {
            "trial-pack",
            "review-record",
            "builder-run-conformance-ledger",
            "target-scorecard",
            "verification-record",
            "release-record",
        },
    ),
    "review": (
        "trials",
        {"target-scorecard", "verification-record", "release-record"},
    ),
    "verification": ("scored", {"verification-record", "release-record"}),
    "delivery-intent": ("verified", {"release-record"}),
}


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
        destination, invalidated_types = rule
        if _STAGES.index(destination) > _STAGES.index(current["stage"]):
            raise RunStateError("material change does not apply at the current stage")
        changed = current["artifact_index"].get(changed_artifact_id)
        if changed is None or changed["derived_status"] != "accepted":
            raise RunStateError("changed artifact is missing or already invalidated")
        _validate_target_unchanged(current, root)
        invalidated_records = [
            record
            for record in current["artifact_index"].values()
            if record["type"] in invalidated_types
            and record["derived_status"] == "accepted"
            and record["artifact_id"] != changed_artifact_id
        ]
        if not invalidated_records:
            raise RunStateError("material change has no current downstream evidence to invalidate")
        invalidation_id = f"invalidation-{expected_sequence + 1:08d}"
        artifact = _artifact_directory(run, invalidation_id)
        receipt_path: Path | None = None
        try:
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
            envelope = _create_artifact(
                run=run,
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                mode=current["mode"]["name"],
                stage=current["stage"],
                sequence=expected_sequence + 1,
                artifact_id=invalidation_id,
                artifact_type="invalidation-record",
                files={"record.json": canonical_json_bytes(payload)},
                primary_path="record.json",
                producer="main-agent",
                input_bindings=[
                    {"artifact_id": changed_artifact_id, "digest": changed["digest"]}
                ],
                limitations=[],
            )
            bindings = [
                {
                    "artifact_id": invalidation_id,
                    "envelope_digest": envelope["envelope_digest"],
                    "status": "accepted",
                },
                {
                    "artifact_id": changed_artifact_id,
                    "envelope_digest": changed["digest"],
                    "status": "superseded",
                },
            ] + [
                {
                    "artifact_id": record["artifact_id"],
                    "envelope_digest": record["digest"],
                    "status": "invalidated",
                }
                for record in invalidated_records
            ]
            receipt = _new_receipt(
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                sequence=expected_sequence + 1,
                prior_receipt_digest=current["head_transition_digest"],
                event="invalidate",
                source_stage=current["stage"],
                destination_stage=destination,
                relevant_artifact_digests=bindings,
                target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
                authority_event_digest=None,
            )
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            if artifact.exists() and not artifact.is_symlink():
                shutil.rmtree(artifact)
                _fsync_directory(artifact.parent)
            _atomic_json(run / "current.json", current)
            raise
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


def _current_event_artifact(
    run: Path,
    current: dict[str, Any],
    event: str,
    artifact_type: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    artifact_id, envelope = _event_artifact(run, event, artifact_type)
    record = current["artifact_index"].get(artifact_id)
    if record is None or record["derived_status"] != "accepted" or record["digest"] != envelope["envelope_digest"]:
        raise RunStateError(f"current {artifact_type} evidence is invalidated or stale")
    return artifact_id, envelope, _artifact_payload_json(run, artifact_id)


def _validate_final_evidence(
    run: Path, current: dict[str, Any], release_artifact_id: str
) -> list[str]:
    candidate_id, candidate_envelope, candidate = _current_event_artifact(
        run, current, "accept-candidate", "candidate-record"
    )
    candidate_digest = candidate_envelope["envelope_digest"]
    candidate_revision = candidate.get("candidate_revision")
    if not isinstance(candidate_revision, str) or not candidate_revision:
        raise RunStateError("candidate revision is missing")
    trials_id, _, trials = _current_event_artifact(
        run, current, "complete-trials", "trial-pack"
    )
    if (
        trials.get("candidate_digest") != candidate_digest
        or trials.get("candidate_revision") != candidate_revision
        or trials.get("status") != "pass"
    ):
        raise RunStateError("trial evidence is stale or failing")
    review_id, review_envelope, review = _current_event_artifact(
        run, current, "accept-review", "review-record"
    )
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
            isinstance(item, dict) and item.get("severity") in {"high", "medium"}
            for item in findings
        )
    ):
        raise RunStateError("final review is stale, invalid, or not ready")
    conformance_id, conformance_envelope, conformance = _current_event_artifact(
        run, current, "accept-scores", "builder-run-conformance-ledger"
    )
    gates = conformance.get("gates")
    if (
        conformance.get("candidate_digest") != candidate_digest
        or not isinstance(gates, dict)
        or not gates
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
        run, current, "accept-scores", "target-scorecard"
    )
    categories = scorecard.get("categories")
    if (
        scorecard.get("candidate_digest") != candidate_digest
        or scorecard.get("candidate_revision") != candidate_revision
        or not isinstance(categories, list)
        or [item.get("name") if isinstance(item, dict) else None for item in categories]
        != list(_SCORE_CATEGORIES)
    ):
        raise RunStateError("target scorecard binding or categories are invalid")
    for category in categories:
        criteria = category.get("criteria")
        if (
            category.get("score") != 10
            or isinstance(category.get("score"), bool)
            or not isinstance(criteria, dict)
            or not criteria
            or any(value is not True for value in criteria.values())
        ):
            raise RunStateError("every target score and binary criterion must pass at 10")
    verification_id, verification_envelope, verification = _current_event_artifact(
        run, current, "accept-verification", "verification-record"
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
    contract_id, contract_envelope = _event_artifact(
        run, "accept-contract", "skill-contract"
    )
    confirmation_id, confirmation_envelope = _event_artifact(
        run, "confirm-contract", "user-confirmation-record"
    )
    evaluation_id, evaluation_envelope = _event_artifact(
        run, "freeze-evaluation", "evaluation-pack"
    )
    expected_release = {
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
        receipt = _new_receipt(
            workflow_id=workflow_id,
            target_identity=current["target_identity"]["canonical"],
            sequence=expected_sequence + 1,
            prior_receipt_digest=current["head_transition_digest"],
            event="finalize",
            source_stage="verified",
            destination_stage="finalized",
            relevant_artifact_digests=bindings,
            target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_event_digest=None,
        )
        receipt_path: Path | None = None
        lock_path = root / "target-locks" / _target_lock_name(
            current["target_identity"]["canonical"]
        )
        try:
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
            _validate_regular(lock_path, "active-target lock")
            lock_path.unlink()
            _fsync_directory(lock_path.parent)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            _atomic_json(run / "current.json", current)
            raise
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
    with _locked_root(state_root, create=False) as root:
        run = _run_directory(root, workflow_id)
        current = _derive_index(run)
        if _read_json(run / "current.json") != current:
            raise RunStateError("derived current index is stale or corrupt")
        if current["head_sequence"] != expected_sequence:
            raise RevisionConflict("receipt sequence conflict")
        if current["stage"] != "finalized":
            raise RunStateError("delivery may be recorded only for a finalized run")
        _validate_target_unchanged(current, root)
        candidate_id, candidate_envelope, candidate = _current_event_artifact(
            run, current, "accept-candidate", "candidate-record"
        )
        if delivery["finalized_revision"] != candidate.get("candidate_revision"):
            raise RunStateError("delivery revision does not match the finalized candidate")
        release_id, release_envelope, _ = _current_event_artifact(
            run, current, "finalize", "release-record"
        )
        artifact_id = f"delivery-{expected_sequence + 1:08d}"
        artifact = _artifact_directory(run, artifact_id)
        receipt_path: Path | None = None
        try:
            payload = {
                "schema_version": "skill-builder-delivery-acceptance.v1",
                **delivery,
                "candidate_digest": candidate_envelope["envelope_digest"],
                "release_digest": release_envelope["envelope_digest"],
                "timestamp": _now(),
            }
            envelope = _create_artifact(
                run=run,
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                mode=current["mode"]["name"],
                stage="finalized",
                sequence=expected_sequence + 1,
                artifact_id=artifact_id,
                artifact_type="delivery-acceptance-record",
                files={"record.json": canonical_json_bytes(payload)},
                primary_path="record.json",
                producer=delivery["actor"],
                input_bindings=[
                    {"artifact_id": candidate_id, "digest": candidate_envelope["envelope_digest"]},
                    {"artifact_id": release_id, "digest": release_envelope["envelope_digest"]},
                ],
                limitations=[],
            )
            receipt = _new_receipt(
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                sequence=expected_sequence + 1,
                prior_receipt_digest=current["head_transition_digest"],
                event="record-delivery",
                source_stage="finalized",
                destination_stage="delivered",
                relevant_artifact_digests=[
                    {
                        "artifact_id": artifact_id,
                        "envelope_digest": envelope["envelope_digest"],
                        "status": "accepted",
                    }
                ],
                target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
                authority_event_digest=authority_event_digest,
            )
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            if artifact.exists() and not artifact.is_symlink():
                shutil.rmtree(artifact)
                _fsync_directory(artifact.parent)
            _atomic_json(run / "current.json", current)
            raise
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
    actor: str,
    state_root: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(authority_event_digest, str) or not _DIGEST_RE.fullmatch(authority_event_digest):
        raise RunStateError("cleanup-authority event digest is invalid")
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
        artifact = _artifact_directory(run, artifact_id)
        receipt_path: Path | None = None
        try:
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
            envelope = _create_artifact(
                run=run,
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                mode=current["mode"]["name"],
                stage="delivered",
                sequence=expected_sequence + 1,
                artifact_id=artifact_id,
                artifact_type="cleanup-authority-record",
                files={"record.json": canonical_json_bytes(payload)},
                primary_path="record.json",
                producer=actor,
                input_bindings=[
                    {"artifact_id": delivery_id, "digest": delivery_envelope["envelope_digest"]}
                ],
                limitations=[],
            )
            receipt = _new_receipt(
                workflow_id=workflow_id,
                target_identity=current["target_identity"]["canonical"],
                sequence=expected_sequence + 1,
                prior_receipt_digest=current["head_transition_digest"],
                event="record-cleanup-authority",
                source_stage="delivered",
                destination_stage="delivered",
                relevant_artifact_digests=[
                    {
                        "artifact_id": artifact_id,
                        "envelope_digest": envelope["envelope_digest"],
                        "status": "accepted",
                    }
                ],
                target_snapshot_digest=current["target_snapshot"]["snapshot_digest"],
                authority_event_digest=authority_event_digest,
            )
            receipt_path = _write_receipt(run, receipt)
            derived = _derive_index(run)
            _atomic_json(run / "current.json", derived)
        except BaseException:
            if receipt_path is not None and receipt_path.exists() and not receipt_path.is_symlink():
                receipt_path.unlink()
                _fsync_directory(receipt_path.parent)
            if artifact.exists() and not artifact.is_symlink():
                shutil.rmtree(artifact)
                _fsync_directory(artifact.parent)
            _atomic_json(run / "current.json", current)
            raise
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
    identity = tombstone.get("run_directory_identity")
    if not isinstance(identity, dict) or set(identity) != {"name", "device", "inode"} or identity.get("name") != workflow_id:
        raise RunStateError("cleanup tombstone run-directory identity is invalid")
    return tombstone


def _final_run_manifest(run: Path, current: dict[str, Any]) -> dict[str, Any]:
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
        "head_transition_digest": current["head_transition_digest"],
        "entries": entries,
        "observed_item_count": len(entries),
        "observed_byte_count": sum(item["byte_count"] for item in entries),
    }
    manifest["manifest_digest"] = canonical_digest(manifest, "manifest_digest")
    return manifest


def _remove_owned_tree(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RunStateError("cleanup boundary is not an owned directory")
    _validate_private_directory(path, "cleanup directory")
    for child in list(path.iterdir()):
        child_info = child.lstat()
        if stat.S_ISLNK(child_info.st_mode):
            raise RunStateError("cleanup boundary contains a symlink")
        if stat.S_ISDIR(child_info.st_mode):
            _remove_owned_tree(child)
        elif stat.S_ISREG(child_info.st_mode):
            _validate_regular(child, "cleanup file")
            child.unlink()
        else:
            raise RunStateError("cleanup boundary contains an unowned file type")
    path.rmdir()


def cleanup_run(
    *,
    workflow_id: str,
    expected_sequence: int,
    acknowledge_cleanup: bool,
    state_root: Path | None = None,
) -> dict[str, Any]:
    if acknowledge_cleanup is not True:
        raise RunStateError("cleanup requires explicit acknowledgement")
    with _locked_root(state_root, create=False) as root:
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
        tombstone_path = root / "tombstones" / f"{workflow_id}.json"
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
        _remove_owned_tree(run)
        _fsync_directory(root / "live")
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
    payload = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
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
        )
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
            result = record_delivery(state_root=state_root, **payload)
        elif args.command == "cleanup-authority":
            result = record_cleanup_authority(state_root=state_root, **payload)
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
