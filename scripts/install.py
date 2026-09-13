from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

# ``install.py`` is a documented direct CLI.  Suppress local bytecode before
# importing the repository validator/builder so dry runs remain read-only.
sys.dont_write_bytecode = True

try:
    from scripts.build_opencode_package import BuildError as OpencodeBuildError
    from scripts.build_opencode_package import (
        _reject_symlink_components,
        build_opencode_package,
    )
    from scripts.artifact_contract import (
        COPY_FILES,
        COPY_LICENSES,
        COPY_TREES,
        PLATFORM_FILES,
        PLATFORM_PLUGIN_DIRECTORY,
        canonical_provenance,
    )
    from scripts.render_opencode import (
        EXPECTED_AGENT_NAMES as _OPENCODE_AGENT_NAMES,
        render_all as _render_opencode_all,
        skill_inventory as _skill_inventory,
    )
    from scripts.validate import validate_repository
except ModuleNotFoundError:
    from build_opencode_package import BuildError as OpencodeBuildError
    from build_opencode_package import (
        _reject_symlink_components,
        build_opencode_package,
    )
    from artifact_contract import (
        COPY_FILES,
        COPY_LICENSES,
        COPY_TREES,
        PLATFORM_FILES,
        PLATFORM_PLUGIN_DIRECTORY,
        canonical_provenance,
    )
    from render_opencode import (
        EXPECTED_AGENT_NAMES as _OPENCODE_AGENT_NAMES,
        render_all as _render_opencode_all,
        skill_inventory as _skill_inventory,
    )
    from validate import validate_repository


MARKETPLACE_NAME = "expskill"
PLUGIN_NAME = "expskill"
PLUGIN_SELECTOR = "expskill@expskill"
PROFILE_NAMES = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
RETIRED_PROFILE_NAMES = (
    "expskill-critical-reviewer",
    "expskill-implementer-high",
    "expskill-reviewer",
    "expskill-verifier",
    "expskill-verifier-low",
)
RECEIPT_DIRECTORY = "expskill"
RECEIPT_FILENAME = "install.json"
OPENCODE_RECEIPT_FILENAME = "install-opencode.json"
OPENCODE_PACKAGE_NAME = "opencode-expskill"
OPENCODE_ARTIFACT_DIRECTORY = "opencode-artifact"
OPENCODE_ARTIFACT_ANCHOR_FILE = "package.json"
OPENCODE_ARTIFACT_ANCHOR_PREFIX = f".{OPENCODE_ARTIFACT_DIRECTORY}.anchor-"
OPENCODE_RECEIPT_TEARDOWN_PHASES = frozenset(
    {"committed", "removing-links", "artifact-removed", "anchor-removed"}
)
OPENCODE_RECEIPT_PENDING_PHASES = frozenset(
    {
        "none",
        "migration-prepared",
        "swap-prepared",
        "swap-anchor-recorded",
        "swap-backup-created",
        "swap-published",
        "swap-old-artifact-removed",
        "swap-old-anchor-removed",
        "publish-prepared",
        "publish-anchor-recorded",
        "publish-published",
        "publish-planned-links",
        "publish-rollback-prepared",
        "publish-rollback-artifact-removed",
        "publish-rollback-anchor-removed",
        "swap-rollback-prepared",
        "swap-rollback-candidate-removed",
        "swap-rollback-anchor-removed",
        "swap-rollback-restored",
    }
)
OPENCODE_RETIREMENT_PHASES = frozenset(
    {"prepared", "exchanged", "reclaimed", "done"}
)
OPENCODE_RETIREMENT_KINDS = frozenset({"leaf", "directory"})
OPENCODE_RETIREMENT_ROLES = frozenset(
    {"final-link", "staged-link", "anchor", "artifact", "candidate", "backup"}
)
OPENCODE_RETIREMENT_SECRET_BYTES = 32
OPENCODE_RETIREMENT_TOKEN_BYTES = 32
OPENCODE_RECEIPT_GENERATION_PREFIX = ".install-opencode.json.receipt-"
OPENCODE_RECEIPT_GENERATION_SUFFIX = ".retire"
OPENCODE_RECEIPT_RETIREMENT_SCHEMA = "opencode-receipt-retirement.v1"
OPENCODE_RECEIPT_RETIREMENT_PHASES = frozenset(
    {"prepared", "exchanged", "reclaimed", "done"}
)
# Exact source/destination roster emitted by the parent-repository installer
# before receipt-owned artifacts were introduced.  These names are deliberately
# frozen: receipt migration must not turn arbitrary receipt text into deletion
# authority.
LEGACY_OPENCODE_SKILLS = (
    "brainstorm",
    "design",
    "grill-me",
    "implement",
    "plan",
    "setup-ui-testing",
    "skill-builder",
    "test",
    "unslop",
    "use-expskill",
)
LEGACY_OPENCODE_AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
LEGACY_OPENCODE_PLUGINS = ("unslop.js", "execution-policy.js")


class InstallError(RuntimeError):
    pass


class Runner(Protocol):
    def __call__(self, command: list[str]) -> object:
        ...


@dataclass(frozen=True)
class ProfileLink:
    source: Path
    destination: Path
    # OpenCode receipts bind deletion authority to the symlink inode, not only
    # to its target text.  These fields deliberately do not participate in
    # logical link equality so inventories built before publication can still
    # be compared with their committed, identity-bearing form.
    destination_dev: int | None = field(default=None, compare=False)
    destination_ino: int | None = field(default=None, compare=False)
    # During OpenCode publication the symlink is first made durable at this
    # descriptor-bound hidden name.  The receipt records this pathname and its
    # no-follow inode identity before the exclusive rename to ``destination``.
    staged_destination: Path | None = field(default=None, compare=False)


@dataclass(frozen=True)
class InstallResult:
    links: tuple[ProfileLink, ...] = ()
    created_links: tuple[ProfileLink, ...] = ()
    removed_links: tuple[ProfileLink, ...] = ()
    marketplace_added: bool = False
    plugin_installed: bool = False


class _JournaledLinkList(list[ProfileLink]):
    """Created-link inventory carrying a prepublication persistence hook."""

    def __init__(self, record_staged: Callable[[ProfileLink], None]) -> None:
        super().__init__()
        self.record_staged = record_staged


@dataclass(frozen=True)
class _PendingRetirement:
    """One receipt-bound, exact private retirement capability."""

    role: str
    kind: str
    source: Path
    private: Path
    expected_dev: int
    expected_ino: int
    lineage: str
    token: str
    phase: str
    source_kind: str = "public"
    placeholder_dev: int | None = None
    placeholder_ino: int | None = None
    # The authority is intentionally serialized only while this exact
    # retirement is pending.  Public and anchor names contain no part of it.
    # ``auth`` authenticates every mutable field, including the phase, so a
    # torn or hand-edited receipt cannot grant a new pathname capability.
    secret: str | None = field(default=None, compare=False)
    auth: str | None = field(default=None, compare=False)


def _retirement_secret() -> str:
    """Allocate one receipt-bound secret capability for a retirement."""

    return secrets.token_hex(OPENCODE_RETIREMENT_SECRET_BYTES)


def _retirement_secret_bytes(secret: str) -> bytes:
    if (
        not isinstance(secret, str)
        or len(secret) != OPENCODE_RETIREMENT_SECRET_BYTES * 2
        or any(character not in "0123456789abcdef" for character in secret)
    ):
        raise InstallError("OpenCode retirement secret is malformed")
    try:
        return bytes.fromhex(secret)
    except ValueError as error:
        raise InstallError("OpenCode retirement secret is malformed") from error


def _retirement_message(
    lineage: str,
    role: str,
    kind: str,
    source: Path,
    expected_dev: int,
    expected_ino: int,
    token: str,
    phase: str,
    source_kind: str,
    private: Path | None = None,
    placeholder_dev: int | None = None,
    placeholder_ino: int | None = None,
) -> bytes:
    """Serialize the closed retirement facts authenticated by ``secret``."""

    values = {
        "expected_dev": expected_dev,
        "expected_ino": expected_ino,
        "kind": kind,
        "lineage": lineage,
        "phase": phase,
        "placeholder_dev": placeholder_dev,
        "placeholder_ino": placeholder_ino,
        "private": None if private is None else str(_lexical_absolute(private)),
        "role": role,
        "source": str(_lexical_absolute(source)),
        "source_kind": source_kind,
        "token": token,
    }
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def _retirement_token(
    lineage: str,
    role: str,
    kind: str,
    source: Path,
    expected_dev: int,
    expected_ino: int,
    secret: str,
) -> str:
    """Return a retirement token bound to the secret capability."""

    return hmac.new(
        _retirement_secret_bytes(secret),
        b"opencode-retirement.v4\0"
        + _retirement_message(
            lineage,
            role,
            kind,
            source,
            expected_dev,
            expected_ino,
            "",
            "",
            "",
        ),
        hashlib.sha256,
    ).hexdigest()


def _retirement_auth(pending: _PendingRetirement) -> str:
    """Authenticate a complete serialized pending retirement."""

    if pending.secret is None:
        raise InstallError("OpenCode retirement secret is missing")
    return hmac.new(
        _retirement_secret_bytes(pending.secret),
        b"opencode-retirement-auth.v1\0"
        + _retirement_message(
            pending.lineage,
            pending.role,
            pending.kind,
            pending.source,
            pending.expected_dev,
            pending.expected_ino,
            pending.token,
            pending.phase,
            pending.source_kind,
            pending.private,
            pending.placeholder_dev,
            pending.placeholder_ino,
        ),
        hashlib.sha256,
    ).hexdigest()


def _retirement_private_path(source: Path, token: str, kind: str) -> Path:
    suffix = ".retire-dir" if kind == "directory" else ".retire"
    return source.parent / f".{source.name}.expskill-{token}{suffix}"


def _pending_retirement_payload(
    pending: _PendingRetirement,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "role": pending.role,
        "kind": pending.kind,
        "source": str(pending.source),
        "private": str(pending.private),
        "expected_dev": pending.expected_dev,
        "expected_ino": pending.expected_ino,
        "lineage": pending.lineage,
        "token": pending.token,
        "phase": pending.phase,
        "source_kind": pending.source_kind,
    }
    if pending.secret is None:
        raise InstallError("OpenCode retirement secret is missing")
    # Compute the authentication tag at serialization time so phase
    # transitions cannot accidentally retain a tag for the previous phase.
    # The secret itself never participates in any public name.
    serialized = replace(pending, auth=None)
    payload["secret"] = pending.secret
    payload["auth"] = _retirement_auth(serialized)
    if pending.placeholder_dev is not None or pending.placeholder_ino is not None:
        if pending.placeholder_dev is None or pending.placeholder_ino is None:
            raise InstallError("OpenCode retirement placeholder identity is incomplete")
        payload["placeholder_dev"] = pending.placeholder_dev
        payload["placeholder_ino"] = pending.placeholder_ino
    return payload


def _pending_retirement_from_payload(
    value: object, lineage: str, *, require_secret: bool = False
) -> _PendingRetirement:
    """Decode the closed, authenticated retirement capability shape."""

    if not isinstance(value, dict):
        raise InstallError("OpenCode pending retirement is malformed")
    required = {
        "role",
        "kind",
        "source",
        "private",
        "expected_dev",
        "expected_ino",
        "lineage",
        "token",
        "phase",
        "source_kind",
    }
    placeholder = {"placeholder_dev", "placeholder_ino"}
    authenticated = {"secret", "auth"}
    allowed_shapes = {
        frozenset(required | authenticated),
        frozenset(required | placeholder | authenticated),
    }
    if frozenset(value) not in allowed_shapes:
        raise InstallError("OpenCode pending retirement is malformed")
    has_secret = "secret" in value or "auth" in value
    if has_secret != ("secret" in value and "auth" in value):
        raise InstallError("OpenCode pending retirement authentication is incomplete")
    if not has_secret:
        raise InstallError("OpenCode pending retirement lacks secret authority")
    role = value.get("role")
    kind = value.get("kind")
    phase = value.get("phase")
    source_kind = value.get("source_kind")
    source_value = value.get("source")
    private_value = value.get("private")
    expected_dev = value.get("expected_dev")
    expected_ino = value.get("expected_ino")
    token = value.get("token")
    secret = value.get("secret")
    auth = value.get("auth")
    if (
        role not in OPENCODE_RETIREMENT_ROLES
        or kind not in OPENCODE_RETIREMENT_KINDS
        or phase not in OPENCODE_RETIREMENT_PHASES
        or source_kind not in {"public", "quarantine"}
        or value.get("lineage") != lineage
        or not isinstance(source_value, str)
        or not isinstance(private_value, str)
        or not isinstance(expected_dev, int)
        or expected_dev <= 0
        or not isinstance(expected_ino, int)
        or expected_ino <= 0
        or not isinstance(token, str)
        or len(token)
        not in {
            OPENCODE_RETIREMENT_TOKEN_BYTES,
            OPENCODE_RETIREMENT_TOKEN_BYTES * 2,
        }
        or any(character not in "0123456789abcdef" for character in token)
    ):
        raise InstallError("OpenCode pending retirement is malformed")
    if has_secret:
        if not isinstance(secret, str) or not isinstance(auth, str):
            raise InstallError("OpenCode pending retirement authentication is malformed")
        _retirement_secret_bytes(secret)
        if len(auth) != hashlib.sha256().digest_size * 2 or any(
            character not in "0123456789abcdef" for character in auth
        ):
            raise InstallError("OpenCode pending retirement authentication is malformed")
        if len(token) != OPENCODE_RETIREMENT_TOKEN_BYTES * 2:
            raise InstallError("OpenCode pending retirement token is malformed")
    source_raw = Path(source_value).expanduser()
    private_raw = Path(private_value).expanduser()
    if (
        not source_raw.is_absolute()
        or not private_raw.is_absolute()
        or _has_dot_components(source_raw)
        or _has_dot_components(private_raw)
    ):
        raise InstallError("OpenCode pending retirement path is malformed")
    source = _lexical_absolute(source_raw)
    private = _lexical_absolute(private_raw)
    expected_token = _retirement_token(
        lineage,
        role,
        kind,
        source,
        expected_dev,
        expected_ino,
        secret,
    )
    if token != expected_token or private != _retirement_private_path(
        source, token, kind
    ):
        raise InstallError("OpenCode pending retirement token or path is invalid")
    placeholder_dev = value.get("placeholder_dev")
    placeholder_ino = value.get("placeholder_ino")
    if placeholder_dev is not None or placeholder_ino is not None:
        if (
            not isinstance(placeholder_dev, int)
            or placeholder_dev <= 0
            or not isinstance(placeholder_ino, int)
            or placeholder_ino <= 0
        ):
            raise InstallError("OpenCode retirement placeholder identity is malformed")
    if (kind == "directory") != (role in {"artifact", "candidate", "backup"}):
        raise InstallError("OpenCode pending retirement role and kind disagree")
    pending = _PendingRetirement(
        role=role,
        kind=kind,
        source=source,
        private=private,
        expected_dev=expected_dev,
        expected_ino=expected_ino,
        placeholder_dev=placeholder_dev,
        placeholder_ino=placeholder_ino,
        lineage=lineage,
        token=token,
        phase=phase,
        source_kind=source_kind,
        secret=secret,
        auth=auth,
    )
    if has_secret and not hmac.compare_digest(auth or "", _retirement_auth(pending)):
        raise InstallError("OpenCode pending retirement authentication failed")
    return pending


def _validate_pending_retirement_authority(
    retirement: _PendingRetirement,
    *,
    links: Sequence[ProfileLink],
    artifact_root: Path | None,
    artifact_identity: tuple[int | None, int | None],
    artifact_anchor: tuple[Path | None, int | None, int | None],
    pending_swap: "_PendingSwap | None",
    pending_publish: "_PendingPublish | None",
) -> None:
    """Bind the journal role to authority already frozen elsewhere in receipt."""

    authority: set[tuple[str, Path, int, int]] = set()
    for link in links:
        if link.destination_dev is None or link.destination_ino is None:
            continue
        authority.add(
            ("final-link", link.destination, link.destination_dev, link.destination_ino)
        )
        if link.staged_destination is not None:
            authority.add(
                (
                    "staged-link",
                    link.staged_destination,
                    link.destination_dev,
                    link.destination_ino,
                )
            )
    artifact_dev, artifact_ino = artifact_identity
    if artifact_root is not None and artifact_dev is not None and artifact_ino is not None:
        authority.add(("artifact", artifact_root, artifact_dev, artifact_ino))
    anchor, anchor_dev, anchor_ino = artifact_anchor
    if anchor is not None and anchor_dev is not None and anchor_ino is not None:
        authority.add(("anchor", anchor, anchor_dev, anchor_ino))
    for pending in (pending_swap, pending_publish):
        if pending is None:
            continue
        authority.add(
            (
                "candidate",
                pending.candidate,
                pending.candidate_dev,
                pending.candidate_ino,
            )
        )
        # A candidate can already occupy the public artifact pathname when the
        # process stopped between rename and its outer phase write.
        authority.add(
            (
                "candidate",
                pending.artifact,
                pending.candidate_dev,
                pending.candidate_ino,
            )
        )
        candidate_anchor = pending.candidate_anchor
        candidate_anchor_dev = pending.candidate_anchor_dev
        candidate_anchor_ino = pending.candidate_anchor_ino
        if (
            candidate_anchor is not None
            and candidate_anchor_dev is not None
            and candidate_anchor_ino is not None
        ):
            authority.add(
                (
                    "anchor",
                    candidate_anchor,
                    candidate_anchor_dev,
                    candidate_anchor_ino,
                )
            )
    if pending_swap is not None:
        authority.add(
            (
                "backup",
                pending_swap.backup,
                pending_swap.backup_dev,
                pending_swap.backup_ino,
            )
        )
        if (
            pending_swap.old_anchor is not None
            and pending_swap.old_anchor_dev is not None
            and pending_swap.old_anchor_ino is not None
        ):
            authority.add(
                (
                    "anchor",
                    pending_swap.old_anchor,
                    pending_swap.old_anchor_dev,
                    pending_swap.old_anchor_ino,
                )
            )
    expected = (
        retirement.role,
        retirement.source,
        retirement.expected_dev,
        retirement.expected_ino,
    )
    if retirement.source_kind == "quarantine":
        expected = next(
            (
                item
                for item in authority
                if item[0] == retirement.role
                and item[2:] == (retirement.expected_dev, retirement.expected_ino)
                and _deletion_quarantine_path(
                    item[1], retirement.expected_dev, retirement.expected_ino
                )
                == retirement.source
            ),
            expected,
        )
    if expected not in authority:
        raise InstallError("OpenCode pending retirement lacks receipt authority")


@dataclass(frozen=True)
class _Receipt:
    repository_root: Path
    links: tuple[ProfileLink, ...]
    marketplace_added: bool
    plugin_installed: bool
    artifact_root: Path | None = None
    artifact_dev: int | None = None
    artifact_ino: int | None = None
    artifact_digest: str | None = None
    lineage: str | None = None
    artifact_anchor: Path | None = None
    artifact_anchor_dev: int | None = None
    artifact_anchor_ino: int | None = None
    teardown_phase: str = "committed"
    pending_swap: "_PendingSwap | None" = None
    pending_publish: "_PendingPublish | None" = None
    # Legacy adoption freezes every source of deletion authority in a durable
    # receipt before the deterministic package anchor is created.  This bit
    # distinguishes that prepared state from a fully committed migration.
    pending_migration: bool = False
    pending_retirement: _PendingRetirement | None = None


@dataclass(frozen=True)
class _PendingPublish:
    """Identity-bound state written before an initial artifact publication."""

    lineage: str
    artifact: Path
    candidate: Path
    candidate_dev: int
    candidate_ino: int
    phase: str
    candidate_digest: str | None = None
    candidate_anchor: Path | None = None
    candidate_anchor_dev: int | None = None
    candidate_anchor_ino: int | None = None
    planned_links: bool = False


@dataclass(frozen=True)
class _PendingSwap:
    """Receipt-owned, identity-bound publication state."""

    lineage: str
    artifact: Path
    candidate: Path
    candidate_dev: int
    candidate_ino: int
    backup: Path
    backup_dev: int
    backup_ino: int
    live_dev: int
    live_ino: int
    phase: str
    candidate_digest: str | None = None
    backup_digest: str | None = None
    candidate_anchor: Path | None = None
    candidate_anchor_dev: int | None = None
    candidate_anchor_ino: int | None = None
    old_anchor: Path | None = None
    old_anchor_dev: int | None = None
    old_anchor_ino: int | None = None


def _pending_swap_payload(pending: _PendingSwap) -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact": str(pending.artifact),
        "backup": str(pending.backup),
        "backup_dev": pending.backup_dev,
        "backup_ino": pending.backup_ino,
        "candidate": str(pending.candidate),
        "candidate_dev": pending.candidate_dev,
        "candidate_ino": pending.candidate_ino,
        "lineage": pending.lineage,
        "live_dev": pending.live_dev,
        "live_ino": pending.live_ino,
        "phase": pending.phase,
    }
    if pending.candidate_digest is not None:
        payload["candidate_digest"] = pending.candidate_digest
    if pending.backup_digest is not None:
        payload["backup_digest"] = pending.backup_digest
    if pending.candidate_anchor is not None:
        if pending.candidate_anchor_dev is None or pending.candidate_anchor_ino is None:
            raise InstallError("pending OpenCode candidate anchor identity is incomplete")
        payload["candidate_anchor"] = str(pending.candidate_anchor)
        payload["candidate_anchor_dev"] = pending.candidate_anchor_dev
        payload["candidate_anchor_ino"] = pending.candidate_anchor_ino
    if pending.old_anchor is not None:
        if pending.old_anchor_dev is None or pending.old_anchor_ino is None:
            raise InstallError("pending OpenCode old anchor identity is incomplete")
        payload["old_anchor"] = str(pending.old_anchor)
        payload["old_anchor_dev"] = pending.old_anchor_dev
        payload["old_anchor_ino"] = pending.old_anchor_ino
    return payload


def _pending_swap_checksum(lineage: str, payload: Mapping[str, object]) -> str:
    """Detect torn/corrupt fields; this public checksum grants no ownership."""

    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        b"opencode-pending-swap.v1\0" + lineage.encode() + b"\0" + canonical.encode()
    ).hexdigest()


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class _CreatedStateDirectory:
    """Descriptor-bound identity for one directory created by this call."""

    directory: Path
    parent_fd: int
    directory_fd: int
    identity: tuple[int, int]


@dataclass
class _StateBinding:
    """A state directory retained through one installer transaction."""

    directory: Path
    directory_fd: int
    identity: tuple[int, int]
    validated_leaves: dict[str, tuple[int, int]] = field(default_factory=dict)
    created_directories: tuple[_CreatedStateDirectory, ...] = ()


@dataclass
class _ConfigBinding:
    """No-follow config root and destination parents retained for one operation."""

    directory: Path
    directory_fd: int
    identity: tuple[int, int]
    parents: dict[tuple[str, ...], tuple[int, tuple[int, int]]] = field(
        default_factory=dict
    )
    validated_leaves: dict[Path, tuple[int, int]] = field(default_factory=dict)


_STATE_BINDINGS: dict[str, _StateBinding] = {}
_CONFIG_BINDINGS: dict[str, _ConfigBinding] = {}


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _renameat2(
    source_fd: int,
    source_name: str,
    target_fd: int,
    target_name: str,
    flags: int,
    label: str,
) -> None:
    """Perform the Linux conditional rename primitive used by state publication."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renameat2
    except (AttributeError, OSError) as error:
        raise InstallError(f"{label} is unavailable") from error
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        source_fd,
        os.fsencode(source_name),
        target_fd,
        os.fsencode(target_name),
        flags,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))


def _renameat_noreplace(
    source_fd: int, source_name: str, target_fd: int, target_name: str
) -> None:
    _renameat2(
        source_fd,
        source_name,
        target_fd,
        target_name,
        1,
        "exclusive rename",
    )


def _renameat_exchange(
    source_fd: int, source_name: str, target_fd: int, target_name: str
) -> None:
    _renameat2(
        source_fd,
        source_name,
        target_fd,
        target_name,
        2,
        "exchange rename",
    )


def _link_open_descriptor(source_fd: int, target_fd: int, target_name: str) -> None:
    """Hard-link the exact opened inode without resolving its source pathname."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.linkat
    except (AttributeError, OSError) as error:
        raise InstallError("descriptor hard-link is unavailable") from error
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    result = function(
        source_fd,
        b"",
        target_fd,
        os.fsencode(target_name),
        0x1000,  # AT_EMPTY_PATH
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    raise OSError(error_number, os.strerror(error_number))


def _open_state_binding(directory: Path, *, create: bool) -> _StateBinding | None:
    """Open every ancestor without following links and optionally create it."""

    absolute = _lexical_absolute(directory)
    descriptor = os.open(absolute.anchor, _directory_open_flags())
    created_directories: list[_CreatedStateDirectory] = []
    current = Path(absolute.anchor)
    try:
        for component in absolute.parts[1:]:
            created = False
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    descriptor = -1
                    return None
                os.mkdir(component, mode=0o755, dir_fd=descriptor)
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
                created = True
            current /= component
            if created:
                metadata = os.fstat(child)
                created_directories.append(
                    _CreatedStateDirectory(
                        directory=current,
                        parent_fd=os.dup(descriptor),
                        directory_fd=os.dup(child),
                        identity=(metadata.st_dev, metadata.st_ino),
                    )
                )
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        binding = _StateBinding(
            directory=absolute,
            directory_fd=descriptor,
            identity=(metadata.st_dev, metadata.st_ino),
            created_directories=tuple(created_directories),
        )
        _verify_state_binding(binding)
        # The retained state-directory descriptor is also the transaction
        # lock.  Retirement records below are safe to reclaim only while one
        # installer owns this lock; Linux does not provide unlink-by-inode.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return binding
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        for created in created_directories:
            os.close(created.directory_fd)
            os.close(created.parent_fd)
        raise


def _close_state_binding(binding: _StateBinding) -> None:
    for created in binding.created_directories:
        os.close(created.directory_fd)
        os.close(created.parent_fd)
    os.close(binding.directory_fd)


def _verify_state_binding(binding: _StateBinding) -> None:
    try:
        metadata = os.stat(binding.directory, follow_symlinks=False)
    except OSError as error:
        raise InstallError(
            f"OpenCode state directory binding was replaced: {binding.directory}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != binding.identity
    ):
        raise InstallError(
            f"OpenCode state directory binding was replaced: {binding.directory}"
        )


def _state_binding(path: Path) -> _StateBinding | None:
    return _STATE_BINDINGS.get(str(_lexical_absolute(path).parent))


def _state_lstat(path: Path) -> os.stat_result:
    binding = _state_binding(path)
    if binding is None:
        return os.lstat(path)
    _verify_state_binding(binding)
    return os.stat(path.name, dir_fd=binding.directory_fd, follow_symlinks=False)


def _receipt_deletion_phase(receipt: _Receipt) -> tuple[str, str]:
    """Return the closed, filename-safe phase pair bound to receipt deletion."""

    if receipt.pending_retirement is not None:
        raise InstallError("OpenCode receipt has an outstanding retirement")
    current_phase = receipt.teardown_phase
    if current_phase not in OPENCODE_RECEIPT_TEARDOWN_PHASES:
        raise InstallError("OpenCode receipt deletion phase is invalid")
    if receipt.pending_swap is not None:
        pending_phase = f"swap-{receipt.pending_swap.phase}"
    elif receipt.pending_publish is not None:
        pending_phase = f"publish-{receipt.pending_publish.phase}"
    elif receipt.pending_migration:
        pending_phase = "migration-prepared"
    else:
        pending_phase = "none"
    if pending_phase not in OPENCODE_RECEIPT_PENDING_PHASES:
        raise InstallError("OpenCode receipt pending deletion phase is invalid")
    return current_phase, pending_phase


def _receipt_deletion_quarantine_path(
    path: Path,
    dev: int,
    ino: int,
    lineage: str,
    current_phase: str,
    pending_phase: str,
) -> Path:
    """Bind recoverable receipt deletion to inode, lineage, and exact phase."""

    if path.name != OPENCODE_RECEIPT_FILENAME:
        raise InstallError(f"unsupported receipt deletion path: {path}")
    identity = f"{dev:x}-{ino:x}"
    token = hashlib.sha256(
        b"opencode-receipt-deletion.v2\0"
        + path.name.encode()
        + b"\0"
        + identity.encode()
        + b"\0"
        + lineage.encode()
        + b"\0"
        + current_phase.encode()
        + b"\0"
        + pending_phase.encode()
    ).hexdigest()[:32]
    return path.parent / (
        f".{path.name}.{identity}.{token}.{pending_phase}.{current_phase}.delete"
    )


def _receipt_deletion_pin_path(
    path: Path,
    dev: int,
    ino: int,
    lineage: str,
    current_phase: str,
    pending_phase: str,
) -> Path:
    """Name the durable hard-link pin for one exact receipt deletion."""

    quarantine = _receipt_deletion_quarantine_path(
        path, dev, ino, lineage, current_phase, pending_phase
    )
    return quarantine.with_name(f"{quarantine.name.removesuffix('.delete')}.pin")


def _receipt_deletion_descriptor(
    path: Path, contents: str | None = None
) -> tuple[str, str, str]:
    """Parse the receipt fields required to create its deletion capability."""

    if path.name != OPENCODE_RECEIPT_FILENAME:
        raise InstallError(f"unsupported receipt deletion path: {path}")
    try:
        payload = json.loads(_read_state_text(path) if contents is None else contents)
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {path}")
    if payload.get("pending_retirement") is not None:
        raise InstallError(f"receipt has an outstanding retirement: {path}")
    lineage = payload.get("lineage")
    if not isinstance(lineage, str) or len(lineage) < 32:
        raise InstallError(f"receipt deletion lineage is malformed: {path}")
    current_phase = payload.get("teardown_phase", "committed")
    if current_phase not in OPENCODE_RECEIPT_TEARDOWN_PHASES:
        raise InstallError(f"receipt deletion phase is malformed: {path}")
    pending_swap = payload.get("pending_swap")
    pending_publish = payload.get("pending_publish")
    pending_migration = payload.get("pending_migration")
    if (
        sum(
            value is not None
            for value in (pending_swap, pending_publish, pending_migration)
        )
        > 1
    ):
        raise InstallError(f"receipt deletion pending phase is malformed: {path}")
    if pending_swap is not None:
        if not isinstance(pending_swap, dict) or not isinstance(
            pending_swap.get("phase"), str
        ):
            raise InstallError(f"receipt deletion pending phase is malformed: {path}")
        pending_phase = f"swap-{pending_swap['phase']}"
    elif pending_publish is not None:
        if not isinstance(pending_publish, dict) or not isinstance(
            pending_publish.get("phase"), str
        ):
            raise InstallError(f"receipt deletion pending phase is malformed: {path}")
        pending_phase = f"publish-{pending_publish['phase']}"
    elif pending_migration is not None:
        if pending_migration != {"phase": "prepared"}:
            raise InstallError(f"receipt deletion pending phase is malformed: {path}")
        pending_phase = "migration-prepared"
    else:
        pending_phase = "none"
    if pending_phase not in OPENCODE_RECEIPT_PENDING_PHASES:
        raise InstallError(f"receipt deletion pending phase is malformed: {path}")
    return lineage, current_phase, pending_phase


def _receipt_retirement_token(
    path: Path, expected: tuple[int, int], secret: str
) -> str:
    return hmac.new(
        _retirement_secret_bytes(secret),
        b"opencode-receipt-retirement.v1\0"
        + os.fsencode(_lexical_absolute(path))
        + b"\0"
        + f"{expected[0]:x}-{expected[1]:x}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _receipt_retirement_private_path(
    path: Path, expected: tuple[int, int], secret: str
) -> Path:
    token = _receipt_retirement_token(path, expected, secret)
    return path.parent / f".{path.name}.{token}.retire"


def _receipt_retirement_sidecar_path(
    path: Path, expected: tuple[int, int], secret: str
) -> Path:
    token = _receipt_retirement_token(path, expected, secret)
    return path.parent / f".{path.name}.{token}.journal"


def _receipt_retirement_message(payload: Mapping[str, object]) -> bytes:
    keys = [
        "expected_dev",
        "expected_ino",
        "phase",
        "private",
        "schema",
        "secret",
        "sidecar",
        "source",
        "token",
    ]
    if "placeholder_dev" in payload or "placeholder_ino" in payload:
        keys.extend(("placeholder_dev", "placeholder_ino"))
    values = {key: payload[key] for key in keys}
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def _receipt_retirement_auth(payload: Mapping[str, object]) -> str:
    secret = payload.get("secret")
    if not isinstance(secret, str):
        raise InstallError("receipt retirement sidecar secret is malformed")
    return hmac.new(
        _retirement_secret_bytes(secret),
        b"opencode-receipt-retirement-auth.v1\0"
        + _receipt_retirement_message(payload),
        hashlib.sha256,
    ).hexdigest()


def _receipt_retirement_payload(
    path: Path,
    expected: tuple[int, int],
    secret: str,
    phase: str,
    placeholder: tuple[int, int] | None = None,
) -> dict[str, object]:
    private = _receipt_retirement_private_path(path, expected, secret)
    sidecar = _receipt_retirement_sidecar_path(path, expected, secret)
    payload: dict[str, object] = {
        "expected_dev": expected[0],
        "expected_ino": expected[1],
        "phase": phase,
        "private": str(private),
        "schema": OPENCODE_RECEIPT_RETIREMENT_SCHEMA,
        "secret": secret,
        "sidecar": str(sidecar),
        "source": str(_lexical_absolute(path)),
        "token": _receipt_retirement_token(path, expected, secret),
    }
    if placeholder is not None:
        payload["placeholder_dev"] = placeholder[0]
        payload["placeholder_ino"] = placeholder[1]
    payload["auth"] = _receipt_retirement_auth(payload)
    return payload


def _receipt_retirement_from_payload(
    value: object, sidecar_path: Path
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InstallError("receipt retirement sidecar is malformed")
    required = {
        "expected_dev",
        "expected_ino",
        "phase",
        "private",
        "schema",
        "secret",
        "sidecar",
        "source",
        "token",
        "auth",
    }
    placeholder_fields = {"placeholder_dev", "placeholder_ino"}
    if frozenset(value) not in {
        frozenset(required),
        frozenset(required | placeholder_fields),
    }:
        raise InstallError("receipt retirement sidecar is malformed")
    expected_dev = value.get("expected_dev")
    expected_ino = value.get("expected_ino")
    phase = value.get("phase")
    source_value = value.get("source")
    private_value = value.get("private")
    sidecar_value = value.get("sidecar")
    secret = value.get("secret")
    token = value.get("token")
    auth = value.get("auth")
    placeholder_dev = value.get("placeholder_dev")
    placeholder_ino = value.get("placeholder_ino")
    if (
        not isinstance(expected_dev, int)
        or expected_dev <= 0
        or not isinstance(expected_ino, int)
        or expected_ino <= 0
        or phase not in OPENCODE_RECEIPT_RETIREMENT_PHASES
        or value.get("schema") != OPENCODE_RECEIPT_RETIREMENT_SCHEMA
        or not isinstance(source_value, str)
        or not isinstance(private_value, str)
        or not isinstance(sidecar_value, str)
        or not isinstance(secret, str)
        or not isinstance(token, str)
        or not isinstance(auth, str)
        or len(token) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in token)
        or len(auth) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in auth)
        or ((placeholder_dev is None) != (placeholder_ino is None))
        or (
            placeholder_dev is not None
            and (
                not isinstance(placeholder_dev, int)
                or placeholder_dev <= 0
                or not isinstance(placeholder_ino, int)
                or placeholder_ino <= 0
            )
        )
    ):
        raise InstallError("receipt retirement sidecar is malformed")
    _retirement_secret_bytes(secret)
    source_raw = Path(source_value).expanduser()
    private_raw = Path(private_value).expanduser()
    sidecar_raw = Path(sidecar_value).expanduser()
    if (
        not source_raw.is_absolute()
        or not private_raw.is_absolute()
        or not sidecar_raw.is_absolute()
        or _has_dot_components(source_raw)
        or _has_dot_components(private_raw)
        or _has_dot_components(sidecar_raw)
    ):
        raise InstallError("receipt retirement sidecar path is malformed")
    source = _lexical_absolute(source_raw)
    private = _lexical_absolute(private_raw)
    sidecar = _lexical_absolute(sidecar_raw)
    if source.name != OPENCODE_RECEIPT_FILENAME:
        raise InstallError("receipt retirement source is not the canonical receipt")
    expected = (expected_dev, expected_ino)
    expected_token = _receipt_retirement_token(source, expected, secret)
    if (
        sidecar != _lexical_absolute(sidecar_path)
        or sidecar.parent != source.parent
        or private != _receipt_retirement_private_path(source, expected, secret)
        or sidecar != _receipt_retirement_sidecar_path(source, expected, secret)
        or token != expected_token
    ):
        raise InstallError("receipt retirement sidecar token or path is invalid")
    normalized = dict(value)
    if not hmac.compare_digest(auth, _receipt_retirement_auth(normalized)):
        raise InstallError("receipt retirement sidecar authentication failed")
    return normalized


def _receipt_sidecar_stage_name(
    sidecar_name: str,
    displaced: tuple[int, int],
    published: tuple[int, int],
) -> str:
    return (
        f".{sidecar_name}.stage-"
        f"{displaced[0]:x}-{displaced[1]:x}."
        f"{published[0]:x}-{published[1]:x}"
    )


def _receipt_sidecar_stage_descriptor(
    sidecar_name: str, candidate: str
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    prefix = f".{sidecar_name}.stage-"
    if not candidate.startswith(prefix):
        return None
    try:
        displaced_text, published_text = candidate[len(prefix) :].split(".", 1)
        displaced = tuple(int(value, 16) for value in displaced_text.split("-", 1))
        published = tuple(int(value, 16) for value in published_text.split("-", 1))
    except (TypeError, ValueError):
        return None
    if len(displaced) != 2 or len(published) != 2 or min(*displaced, *published) <= 0:
        return None
    return (displaced[0], displaced[1]), (published[0], published[1])


def _receipt_sidecar_stage_record(
    binding: _StateBinding, sidecar_name: str
) -> tuple[str, tuple[int, int], tuple[int, int]] | None:
    prefix = f".{sidecar_name}.stage"
    matches = [name for name in os.listdir(binding.directory_fd) if name.startswith(prefix)]
    if not matches:
        return None
    if len(matches) != 1:
        raise InstallError("receipt retirement sidecar has ambiguous stages")
    name = matches[0]
    descriptor = _receipt_sidecar_stage_descriptor(sidecar_name, name)
    if descriptor is None:
        raise InstallError("receipt retirement sidecar stage is malformed")
    return name, *descriptor


def _write_receipt_retirement_sidecar(
    sidecar_path: Path, payload: Mapping[str, object]
) -> None:
    """Durably publish one authenticated sidecar phase without random names."""

    binding = _state_binding(sidecar_path)
    if binding is None:
        raise InstallError("receipt retirement sidecar requires the installer lock")
    _verify_state_binding(binding)
    expected_sidecar = _lexical_absolute(Path(str(payload.get("sidecar"))))
    if expected_sidecar != _lexical_absolute(sidecar_path):
        raise InstallError("receipt retirement sidecar path changed")
    next_payload = _receipt_retirement_from_payload(payload, sidecar_path)
    encoded = (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode()
    sidecar_name = sidecar_path.name
    current = _state_metadata(binding, sidecar_name)
    stage_record = _receipt_sidecar_stage_record(binding, sidecar_name)
    if current is None:
        if stage_record is not None:
            raise InstallError("receipt retirement sidecar stage lacks its public generation")
        _write_state_generation(binding, sidecar_name, encoded)
        os.fsync(binding.directory_fd)
        binding.validated_leaves.pop(sidecar_name, None)
        return
    if not stat.S_ISREG(current.st_mode):
        raise InstallError("receipt retirement sidecar is occupied")
    try:
        current_value = json.loads(
            _read_state_text(sidecar_path)
        )
        current_payload = _receipt_retirement_from_payload(
            current_value, sidecar_path
        )
    except (OSError, json.JSONDecodeError, InstallError) as error:
        raise InstallError("receipt retirement sidecar is not authenticated") from error
    if any(
        current_payload[key] != next_payload[key]
        for key in ("source", "private", "secret", "sidecar", "token")
    ):
        raise InstallError("receipt retirement sidecar belongs to another record")
    current_identity = (current.st_dev, current.st_ino)
    if stage_record is not None:
        stage_name, displaced_identity, stage_identity = stage_record
        stage = _state_metadata(binding, stage_name)
        if stage is None or not stat.S_ISREG(stage.st_mode):
            raise InstallError("receipt retirement sidecar stage is occupied")
        observed_stage_identity = (stage.st_dev, stage.st_ino)
        if observed_stage_identity not in {displaced_identity, stage_identity}:
            if current_identity == stage_identity:
                _renameat_exchange(
                    binding.directory_fd,
                    sidecar_name,
                    binding.directory_fd,
                    stage_name,
                )
                os.fsync(binding.directory_fd)
                raise InstallError(
                    "receipt retirement sidecar restored a displaced public replacement"
                )
            raise InstallError("receipt retirement sidecar stage identity changed")
        try:
            stage_value = json.loads(_read_state_text(sidecar_path.parent / stage_name))
            stage_payload = _receipt_retirement_from_payload(
                stage_value, sidecar_path
            )
        except (OSError, json.JSONDecodeError, InstallError) as error:
            raise InstallError(
                "receipt retirement sidecar stage is not authenticated"
            ) from error
        if any(
            stage_payload[key] != next_payload[key]
            for key in ("source", "private", "secret", "sidecar", "token")
        ):
            raise InstallError("receipt retirement sidecar stage belongs to another record")
        if (
            current_identity == stage_identity
            and observed_stage_identity == displaced_identity
        ):
            _unlink_private_state_inode(
                binding,
                stage_name,
                displaced_identity,
                "receipt retirement sidecar generation",
            )
            binding.validated_leaves.pop(sidecar_name, None)
            stage_record = None
            if current_payload == next_payload:
                _verify_state_binding(binding)
                return
        if (
            current_identity == displaced_identity
            and observed_stage_identity == stage_identity
            and stage_payload != next_payload
        ):
            # The exact anonymous candidate was linked, but its exchange did
            # not occur and the retry had to allocate a fresh retirement
            # placeholder identity.  The exact old public sidecar remains the
            # authority, so this exact identity-bound candidate can be retired.
            _unlink_private_state_inode(
                binding,
                stage_name,
                stage_identity,
                "receipt retirement sidecar candidate",
            )
            stage_record = None
        if not (
            stage_record is None
            or (
                current_identity == displaced_identity
                and observed_stage_identity == stage_identity
                and stage_payload == next_payload
            )
        ):
            # A public replacement after exchange and the displaced exact
            # sidecar generation remain intact.  Byte equality is not identity.
            raise InstallError("receipt retirement sidecar generation is ambiguous")
    if stage_record is None:
        stage_name, stage_identity = _write_named_state_generation(
            binding,
            lambda identity: _receipt_sidecar_stage_name(
                sidecar_name, current_identity, identity
            ),
            encoded,
        )
        displaced_identity = current_identity
    exchanged = False
    try:
        exchanged = True
        _renameat_exchange(
            binding.directory_fd,
            stage_name,
            binding.directory_fd,
            sidecar_name,
        )
        os.fsync(binding.directory_fd)
        live = _state_metadata(binding, sidecar_name)
        if live is None or (live.st_dev, live.st_ino) != stage_identity:
            raise InstallError(
                "receipt retirement sidecar was replaced during exchange"
            )
        displaced = _state_metadata(binding, stage_name)
        if displaced is None:
            raise InstallError("receipt retirement sidecar generation disappeared")
        if (displaced.st_dev, displaced.st_ino) != displaced_identity:
            _renameat_exchange(
                binding.directory_fd,
                sidecar_name,
                binding.directory_fd,
                stage_name,
            )
            os.fsync(binding.directory_fd)
            raise InstallError(
                "receipt retirement sidecar was replaced during exchange"
            )
        # The displaced sidecar is itself private and exact.  Reclaiming it is
        # separate from the receipt object retirement below.
        _unlink_private_state_inode(
            binding,
            stage_name,
            displaced_identity,
            "receipt retirement sidecar",
        )
        binding.validated_leaves.pop(sidecar_name, None)
    except (OSError, InstallError) as error:
        if exchanged:
            # Keep the old stage generation for the locked recovery pass.  A
            # phase transition is never silently converted into direct
            # canonical deletion.
            raise
        raise InstallError(f"cannot write receipt retirement sidecar: {error}") from error
    _verify_state_binding(binding)


def _resume_receipt_retirement(
    sidecar_path: Path, payload: Mapping[str, object]
) -> None:
    binding = _state_binding(sidecar_path)
    if binding is None:
        raise InstallError("receipt retirement recovery requires the installer lock")
    current = _receipt_retirement_from_payload(payload, sidecar_path)
    source = Path(str(current["source"]))
    private = Path(str(current["private"]))
    expected = (int(current["expected_dev"]), int(current["expected_ino"]))

    def metadata(path: Path) -> os.stat_result | None:
        return _state_metadata(binding, path.name)

    def exact(value: os.stat_result | None) -> bool:
        return value is not None and (value.st_dev, value.st_ino) == expected

    def identity(value: os.stat_result | None) -> tuple[int, int] | None:
        return None if value is None else (value.st_dev, value.st_ino)

    phase = str(current["phase"])
    if phase == "prepared":
        source_metadata = metadata(source)
        private_metadata = metadata(private)
        placeholder = (
            None
            if current.get("placeholder_dev") is None
            or current.get("placeholder_ino") is None
            else (
                int(current["placeholder_dev"]),
                int(current["placeholder_ino"]),
            )
        )
        if placeholder is None or (private_metadata is None and exact(source_metadata)):
            if private_metadata is not None:
                raise InstallError(
                    f"receipt retirement private name is occupied: {private}"
                )
            if not exact(source_metadata):
                raise InstallError("receipt path was replaced before retirement exchange")
            descriptor, placeholder = _prepare_anonymous_generation(
                binding.directory_fd, b"", "receipt retirement placeholder"
            )
            try:
                current = dict(current)
                current["placeholder_dev"] = placeholder[0]
                current["placeholder_ino"] = placeholder[1]
                current["auth"] = _receipt_retirement_auth(current)
                _write_receipt_retirement_sidecar(sidecar_path, current)
                if metadata(private) is not None:
                    raise InstallError(
                        f"receipt retirement private name is occupied: {private}"
                    )
                if not exact(metadata(source)):
                    raise InstallError(
                        "receipt path was replaced before retirement exchange"
                    )
                _link_open_descriptor(
                    descriptor, binding.directory_fd, private.name
                )
                os.fsync(binding.directory_fd)
            finally:
                os.close(descriptor)
            source_metadata = metadata(source)
            private_metadata = metadata(private)

        assert placeholder is not None
        source_identity = identity(source_metadata)
        private_identity = identity(private_metadata)
        if source_identity == placeholder:
            if private_identity == expected:
                pass
            elif private_metadata is not None:
                _renameat_exchange(
                    binding.directory_fd,
                    source.name,
                    binding.directory_fd,
                    private.name,
                )
                os.fsync(binding.directory_fd)
                raise InstallError(
                    "receipt retirement restored a displaced public replacement"
                )
            else:
                raise InstallError("receipt retirement private object disappeared")
        elif exact(source_metadata) and private_identity == placeholder:
            _renameat_exchange(
                binding.directory_fd,
                source.name,
                binding.directory_fd,
                private.name,
            )
            os.fsync(binding.directory_fd)
            source_metadata = metadata(source)
            private_metadata = metadata(private)
            if identity(source_metadata) != placeholder:
                raise InstallError("receipt retirement placeholder publication failed")
            if not exact(private_metadata):
                if private_metadata is not None:
                    _renameat_exchange(
                        binding.directory_fd,
                        source.name,
                        binding.directory_fd,
                        private.name,
                    )
                    os.fsync(binding.directory_fd)
                raise InstallError(
                    "receipt retirement restored a displaced public replacement"
                )
        else:
            raise InstallError("receipt path was replaced before retirement exchange")
        current = dict(current)
        current["phase"] = "exchanged"
        current["auth"] = _receipt_retirement_auth(current)
        _write_receipt_retirement_sidecar(sidecar_path, current)
        phase = "exchanged"
    if phase == "exchanged":
        if current.get("placeholder_dev") is None or current.get("placeholder_ino") is None:
            raise InstallError("receipt retirement placeholder authority is missing")
        placeholder = (
            int(current["placeholder_dev"]),
            int(current["placeholder_ino"]),
        )
        placeholder_path = private.with_name(
            f"{private.name}.{placeholder[0]:x}-{placeholder[1]:x}.placeholder"
        )
        source_metadata = metadata(source)
        staged_placeholder = metadata(placeholder_path)
        if source_metadata is not None:
            if identity(source_metadata) != placeholder:
                raise InstallError("receipt retirement public pathname was replaced")
            if staged_placeholder is not None:
                raise InstallError("receipt retirement placeholder path is occupied")
            _renameat_noreplace(
                binding.directory_fd,
                source.name,
                binding.directory_fd,
                placeholder_path.name,
            )
            os.fsync(binding.directory_fd)
            staged_placeholder = metadata(placeholder_path)
        if staged_placeholder is not None:
            _unlink_private_state_inode(
                binding,
                placeholder_path.name,
                placeholder,
                "receipt retirement placeholder",
            )
        if metadata(source) is not None:
            raise InstallError("receipt retirement public pathname reappeared")
        private_metadata = metadata(private)
        if private_metadata is not None and not exact(private_metadata):
            raise InstallError(f"receipt retirement private identity changed: {private}")
        if private_metadata is not None:
            _unlink_private_state_inode(
                binding,
                private.name,
                expected,
                "receipt retirement",
            )
        current = dict(current)
        current["phase"] = "reclaimed"
        current["auth"] = _receipt_retirement_auth(current)
        _write_receipt_retirement_sidecar(sidecar_path, current)
        phase = "reclaimed"
    if phase == "reclaimed":
        if metadata(private) is not None:
            raise InstallError("receipt retirement private path reappeared")
        current = dict(current)
        current["phase"] = "done"
        current["auth"] = _receipt_retirement_auth(current)
        _write_receipt_retirement_sidecar(sidecar_path, current)
        phase = "done"
    if phase != "done":
        raise InstallError("receipt retirement phase is not recoverable")
    sidecar_metadata = _state_metadata(binding, sidecar_path.name)
    if sidecar_metadata is None or not stat.S_ISREG(sidecar_metadata.st_mode):
        raise InstallError("receipt retirement sidecar disappeared")
    try:
        sidecar_value = json.loads(_read_state_text(sidecar_path))
        live_sidecar = _receipt_retirement_from_payload(
            sidecar_value, sidecar_path
        )
    except (OSError, json.JSONDecodeError, InstallError) as error:
        raise InstallError("receipt retirement sidecar was replaced") from error
    if live_sidecar != current:
        raise InstallError("receipt retirement sidecar phase changed during cleanup")
    refreshed = _state_metadata(binding, sidecar_path.name)
    if refreshed is None or (
        refreshed.st_dev,
        refreshed.st_ino,
    ) != (sidecar_metadata.st_dev, sidecar_metadata.st_ino):
        raise InstallError("receipt retirement sidecar identity changed during cleanup")
    _unlink_private_state_inode(
        binding,
        sidecar_path.name,
        (sidecar_metadata.st_dev, sidecar_metadata.st_ino),
        "receipt retirement sidecar",
    )
    _verify_state_binding(binding)


def _unlink_state_path(path: Path) -> None:
    """Retire a receipt through the authenticated sidecar protocol only."""

    binding = _state_binding(path)
    if binding is None:
        raise InstallError("OpenCode receipt deletion requires the installer lock")
    _verify_state_binding(binding)
    # An ordinary receipt rewrite may have exchanged the canonical inode just
    # before process death.  Resolve that private generation before creating a
    # final-retirement authority; otherwise final deletion could strand the
    # displaced generation outside the closed protocol.
    recovered_generation = _recover_receipt_generations(path, binding)
    if recovered_generation:
        # The recovery pass has proved the canonical inode from the exact
        # generation record.  A prior read may still cache the displaced
        # identity in the binding; discard that stale observation before the
        # final-retirement identity check below.
        binding.validated_leaves.pop(path.name, None)
    pending_generations = _receipt_generation_names(path, binding)
    if pending_generations:
        raise InstallError(
            "cannot delete receipt while a generation is pending: "
            + ", ".join(sorted(pending_generations))
        )
    current = _state_metadata(binding, path.name)
    if current is None or not stat.S_ISREG(current.st_mode):
        raise InstallError(f"receipt path is not a regular file: {path}")
    expected = (current.st_dev, current.st_ino)
    validated = binding.validated_leaves.get(path.name)
    if validated is not None and validated != expected:
        raise InstallError(f"receipt path identity changed before deletion: {path}")
    try:
        contents = _read_state_text(path)
        _receipt_deletion_descriptor(path, contents)
        receipt_payload = json.loads(contents)
    except (OSError, InstallError) as error:
        if isinstance(error, InstallError):
            raise
        raise InstallError(f"cannot open exact receipt for deletion: {path}: {error}") from error
    if not isinstance(receipt_payload, dict):
        raise InstallError(f"receipt is malformed: {path}")
    secret_value = receipt_payload.get("receipt_secret")
    if secret_value is None:
        # Receipts written before this protocol acquire one durable secret
        # before any final-retirement name is derived.  A retry therefore
        # never has to reconstruct authority from public inode/path facts.
        secret = _retirement_secret()
        receipt_payload["receipt_secret"] = secret
        if not _write_state_payload(path, receipt_payload):
            raise InstallError("receipt secret migration requires the installer lock")
        current = _state_metadata(binding, path.name)
        if current is None or not stat.S_ISREG(current.st_mode):
            raise InstallError(f"receipt path disappeared during secret migration: {path}")
        expected = (current.st_dev, current.st_ino)
        binding.validated_leaves[path.name] = expected
    else:
        if not isinstance(secret_value, str):
            raise InstallError("receipt retirement secret is malformed")
        _retirement_secret_bytes(secret_value)
        secret = secret_value
    sidecar_path = _receipt_retirement_sidecar_path(path, expected, secret)
    private_path = _receipt_retirement_private_path(path, expected, secret)
    if _state_metadata(binding, sidecar_path.name) is not None:
        raise InstallError(f"receipt retirement sidecar is occupied: {sidecar_path}")
    if _state_metadata(binding, private_path.name) is not None:
        raise InstallError(f"receipt retirement private name is occupied: {private_path}")
    payload = _receipt_retirement_payload(path, expected, secret, "prepared")
    _write_receipt_retirement_sidecar(sidecar_path, payload)
    _resume_receipt_retirement(sidecar_path, payload)
    binding.validated_leaves.pop(path.name, None)
    _verify_state_binding(binding)


def _lexists(path: Path) -> bool:
    try:
        _state_lstat(path)
    except OSError:
        return False
    return True


def _canonical_repository_root(repo_root: Path, require_directory: bool = True) -> Path:
    candidate = Path(repo_root).expanduser()
    _reject_symlink_components(candidate, "repository root")
    try:
        canonical = candidate.resolve(strict=require_directory)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"repository root cannot be resolved: {candidate}: {error}") from error
    if require_directory and not canonical.is_dir():
        raise InstallError(f"repository root is not a directory: {canonical}")
    return canonical


def _validate_repository(repository_root: Path) -> None:
    errors = validate_repository(repository_root)
    # The upstream-derived copies are intentionally editable canonical skill
    # sources.  Their parity diagnostics are release hygiene checks, not
    # semantic defects that should prevent rebuilding a local artifact after a
    # legitimate source edit.  Preserve every other validator failure here.
    errors = tuple(
        error
        for error in errors
        if "does not match its declared derived upstream copy" not in error
    )
    if errors:
        raise InstallError("repository validation failed: " + "; ".join(errors))


def _assert_relative_no_symlink_components(root: Path, relative: Sequence[str]) -> None:
    current = root
    for component in relative:
        current = current / component
        if current.is_symlink():
            raise InstallError(f"repository profile path contains a symlink: {current}")


def _profile_sources(repository_root: Path) -> tuple[Path, ...]:
    _assert_relative_no_symlink_components(
        repository_root,
        ("packages", "expskill", "assets", "agents"),
    )
    agents_root = repository_root / "packages" / "expskill" / "assets" / "agents"
    if not agents_root.is_dir():
        raise InstallError(f"agent source directory is missing: {agents_root}")
    try:
        resolved_agents_root = agents_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"agent source directory cannot be resolved: {agents_root}: {error}") from error
    if resolved_agents_root != agents_root:
        raise InstallError(f"agent source directory resolves outside the repository: {agents_root}")
    discovered = tuple(sorted(agents_root.glob("expskill-*.toml"), key=lambda path: path.name))
    expected_names = {f"{name}.toml" for name in PROFILE_NAMES}
    discovered_names = {path.name for path in discovered}
    if discovered_names != expected_names or len(discovered) != len(expected_names):
        found = ", ".join(sorted(discovered_names)) or "none"
        expected = ", ".join(sorted(expected_names))
        raise InstallError(
            f"agent sources must be exactly the validated profiles; found {found}; expected {expected}"
        )
    sources: list[Path] = []
    for path in discovered:
        if path.is_symlink() or not path.is_file():
            raise InstallError(f"agent source is not a regular file: {path}")
        try:
            profile = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise InstallError(f"agent source is not valid TOML: {path}: {error}") from error
        if not isinstance(profile, dict) or profile.get("name") != path.stem:
            raise InstallError(f"agent source profile name does not match {path.name}")
        try:
            source = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise InstallError(f"agent source cannot be resolved: {path}: {error}") from error
        try:
            source.relative_to(agents_root)
        except ValueError as error:
            raise InstallError(f"agent source resolves outside the agent directory: {path}") from error
        if source.parent != agents_root:
            raise InstallError(f"agent source resolves outside the exact agent directory: {path}")
        sources.append(source)
    return tuple(sources)


def _validate_agent_directory(agents_directory: Path) -> None:
    if _lexists(agents_directory):
        if agents_directory.is_symlink():
            raise InstallError(f"agent destination directory is a symlink: {agents_directory}")
        if not agents_directory.is_dir():
            raise InstallError(f"agent destination directory is not a directory: {agents_directory}")
        return
    current = agents_directory.parent
    while current != current.parent:
        if _lexists(current) and not current.is_dir():
            raise InstallError(f"agent destination parent is not a directory: {current}")
        current = current.parent


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(os.fspath(path))))


def _has_dot_components(path: Path) -> bool:
    return any(component in {".", ".."} for component in path.parts)


def _same_owned_link(destination: Path, source: Path) -> bool:
    if _config_binding(destination) is not None:
        return _bound_link_identity(destination, source)
    if not destination.is_symlink():
        return False
    try:
        return destination.resolve(strict=False) == source.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _expected_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    _validate_repository(canonical_root)
    sources = _profile_sources(canonical_root)
    canonical_codex_home = Path(codex_home).expanduser().resolve(strict=False)
    agents_directory = canonical_codex_home / "agents"
    _validate_agent_directory(agents_directory)
    return tuple(
        ProfileLink(source=source, destination=agents_directory / source.name)
        for source in sources
    )


def _allowlisted_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    canonical_codex_home = Path(codex_home).expanduser().resolve(strict=False)
    agents_directory = canonical_codex_home / "agents"
    _validate_agent_directory(agents_directory)
    source_directory = canonical_root / "packages" / "expskill" / "assets" / "agents"
    return tuple(
        ProfileLink(
            source=_lexical_absolute(source_directory / f"{name}.toml"),
            destination=agents_directory / f"{name}.toml",
        )
        for name in (*PROFILE_NAMES, *RETIRED_PROFILE_NAMES)
    )


def preflight_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    links = _expected_links(repo_root, codex_home)
    for link in links:
        if not _lexists(link.destination):
            continue
        if not _same_owned_link(link.destination, link.source):
            raise InstallError(f"refusing conflicting agent destination: {link.destination}")
    return links


def _receipt_path(state_home: Path) -> Path:
    canonical_state_home = Path(state_home).expanduser().resolve(strict=False)
    return canonical_state_home / RECEIPT_DIRECTORY / RECEIPT_FILENAME


def _opencode_link_staging_path(source: Path, destination: Path) -> Path:
    """Return the fixed hidden publication name for one OpenCode link pair."""

    identity = hashlib.sha256(
        b"opencode-link-stage.v1\0"
        + os.fsencode(_lexical_absolute(source))
        + b"\0"
        + os.fsencode(_lexical_absolute(destination))
    ).hexdigest()[:32]
    return destination.parent / f".{destination.name}.expskill-{identity}.link"


def _alternate_opencode_link_staging_path(source: Path, destination: Path) -> Path:
    base = _opencode_link_staging_path(source, destination)
    token = uuid.uuid4().hex
    return base.with_name(f"{base.name.removesuffix('.link')}.{token}.link")


def _valid_opencode_link_staging_path(
    source: Path, destination: Path, staged: Path
) -> bool:
    base = _opencode_link_staging_path(source, destination)
    if staged == base:
        return True
    prefix = f"{base.name.removesuffix('.link')}."
    if staged.parent != base.parent or not staged.name.startswith(prefix):
        return False
    token = staged.name[len(prefix) :].removesuffix(".link")
    return (
        staged.name.endswith(".link")
        and len(token) == 32
        and all(character in "0123456789abcdef" for character in token)
    )


def _deletion_quarantine_path(path: Path, dev: int, ino: int) -> Path:
    """Return a receipt-recoverable deletion name for one exact inode."""

    return path.parent / f".{path.name}.{dev:x}-{ino:x}.delete"


def _unlink_exact_leaf_via_exchange(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
    label: str,
) -> bool:
    """Remove an exact leaf through a receipt-recoverable private record."""

    return _remove_exact_via_exchange(
        parent_fd, name, expected, label, directory=False
    )


def _retirement_record_descriptor(
    candidate: str, *, directory: bool = False
) -> tuple[str, tuple[int, int], tuple[int, int]] | None:
    """Decode one identity-bearing retirement pathname from its right edge."""

    suffix = ".retire-dir" if directory else ".retire"
    if not candidate.endswith(suffix):
        return None
    encoded = candidate[: -len(suffix)]
    try:
        base, expected_text, placeholder_text = encoded.rsplit(".", 2)
        expected_dev, expected_ino = (
            int(value, 16) for value in expected_text.split("-", 1)
        )
        placeholder_dev, placeholder_ino = (
            int(value, 16) for value in placeholder_text.split("-", 1)
        )
    except (TypeError, ValueError):
        return None
    if min(expected_dev, expected_ino, placeholder_dev, placeholder_ino) <= 0:
        return None
    return (
        base.removeprefix("."),
        (expected_dev, expected_ino),
        (placeholder_dev, placeholder_ino),
    )


def _retirement_record_exists(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
    *,
    directory: bool,
) -> bool:
    """Return whether a private record reserves this exact retirement."""

    return any(
        descriptor is not None
        and descriptor[0] == name
        and descriptor[1] == expected
        for descriptor in (
            _retirement_record_descriptor(candidate, directory=directory)
            for candidate in os.listdir(parent_fd)
        )
    )


def _exact_retired_object_exists(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    expected: tuple[int, int] | None = None,
) -> bool:
    """Recognize an exact object at its identity-bearing retirement name."""

    for candidate in os.listdir(parent_fd):
        descriptor = _retirement_record_descriptor(candidate, directory=directory)
        if descriptor is None or descriptor[0] != name:
            continue
        recorded, placeholder = descriptor[1:]
        if expected is not None and recorded != expected:
            continue
        try:
            retired = os.stat(
                candidate, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            continue
        if (retired.st_dev, retired.st_ino) != recorded or (
            stat.S_ISDIR(retired.st_mode) != directory
        ):
            continue
        placeholder_name = (
            f".{name}.{recorded[0]:x}-{recorded[1]:x}."
            f"{placeholder[0]:x}-{placeholder[1]:x}.sentinel"
            f"{'-dir' if directory else ''}"
        )
        public = None
        private_placeholder = None
        try:
            public = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        try:
            private_placeholder = os.stat(
                placeholder_name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            pass
        for observed in (public, private_placeholder):
            if observed is not None and (
                not stat.S_ISREG(observed.st_mode)
                or stat.S_ISLNK(observed.st_mode)
                or (observed.st_dev, observed.st_ino) != placeholder
            ):
                return False
        if public is not None and private_placeholder is not None:
            return False
        return True
    return False


def _rmdir_exact_via_exchange(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
    label: str,
) -> bool:
    """Remove one empty exact directory through a recoverable exchange."""

    return _remove_exact_via_exchange(
        parent_fd, name, expected, label, directory=True
    )


def _remove_exact_via_exchange(
    parent_fd: int,
    name: str,
    expected: tuple[int, int],
    label: str,
    *,
    directory: bool,
) -> bool:
    """Retire an exact inode using a durable, identity-bearing private name.

    Linux has no conditional unlink-by-inode operation.  The installer lock is
    therefore part of the normal-reclamation boundary.  Before exchange, both
    the owned and placeholder identities are encoded in a deterministic name
    and made durable.  A retry can resume every exchange state.  Any observed
    mismatch is preserved and reported so the caller cannot retire its receipt
    authority.  The final path operation is still pathname based and is never
    represented as stronger than that kernel primitive.
    """

    kind_suffix = "-dir" if directory else ""
    prefix = f".{name}.{expected[0]:x}-{expected[1]:x}."
    retire_suffix = f".retire{kind_suffix}"
    sentinel_suffix = f".sentinel{kind_suffix}"

    def metadata(candidate: str) -> os.stat_result | None:
        try:
            return os.stat(candidate, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def identity(value: os.stat_result) -> tuple[int, int]:
        return value.st_dev, value.st_ino

    def require_kind(value: os.stat_result, description: str) -> None:
        matches = stat.S_ISDIR(value.st_mode) if directory else not stat.S_ISDIR(value.st_mode)
        if not matches:
            raise InstallError(f"{label} {description} changed type")

    def require_placeholder(value: os.stat_result, description: str) -> None:
        if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
            raise InstallError(f"{label} {description} changed type")

    def private_names() -> tuple[str, tuple[int, int]] | None:
        matches = [
            candidate
            for candidate in os.listdir(parent_fd)
            if candidate.startswith(prefix) and candidate.endswith(retire_suffix)
        ]
        if len(matches) > 1:
            raise InstallError(f"{label} has ambiguous retirement records")
        if not matches:
            return None
        candidate = matches[0]
        encoded = candidate[len(prefix) : -len(retire_suffix)]
        try:
            dev_text, ino_text = encoded.split("-", 1)
            placeholder_identity = (int(dev_text, 16), int(ino_text, 16))
        except ValueError as error:
            raise InstallError(f"{label} retirement record is malformed") from error
        if placeholder_identity[0] <= 0 or placeholder_identity[1] <= 0:
            raise InstallError(f"{label} retirement record is malformed")
        return candidate, placeholder_identity

    try:
        record = private_names()
        current = metadata(name)
        if record is None:
            if current is None:
                return False
            require_kind(current, "pathname")
            if identity(current) != expected:
                return False
            # O_TMPFILE gives the exchange placeholder an identity before it
            # has any pathname.  Its first and only link is therefore already
            # the final receipt-derived retirement record; a crash cannot
            # strand an unjournaled preparation name.
            descriptor = os.open(
                ".",
                os.O_WRONLY
                | getattr(os, "O_TMPFILE", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            try:
                created = os.fstat(descriptor)
                placeholder_identity = identity(created)
                retirement = (
                    f"{prefix}{placeholder_identity[0]:x}-{placeholder_identity[1]:x}"
                    f"{retire_suffix}"
                )
                os.fsync(descriptor)
                _link_open_descriptor(descriptor, parent_fd, retirement)
                os.fsync(parent_fd)
            finally:
                os.close(descriptor)
            record = retirement, placeholder_identity

        retirement, placeholder_identity = record
        placeholder = (
            f"{prefix}{placeholder_identity[0]:x}-{placeholder_identity[1]:x}"
            f"{sentinel_suffix}"
        )
        retired = metadata(retirement)
        if retired is None:
            # Both terminal names absent means a prior unlink completed; the
            # directory barrier below makes that absence durable on retry.
            if metadata(placeholder) is not None or current is not None:
                raise InstallError(f"{label} retirement record disappeared")
            os.fsync(parent_fd)
            return True
        retired_identity = identity(retired)
        if retired_identity == placeholder_identity:
            require_placeholder(retired, "retirement placeholder")
            if current is None or identity(current) != expected:
                raise InstallError(f"{label} identity changed before final exchange")
            require_kind(current, "pathname")
            _renameat_exchange(parent_fd, name, parent_fd, retirement)
            retired = metadata(retirement)
            current = metadata(name)
            if (
                retired is None
                or identity(retired) != expected
                or current is None
                or identity(current) != placeholder_identity
            ):
                try:
                    _renameat_exchange(parent_fd, name, parent_fd, retirement)
                    os.fsync(parent_fd)
                except (OSError, InstallError) as reverse_error:
                    raise InstallError(
                        f"{label} replacement could not be restored after exchange"
                    ) from reverse_error
                raise InstallError(f"{label} identity changed at final exchange")
            os.fsync(parent_fd)
        elif retired_identity != expected:
            raise InstallError(f"{label} retirement pathname was replaced")
        else:
            require_kind(retired, "retirement pathname")

        current = metadata(name)
        private_placeholder = metadata(placeholder)
        if current is not None:
            require_placeholder(current, "placeholder")
            if identity(current) != placeholder_identity:
                raise InstallError(f"{label} public pathname was replaced after exchange")
            if private_placeholder is not None:
                raise InstallError(f"{label} placeholder pathname is occupied")
            _renameat_noreplace(parent_fd, name, parent_fd, placeholder)
            os.fsync(parent_fd)
            private_placeholder = metadata(placeholder)
        if private_placeholder is not None:
            require_placeholder(private_placeholder, "private placeholder")
            if identity(private_placeholder) != placeholder_identity:
                raise InstallError(f"{label} private placeholder was replaced")
            # Revalidate immediately before the raw pathname operation.  The
            # installer lock excludes another normal reclaimer; this is not a
            # claim that unlink/rmdir itself is inode-conditional.
            private_placeholder = metadata(placeholder)
            if private_placeholder is None or identity(
                private_placeholder
            ) != placeholder_identity:
                raise InstallError(f"{label} private placeholder changed before removal")
            os.unlink(placeholder, dir_fd=parent_fd)
            os.fsync(parent_fd)

        retired = metadata(retirement)
        if retired is None or identity(retired) != expected:
            raise InstallError(f"{label} retirement pathname changed before removal")
        require_kind(retired, "retired object")
        if directory:
            os.rmdir(retirement, dir_fd=parent_fd)
        else:
            os.unlink(retirement, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(f"cannot conditionally remove {label}: {error}") from error


def _receipt_links(
    value: object,
    receipt_path: Path,
    expected_links: Sequence[ProfileLink],
) -> tuple[ProfileLink, ...]:
    if not isinstance(value, list):
        raise InstallError(f"receipt links are malformed: {receipt_path}")
    expected = {(link.source, link.destination): link for link in expected_links}
    links: list[ProfileLink] = []
    seen_pairs: set[tuple[Path, Path]] = set()
    seen_destinations: set[Path] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source_value = entry.get("source")
        destination_value = entry.get("destination")
        if not isinstance(source_value, str) or not isinstance(destination_value, str):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source = Path(source_value).expanduser()
        destination = Path(destination_value).expanduser()
        if not source.is_absolute() or not destination.is_absolute():
            raise InstallError(f"receipt links must be absolute paths: {receipt_path}")
        if _has_dot_components(source) or _has_dot_components(destination):
            raise InstallError(f"receipt link contains traversal: {receipt_path}")
        canonical_source = _lexical_absolute(source)
        lexical_destination = _lexical_absolute(destination)
        pair = (canonical_source, lexical_destination)
        if pair not in expected:
            raise InstallError(f"receipt link is outside the selected repository or Codex home: {receipt_path}")
        if pair in seen_pairs or lexical_destination in seen_destinations:
            raise InstallError(f"receipt link is duplicated: {receipt_path}")
        destination_dev_value = entry.get("destination_dev")
        destination_ino_value = entry.get("destination_ino")
        if destination_dev_value is None and destination_ino_value is None:
            destination_dev = None
            destination_ino = None
        elif (
            isinstance(destination_dev_value, int)
            and destination_dev_value > 0
            and isinstance(destination_ino_value, int)
            and destination_ino_value > 0
        ):
            destination_dev = destination_dev_value
            destination_ino = destination_ino_value
        else:
            raise InstallError(f"receipt link identity is malformed: {receipt_path}")
        staged_value = entry.get("staged_destination")
        if staged_value is None:
            staged_destination = None
        elif (
            isinstance(staged_value, str)
            and destination_dev is not None
            and destination_ino is not None
        ):
            staged_raw = Path(staged_value).expanduser()
            if (
                not staged_raw.is_absolute()
                or _has_dot_components(staged_raw)
            ):
                raise InstallError(
                    f"receipt staged link path is malformed: {receipt_path}"
                )
            staged_destination = _lexical_absolute(staged_raw)
            if not _valid_opencode_link_staging_path(
                canonical_source, lexical_destination, staged_destination
            ):
                raise InstallError(
                    f"receipt staged link path is outside its destination: {receipt_path}"
                )
        else:
            raise InstallError(
                f"receipt staged link identity is malformed: {receipt_path}"
            )
        seen_pairs.add(pair)
        seen_destinations.add(lexical_destination)
        links.append(
            ProfileLink(
                source=expected[pair].source,
                destination=expected[pair].destination,
                destination_dev=destination_dev,
                destination_ino=destination_ino,
                staged_destination=staged_destination,
            )
        )
    return tuple(links)


def _read_state_text(path: Path) -> str:
    binding = _state_binding(path)
    if binding is None:
        return path.read_text(encoding="utf-8")
    _verify_state_binding(binding)
    descriptor = os.open(
        path.name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=binding.directory_fd,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallError(f"receipt path is not a regular file: {path}")
        identity = (metadata.st_dev, metadata.st_ino)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            contents = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _verify_state_binding(binding)
    try:
        current = os.stat(
            path.name, dir_fd=binding.directory_fd, follow_symlinks=False
        )
    except OSError as error:
        raise InstallError(f"receipt path changed while being read: {path}") from error
    if (current.st_dev, current.st_ino) != identity:
        raise InstallError(f"receipt path changed while being read: {path}")
    binding.validated_leaves[path.name] = identity
    return contents


def _receipt_generation_token(
    path: Path,
    expected: tuple[int, int],
    published: tuple[int, int],
    secret: str,
) -> str:
    """Bind a private generation to both displaced and published identities."""

    return hmac.new(
        _retirement_secret_bytes(secret),
        b"opencode-receipt-generation.v2\0"
        + os.fsencode(_lexical_absolute(path))
        + b"\0"
        + f"{expected[0]:x}-{expected[1]:x}\0".encode()
        + f"{published[0]:x}-{published[1]:x}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _receipt_generation_path(
    path: Path,
    expected: tuple[int, int],
    published: tuple[int, int],
    secret: str,
) -> Path:
    token = _receipt_generation_token(path, expected, published, secret)
    return path.parent / (
        f".{path.name}.receipt-{expected[0]:x}-{expected[1]:x}."
        f"{published[0]:x}-{published[1]:x}.{token}"
        f"{OPENCODE_RECEIPT_GENERATION_SUFFIX}"
    )


def _receipt_generation_descriptor(
    path: Path, name: str
) -> tuple[tuple[int, int], tuple[int, int], str] | None:
    prefix = f".{path.name}.receipt-"
    suffix = OPENCODE_RECEIPT_GENERATION_SUFFIX
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    encoded = name[len(prefix) : -len(suffix)]
    try:
        identity, published_identity, token = encoded.split(".", 2)
        dev_text, ino_text = identity.split("-", 1)
        expected = (int(dev_text, 16), int(ino_text, 16))
        published_dev_text, published_ino_text = published_identity.split("-", 1)
        published = (
            int(published_dev_text, 16),
            int(published_ino_text, 16),
        )
    except (TypeError, ValueError):
        return None
    if (
        min(*expected, *published) <= 0
        or len(token) != hashlib.sha256().digest_size * 2
        or any(character not in "0123456789abcdef" for character in token)
    ):
        return None
    return expected, published, token


def _payload_receipt_secret(
    payload: Mapping[str, object], previous: Mapping[str, object] | None = None
) -> str:
    value = payload.get("receipt_secret")
    if value is None and previous is not None:
        value = previous.get("receipt_secret")
    if value is None:
        return _retirement_secret()
    if not isinstance(value, str):
        raise InstallError("receipt generation secret is malformed")
    _retirement_secret_bytes(value)
    return value


def _state_metadata(
    binding: _StateBinding, name: str
) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=binding.directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _unlink_private_state_inode(
    binding: _StateBinding,
    name: str,
    expected: tuple[int, int],
    label: str,
) -> None:
    metadata = _state_metadata(binding, name)
    if metadata is None:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != expected
    ):
        raise InstallError(f"{label} private identity changed")
    # All reclaim paths point beneath the locked state directory.  In
    # particular, never unlink a canonical receipt pathname here.
    if name == OPENCODE_RECEIPT_FILENAME:
        raise InstallError(f"{label} attempted canonical receipt deletion")
    os.unlink(name, dir_fd=binding.directory_fd)
    os.fsync(binding.directory_fd)


def _recover_receipt_generations(
    path: Path,
    binding: _StateBinding,
    *,
    expected_payload: Mapping[str, object] | None = None,
) -> bool:
    """Reclaim displaced receipt generations left by a crashed exchange."""

    _verify_state_binding(binding)
    recovered = False
    for name in tuple(os.listdir(binding.directory_fd)):
        descriptor = _receipt_generation_descriptor(path, name)
        if descriptor is None:
            continue
        expected, published, token = descriptor
        metadata = _state_metadata(binding, name)
        if metadata is None:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            # A foreign object at a private name is never deletion authority.
            continue
        private_identity = (metadata.st_dev, metadata.st_ino)
        canonical = _state_metadata(binding, path.name)
        canonical_identity = (
            None if canonical is None else (canonical.st_dev, canonical.st_ino)
        )

        # The authenticated name is verified from an exact installer
        # generation, never from arbitrary bytes at either public or private
        # name.  At least one of the two identity-bound generations must still
        # be present for this record to carry recovery authority.
        authority_payload: dict[str, object] | None = None
        private_payload: dict[str, object] | None = None
        for candidate_name, candidate_identity in (
            (path.name, canonical_identity),
            (name, private_identity),
        ):
            if candidate_identity not in {expected, published}:
                continue
            try:
                value = json.loads(_read_state_text(path.parent / candidate_name))
            except (OSError, json.JSONDecodeError, InstallError):
                continue
            if isinstance(value, dict):
                if candidate_name == name:
                    private_payload = value
                authority_payload = value
        if authority_payload is None:
            continue
        try:
            secret = authority_payload.get("receipt_secret")
            if not isinstance(secret, str):
                continue
            expected_token = _receipt_generation_token(
                path, expected, published, secret
            )
        except InstallError:
            continue
        if not hmac.compare_digest(token, expected_token):
            continue
        if private_identity in {expected, published}:
            if private_payload is None:
                # In-place corruption of an exact private inode removes its
                # content authority; identity alone never permits deletion.
                continue
            private_secret = private_payload.get("receipt_secret")
            try:
                private_token = (
                    _receipt_generation_token(
                        path, expected, published, private_secret
                    )
                    if isinstance(private_secret, str)
                    else ""
                )
            except InstallError:
                continue
            if not hmac.compare_digest(token, private_token):
                continue

        if private_identity == published and canonical_identity == expected:
            # The anonymous candidate was durably linked but the exchange did
            # not happen.  Its exact identity is in the authenticated private
            # name, so it is safe to discard without comparing caller bytes.
            _unlink_private_state_inode(
                binding,
                name,
                published,
                "receipt generation candidate",
            )
            recovered = True
            continue

        if private_identity not in {expected, published}:
            if canonical_identity == published:
                # A replacement immediately before exchange was displaced to
                # the private record while the exact installer generation
                # became public.  The public identity proves that one reverse
                # exchange restores the exact foreign inode without overwrite.
                _renameat_exchange(
                    binding.directory_fd,
                    path.name,
                    binding.directory_fd,
                    name,
                )
                os.fsync(binding.directory_fd)
                raise InstallError(
                    "receipt generation restored a displaced public replacement"
                )
            continue

        if private_identity == published:
            # This is the compensated form above: the displaced foreign
            # object is public and the exact installer candidate is private.
            # Preserve both and fail closed through the pending-name gate.
            continue

        if canonical_identity is None:
            # This is the only recovery branch that restores a canonical
            # pathname.  The private inode is exact and its record is
            # authenticated by the receipt payload it carries.
            _renameat_noreplace(
                binding.directory_fd,
                name,
                binding.directory_fd,
                path.name,
            )
            os.fsync(binding.directory_fd)
            canonical = _state_metadata(binding, path.name)
            if canonical is None or (
                canonical.st_dev,
                canonical.st_ino,
            ) != expected:
                raise InstallError("receipt generation recovery lost exact identity")
            recovered = True
            continue
        if canonical_identity == published:
            # Only the exact published generation authorizes reclamation of
            # the exact displaced generation.  Byte equality at the public
            # name is intentionally irrelevant.
            _unlink_private_state_inode(binding, name, expected, "receipt generation")
            recovered = True
            continue
        # A newer public occupant and the displaced installer receipt are both
        # preserved.  The still-present private record makes the caller fail.
    _verify_state_binding(binding)
    return recovered


def _receipt_generation_names(
    path: Path, binding: _StateBinding
) -> tuple[str, ...]:
    """Return still-occupied names in the receipt-generation namespace."""

    return tuple(
        name
        for name in os.listdir(binding.directory_fd)
        if _receipt_generation_descriptor(path, name) is not None
        and _state_metadata(binding, name) is not None
    )


def _write_state_generation(
    binding: _StateBinding,
    name: str,
    encoded: bytes,
) -> tuple[int, int]:
    """Atomically link one fully-written private regular-file generation."""

    _, identity = _write_named_state_generation(
        binding, lambda _identity: name, encoded
    )
    return identity


def _prepare_state_generation(
    binding: _StateBinding, encoded: bytes
) -> tuple[int, tuple[int, int]]:
    """Fully write and fsync an anonymous inode before it has a pathname."""

    return _prepare_anonymous_generation(
        binding.directory_fd, encoded, "receipt generation"
    )


def _prepare_anonymous_generation(
    directory_fd: int, encoded: bytes, label: str
) -> tuple[int, tuple[int, int]]:
    """Fully write one unnamed regular file for later descriptor linking."""

    temporary_flag = getattr(os, "O_TMPFILE", 0)
    if not temporary_flag:
        raise InstallError(f"anonymous {label} is unavailable")
    descriptor = -1
    try:
        descriptor = os.open(
            ".",
            os.O_WRONLY
            | temporary_flag
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise InstallError(f"{label} write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        return descriptor, (metadata.st_dev, metadata.st_ino)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _write_named_state_generation(
    binding: _StateBinding,
    name_from_identity: Callable[[tuple[int, int]], str],
    encoded: bytes,
) -> tuple[str, tuple[int, int]]:
    """Publish an anonymous generation at its identity-derived final name."""

    descriptor, identity = _prepare_state_generation(binding, encoded)
    try:
        name = name_from_identity(identity)
        if not name or Path(name).name != name:
            raise InstallError("receipt generation name is malformed")
        _link_open_descriptor(descriptor, binding.directory_fd, name)
        os.fsync(binding.directory_fd)
        linked = _state_metadata(binding, name)
        if linked is None or (linked.st_dev, linked.st_ino) != identity:
            raise InstallError("receipt generation publication lost exact identity")
        return name, identity
    finally:
        os.close(descriptor)


def _write_state_payload(path: Path, payload: Mapping[str, object]) -> bool:
    """Write through the retained state descriptor with receipt generation recovery."""

    binding = _state_binding(path)
    if binding is None:
        return False
    _verify_state_binding(binding)
    current_identity: tuple[int, int] | None = None
    previous_payload: dict[str, object] | None = None
    validated_identity: tuple[int, int] | None = None
    try:
        current = os.stat(path.name, dir_fd=binding.directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(current.st_mode):
            raise InstallError(f"receipt path is not a regular file: {path}")
        current_identity = (current.st_dev, current.st_ino)
        validated = binding.validated_leaves.get(path.name)
        validated_identity = validated
        try:
            observed_payload = json.loads(_read_state_text(path))
        except (OSError, json.JSONDecodeError) as error:
            raise InstallError(
                f"cannot read current receipt generation: {path}: {error}"
            ) from error
        if isinstance(observed_payload, dict):
            previous_payload = observed_payload
    secret = _payload_receipt_secret(payload, previous_payload)
    output_payload = dict(payload)
    output_payload["receipt_secret"] = secret
    encoded = (json.dumps(output_payload, indent=2, sort_keys=True) + "\n").encode()
    recovered_generation = _recover_receipt_generations(
        path,
        binding,
        expected_payload=output_payload,
    )
    pending_generations = _receipt_generation_names(path, binding)
    if pending_generations:
        raise InstallError(
            "receipt generation recovery is incomplete: "
            + ", ".join(sorted(pending_generations))
        )
    if recovered_generation and current_identity is None:
        recovered = _state_metadata(binding, path.name)
        if recovered is None or not stat.S_ISREG(recovered.st_mode):
            raise InstallError(f"receipt path disappeared during recovery: {path}")
        current_identity = (recovered.st_dev, recovered.st_ino)
    if validated_identity is not None and current_identity != validated_identity:
        if not recovered_generation:
            raise InstallError(f"receipt path identity changed before write: {path}")
        current = _state_metadata(binding, path.name)
        if current is None or not stat.S_ISREG(current.st_mode):
            raise InstallError(f"receipt path disappeared during recovery: {path}")
        current_identity = (current.st_dev, current.st_ino)
    generation_name: str | None = None
    generation_identity: tuple[int, int] | None = None
    exchanged = False
    published = False
    try:
        if current_identity is None:
            # There is no displaced generation on first publication.  Keep
            # the candidate deterministic and private until the no-replace
            # publication boundary.
            digest = hashlib.sha256(encoded).hexdigest()
            generation_name = f".{path.name}.receipt-initial-{digest}.stage"
            generation_identity = _write_state_generation(
                binding, generation_name, encoded
            )
            _renameat_noreplace(
                binding.directory_fd,
                generation_name,
                binding.directory_fd,
                path.name,
            )
            exchanged = True
            published = True
        else:
            generation_name, generation_identity = _write_named_state_generation(
                binding,
                lambda identity: _receipt_generation_path(
                    path, current_identity, identity, secret
                ).name,
                encoded,
            )
            # The displaced generation lands directly in its deterministic
            # private retirement namespace.  No random .tmp pathname can
            # become an orphaned authority after the exchange.
            # Mark the boundary before invoking the syscall: test and fault
            # injectors may raise immediately after a successful exchange,
            # and that old inode must remain for crash recovery.
            exchanged = True
            _renameat_exchange(
                binding.directory_fd,
                generation_name,
                binding.directory_fd,
                path.name,
            )
            os.fsync(binding.directory_fd)
            live = _state_metadata(binding, path.name)
            displaced = _state_metadata(binding, generation_name)
            if live is None or (
                live.st_dev,
                live.st_ino,
            ) != generation_identity:
                # A byte-identical public replacement has no authority.  Keep
                # the displaced receipt generation and the foreign public
                # object intact for a locked retry to fail closed.
                raise InstallError(f"receipt path identity changed during write: {path}")
            if displaced is None or (
                displaced.st_dev,
                displaced.st_ino,
            ) != current_identity:
                if displaced is not None:
                    # A pre-syscall replacement was displaced by the exchange.
                    # The exact published identity at the public name proves
                    # that reversing once restores it without overwrite.
                    _renameat_exchange(
                        binding.directory_fd,
                        path.name,
                        binding.directory_fd,
                        generation_name,
                    )
                    os.fsync(binding.directory_fd)
                raise InstallError(f"receipt path identity changed during write: {path}")
            _unlink_private_state_inode(
                binding,
                generation_name,
                current_identity,
                "receipt generation",
            )
        published = True
        os.fsync(binding.directory_fd)
        _verify_state_binding(binding)
        written = os.stat(
            path.name, dir_fd=binding.directory_fd, follow_symlinks=False
        )
        binding.validated_leaves[path.name] = (written.st_dev, written.st_ino)
        return True
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(f"cannot durably write receipt: {path}: {error}") from error
    finally:
        if not published and generation_name is not None and generation_identity is not None:
            try:
                remaining = _state_metadata(binding, generation_name)
                # After a successful exchange the private name contains the
                # displaced old identity.  Retain that generation for the
                # retry.  A failed syscall (or a compensated exchange) leaves
                # the newly-created candidate identity, which is safe to
                # reclaim in the private namespace.
                if remaining is not None and (
                    not exchanged
                    or (remaining.st_dev, remaining.st_ino) == generation_identity
                ):
                    _unlink_private_state_inode(
                        binding,
                        generation_name,
                        generation_identity,
                        "receipt generation",
                    )
                elif exchanged and remaining is None:
                    # A fault immediately after exact old-generation reclaim
                    # leaves the canonical candidate as the only generation.
                    # A real process restart has no cached leaf identity; make
                    # an in-process SystemExit retry observe the same state.
                    binding.validated_leaves.pop(path.name, None)
            except OSError:
                pass


def _pending_anchor(
    payload: Mapping[str, object],
    receipt_path: Path,
    *,
    exact_token: str | None = None,
) -> tuple[Path | None, int | None, int | None]:
    values = tuple(
        payload.get(key)
        for key in (
            "candidate_anchor",
            "candidate_anchor_dev",
            "candidate_anchor_ino",
        )
    )
    if values == (None, None, None):
        return None, None, None
    path_value, dev, ino = values
    if not (
        isinstance(path_value, str)
        and isinstance(dev, int)
        and dev > 0
        and isinstance(ino, int)
        and ino > 0
    ):
        raise InstallError(f"receipt pending anchor is malformed: {receipt_path}")
    raw_path = Path(path_value).expanduser()
    if not raw_path.is_absolute() or _has_dot_components(raw_path):
        raise InstallError(f"receipt pending anchor path is malformed: {receipt_path}")
    anchor = _lexical_absolute(raw_path)
    expected_name = (
        f"{OPENCODE_ARTIFACT_ANCHOR_PREFIX}{exact_token}"
        if exact_token is not None
        else None
    )
    if (
        anchor.parent != receipt_path.parent
        or (
            expected_name is not None
            and anchor.name != expected_name
        )
        or (
            expected_name is None
            and not anchor.name.startswith(OPENCODE_ARTIFACT_ANCHOR_PREFIX)
        )
    ):
        raise InstallError(f"receipt pending anchor is outside owned state: {receipt_path}")
    return anchor, dev, ino


def _read_receipt(
    receipt_path: Path,
    repository_root: Path,
    expected_links: Sequence[ProfileLink],
    *,
    require_retirement_secret: bool = False,
) -> _Receipt | None:
    if not _lexists(receipt_path):
        return None
    if not stat.S_ISREG(_state_lstat(receipt_path).st_mode):
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    repository_value = payload.get("repository_root")
    if not isinstance(repository_value, str):
        raise InstallError(f"receipt repository is missing: {receipt_path}")
    try:
        recorded_root = Path(repository_value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"receipt repository cannot be resolved: {receipt_path}") from error
    if recorded_root != repository_root:
        raise InstallError(
            f"receipt repository mismatch: recorded {recorded_root}, requested {repository_root}"
        )
    marketplace_added = payload.get("marketplace_added")
    plugin_installed = payload.get("plugin_installed")
    if not isinstance(marketplace_added, bool) or not isinstance(plugin_installed, bool):
        raise InstallError(f"receipt ownership flags are malformed: {receipt_path}")
    artifact_value = payload.get("artifact_root")
    artifact_root: Path | None = None
    if artifact_value is not None:
        if not isinstance(artifact_value, str):
            raise InstallError(f"receipt artifact root is malformed: {receipt_path}")
        artifact_root = Path(artifact_value).expanduser()
        if not artifact_root.is_absolute() or _has_dot_components(artifact_root):
            raise InstallError(f"receipt artifact root must be an absolute path: {receipt_path}")
        artifact_root = _lexical_absolute(artifact_root)
    artifact_dev_value = payload.get("artifact_dev")
    artifact_ino_value = payload.get("artifact_ino")
    if artifact_dev_value is None and artifact_ino_value is None:
        artifact_dev = None
        artifact_ino = None
    elif (
        artifact_root is not None
        and isinstance(artifact_dev_value, int)
        and artifact_dev_value > 0
        and isinstance(artifact_ino_value, int)
        and artifact_ino_value > 0
    ):
        artifact_dev = artifact_dev_value
        artifact_ino = artifact_ino_value
    else:
        raise InstallError(f"receipt artifact identity is malformed: {receipt_path}")
    artifact_digest_value = payload.get("artifact_digest")
    if artifact_digest_value is None:
        artifact_digest = None
    elif (
        artifact_root is not None
        and artifact_dev is not None
        and isinstance(artifact_digest_value, str)
        and len(artifact_digest_value) == 64
        and all(character in "0123456789abcdef" for character in artifact_digest_value)
    ):
        artifact_digest = artifact_digest_value
    else:
        raise InstallError(f"receipt artifact evidence is malformed: {receipt_path}")
    links = _receipt_links(payload.get("links"), receipt_path, expected_links)
    lineage_value = payload.get("lineage")
    lineage: str | None
    if lineage_value is None:
        lineage = None
    elif isinstance(lineage_value, str) and len(lineage_value) >= 32:
        lineage = lineage_value
    else:
        raise InstallError(f"receipt lineage is malformed: {receipt_path}")
    receipt_secret_value = payload.get("receipt_secret")
    if receipt_secret_value is not None:
        if not isinstance(receipt_secret_value, str):
            raise InstallError(f"receipt generation secret is malformed: {receipt_path}")
        _retirement_secret_bytes(receipt_secret_value)
    anchor_values = tuple(
        payload.get(key)
        for key in ("artifact_anchor", "artifact_anchor_dev", "artifact_anchor_ino")
    )
    if anchor_values == (None, None, None):
        artifact_anchor = None
        artifact_anchor_dev = None
        artifact_anchor_ino = None
    else:
        anchor_path_value, artifact_anchor_dev, artifact_anchor_ino = anchor_values
        if not (
            artifact_root is not None
            and isinstance(anchor_path_value, str)
            and isinstance(artifact_anchor_dev, int)
            and artifact_anchor_dev > 0
            and isinstance(artifact_anchor_ino, int)
            and artifact_anchor_ino > 0
        ):
            raise InstallError(f"receipt artifact anchor is malformed: {receipt_path}")
        raw_anchor = Path(anchor_path_value).expanduser()
        if not raw_anchor.is_absolute() or _has_dot_components(raw_anchor):
            raise InstallError(f"receipt artifact anchor is malformed: {receipt_path}")
        artifact_anchor = _lexical_absolute(raw_anchor)
        if (
            artifact_anchor.parent != receipt_path.parent
            or not artifact_anchor.name.startswith(OPENCODE_ARTIFACT_ANCHOR_PREFIX)
        ):
            raise InstallError(f"receipt artifact anchor is outside owned state: {receipt_path}")
    teardown_phase = payload.get("teardown_phase", "committed")
    if teardown_phase not in {
        "committed",
        "removing-links",
        "artifact-removed",
        "anchor-removed",
    }:
        raise InstallError(f"receipt teardown phase is malformed: {receipt_path}")
    pending_value = payload.get("pending_swap")
    pending: _PendingSwap | None = None
    if pending_value is not None:
        if not isinstance(pending_value, dict) or lineage is None:
            raise InstallError(f"receipt pending swap is malformed: {receipt_path}")
        required = {
            "lineage",
            "artifact",
            "candidate",
            "candidate_dev",
            "candidate_ino",
            "backup",
            "backup_dev",
            "backup_ino",
            "live_dev",
            "live_ino",
            "phase",
        }
        evidence_keys = {"candidate_digest", "backup_digest"}
        anchor_keys = {
            "candidate_anchor",
            "candidate_anchor_dev",
            "candidate_anchor_ino",
        }
        old_anchor_keys = {"old_anchor", "old_anchor_dev", "old_anchor_ino"}
        allowed_pending_shapes = {
            frozenset(required | optional)
            for optional in (
                set(),
                evidence_keys,
                anchor_keys,
                evidence_keys | anchor_keys,
                old_anchor_keys,
                evidence_keys | old_anchor_keys,
                anchor_keys | old_anchor_keys,
                evidence_keys | anchor_keys | old_anchor_keys,
            )
        }
        if frozenset(pending_value) not in allowed_pending_shapes:
            raise InstallError(f"receipt pending swap is malformed: {receipt_path}")
        if pending_value.get("lineage") != lineage:
            raise InstallError(f"receipt pending swap lineage mismatch: {receipt_path}")
        checksum_keys = {
            key
            for key in ("pending_swap_checksum", "pending_swap_auth")
            if key in payload
        }
        checksum_value = payload.get(next(iter(checksum_keys))) if len(checksum_keys) == 1 else None
        if (
            not isinstance(checksum_value, str)
            or checksum_value != _pending_swap_checksum(lineage, pending_value)
        ):
            raise InstallError(f"receipt pending swap checksum failed: {receipt_path}")
        paths = {
            key: pending_value.get(key)
            for key in ("artifact", "candidate", "backup")
        }
        numbers = {
            key: pending_value.get(key)
            for key in (
                "candidate_dev",
                "candidate_ino",
                "backup_dev",
                "backup_ino",
                "live_dev",
                "live_ino",
            )
        }
        if (
            any(not isinstance(value, str) for value in paths.values())
            or any(not isinstance(value, int) or value <= 0 for value in numbers.values())
            or numbers["backup_dev"] != numbers["live_dev"]
            or numbers["backup_ino"] != numbers["live_ino"]
            or (
                numbers["candidate_dev"],
                numbers["candidate_ino"],
            )
            == (numbers["live_dev"], numbers["live_ino"])
            or pending_value.get("phase") not in {
                "prepared",
                "anchor-recorded",
                "backup-created",
                "published",
                "old-artifact-removed",
                "old-anchor-removed",
                "rollback-prepared",
                "rollback-candidate-removed",
                "rollback-anchor-removed",
                "rollback-restored",
            }
        ):
            raise InstallError(f"receipt pending swap is malformed: {receipt_path}")
        candidate_digest = pending_value.get("candidate_digest")
        backup_digest = pending_value.get("backup_digest")
        if not (
            (candidate_digest is None and backup_digest is None)
            or (
                artifact_digest is not None
                and isinstance(candidate_digest, str)
                and len(candidate_digest) == 64
                and all(character in "0123456789abcdef" for character in candidate_digest)
                and isinstance(backup_digest, str)
                and len(backup_digest) == 64
                and all(character in "0123456789abcdef" for character in backup_digest)
                and artifact_digest in {candidate_digest, backup_digest}
            )
        ):
            raise InstallError(f"receipt pending swap evidence is malformed: {receipt_path}")
        candidate_anchor, candidate_anchor_dev, candidate_anchor_ino = (
            _pending_anchor(pending_value, receipt_path)
        )
        old_anchor_value = pending_value.get("old_anchor")
        old_anchor_dev = pending_value.get("old_anchor_dev")
        old_anchor_ino = pending_value.get("old_anchor_ino")
        if old_anchor_value is None and old_anchor_dev is None and old_anchor_ino is None:
            old_anchor = None
        elif (
            isinstance(old_anchor_value, str)
            and isinstance(old_anchor_dev, int)
            and old_anchor_dev > 0
            and isinstance(old_anchor_ino, int)
            and old_anchor_ino > 0
        ):
            old_anchor = _lexical_absolute(Path(old_anchor_value).expanduser())
            if (
                not Path(old_anchor_value).is_absolute()
                or _has_dot_components(Path(old_anchor_value))
                or old_anchor.parent != receipt_path.parent
                or not old_anchor.name.startswith(OPENCODE_ARTIFACT_ANCHOR_PREFIX)
            ):
                raise InstallError(f"receipt old anchor is outside owned state: {receipt_path}")
        else:
            raise InstallError(f"receipt old anchor is malformed: {receipt_path}")
        pending_paths = {key: _lexical_absolute(Path(value).expanduser()) for key, value in paths.items()}
        expected_artifact = _lexical_absolute(receipt_path.parent / OPENCODE_ARTIFACT_DIRECTORY)
        if pending_paths["artifact"] != expected_artifact or any(
            path.parent != expected_artifact.parent for path in pending_paths.values()
        ):
            raise InstallError(f"receipt pending swap path is outside owned state: {receipt_path}")
        if (
            pending_paths["candidate"].name != f".{OPENCODE_ARTIFACT_DIRECTORY}.next-{pending_paths['candidate'].name.rsplit('-', 1)[-1]}"
            or pending_paths["backup"].name != f".{OPENCODE_ARTIFACT_DIRECTORY}.old-{pending_paths['backup'].name.rsplit('-', 1)[-1]}"
            or pending_paths["candidate"] == pending_paths["backup"]
        ):
            raise InstallError(f"receipt pending swap names are malformed: {receipt_path}")
        pending = _PendingSwap(
            lineage=lineage,
            artifact=pending_paths["artifact"],
            candidate=pending_paths["candidate"],
            candidate_dev=numbers["candidate_dev"],
            candidate_ino=numbers["candidate_ino"],
            backup=pending_paths["backup"],
            backup_dev=numbers["backup_dev"],
            backup_ino=numbers["backup_ino"],
            live_dev=numbers["live_dev"],
            live_ino=numbers["live_ino"],
            phase=pending_value["phase"],
            candidate_digest=candidate_digest,
            backup_digest=backup_digest,
            candidate_anchor=candidate_anchor,
            candidate_anchor_dev=candidate_anchor_dev,
            candidate_anchor_ino=candidate_anchor_ino,
            old_anchor=old_anchor,
            old_anchor_dev=old_anchor_dev,
            old_anchor_ino=old_anchor_ino,
        )
        committed_pending_identities = {(pending.live_dev, pending.live_ino)}
        if pending.phase in {
            "published",
            "old-artifact-removed",
            "old-anchor-removed",
        }:
            committed_pending_identities.add(
                (pending.candidate_dev, pending.candidate_ino)
            )
        if artifact_dev is not None and (
            artifact_dev,
            artifact_ino,
        ) not in committed_pending_identities:
            raise InstallError(
                f"receipt pending swap does not extend the committed artifact: {receipt_path}"
            )
    publish_value = payload.get("pending_publish")
    pending_publish: _PendingPublish | None = None
    if publish_value is not None:
        required_publish = {
            "lineage",
            "artifact",
            "candidate",
            "candidate_dev",
            "candidate_ino",
            "phase",
        }
        publish_anchor_keys = {
            "candidate_anchor",
            "candidate_anchor_dev",
            "candidate_anchor_ino",
            "planned_links",
        }
        if (
            not isinstance(publish_value, dict)
            or frozenset(publish_value)
            not in {
                frozenset(required_publish),
                frozenset(required_publish | {"candidate_digest"}),
                frozenset(required_publish | publish_anchor_keys),
                frozenset(
                    required_publish | {"candidate_digest"} | publish_anchor_keys
                ),
            }
            or lineage is None
            or pending is not None
            or publish_value.get("lineage") != lineage
            or publish_value.get("phase") not in {
                "prepared",
                "anchor-recorded",
                "published",
                "planned-links",
                "rollback-prepared",
                "rollback-artifact-removed",
                "rollback-anchor-removed",
            }
        ):
            raise InstallError(f"receipt pending publish is malformed: {receipt_path}")
        publish_digest = publish_value.get("candidate_digest")
        if publish_digest is not None and not (
            isinstance(publish_digest, str)
            and len(publish_digest) == 64
            and all(character in "0123456789abcdef" for character in publish_digest)
        ):
            raise InstallError(f"receipt pending publish evidence is malformed: {receipt_path}")
        planned_links = publish_value.get("planned_links", False)
        if not isinstance(planned_links, bool):
            raise InstallError(
                f"receipt pending publish planned-link state is malformed: {receipt_path}"
            )
        publish_anchor, publish_anchor_dev, publish_anchor_ino = _pending_anchor(
            publish_value, receipt_path, exact_token=lineage
        )
        publish_paths = {
            key: publish_value.get(key) for key in ("artifact", "candidate")
        }
        publish_numbers = {
            key: publish_value.get(key) for key in ("candidate_dev", "candidate_ino")
        }
        if any(not isinstance(value, str) for value in publish_paths.values()) or any(
            not isinstance(value, int) or value <= 0 for value in publish_numbers.values()
        ):
            raise InstallError(f"receipt pending publish is malformed: {receipt_path}")
        normalized_publish_paths = {
            key: _lexical_absolute(Path(value).expanduser())
            for key, value in publish_paths.items()
        }
        expected_artifact = _lexical_absolute(
            receipt_path.parent / OPENCODE_ARTIFACT_DIRECTORY
        )
        if (
            normalized_publish_paths["artifact"] != expected_artifact
            or normalized_publish_paths["candidate"].parent != expected_artifact.parent
            or not normalized_publish_paths["candidate"].name.startswith(
                f".{OPENCODE_ARTIFACT_DIRECTORY}.next-"
            )
        ):
            raise InstallError(f"receipt pending publish path is invalid: {receipt_path}")
        pending_publish = _PendingPublish(
            lineage=lineage,
            artifact=normalized_publish_paths["artifact"],
            candidate=normalized_publish_paths["candidate"],
            candidate_dev=publish_numbers["candidate_dev"],
            candidate_ino=publish_numbers["candidate_ino"],
            phase=publish_value["phase"],
            candidate_digest=publish_digest,
            candidate_anchor=publish_anchor,
            candidate_anchor_dev=publish_anchor_dev,
            candidate_anchor_ino=publish_anchor_ino,
            planned_links=planned_links,
        )
    migration_value = payload.get("pending_migration")
    pending_migration = migration_value is not None
    if pending_migration and (
        migration_value != {"phase": "prepared"}
        or artifact_root is None
        or artifact_dev is None
        or artifact_ino is None
        or artifact_digest is None
        or lineage is None
        or artifact_anchor is None
        or artifact_anchor_dev is None
        or artifact_anchor_ino is None
        or pending is not None
        or pending_publish is not None
        or teardown_phase != "committed"
        or any(
            link.destination_dev is None or link.destination_ino is None
            for link in links
        )
    ):
        raise InstallError(f"receipt pending migration is malformed: {receipt_path}")
    retirement_value = payload.get("pending_retirement")
    pending_retirement = None
    if retirement_value is not None:
        if lineage is None:
            raise InstallError(f"receipt pending retirement lacks lineage: {receipt_path}")
        try:
            pending_retirement = _pending_retirement_from_payload(
                retirement_value,
                lineage,
                require_secret=require_retirement_secret,
            )
            _validate_pending_retirement_authority(
                pending_retirement,
                links=links,
                artifact_root=artifact_root,
                artifact_identity=(artifact_dev, artifact_ino),
                artifact_anchor=(
                    artifact_anchor,
                    artifact_anchor_dev,
                    artifact_anchor_ino,
                ),
                pending_swap=pending,
                pending_publish=pending_publish,
            )
        except InstallError as error:
            raise InstallError(
                f"receipt pending retirement is malformed: {receipt_path}: {error}"
            ) from error
    return _Receipt(
        repository_root=recorded_root,
        links=links,
        marketplace_added=marketplace_added,
        plugin_installed=plugin_installed,
        artifact_root=artifact_root,
        artifact_dev=artifact_dev,
        artifact_ino=artifact_ino,
        artifact_digest=artifact_digest,
        lineage=lineage,
        artifact_anchor=artifact_anchor,
        artifact_anchor_dev=artifact_anchor_dev,
        artifact_anchor_ino=artifact_anchor_ino,
        teardown_phase=teardown_phase,
        pending_swap=pending,
        pending_publish=pending_publish,
        pending_migration=pending_migration,
        pending_retirement=pending_retirement,
    )


def _write_receipt(receipt_path: Path, receipt: _Receipt) -> None:
    receipt_directory = receipt_path.parent
    if _lexists(receipt_path) and not stat.S_ISREG(_state_lstat(receipt_path).st_mode):
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    binding = _state_binding(receipt_path)
    if binding is not None:
        _verify_state_binding(binding)
    else:
        try:
            receipt_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallError(
                f"cannot create receipt directory: {receipt_directory}: {error}"
            ) from error
    serialized_links: list[dict[str, object]] = []
    for link in sorted(receipt.links, key=lambda item: str(item.destination)):
        entry: dict[str, object] = {
            "destination": str(link.destination),
            "source": str(link.source),
        }
        if link.destination_dev is not None or link.destination_ino is not None:
            if link.destination_dev is None or link.destination_ino is None:
                raise InstallError(f"receipt link identity is incomplete: {receipt_path}")
            entry["destination_dev"] = link.destination_dev
            entry["destination_ino"] = link.destination_ino
        if link.staged_destination is not None:
            if link.destination_dev is None or link.destination_ino is None:
                raise InstallError(
                    f"receipt staged link lacks identity: {receipt_path}"
                )
            if not _valid_opencode_link_staging_path(
                link.source, link.destination, link.staged_destination
            ):
                raise InstallError(
                    f"receipt staged link path is invalid: {receipt_path}"
                )
            entry["staged_destination"] = str(link.staged_destination)
        serialized_links.append(entry)
    payload = {
        "links": serialized_links,
        "marketplace_added": receipt.marketplace_added,
        "plugin_installed": receipt.plugin_installed,
        "repository_root": str(receipt.repository_root),
    }
    if receipt.artifact_root is not None:
        payload["artifact_root"] = str(receipt.artifact_root)
    if receipt.artifact_dev is not None or receipt.artifact_ino is not None:
        if receipt.artifact_dev is None or receipt.artifact_ino is None:
            raise InstallError(f"receipt artifact identity is incomplete: {receipt_path}")
        payload["artifact_dev"] = receipt.artifact_dev
        payload["artifact_ino"] = receipt.artifact_ino
    if receipt.artifact_digest is not None:
        if receipt.artifact_dev is None or receipt.artifact_ino is None:
            raise InstallError(f"receipt artifact evidence lacks identity: {receipt_path}")
        payload["artifact_digest"] = receipt.artifact_digest
    if receipt.lineage is not None:
        payload["lineage"] = receipt.lineage
    if receipt.artifact_anchor is not None:
        if receipt.artifact_anchor_dev is None or receipt.artifact_anchor_ino is None:
            raise InstallError(f"receipt artifact anchor identity is incomplete: {receipt_path}")
        payload["artifact_anchor"] = str(receipt.artifact_anchor)
        payload["artifact_anchor_dev"] = receipt.artifact_anchor_dev
        payload["artifact_anchor_ino"] = receipt.artifact_anchor_ino
    if receipt.artifact_root is not None:
        if receipt.teardown_phase not in {
            "committed",
            "removing-links",
            "artifact-removed",
            "anchor-removed",
        }:
            raise InstallError(f"receipt teardown phase is invalid: {receipt_path}")
        payload["teardown_phase"] = receipt.teardown_phase
    if receipt.pending_swap is not None:
        pending = receipt.pending_swap
        pending_payload = _pending_swap_payload(pending)
        payload["pending_swap"] = pending_payload
        payload["pending_swap_checksum"] = _pending_swap_checksum(
            pending.lineage, pending_payload
        )
    if receipt.pending_publish is not None:
        pending_publish = receipt.pending_publish
        payload["pending_publish"] = {
            "artifact": str(pending_publish.artifact),
            "candidate": str(pending_publish.candidate),
            "candidate_dev": pending_publish.candidate_dev,
            "candidate_ino": pending_publish.candidate_ino,
            "lineage": pending_publish.lineage,
            "phase": pending_publish.phase,
        }
        if pending_publish.candidate_digest is not None:
            payload["pending_publish"]["candidate_digest"] = (
                pending_publish.candidate_digest
            )
        if pending_publish.candidate_anchor is not None:
            if (
                pending_publish.candidate_anchor_dev is None
                or pending_publish.candidate_anchor_ino is None
            ):
                raise InstallError(
                    f"pending OpenCode publication anchor identity is incomplete: {receipt_path}"
                )
            payload["pending_publish"]["candidate_anchor"] = str(
                pending_publish.candidate_anchor
            )
            payload["pending_publish"]["candidate_anchor_dev"] = (
                pending_publish.candidate_anchor_dev
            )
            payload["pending_publish"]["candidate_anchor_ino"] = (
                pending_publish.candidate_anchor_ino
            )
            payload["pending_publish"]["planned_links"] = (
                pending_publish.planned_links
            )
    if receipt.pending_migration:
        if (
            receipt.pending_swap is not None
            or receipt.pending_publish is not None
            or receipt.artifact_root is None
            or receipt.artifact_dev is None
            or receipt.artifact_ino is None
            or receipt.artifact_digest is None
            or receipt.lineage is None
            or receipt.artifact_anchor is None
            or receipt.artifact_anchor_dev is None
            or receipt.artifact_anchor_ino is None
            or any(
                link.destination_dev is None or link.destination_ino is None
                for link in receipt.links
            )
        ):
            raise InstallError(
                f"pending OpenCode migration receipt is incomplete: {receipt_path}"
            )
        payload["pending_migration"] = {"phase": "prepared"}
    if receipt.pending_retirement is not None:
        if receipt.lineage is None:
            raise InstallError(
                f"pending OpenCode retirement lacks lineage: {receipt_path}"
            )
        payload["pending_retirement"] = _pending_retirement_payload(
            receipt.pending_retirement
        )
    if binding is not None and _lexists(receipt_path):
        try:
            durable_payload = json.loads(_read_state_text(receipt_path))
        except (OSError, json.JSONDecodeError) as error:
            raise InstallError(
                f"cannot verify receipt retirement before write: {receipt_path}: {error}"
            ) from error
        if isinstance(durable_payload, dict):
            durable_retirement = durable_payload.get("pending_retirement")
            if durable_retirement is not None:
                durable_phase = (
                    durable_retirement.get("phase")
                    if isinstance(durable_retirement, dict)
                    else None
                )
                if durable_phase != "done" and payload.get(
                    "pending_retirement"
                ) != durable_retirement:
                    raise InstallError(
                        "cannot overwrite an outstanding OpenCode retirement"
                    )
    if _write_state_payload(receipt_path, payload):
        return
    temporary_path: Path | None = None
    write_error: InstallError | None = None
    write_cause: OSError | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{RECEIPT_FILENAME}.", suffix=".tmp", dir=receipt_directory
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, receipt_path)
        temporary_path = None
        directory_fd = os.open(
            receipt_directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        write_cause = error
        write_error = InstallError(f"cannot write receipt: {receipt_path}: {error}")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError as error:
                cleanup_failure = f"temporary receipt cleanup failed: {temporary_path}: {error}"
                if write_error is None:
                    write_error = InstallError(cleanup_failure)
                    write_cause = error
                else:
                    write_error = InstallError(f"{write_error}; {cleanup_failure}")
    if write_error is not None:
        raise write_error from write_cause


def _invoke_runner(run: Runner | Callable[[Sequence[str]], object], command: list[str]) -> _CommandResult:
    try:
        if callable(run):
            raw_result = run(command)
        else:
            runner_method = getattr(run, "run", None)
            if not callable(runner_method):
                raise TypeError("runner must be callable or provide run()")
            raw_result = runner_method(command)
    except subprocess.CalledProcessError as error:
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else str(error)
        return _CommandResult(error.returncode, stdout, stderr)
    except OSError as error:
        raise InstallError(f"command could not be executed: {' '.join(command)}: {error}") from error
    except Exception as error:
        raise InstallError(f"command runner failed for {' '.join(command)}: {error}") from error
    if isinstance(raw_result, Mapping):
        return _CommandResult(0, json.dumps(raw_result), "")
    returncode = getattr(raw_result, "returncode", getattr(raw_result, "exit_code", None))
    stdout = getattr(raw_result, "stdout", "")
    stderr = getattr(raw_result, "stderr", "")
    if returncode is None and isinstance(raw_result, tuple) and len(raw_result) == 3:
        returncode, stdout, stderr = raw_result
    if returncode is None and isinstance(raw_result, str):
        returncode = 0
        stdout = raw_result
    if not isinstance(returncode, int):
        raise InstallError(f"command runner returned an unsupported result for {' '.join(command)}")
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    if not isinstance(stdout, str):
        stdout = str(stdout)
    if not isinstance(stderr, str):
        stderr = str(stderr)
    return _CommandResult(returncode, stdout, stderr)


def _run_command(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> _CommandResult:
    return _invoke_runner(run, command)


def _require_success(command: list[str], result: _CommandResult) -> None:
    if result.returncode == 0:
        return
    details = result.stderr.strip() or result.stdout.strip()
    suffix = f": {details}" if details else ""
    raise InstallError(
        f"command failed with exit code {result.returncode}: {' '.join(command)}{suffix}"
    )


def _parse_json(command: list[str], result: _CommandResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise InstallError(f"command returned invalid JSON: {' '.join(command)}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"command returned non-object JSON: {' '.join(command)}")
    return payload


def _run_json(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> dict[str, Any]:
    result = _run_command(run, command)
    _require_success(command, result)
    return _parse_json(command, result)


def _canonical_source(value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _marketplace_state(payload: Mapping[str, Any], repository_root: Path) -> str:
    marketplaces = payload.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallError("marketplace list JSON did not contain marketplaces")
    found = False
    for marketplace in marketplaces:
        if not isinstance(marketplace, dict):
            raise InstallError("marketplace list JSON contained a non-object entry")
        if marketplace.get("name") != MARKETPLACE_NAME:
            continue
        found = True
        candidates = [_canonical_source(marketplace.get("root"))]
        marketplace_source = marketplace.get("marketplaceSource")
        if isinstance(marketplace_source, dict):
            candidates.append(_canonical_source(marketplace_source.get("source")))
        if any(candidate == repository_root for candidate in candidates):
            return "owned"
    return "foreign" if found else "absent"


def _validate_marketplace_add(payload: Mapping[str, Any], repository_root: Path) -> None:
    if payload.get("marketplaceName") != MARKETPLACE_NAME:
        raise InstallError("marketplace add JSON identified the wrong marketplace")
    installed_root = _canonical_source(payload.get("installedRoot"))
    if installed_root != repository_root:
        raise InstallError("marketplace add JSON identified the wrong repository")
    if not isinstance(payload.get("alreadyAdded"), bool):
        raise InstallError("marketplace add JSON did not report alreadyAdded")


def _plugin_entries(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    installed = payload.get("installed")
    if not isinstance(installed, list):
        raise InstallError("plugin list JSON did not contain installed plugins")
    entries: list[dict[str, Any]] = []
    for entry in installed:
        if not isinstance(entry, dict):
            raise InstallError("plugin list JSON contained a non-object entry")
        entries.append(entry)
    return entries


def _plugin_presence(payload: Mapping[str, Any]) -> str:
    for entry in _plugin_entries(payload):
        if entry.get("pluginId") == PLUGIN_SELECTOR:
            return "present"
    return "absent"


def _plugin_state(payload: Mapping[str, Any], repository_root: Path) -> str:
    for entry in _plugin_entries(payload):
        if entry.get("pluginId") != PLUGIN_SELECTOR:
            continue
        if entry.get("marketplaceName") != MARKETPLACE_NAME:
            return "foreign"
        marketplace_source = entry.get("marketplaceSource")
        source = None
        if isinstance(marketplace_source, dict):
            source = _canonical_source(marketplace_source.get("source"))
        if source == repository_root:
            return "owned"
        return "foreign"
    return "absent"


def _validated_manifest_version(repository_root: Path) -> str:
    manifest_path = repository_root / "packages" / "expskill" / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"validated plugin manifest could not be read: {manifest_path}: {error}") from error
    version = manifest.get("version")
    if not isinstance(version, str):
        raise InstallError(f"validated plugin manifest has no version: {manifest_path}")
    return version


def _validate_plugin_add(payload: Mapping[str, Any], expected_version: str) -> None:
    if payload.get("pluginId") != PLUGIN_SELECTOR:
        raise InstallError("plugin add JSON identified the wrong plugin")
    if payload.get("name") != PLUGIN_NAME:
        raise InstallError("plugin add JSON identified the wrong plugin name")
    if payload.get("marketplaceName") != MARKETPLACE_NAME:
        raise InstallError("plugin add JSON identified the wrong marketplace")
    if payload.get("version") != expected_version:
        raise InstallError("plugin add JSON identified the wrong version")
    if not isinstance(payload.get("installedPath"), str) or not payload["installedPath"]:
        raise InstallError("plugin add JSON did not report an installed path")


def _create_links(links: Sequence[ProfileLink], created: list[ProfileLink]) -> None:
    parents: list[Path] = []
    for link in links:
        if link.destination.parent not in parents:
            parents.append(link.destination.parent)
    for parent in parents:
        try:
            representative = next(
                link.destination for link in links if link.destination.parent == parent
            )
            if _config_binding(representative) is not None:
                _bound_config_parent(representative, create=True)
            else:
                parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallError(
                f"cannot create agent destination directory: {parent}: {error}"
            ) from error
    for link in links:
        if _lexists(link.destination):
            if not _same_owned_link(link.destination, link.source):
                raise InstallError(f"refusing conflicting agent destination: {link.destination}")
            continue
        try:
            publication_hook = getattr(created, "record_staged", None)

            def record_staged(path: Path, dev: int, ino: int) -> None:
                if publication_hook is None:
                    return
                publication_hook(
                    replace(
                        link,
                        destination_dev=dev,
                        destination_ino=ino,
                        staged_destination=path,
                    )
                )

            identity = _create_destination_link(
                link.destination,
                link.source,
                record_staged if publication_hook is not None else None,
            )
        except OSError as error:
            raise InstallError(f"cannot create agent link: {link.destination}: {error}") from error
        created.append(
            link
            if identity is None
            else replace(
                link,
                destination_dev=identity[0],
                destination_ino=identity[1],
            )
        )


def _rollback_links(links: Sequence[ProfileLink]) -> list[str]:
    failures: list[str] = []
    for link in reversed(tuple(links)):
        if link.staged_destination is not None:
            try:
                _remove_recorded_opencode_staging(link)
            except (OSError, InstallError) as error:
                failures.append(f"staged link {link.staged_destination}: {error}")
        if link.destination_dev is not None or link.destination_ino is not None:
            if link.destination_dev is None or link.destination_ino is None:
                failures.append(
                    f"link preserved because ownership is incomplete: {link.destination}"
                )
                continue
            try:
                _unlink_recorded_destination(link)
            except (OSError, InstallError) as error:
                failures.append(f"link {link.destination}: {error}")
            continue
        if not _lexists(link.destination):
            continue
        owned = _same_recorded_link(link.destination, link.source)
        if not owned:
            failures.append(f"link preserved because ownership changed: {link.destination}")
            continue
        try:
            _unlink_destination(link.destination)
        except (OSError, InstallError) as error:
            failures.append(f"link {link.destination}: {error}")
    return failures


def _same_recorded_link(destination: Path, source: Path) -> bool:
    if _config_binding(destination) is not None:
        return _bound_link_identity(destination, source)
    if not destination.is_symlink():
        return False
    try:
        stored_target = Path(os.readlink(destination))
    except OSError:
        return False
    if not stored_target.is_absolute():
        stored_target = destination.parent / stored_target
    return _lexical_absolute(stored_target) == _lexical_absolute(source)


def _prune_retired_links(
    receipt_path: Path,
    receipt: _Receipt,
    current_links: Sequence[ProfileLink],
) -> tuple[_Receipt, tuple[ProfileLink, ...]]:
    current = receipt
    removed: list[ProfileLink] = []
    current_destinations = {link.destination for link in current_links}
    for link in receipt.links:
        if link.destination in current_destinations:
            continue
        remaining = tuple(item for item in current.links if item != link)
        if not _lexists(link.destination) or not _same_recorded_link(
            link.destination, link.source
        ):
            current = _persist_receipt(receipt_path, current, links=remaining)
            continue
        try:
            _unlink_destination(link.destination)
        except OSError as error:
            raise InstallError(
                f"cannot remove retired agent link: {link.destination}: {error}"
            ) from error
        removed.append(link)
        current = _persist_receipt(receipt_path, current, links=remaining)
    return current, tuple(removed)


def _remove_command(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> str | None:
    try:
        result = _run_command(run, command)
    except InstallError as error:
        return str(error)
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        suffix = f": {details}" if details else ""
        return f"command failed with exit code {result.returncode}: {' '.join(command)}{suffix}"
    try:
        _parse_json(command, result)
    except InstallError as error:
        return str(error)
    return None


def _cleanup_after_install_failure(
    original: Exception,
    run: Runner | Callable[[Sequence[str]], object],
    created_links: Sequence[ProfileLink],
    plugin_new: bool,
    marketplace_new: bool,
) -> None:
    failures: list[str] = []
    if plugin_new:
        failure = _remove_command(
            run,
            ["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"],
        )
        if failure:
            failures.append(f"plugin rollback: {failure}")
    if marketplace_new:
        failure = _remove_command(
            run,
            ["codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME, "--json"],
        )
        if failure:
            failures.append(f"marketplace rollback: {failure}")
    failures.extend(f"link rollback: {failure}" for failure in _rollback_links(created_links))
    if failures:
        raise InstallError(f"{original}; residual state or rollback failures: {'; '.join(failures)}") from original
    if isinstance(original, InstallError):
        raise original
    raise InstallError(str(original)) from original


def install(
    repo_root: Path,
    codex_home: Path,
    state_home: Path,
    run: Runner | Callable[[Sequence[str]], object],
    agents_only: bool = False,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    links = preflight_links(canonical_root, codex_home)
    receipt_links = _allowlisted_links(canonical_root, codex_home)
    plugin_version = _validated_manifest_version(canonical_root)
    receipt_path_value = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path_value, canonical_root, receipt_links)
    if not agents_only:
        marketplace_payload = _run_json(
            run,
            ["codex", "plugin", "marketplace", "list", "--json"],
        )
        marketplace_state = _marketplace_state(marketplace_payload, canonical_root)
        if marketplace_state == "foreign":
            raise InstallError("marketplace name conflict from another repository")
    created_links: list[ProfileLink] = []
    marketplace_new = False
    plugin_new = False
    removed_links: tuple[ProfileLink, ...] = ()
    try:
        _create_links(links, created_links)
        if not agents_only:
            marketplace_add_command = [
                "codex",
                "plugin",
                "marketplace",
                "add",
                str(canonical_root),
                "--json",
            ]
            marketplace_add_result = _run_command(run, marketplace_add_command)
            _require_success(marketplace_add_command, marketplace_add_result)
            marketplace_new = marketplace_state == "absent"
            marketplace_add_json = _parse_json(marketplace_add_command, marketplace_add_result)
            _validate_marketplace_add(marketplace_add_json, canonical_root)
            plugin_payload = _run_json(run, ["codex", "plugin", "list", "--json"])
            plugin_state = _plugin_presence(plugin_payload)
            plugin_add_command = ["codex", "plugin", "add", PLUGIN_SELECTOR, "--json"]
            plugin_add_result = _run_command(run, plugin_add_command)
            _require_success(plugin_add_command, plugin_add_result)
            plugin_new = plugin_state == "absent"
            plugin_add_json = _parse_json(plugin_add_command, plugin_add_result)
            _validate_plugin_add(plugin_add_json, plugin_version)
        if receipt is not None:
            receipt, removed_links = _prune_retired_links(
                receipt_path_value,
                receipt,
                links,
            )
        previous_links = () if receipt is None else receipt.links
        merged_links = list(previous_links)
        known_destinations = {link.destination for link in merged_links}
        for link in created_links:
            if link.destination not in known_destinations:
                merged_links.append(link)
                known_destinations.add(link.destination)
        merged_receipt = _Receipt(
            repository_root=canonical_root,
            links=tuple(merged_links),
            marketplace_added=(receipt.marketplace_added if receipt else False) or marketplace_new,
            plugin_installed=(receipt.plugin_installed if receipt else False) or plugin_new,
        )
        _write_receipt(receipt_path_value, merged_receipt)
    except Exception as error:
        _cleanup_after_install_failure(
            error,
            run,
            created_links,
            plugin_new,
            marketplace_new,
        )
    return InstallResult(
        links=links,
        created_links=tuple(created_links),
        removed_links=removed_links,
        marketplace_added=marketplace_new,
        plugin_installed=plugin_new,
    )


def _marketplace_matches(repository_root: Path, payload: Mapping[str, Any]) -> str:
    return _marketplace_state(payload, repository_root)


def _persist_receipt(
    receipt_path: Path,
    receipt: _Receipt,
    *,
    links: Sequence[ProfileLink] | None = None,
    marketplace_added: bool | None = None,
    plugin_installed: bool | None = None,
) -> _Receipt:
    updated = _Receipt(
        repository_root=receipt.repository_root,
        links=tuple(receipt.links if links is None else links),
        marketplace_added=receipt.marketplace_added if marketplace_added is None else marketplace_added,
        plugin_installed=receipt.plugin_installed if plugin_installed is None else plugin_installed,
        artifact_root=receipt.artifact_root,
        artifact_dev=receipt.artifact_dev,
        artifact_ino=receipt.artifact_ino,
        artifact_digest=receipt.artifact_digest,
        lineage=receipt.lineage,
        artifact_anchor=receipt.artifact_anchor,
        artifact_anchor_dev=receipt.artifact_anchor_dev,
        artifact_anchor_ino=receipt.artifact_anchor_ino,
        teardown_phase=receipt.teardown_phase,
        pending_swap=receipt.pending_swap,
        pending_publish=receipt.pending_publish,
        pending_migration=receipt.pending_migration,
        pending_retirement=receipt.pending_retirement,
    )
    _write_receipt(receipt_path, updated)
    return updated


def _remove_owned_links(
    receipt_path: Path,
    receipt: _Receipt,
) -> tuple[_Receipt, tuple[ProfileLink, ...], list[str]]:
    current = receipt
    removed: list[ProfileLink] = []
    failures: list[str] = []
    for link in receipt.links:
        if not _lexists(link.destination):
            current = _persist_receipt(receipt_path, current, links=tuple(item for item in current.links if item != link))
            continue
        if not _same_recorded_link(link.destination, link.source):
            current = _persist_receipt(receipt_path, current, links=tuple(item for item in current.links if item != link))
            continue
        try:
            _unlink_destination(link.destination)
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
            continue
        removed.append(link)
        current = _persist_receipt(receipt_path, current, links=tuple(item for item in current.links if item != link))
    return current, tuple(removed), failures


def uninstall(
    repo_root: Path,
    codex_home: Path,
    state_home: Path,
    run: Runner | Callable[[Sequence[str]], object],
    agents_only: bool = False,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    links = _allowlisted_links(canonical_root, codex_home)
    receipt_path_value = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path_value, canonical_root, links)
    if receipt is None:
        return InstallResult(links=links)
    current = receipt
    plugin_state: str | None = None
    marketplace_state: str | None = None
    if not agents_only and receipt.plugin_installed:
        plugin_payload = _run_json(run, ["codex", "plugin", "list", "--json"])
        plugin_state = _plugin_state(plugin_payload, canonical_root)
    if not agents_only and receipt.marketplace_added:
        marketplace_payload = _run_json(
            run,
            ["codex", "plugin", "marketplace", "list", "--json"],
        )
        marketplace_state = _marketplace_matches(canonical_root, marketplace_payload)
    if plugin_state in {"owned", "absent"}:
        plugin_remove = ["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"]
        plugin_remove_result = _run_command(run, plugin_remove)
        _require_success(plugin_remove, plugin_remove_result)
        _parse_json(plugin_remove, plugin_remove_result)
        current = _persist_receipt(receipt_path_value, current, plugin_installed=False)
    elif plugin_state == "foreign":
        current = _persist_receipt(receipt_path_value, current, plugin_installed=False)
    if marketplace_state == "owned":
        marketplace_remove = [
            "codex",
            "plugin",
            "marketplace",
            "remove",
            MARKETPLACE_NAME,
            "--json",
        ]
        marketplace_remove_result = _run_command(run, marketplace_remove)
        _require_success(marketplace_remove, marketplace_remove_result)
        _parse_json(marketplace_remove, marketplace_remove_result)
        current = _persist_receipt(receipt_path_value, current, marketplace_added=False)
    elif marketplace_state in {"absent", "foreign"}:
        current = _persist_receipt(receipt_path_value, current, marketplace_added=False)
    current, removed_links, link_failures = _remove_owned_links(receipt_path_value, current)
    if link_failures:
        raise InstallError("owned link cleanup failed: " + "; ".join(link_failures))
    if current.links:
        raise InstallError("owned link cleanup did not converge")
    if current.marketplace_added or current.plugin_installed:
        return InstallResult(
            links=links,
            removed_links=removed_links,
            marketplace_added=current.marketplace_added,
            plugin_installed=current.plugin_installed,
        )
    if receipt_path_value.is_symlink() or not receipt_path_value.is_file():
        raise InstallError(f"receipt path is not a regular file: {receipt_path_value}")
    try:
        receipt_path_value.unlink()
    except OSError as error:
        raise InstallError(f"cannot remove receipt: {receipt_path_value}: {error}") from error
    return InstallResult(
        links=links,
        removed_links=removed_links,
        marketplace_added=receipt.marketplace_added,
        plugin_installed=receipt.plugin_installed,
    )


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def _default_state_home() -> Path:
    return Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    ).expanduser()


def _print_dry_run(repo_root: Path, codex_home: Path, agents_only: bool = False) -> None:
    links = preflight_links(repo_root, codex_home)
    for link in links:
        print(f"link {link.destination} -> {link.source}")
    if agents_only:
        print("codex agent links only: the plugin itself stays managed through codex plugin CLI")
        return
    repository = links[0].source.parent.parent.parent.parent.parent
    print(f"codex plugin marketplace add {repository} --json")
    print(f"codex plugin add {PLUGIN_SELECTOR} --json")


def _default_opencode_config_dir() -> Path:
    explicit = os.environ.get("OPENCODE_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        xdg_path = Path(xdg_config_home)
        if xdg_path.is_absolute():
            return xdg_path / "opencode"
    return Path.home() / ".config" / "opencode"


def _opencode_receipt_path(state_home: Path) -> Path:
    # Inspect the caller's lexical path before resolving it.  A symlinked
    # state ancestor must never be silently redirected to an external tree.
    _assert_no_symlink_components(Path(state_home).expanduser(), "opencode state")
    try:
        canonical_state_home = Path(state_home).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"opencode state home cannot be resolved: {state_home}: {error}") from error
    receipt_directory = canonical_state_home / RECEIPT_DIRECTORY
    _assert_no_symlink_components(receipt_directory, "opencode state")
    if _lexists(receipt_directory) and not receipt_directory.is_dir():
        raise InstallError(f"opencode state directory is not a directory: {receipt_directory}")
    return receipt_directory / OPENCODE_RECEIPT_FILENAME


def _opencode_artifact_path(state_home: Path) -> Path:
    return _opencode_receipt_path(state_home).parent / OPENCODE_ARTIFACT_DIRECTORY


def _legacy_opencode_expected_links(
    repo_root: Path, config_dir: Path
) -> tuple[ProfileLink, ...]:
    """Return only the exact roster emitted by the original feature head."""

    canonical_root = _canonical_repository_root(repo_root)
    canonical_config = _canonical_opencode_config(config_dir)
    codex_skills = canonical_root / "packages" / "codex" / "skills"
    opencode_root = canonical_root / "packages" / "opencode"
    links: list[ProfileLink] = []
    for name in LEGACY_OPENCODE_SKILLS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(codex_skills / name),
                destination=canonical_config / "skills" / name,
            )
        )
    for name in LEGACY_OPENCODE_SKILLS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(opencode_root / "commands" / f"{name}.md"),
                destination=canonical_config / "commands" / f"{name}.md",
            )
        )
    for name in LEGACY_OPENCODE_AGENTS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(opencode_root / "agents" / f"{name}.md"),
                destination=canonical_config / "agents" / f"{name}.md",
            )
        )
    for name in LEGACY_OPENCODE_PLUGINS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(opencode_root / "plugins" / name),
                destination=canonical_config / "plugins" / name,
            )
        )
    for link in links:
        _validate_opencode_destination(link.destination, canonical_config)
    return tuple(links)


def _assert_no_symlink_components(path: Path, label: str) -> None:
    """Reject symlinked ancestors before reading or removing owned state."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError as error:
            raise InstallError(f"{label} cannot be inspected: {current}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise InstallError(f"{label} path component must not be a symlink: {current}")


def _canonical_opencode_config(config_dir: Path) -> Path:
    _assert_no_symlink_components(Path(config_dir).expanduser(), "OpenCode config")
    try:
        canonical = Path(config_dir).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"OpenCode config directory cannot be resolved: {config_dir}: {error}") from error
    if _lexists(canonical) and not canonical.is_dir():
        raise InstallError(f"OpenCode config path is not a directory: {canonical}")
    return canonical


def _open_config_binding(directory: Path, *, create: bool) -> _ConfigBinding | None:
    """Open a config root component-by-component without following links."""

    absolute = _lexical_absolute(directory)
    descriptor = os.open(absolute.anchor, _directory_open_flags())
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    descriptor = -1
                    return None
                os.mkdir(component, mode=0o755, dir_fd=descriptor)
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        binding = _ConfigBinding(
            directory=absolute,
            directory_fd=descriptor,
            identity=(metadata.st_dev, metadata.st_ino),
        )
        _verify_config_binding(binding)
        return binding
    except Exception:
        os.close(descriptor)
        raise


def _verify_config_binding(binding: _ConfigBinding) -> None:
    try:
        metadata = os.stat(binding.directory, follow_symlinks=False)
    except OSError as error:
        raise InstallError(
            f"OpenCode config directory binding was replaced: {binding.directory}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != binding.identity
    ):
        raise InstallError(
            f"OpenCode config directory binding was replaced: {binding.directory}"
        )


def _config_binding(path: Path) -> _ConfigBinding | None:
    candidate = _lexical_absolute(path)
    for binding in _CONFIG_BINDINGS.values():
        if candidate != binding.directory and _path_is_within(
            candidate, binding.directory
        ):
            return binding
    return None


def _bound_config_parent(
    destination: Path, *, create: bool
) -> tuple[_ConfigBinding, int] | None:
    binding = _config_binding(destination)
    if binding is None:
        return None
    _verify_config_binding(binding)
    relative = _lexical_absolute(destination).parent.relative_to(binding.directory)
    parts = relative.parts
    descriptor = os.dup(binding.directory_fd)
    try:
        for component in parts:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    descriptor = -1
                    return None
                os.mkdir(component, mode=0o755, dir_fd=descriptor)
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        retained = binding.parents.get(parts)
        if retained is None:
            binding.parents[parts] = (descriptor, identity)
            descriptor = -1
            parent_fd = binding.parents[parts][0]
        else:
            parent_fd, expected = retained
            retained_metadata = os.fstat(parent_fd)
            if identity != expected or (
                retained_metadata.st_dev,
                retained_metadata.st_ino,
            ) != expected:
                raise InstallError(
                    f"OpenCode config destination parent was replaced: {destination.parent}"
                )
        _verify_config_binding(binding)
        return binding, parent_fd
    except OSError as error:
        raise InstallError(
            f"OpenCode config destination parent cannot be opened: {destination.parent}: {error}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _close_config_binding(binding: _ConfigBinding) -> None:
    for descriptor, _identity in binding.parents.values():
        os.close(descriptor)
    os.close(binding.directory_fd)


def _bound_link_identity(destination: Path, source: Path) -> bool:
    bound = _bound_config_parent(destination, create=False)
    if bound is None:
        return False
    binding, parent_fd = bound
    try:
        metadata = os.stat(
            destination.name, dir_fd=parent_fd, follow_symlinks=False
        )
        if not stat.S_ISLNK(metadata.st_mode):
            return False
        target = Path(os.readlink(destination.name, dir_fd=parent_fd))
    except OSError:
        return False
    if not target.is_absolute():
        target = destination.parent / target
    if _lexical_absolute(target) != _lexical_absolute(source):
        return False
    binding.validated_leaves[_lexical_absolute(destination)] = (
        metadata.st_dev,
        metadata.st_ino,
    )
    return True


def _create_destination_link(
    destination: Path,
    source: Path,
    record_staged: Callable[[Path, int, int], None] | None = None,
) -> tuple[int, int] | None:
    bound = _bound_config_parent(destination, create=True)
    if bound is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
        return None
    binding, parent_fd = bound
    _verify_config_binding(binding)
    temporary_path = (
        _opencode_link_staging_path(source, destination)
        if record_staged is not None
        else destination.parent / f".{destination.name}.{uuid.uuid4().hex}.link"
    )
    temporary_name = temporary_path.name
    temporary_identity: tuple[int, int] | None = None
    journaled = False
    published = False
    try:
        while True:
            try:
                os.symlink(os.fspath(source), temporary_name, dir_fd=parent_fd)
                break
            except FileExistsError:
                if record_staged is None:
                    raise
                temporary_path = _alternate_opencode_link_staging_path(
                    source, destination
                )
                temporary_name = temporary_path.name
        temporary = os.stat(
            temporary_name, dir_fd=parent_fd, follow_symlinks=False
        )
        if not stat.S_ISLNK(temporary.st_mode):
            raise InstallError(
                f"OpenCode temporary destination is not a symlink: {destination}"
            )
        temporary_identity = (temporary.st_dev, temporary.st_ino)
        target = Path(os.readlink(temporary_name, dir_fd=parent_fd))
        if not target.is_absolute():
            target = destination.parent / target
        if _lexical_absolute(target) != _lexical_absolute(source):
            raise InstallError(
                f"OpenCode temporary link target changed: {destination}"
            )
        if record_staged is not None:
            record_staged(
                temporary_path,
                temporary_identity[0],
                temporary_identity[1],
            )
            journaled = True
        # The receipt's exact pathname/dev/ino authority is durable before the
        # directory fsync can make the staged symlink survive a power loss.
        os.fsync(parent_fd)
        _renameat_noreplace(
            parent_fd,
            temporary_name,
            parent_fd,
            destination.name,
        )
        published = True
        os.fsync(parent_fd)
        current = os.stat(
            destination.name, dir_fd=parent_fd, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != temporary_identity:
            raise InstallError(
                f"OpenCode config destination identity changed: {destination}"
            )
        binding.validated_leaves[
            _lexical_absolute(destination)
        ] = temporary_identity
        _verify_config_binding(binding)
        return temporary_identity
    finally:
        # If OpenCode receipt persistence fails before ``journaled`` becomes
        # true, no durable authority exists for this newly-created hidden inode.
        # Preserve it rather than inventing an unjournaled OpenCode cleanup
        # path.  The non-OpenCode installer still owns its temporary inode
        # directly within this process and keeps its existing compensation.
        if (
            record_staged is None
            and not published
            and temporary_identity is not None
        ):
            try:
                _unlink_exact_leaf_via_exchange(
                    parent_fd,
                    temporary_name,
                    temporary_identity,
                    "temporary destination",
                )
            except (OSError, InstallError):
                pass


def _capture_opencode_link_identity(link: ProfileLink) -> ProfileLink:
    """Freeze one no-follow symlink identity after validating its binding."""

    bound = _bound_config_parent(link.destination, create=False)
    if bound is None:
        raise InstallError(f"OpenCode link parent is missing: {link.destination}")
    binding, parent_fd = bound
    _verify_config_binding(binding)
    try:
        metadata = os.stat(
            link.destination.name, dir_fd=parent_fd, follow_symlinks=False
        )
        target = Path(os.readlink(link.destination.name, dir_fd=parent_fd))
    except OSError as error:
        raise InstallError(
            f"OpenCode link identity cannot be captured: {link.destination}: {error}"
        ) from error
    if not stat.S_ISLNK(metadata.st_mode):
        raise InstallError(f"OpenCode destination is not a symlink: {link.destination}")
    if not target.is_absolute():
        target = link.destination.parent / target
    if _lexical_absolute(target) != _lexical_absolute(link.source):
        raise InstallError(f"OpenCode link target changed: {link.destination}")
    identity = (metadata.st_dev, metadata.st_ino)
    binding.validated_leaves[_lexical_absolute(link.destination)] = identity
    _verify_config_binding(binding)
    return replace(
        link,
        destination_dev=identity[0],
        destination_ino=identity[1],
    )


def _recorded_opencode_link_is_live(link: ProfileLink) -> bool:
    """Match both target text and the committed no-follow symlink inode."""

    if link.destination_dev is None or link.destination_ino is None:
        return False
    if not _bound_link_identity(link.destination, link.source):
        return False
    binding = _config_binding(link.destination)
    if binding is None:
        return False
    return binding.validated_leaves.get(_lexical_absolute(link.destination)) == (
        link.destination_dev,
        link.destination_ino,
    )


def _recorded_opencode_link_path_is_live(link: ProfileLink, path: Path) -> bool:
    """Match a final or staged pathname to the receipt's symlink authority."""

    if link.destination_dev is None or link.destination_ino is None:
        return False
    bound = _bound_config_parent(path, create=False)
    if bound is None:
        return False
    binding, parent_fd = bound
    _verify_config_binding(binding)
    try:
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        target = Path(os.readlink(path.name, dir_fd=parent_fd))
    except OSError:
        return False
    if not stat.S_ISLNK(metadata.st_mode):
        return False
    if not target.is_absolute():
        target = path.parent / target
    if _lexical_absolute(target) != _lexical_absolute(link.source):
        return False
    identity = (metadata.st_dev, metadata.st_ino)
    if identity != (link.destination_dev, link.destination_ino):
        return False
    binding.validated_leaves[_lexical_absolute(path)] = identity
    _verify_config_binding(binding)
    return True


def _remove_recorded_opencode_staging(link: ProfileLink) -> bool:
    """Remove only the exact staged inode cited by the durable receipt."""

    staging = link.staged_destination
    if (
        staging is None
        or link.destination_dev is None
        or link.destination_ino is None
    ):
        return False
    return _unlink_recorded_destination(link, staging)


def _recover_staged_opencode_links(receipt: _Receipt) -> None:
    """Idempotently finish receipt-journaled staging-to-final renames."""

    for link in receipt.links:
        staging = link.staged_destination
        if staging is None:
            continue
        if link.destination_dev is None or link.destination_ino is None:
            continue
        identity = (link.destination_dev, link.destination_ino)
        final_quarantine = _deletion_quarantine_path(link.destination, *identity)
        staged_quarantine = _deletion_quarantine_path(staging, *identity)
        if _recorded_opencode_link_path_is_live(link, final_quarantine):
            _unlink_recorded_destination(link)
        if _recorded_opencode_link_path_is_live(link, staged_quarantine):
            _unlink_recorded_destination(link, staging)
        final_owned = _recorded_opencode_link_is_live(link)
        staged_owned = _recorded_opencode_link_path_is_live(link, staging)
        if final_owned:
            if staged_owned:
                _remove_recorded_opencode_staging(link)
            continue
        if _lexists(link.destination):
            # A foreign or same-target replacement is never overwritten or
            # adopted.  The exact installer staging object no longer has a
            # publication path, so clean only that recorded inode.
            if staged_owned:
                _remove_recorded_opencode_staging(link)
            continue
        if not staged_owned:
            continue
        bound = _bound_config_parent(link.destination, create=False)
        if bound is None:
            raise InstallError(
                f"OpenCode staged link parent is missing: {link.destination}"
            )
        binding, parent_fd = bound
        _verify_config_binding(binding)
        _renameat_noreplace(
            parent_fd,
            staging.name,
            parent_fd,
            link.destination.name,
        )
        os.fsync(parent_fd)
        if not _recorded_opencode_link_is_live(link):
            raise InstallError(
                f"recovered OpenCode link identity changed: {link.destination}"
            )


def _committed_opencode_link(
    link: ProfileLink, previous: ProfileLink | None, *, created: bool
) -> ProfileLink:
    """Capture new ownership or retain a prior exact symlink identity."""

    if created or previous is None:
        return _capture_opencode_link_identity(link)
    if previous.destination_dev is None or previous.destination_ino is None:
        # A planned-but-uncommitted link found after a crash has no frozen
        # inode authority.  Preserve the pathname without adopting it.
        return link
    if (
        _same_recorded_link(link.destination, link.source)
        and not _recorded_opencode_link_is_live(previous)
    ):
        # A same-target replacement is compatible with an idempotent
        # reinstall, but it remains foreign.  Keep the original authority so
        # a later teardown cannot claim the replacement inode.
        return replace(
            link,
            destination_dev=previous.destination_dev,
            destination_ino=previous.destination_ino,
        )
    return _capture_opencode_link_identity(link)


def _unlink_recorded_destination(
    link: ProfileLink, path: Path | None = None
) -> bool:
    """Delete only an independently matched receipt symlink or quarantine."""

    if link.destination_dev is None or link.destination_ino is None:
        return False
    destination = link.destination if path is None else path
    identity = (link.destination_dev, link.destination_ino)
    quarantine = _deletion_quarantine_path(destination, *identity)
    original_owned = _recorded_opencode_link_path_is_live(link, destination)
    quarantine_owned = _recorded_opencode_link_path_is_live(link, quarantine)
    bound = _bound_config_parent(destination, create=False)
    if bound is None:
        return False
    binding, parent_fd = bound
    if not original_owned and not quarantine_owned:
        try:
            os.fsync(parent_fd)
            _verify_config_binding(binding)
        except OSError as error:
            raise InstallError(
                f"cannot durably confirm exact OpenCode link absence: "
                f"{destination}: {error}"
            ) from error
        return False
    role = "final-link" if path is None else "staged-link"
    removed = False
    if quarantine_owned:
        removed = _retire_owned_object(
            quarantine,
            identity,
            role,
            directory=False,
            source_kind="quarantine",
        )
    if original_owned:
        removed = (
            _retire_owned_object(destination, identity, role, directory=False)
            or removed
        )
    return removed


def _unlink_destination(destination: Path) -> bool:
    """Durably remove one exact bound leaf through its recoverable quarantine."""

    bound = _bound_config_parent(destination, create=False)
    if bound is None:
        destination.unlink()
        return True
    binding, parent_fd = bound
    key = _lexical_absolute(destination)
    expected = binding.validated_leaves.get(key)
    if expected is None:
        metadata = os.stat(
            destination.name, dir_fd=parent_fd, follow_symlinks=False
        )
        expected = (metadata.st_dev, metadata.st_ino)
    quarantine = _deletion_quarantine_path(destination, *expected).name

    def metadata(name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    try:
        if _retirement_record_exists(
            parent_fd, quarantine, expected, directory=False
        ):
            removed = _unlink_exact_leaf_via_exchange(
                parent_fd, quarantine, expected, "OpenCode link quarantine"
            )
            os.fsync(parent_fd)
            _verify_config_binding(binding)
            binding.validated_leaves.pop(key, None)
            return removed
        if _retirement_record_exists(
            parent_fd, destination.name, expected, directory=False
        ):
            removed = _unlink_exact_leaf_via_exchange(
                parent_fd,
                destination.name,
                expected,
                "OpenCode config destination",
            )
            os.fsync(parent_fd)
            _verify_config_binding(binding)
            binding.validated_leaves.pop(key, None)
            return removed
        quarantined = metadata(quarantine)
        original = metadata(destination.name)
        quarantine_is_exact = quarantined is not None and (
            quarantined.st_dev,
            quarantined.st_ino,
        ) == expected
        original_is_exact = original is not None and (
            original.st_dev,
            original.st_ino,
        ) == expected
        removed = False
        if quarantine_is_exact:
            removed = _unlink_exact_leaf_via_exchange(
                parent_fd, quarantine, expected, "OpenCode link quarantine"
            )
            if original_is_exact:
                removed = (
                    _unlink_exact_leaf_via_exchange(
                        parent_fd,
                        destination.name,
                        expected,
                        "OpenCode config destination",
                    )
                    or removed
                )
        elif quarantined is not None:
            if original_is_exact:
                # The collision is foreign and survives.  The independently
                # matched original is retired through its own reversible
                # exchange instead of reusing the occupied quarantine name.
                removed = _unlink_exact_leaf_via_exchange(
                    parent_fd,
                    destination.name,
                    expected,
                    "OpenCode config destination",
                )
        elif original_is_exact:
            _renameat_noreplace(
                parent_fd, destination.name, parent_fd, quarantine
            )
            moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            if (moved.st_dev, moved.st_ino) != expected:
                try:
                    _renameat_noreplace(
                        parent_fd, quarantine, parent_fd, destination.name
                    )
                    os.fsync(parent_fd)
                except (OSError, InstallError):
                    pass
                raise InstallError(
                    f"OpenCode config destination identity changed: {destination}"
                )
            removed = _unlink_exact_leaf_via_exchange(
                parent_fd, quarantine, expected, "OpenCode link quarantine"
            )
        # Even an absence result must make a prior successful unlink durable
        # before the receipt is allowed to retire this identity.
        os.fsync(parent_fd)
        _verify_config_binding(binding)
    except OSError as error:
        raise InstallError(
            f"cannot conditionally remove OpenCode link: {destination}: {error}"
        ) from error
    binding.validated_leaves.pop(key, None)
    return removed


def _validate_opencode_destination(destination: Path, config_dir: Path) -> None:
    """Ensure a destination's existing ancestors cannot redirect cleanup."""

    canonical_config = _canonical_opencode_config(config_dir)
    candidate = _lexical_absolute(destination)
    try:
        relative = candidate.relative_to(canonical_config)
    except ValueError as error:
        raise InstallError(f"opencode destination is outside the config directory: {candidate}") from error
    current = canonical_config
    for component in relative.parts[:-1]:
        current /= component
        if current.is_symlink():
            raise InstallError(f"opencode destination parent must not be a symlink: {current}")
        if _lexists(current) and not current.is_dir():
            raise InstallError(f"opencode destination parent is not a directory: {current}")


def _path_is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _fixed_opencode_artifact(state_home: Path, candidate: Path | None = None) -> Path:
    """Return the sole artifact path permitted for this state home."""

    expected = _lexical_absolute(_opencode_artifact_path(state_home))
    _assert_no_symlink_components(expected, "opencode artifact")
    if candidate is not None:
        observed = _lexical_absolute(Path(candidate).expanduser())
        if observed != expected:
            raise InstallError(
                f"receipt artifact is outside the fixed OpenCode state: {observed}"
            )
    if _lexists(expected):
        metadata = _state_lstat(expected)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            binding = _state_binding(expected)
            placeholder_is_pending = False
            if binding is not None and stat.S_ISREG(metadata.st_mode):
                receipt_path = binding.directory / OPENCODE_RECEIPT_FILENAME
                try:
                    value = json.loads(_read_state_text(receipt_path))
                    lineage = value.get("lineage") if isinstance(value, dict) else None
                    pending_value = (
                        value.get("pending_retirement")
                        if isinstance(value, dict)
                        else None
                    )
                    pending = (
                        _pending_retirement_from_payload(
                            pending_value, lineage, require_secret=True
                        )
                        if isinstance(lineage, str) and pending_value is not None
                        else None
                    )
                except (OSError, json.JSONDecodeError, InstallError):
                    pending = None
                placeholder_is_pending = (
                    pending is not None
                    and pending.role == "artifact"
                    and pending.kind == "directory"
                    and pending.source == expected
                    and pending.placeholder_dev is not None
                    and pending.placeholder_ino is not None
                    and (metadata.st_dev, metadata.st_ino)
                    == (pending.placeholder_dev, pending.placeholder_ino)
                )
            if not placeholder_is_pending:
                raise InstallError(
                    f"opencode artifact is not a regular directory: {expected}"
                )
    return expected


def _remove_opencode_artifact(path: Path) -> None:
    if not _lexists(path):
        return
    _assert_no_symlink_components(path, "opencode artifact")
    metadata = _state_lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise InstallError(f"opencode artifact is not a regular directory: {path}")
    binding = _state_binding(path)
    if binding is not None:
        _remove_opencode_artifact_exact(path, metadata.st_dev, metadata.st_ino)
        return
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise InstallError(f"cannot remove opencode artifact: {path}: {error}") from error


def _remove_opencode_artifact_exact(path: Path, dev: int, ino: int) -> None:
    """Exchange an exact root first, then reclaim only its journaled private path."""

    binding = _state_binding(path)
    if binding is None:
        raise InstallError("OpenCode artifact retirement requires bound owned state")
    receipt_path = binding.directory / OPENCODE_RECEIPT_FILENAME
    role = "artifact"
    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if isinstance(payload, dict):
        for key in ("pending_swap", "pending_publish"):
            pending = payload.get(key)
            if not isinstance(pending, dict):
                continue
            if (pending.get("candidate_dev"), pending.get("candidate_ino")) == (
                dev,
                ino,
            ) and str(path) in {pending.get("candidate"), pending.get("artifact")}:
                role = "candidate"
            if key == "pending_swap" and (
                pending.get("backup_dev"), pending.get("backup_ino")
            ) == (dev, ino) and str(path) == pending.get("backup"):
                role = "backup"
    _retire_owned_object(path, (dev, ino), role, directory=True)


def _pending_identity(path: Path, dev: int, ino: int) -> bool:
    try:
        metadata = _state_lstat(path)
    except OSError:
        metadata = None
    if metadata is not None and (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_dev == dev
        and metadata.st_ino == ino
    ):
        return True
    return False


def _artifact_anchor_path(parent: Path, token: str) -> Path:
    return parent / f"{OPENCODE_ARTIFACT_ANCHOR_PREFIX}{token}"


def _artifact_anchor_matches(
    artifact: Path,
    anchor: Path | None,
    anchor_dev: int | None,
    anchor_ino: int | None,
) -> bool:
    """Prove ownership through an inode shared with the artifact itself."""

    if anchor is None or anchor_dev is None or anchor_ino is None:
        return False
    binding = _state_binding(artifact)
    if (
        binding is None
        or _state_binding(anchor) != binding
        or artifact.parent != anchor.parent
        or not anchor.name.startswith(OPENCODE_ARTIFACT_ANCHOR_PREFIX)
    ):
        return False
    artifact_fd = -1
    try:
        _verify_state_binding(binding)
        artifact_fd = os.open(
            artifact.name,
            _directory_open_flags(),
            dir_fd=binding.directory_fd,
        )
        source = os.stat(
            OPENCODE_ARTIFACT_ANCHOR_FILE,
            dir_fd=artifact_fd,
            follow_symlinks=False,
        )
        anchored = os.stat(
            anchor.name,
            dir_fd=binding.directory_fd,
            follow_symlinks=False,
        )
        expected = (anchor_dev, anchor_ino)
        return (
            stat.S_ISREG(source.st_mode)
            and stat.S_ISREG(anchored.st_mode)
            and (source.st_dev, source.st_ino) == expected
            and (anchored.st_dev, anchored.st_ino) == expected
        )
    except OSError:
        return False
    finally:
        if artifact_fd >= 0:
            os.close(artifact_fd)


def _artifact_anchor_identity_is_live(
    anchor: Path | None, anchor_dev: int | None, anchor_ino: int | None
) -> bool:
    """Match a frozen standalone anchor without requiring its former source."""

    if anchor is None or anchor_dev is None or anchor_ino is None:
        return False
    binding = _state_binding(anchor)
    if binding is None:
        return False
    try:
        _verify_state_binding(binding)
        metadata = os.stat(
            anchor.name,
            dir_fd=binding.directory_fd,
            follow_symlinks=False,
        )
    except (OSError, InstallError):
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == (anchor_dev, anchor_ino)
    )


def _create_artifact_anchor(
    artifact: Path, token: str
) -> tuple[Path, int, int]:
    """Create one exclusive descriptor-bound hard-link ownership anchor."""

    binding = _state_binding(artifact)
    anchor = _artifact_anchor_path(artifact.parent, token)
    if binding is None or _state_binding(anchor) != binding:
        raise InstallError("OpenCode artifact anchor requires bound owned state")
    artifact_fd = -1
    created = False
    complete = False
    identity: tuple[int, int] | None = None
    try:
        _verify_state_binding(binding)
        artifact_fd = os.open(
            artifact.name,
            _directory_open_flags(),
            dir_fd=binding.directory_fd,
        )
        source = os.stat(
            OPENCODE_ARTIFACT_ANCHOR_FILE,
            dir_fd=artifact_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(source.st_mode):
            raise InstallError(
                f"OpenCode anchor source is not a regular file: {artifact / OPENCODE_ARTIFACT_ANCHOR_FILE}"
            )
        identity = (source.st_dev, source.st_ino)
        try:
            existing = os.stat(
                anchor.name,
                dir_fd=binding.directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if (
                stat.S_ISREG(existing.st_mode)
                and (existing.st_dev, existing.st_ino) == identity
                and _artifact_anchor_matches(artifact, anchor, *identity)
            ):
                # A prior attempt can die after link(2) and before its caller
                # records success.  The deterministic pathname is adoptable
                # only when it is the exact package.json inode already frozen
                # in the pending receipt.
                complete = True
                return anchor, identity[0], identity[1]
            raise InstallError(
                "OpenCode artifact anchor pathname contains a foreign replacement"
            )
        os.link(
            OPENCODE_ARTIFACT_ANCHOR_FILE,
            anchor.name,
            src_dir_fd=artifact_fd,
            dst_dir_fd=binding.directory_fd,
            follow_symlinks=False,
        )
        created = True
        os.fsync(binding.directory_fd)
        if not _artifact_anchor_matches(artifact, anchor, *identity):
            raise InstallError("OpenCode artifact anchor lost its inode relationship")
        complete = True
        return anchor, identity[0], identity[1]
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(
            f"cannot create durable OpenCode artifact anchor before publication: {error}"
        ) from error
    finally:
        if artifact_fd >= 0:
            os.close(artifact_fd)
        if created and not complete and identity is not None:
            try:
                _retire_owned_object(
                    anchor,
                    identity,
                    "anchor",
                    directory=False,
                )
            except (OSError, InstallError):
                pass


def _remove_artifact_anchor_exact(
    artifact: Path,
    anchor: Path | None,
    anchor_dev: int | None,
    anchor_ino: int | None,
) -> None:
    """Remove only an anchor still hard-linked to the proven artifact file."""

    if not _artifact_anchor_matches(artifact, anchor, anchor_dev, anchor_ino):
        raise InstallError("OpenCode artifact anchor lost its exact inode relationship")
    assert anchor is not None and anchor_dev is not None and anchor_ino is not None
    _unlink_artifact_anchor_identity(anchor, anchor_dev, anchor_ino)


def _remove_candidate_artifact_exact(
    artifact: Path,
    artifact_dev: int,
    artifact_ino: int,
    anchor: Path | None,
    anchor_dev: int | None,
    anchor_ino: int | None,
) -> None:
    """Remove a rollback candidate only while its lifetime anchor proves it."""

    package = artifact / OPENCODE_ARTIFACT_ANCHOR_FILE
    relationship_is_live = _artifact_anchor_matches(
        artifact, anchor, anchor_dev, anchor_ino
    )
    # Once an exact removal attempt has begun, package.json may already be
    # gone.  The durable rollback phase plus the still-exact standalone anchor
    # retains authority to finish clearing the same frozen directory inode.
    partially_removed_with_live_anchor = (
        not _lexists(package)
        and _artifact_anchor_identity_is_live(anchor, anchor_dev, anchor_ino)
    )
    if not relationship_is_live and not partially_removed_with_live_anchor:
        raise InstallError(
            "OpenCode rollback candidate lost its exact permanent anchor relationship"
        )
    _remove_opencode_artifact_exact(artifact, artifact_dev, artifact_ino)


def _unlink_artifact_anchor_identity(anchor: Path, anchor_dev: int, anchor_ino: int) -> None:
    """Durably unlink or confirm absence of one receipt-recorded anchor."""

    binding = _state_binding(anchor)
    if binding is None:
        raise InstallError("OpenCode artifact anchor is outside bound owned state")
    expected = (anchor_dev, anchor_ino)
    quarantine = _deletion_quarantine_path(anchor, *expected)

    def metadata(name: str) -> os.stat_result | None:
        try:
            return os.stat(
                name,
                dir_fd=binding.directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None

    try:
        _verify_state_binding(binding)
        original = metadata(anchor.name)
        quarantined = metadata(quarantine.name)
        original_is_exact = original is not None and (
            original.st_dev,
            original.st_ino,
        ) == expected
        quarantine_is_exact = quarantined is not None and (
            quarantined.st_dev,
            quarantined.st_ino,
        ) == expected
        if quarantine_is_exact:
            _retire_owned_object(
                quarantine,
                expected,
                "anchor",
                directory=False,
                source_kind="quarantine",
            )
        if original_is_exact:
            _retire_owned_object(anchor, expected, "anchor", directory=False)
        if not original_is_exact and not quarantine_is_exact:
            os.fsync(binding.directory_fd)
            _verify_state_binding(binding)
    except OSError as error:
        raise InstallError(f"cannot remove exact OpenCode artifact anchor: {error}") from error


def _rename_noreplace(source: Path, target: Path) -> None:
    """Rename a recovered directory without overwriting a new occupant."""

    if source.parent != target.parent:
        raise InstallError("recovery paths must share one parent")
    parent_fd: int | None = None
    binding = _state_binding(source)
    try:
        if binding is not None:
            if _state_binding(target) != binding:
                raise InstallError("recovery paths do not share one state binding")
            _verify_state_binding(binding)
            parent_fd = os.dup(binding.directory_fd)
        else:
            parent_fd = os.open(
                source.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        _renameat_noreplace(parent_fd, source.name, parent_fd, target.name)
        if binding is not None:
            os.fsync(parent_fd)
            _verify_state_binding(binding)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise InstallError(f"recovery target was replaced: {target}") from error
        raise InstallError(f"exclusive recovery rename failed: {error}") from error
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _receipt_with_pending(receipt: _Receipt, pending: _PendingSwap | None) -> _Receipt:
    return _Receipt(
        repository_root=receipt.repository_root,
        links=receipt.links,
        marketplace_added=receipt.marketplace_added,
        plugin_installed=receipt.plugin_installed,
        artifact_root=receipt.artifact_root,
        artifact_dev=receipt.artifact_dev,
        artifact_ino=receipt.artifact_ino,
        artifact_digest=receipt.artifact_digest,
        lineage=receipt.lineage,
        artifact_anchor=receipt.artifact_anchor,
        artifact_anchor_dev=receipt.artifact_anchor_dev,
        artifact_anchor_ino=receipt.artifact_anchor_ino,
        teardown_phase=receipt.teardown_phase,
        pending_swap=pending,
        pending_publish=receipt.pending_publish,
        pending_migration=receipt.pending_migration,
        pending_retirement=receipt.pending_retirement,
    )


def _receipt_with_pending_publish(
    receipt: _Receipt, pending_publish: _PendingPublish | None
) -> _Receipt:
    return _Receipt(
        repository_root=receipt.repository_root,
        links=receipt.links,
        marketplace_added=receipt.marketplace_added,
        plugin_installed=receipt.plugin_installed,
        artifact_root=receipt.artifact_root,
        artifact_dev=receipt.artifact_dev,
        artifact_ino=receipt.artifact_ino,
        artifact_digest=receipt.artifact_digest,
        lineage=receipt.lineage,
        artifact_anchor=receipt.artifact_anchor,
        artifact_anchor_dev=receipt.artifact_anchor_dev,
        artifact_anchor_ino=receipt.artifact_anchor_ino,
        teardown_phase=receipt.teardown_phase,
        pending_swap=receipt.pending_swap,
        pending_publish=pending_publish,
        pending_migration=receipt.pending_migration,
        pending_retirement=receipt.pending_retirement,
    )


def _rewrite_receipt_pending(receipt_path: Path, pending: _PendingSwap | None) -> None:
    """Durably update only the owned receipt's pending-swap field."""

    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    if pending is None:
        payload.pop("pending_swap", None)
        payload.pop("pending_swap_auth", None)
        payload.pop("pending_swap_checksum", None)
    else:
        pending_payload = _pending_swap_payload(pending)
        payload["pending_swap"] = pending_payload
        payload["pending_swap_checksum"] = _pending_swap_checksum(
            pending.lineage, pending_payload
        )
    _write_receipt_raw(receipt_path, payload)


def _write_receipt_raw(receipt_path: Path, payload: Mapping[str, object]) -> None:
    """Atomically write and fsync an already-owned receipt payload."""

    if _write_state_payload(receipt_path, payload):
        return
    raise InstallError(
        "receipt writes require the descriptor-bound state protocol: "
        f"{receipt_path}"
    )


def _rewrite_receipt_retirement(
    receipt_path: Path, pending: _PendingRetirement | None
) -> None:
    """Durably transition only the one receipt-bound retirement entry."""

    if _state_binding(receipt_path) is None:
        raise InstallError("OpenCode retirement write requires the installer lock")
    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    lineage = payload.get("lineage")
    if not isinstance(lineage, str) or len(lineage) < 32:
        raise InstallError(f"receipt retirement lineage is malformed: {receipt_path}")
    current_value = payload.get("pending_retirement")
    current = (
        _pending_retirement_from_payload(
            current_value, lineage, require_secret=True
        )
        if current_value is not None
        else None
    )
    phase_order = {"prepared": 0, "exchanged": 1, "reclaimed": 2, "done": 3}
    if pending is None:
        if current is not None and current.phase != "done":
            raise InstallError("cannot clear an outstanding OpenCode retirement")
        payload.pop("pending_retirement", None)
    else:
        if pending.lineage != lineage:
            raise InstallError("OpenCode retirement lineage changed")
        if current is not None:
            if current.phase == pending.phase == "prepared":
                current_core = replace(
                    current,
                    placeholder_dev=pending.placeholder_dev,
                    placeholder_ino=pending.placeholder_ino,
                )
            else:
                current_core = replace(current, phase=pending.phase)
            if (
                current_core != pending
                or phase_order[pending.phase] < phase_order[current.phase]
            ):
                raise InstallError("OpenCode retirement transition is invalid")
        elif pending.phase != "prepared":
            raise InstallError("OpenCode retirement must begin in prepared")
        payload["pending_retirement"] = _pending_retirement_payload(pending)
    _write_receipt_raw(receipt_path, payload)


def _retirement_parent(
    pending: _PendingRetirement,
) -> tuple[int, Callable[[], None]]:
    state_binding = _state_binding(pending.source)
    if state_binding is not None and pending.source.parent == state_binding.directory:
        _verify_state_binding(state_binding)
        return os.dup(state_binding.directory_fd), lambda: _verify_state_binding(
            state_binding
        )
    bound = _bound_config_parent(pending.source, create=False)
    if bound is None:
        raise InstallError(
            f"OpenCode retirement parent is missing: {pending.source.parent}"
        )
    config_binding, parent_fd = bound
    _verify_config_binding(config_binding)
    return os.dup(parent_fd), lambda: _verify_config_binding(config_binding)


def _clear_private_retirement_directory(directory_fd: int) -> None:
    """Clear children only beneath an authenticated journaled private root."""

    for entry in list(os.scandir(directory_fd)):
        try:
            metadata = entry.stat(follow_symlinks=False)
        except FileNotFoundError as error:
            # This is not absence of the journaled top-level artifact.  Retain
            # receipt authority and let the locked retry rescan from scratch.
            raise InstallError(
                f"OpenCode private retirement child disappeared: {entry.name}"
            ) from error
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child_fd = -1
            try:
                child_fd = os.open(
                    entry.name, _directory_open_flags(), dir_fd=directory_fd
                )
                child = os.fstat(child_fd)
                if (child.st_dev, child.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise InstallError(
                        f"OpenCode private retirement child changed: {entry.name}"
                    )
                _clear_private_retirement_directory(child_fd)
            except FileNotFoundError as error:
                raise InstallError(
                    f"OpenCode private retirement child disappeared: {entry.name}"
                ) from error
            finally:
                if child_fd >= 0:
                    os.close(child_fd)
            try:
                os.rmdir(entry.name, dir_fd=directory_fd)
            except FileNotFoundError as error:
                raise InstallError(
                    f"OpenCode private retirement child disappeared: {entry.name}"
                ) from error
        else:
            try:
                os.unlink(entry.name, dir_fd=directory_fd)
            except FileNotFoundError as error:
                raise InstallError(
                    f"OpenCode private retirement child disappeared: {entry.name}"
                ) from error
    os.fsync(directory_fd)


def _resume_pending_retirement(
    receipt_path: Path, pending: _PendingRetirement
) -> bool:
    """Recover one journal entry through its closed private-only state machine."""

    if _state_binding(receipt_path) is None:
        raise InstallError("OpenCode retirement recovery requires the installer lock")
    try:
        receipt_payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(receipt_payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    lineage = receipt_payload.get("lineage")
    durable_value = receipt_payload.get("pending_retirement")
    if not isinstance(lineage, str) or durable_value is None:
        raise InstallError("OpenCode retirement recovery lacks a durable entry")
    durable = _pending_retirement_from_payload(
        durable_value, lineage, require_secret=True
    )
    if durable != pending:
        raise InstallError("OpenCode retirement recovery entry changed")
    parent_fd, verify_parent = _retirement_parent(pending)

    def metadata(name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def exact(value: os.stat_result | None) -> bool:
        return value is not None and (value.st_dev, value.st_ino) == (
            pending.expected_dev,
            pending.expected_ino,
        )

    def identity(value: os.stat_result | None) -> tuple[int, int] | None:
        return None if value is None else (value.st_dev, value.st_ino)

    def require_kind(value: os.stat_result, label: str) -> None:
        matches = (
            stat.S_ISDIR(value.st_mode)
            if pending.kind == "directory"
            else not stat.S_ISDIR(value.st_mode)
        )
        if not matches:
            raise InstallError(f"OpenCode retirement {label} changed kind")

    current = pending
    removed = False
    try:
        if current.phase == "prepared":
            source = metadata(current.source.name)
            private = metadata(current.private.name)
            placeholder = (
                None
                if current.placeholder_dev is None or current.placeholder_ino is None
                else (current.placeholder_dev, current.placeholder_ino)
            )
            if placeholder is None or (private is None and exact(source)):
                if private is not None:
                    raise InstallError(
                        f"OpenCode retirement private path is occupied: {current.private}"
                    )
                if not exact(source):
                    raise InstallError("OpenCode retirement public identity changed")
                descriptor, placeholder = _prepare_anonymous_generation(
                    parent_fd, b"", "OpenCode retirement placeholder"
                )
                try:
                    current = replace(
                        current,
                        placeholder_dev=placeholder[0],
                        placeholder_ino=placeholder[1],
                    )
                    # Persist the exact placeholder identity before linking it.
                    # If the process dies before the link, recovery can safely
                    # allocate a fresh placeholder while the exact source remains.
                    _rewrite_receipt_retirement(receipt_path, current)
                    if metadata(current.private.name) is not None:
                        raise InstallError(
                            f"OpenCode retirement private path is occupied: {current.private}"
                        )
                    if not exact(metadata(current.source.name)):
                        raise InstallError("OpenCode retirement public identity changed")
                    _link_open_descriptor(descriptor, parent_fd, current.private.name)
                    os.fsync(parent_fd)
                finally:
                    os.close(descriptor)
                source = metadata(current.source.name)
                private = metadata(current.private.name)

            assert placeholder is not None
            source_identity = identity(source)
            private_identity = identity(private)
            if source_identity == placeholder:
                if private_identity == (current.expected_dev, current.expected_ino):
                    pass
                elif private is not None:
                    # A replacement immediately before exchange was displaced
                    # to private storage.  The exact public placeholder proves
                    # that reversing once restores it without overwrite.
                    _renameat_exchange(
                        parent_fd,
                        current.source.name,
                        parent_fd,
                        current.private.name,
                    )
                    os.fsync(parent_fd)
                    raise InstallError("OpenCode retirement restored public replacement")
                else:
                    raise InstallError("OpenCode retirement private object disappeared")
            elif exact(source) and private_identity == placeholder:
                assert source is not None
                require_kind(source, "source")
                _renameat_exchange(
                    parent_fd,
                    current.source.name,
                    parent_fd,
                    current.private.name,
                )
                os.fsync(parent_fd)
                source = metadata(current.source.name)
                private = metadata(current.private.name)
                if identity(source) != placeholder:
                    raise InstallError("OpenCode retirement placeholder publication failed")
                if not exact(private):
                    if private is not None:
                        _renameat_exchange(
                            parent_fd,
                            current.source.name,
                            parent_fd,
                            current.private.name,
                        )
                        os.fsync(parent_fd)
                    raise InstallError("OpenCode retirement restored public replacement")
            else:
                # A newer public occupant, a compensated exchange, or an
                # occupied private name is never converted into authority.
                raise InstallError("OpenCode retirement public identity changed")
            verify_parent()
            current = replace(current, phase="exchanged")
            _rewrite_receipt_retirement(receipt_path, current)

        if current.phase == "exchanged":
            source = metadata(current.source.name)
            private = metadata(current.private.name)
            if current.placeholder_dev is None or current.placeholder_ino is None:
                raise InstallError("OpenCode retirement placeholder authority is missing")
            placeholder = (current.placeholder_dev, current.placeholder_ino)
            placeholder_name = (
                f"{current.private.name}.{placeholder[0]:x}-{placeholder[1]:x}.placeholder"
            )
            staged_placeholder = metadata(placeholder_name)
            if source is not None:
                if identity(source) != placeholder:
                    raise InstallError("OpenCode retirement public pathname was replaced")
                if staged_placeholder is not None:
                    raise InstallError("OpenCode retirement placeholder path is occupied")
                _renameat_noreplace(
                    parent_fd,
                    current.source.name,
                    parent_fd,
                    placeholder_name,
                )
                os.fsync(parent_fd)
                staged_placeholder = metadata(placeholder_name)
            if staged_placeholder is not None:
                if identity(staged_placeholder) != placeholder:
                    raise InstallError("OpenCode retirement private placeholder changed")
                os.unlink(placeholder_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            if metadata(current.source.name) is not None:
                raise InstallError("OpenCode retirement public pathname reappeared")
            if private is not None and not exact(private):
                raise InstallError("OpenCode retirement private identity changed")
            if private is not None:
                require_kind(private, "private path")
                if current.kind == "directory":
                    directory_fd = os.open(
                        current.private.name,
                        _directory_open_flags(),
                        dir_fd=parent_fd,
                    )
                    try:
                        opened = os.fstat(directory_fd)
                        if (opened.st_dev, opened.st_ino) != (
                            current.expected_dev,
                            current.expected_ino,
                        ):
                            raise InstallError(
                                "OpenCode retirement directory identity changed"
                            )
                        _clear_private_retirement_directory(directory_fd)
                    finally:
                        os.close(directory_fd)
                    again = metadata(current.private.name)
                    if again is None or not exact(again):
                        raise InstallError(
                            "OpenCode retirement directory changed before reclaim"
                        )
                    os.rmdir(current.private.name, dir_fd=parent_fd)
                else:
                    again = metadata(current.private.name)
                    if again is None or not exact(again):
                        raise InstallError(
                            "OpenCode retirement leaf changed before reclaim"
                        )
                    os.unlink(current.private.name, dir_fd=parent_fd)
                removed = True
            os.fsync(parent_fd)
            verify_parent()
            current = replace(current, phase="reclaimed")
            _rewrite_receipt_retirement(receipt_path, current)

        if current.phase == "reclaimed":
            private = metadata(current.private.name)
            if private is not None:
                raise InstallError("OpenCode retirement private path reappeared")
            if exact(metadata(current.source.name)):
                raise InstallError("OpenCode retirement source reappeared")
            os.fsync(parent_fd)
            verify_parent()
            current = replace(current, phase="done")
            _rewrite_receipt_retirement(receipt_path, current)

        if current.phase != "done":
            raise InstallError("OpenCode retirement phase is not recoverable")
        if metadata(current.private.name) is not None:
            raise InstallError("OpenCode completed retirement private path reappeared")
        if exact(metadata(current.source.name)):
            raise InstallError("OpenCode completed retirement source reappeared")
        os.fsync(parent_fd)
        verify_parent()
        _rewrite_receipt_retirement(receipt_path, None)
        return removed
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(f"cannot resume OpenCode retirement: {error}") from error
    finally:
        os.close(parent_fd)


def _retire_owned_object(
    source: Path,
    expected: tuple[int, int],
    role: str,
    *,
    directory: bool,
    source_kind: str = "public",
) -> bool:
    """Prepare and complete one exact receipt-authorized retirement."""

    if role not in OPENCODE_RETIREMENT_ROLES or source_kind not in {
        "public",
        "quarantine",
    }:
        raise InstallError("OpenCode retirement role or source kind is invalid")
    if expected[0] <= 0 or expected[1] <= 0:
        raise InstallError("OpenCode retirement identity is invalid")
    state_bindings = tuple(_STATE_BINDINGS.values())
    config_bindings = tuple(_CONFIG_BINDINGS.values())
    if len(state_bindings) != 1 or len(config_bindings) != 1:
        raise InstallError("OpenCode retirement requires one active installer lock")
    state_binding = state_bindings[0]
    receipt_path = state_binding.directory / OPENCODE_RECEIPT_FILENAME
    if not _lexists(receipt_path):
        raise InstallError("OpenCode retirement lacks its durable receipt")
    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    lineage = payload.get("lineage")
    if not isinstance(lineage, str) or len(lineage) < 32:
        raise InstallError("OpenCode retirement lacks a receipt lineage")
    repository_value = payload.get("repository_root")
    if not isinstance(repository_value, str):
        raise InstallError("OpenCode retirement receipt lacks its repository")
    try:
        repository_root = Path(repository_value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError("OpenCode retirement repository cannot be resolved") from error
    receipt = _read_opencode_receipt(
        receipt_path,
        repository_root,
        config_bindings[0].directory,
        state_binding.directory.parent,
    )
    if receipt is None:
        raise InstallError("OpenCode retirement receipt disappeared")
    existing_value = payload.get("pending_retirement")
    if existing_value is not None:
        if receipt.pending_retirement is None:
            raise InstallError("OpenCode retirement receipt entry disappeared")
        _resume_pending_retirement(receipt_path, receipt.pending_retirement)
        receipt = _read_opencode_receipt(
            receipt_path,
            repository_root,
            config_bindings[0].directory,
            state_binding.directory.parent,
        )
        if receipt is None or receipt.pending_retirement is not None:
            raise InstallError("OpenCode retirement recovery did not close")
    kind = "directory" if directory else "leaf"
    secret = _retirement_secret()
    token = _retirement_token(
        lineage,
        role,
        kind,
        source,
        *expected,
        secret=secret,
    )
    pending = _PendingRetirement(
        role=role,
        kind=kind,
        source=_lexical_absolute(source),
        private=_retirement_private_path(_lexical_absolute(source), token, kind),
        expected_dev=expected[0],
        expected_ino=expected[1],
        lineage=lineage,
        token=token,
        phase="prepared",
        source_kind=source_kind,
        secret=secret,
    )
    _validate_pending_retirement_authority(
        pending,
        links=receipt.links,
        artifact_root=receipt.artifact_root,
        artifact_identity=(receipt.artifact_dev, receipt.artifact_ino),
        artifact_anchor=(
            receipt.artifact_anchor,
            receipt.artifact_anchor_dev,
            receipt.artifact_anchor_ino,
        ),
        pending_swap=receipt.pending_swap,
        pending_publish=receipt.pending_publish,
    )
    parent_fd, verify_parent = _retirement_parent(pending)
    try:
        try:
            observed = os.stat(
                pending.source.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            observed = None
        try:
            occupied = os.stat(
                pending.private.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            occupied = None
        if occupied is not None:
            raise InstallError(
                f"OpenCode retirement private path is occupied: {pending.private}"
            )
        if observed is None or (observed.st_dev, observed.st_ino) != expected:
            os.fsync(parent_fd)
            verify_parent()
            return False
    finally:
        os.close(parent_fd)
    _rewrite_receipt_retirement(receipt_path, pending)
    return _resume_pending_retirement(receipt_path, pending)


def _recover_pending_retirement(receipt_path: Path, receipt: _Receipt) -> None:
    pending = receipt.pending_retirement
    if pending is not None:
        _resume_pending_retirement(receipt_path, pending)


def _rollback_pending_publish(
    receipt_path: Path, receipt: _Receipt
) -> _Receipt | None:
    """Finish an initial-publication rollback from frozen receipt authority."""

    pending = receipt.pending_publish
    if pending is None:
        return receipt
    if pending.phase not in {
        "rollback-prepared",
        "rollback-artifact-removed",
        "rollback-anchor-removed",
    }:
        pending = replace(pending, phase="rollback-prepared")
        receipt = _receipt_with_pending_publish(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase == "rollback-prepared":
        # A planned roster is not ownership evidence.  Initial-publication
        # rollback may remove only a staged or final inode whose exact identity
        # was durably recorded before publication; target text alone never
        # authorizes deletion of a same-target foreign symlink.
        link_failures = _rollback_links(
            tuple(
                link
                for link in receipt.links
                if link.staged_destination is not None
                or (
                    link.destination_dev is not None
                    and link.destination_ino is not None
                )
            )
        )
        if link_failures:
            raise InstallError(
                "OpenCode publication link rollback failed: "
                + "; ".join(link_failures)
            )
        target = None
        if _pending_identity(
            pending.candidate, pending.candidate_dev, pending.candidate_ino
        ):
            target = pending.candidate
        elif _pending_identity(
            pending.artifact, pending.candidate_dev, pending.candidate_ino
        ):
            target = pending.artifact
        if target is not None:
            _remove_candidate_artifact_exact(
                target,
                pending.candidate_dev,
                pending.candidate_ino,
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
        pending = replace(pending, phase="rollback-artifact-removed")
        receipt = _receipt_with_pending_publish(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase == "rollback-artifact-removed":
        if (
            pending.candidate_anchor is not None
            and pending.candidate_anchor_dev is not None
            and pending.candidate_anchor_ino is not None
        ):
            _unlink_artifact_anchor_identity(
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
        pending = replace(pending, phase="rollback-anchor-removed")
        receipt = _receipt_with_pending_publish(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase != "rollback-anchor-removed":
        raise InstallError("OpenCode publication rollback phase is not recoverable")
    _unlink_state_path(receipt_path)
    return None


def _rollback_pending_swap_candidate(
    receipt_path: Path, receipt: _Receipt
) -> _Receipt:
    """Discard only the frozen upgrade candidate, then restore the old live inode."""

    pending = receipt.pending_swap
    if pending is None:
        return receipt
    if pending.phase not in {
        "rollback-prepared",
        "rollback-candidate-removed",
        "rollback-anchor-removed",
        "rollback-restored",
    }:
        pending = replace(pending, phase="rollback-prepared")
        receipt = _receipt_with_pending(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase == "rollback-prepared":
        candidate_path = None
        if _pending_identity(
            pending.candidate, pending.candidate_dev, pending.candidate_ino
        ):
            candidate_path = pending.candidate
        elif _pending_identity(
            pending.artifact, pending.candidate_dev, pending.candidate_ino
        ):
            candidate_path = pending.artifact
        if candidate_path is not None:
            _remove_candidate_artifact_exact(
                candidate_path,
                pending.candidate_dev,
                pending.candidate_ino,
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
        pending = replace(pending, phase="rollback-candidate-removed")
        receipt = _receipt_with_pending(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase == "rollback-candidate-removed":
        if (
            pending.candidate_anchor is not None
            and pending.candidate_anchor_dev is not None
            and pending.candidate_anchor_ino is not None
        ):
            _unlink_artifact_anchor_identity(
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
        pending = replace(pending, phase="rollback-anchor-removed")
        receipt = _receipt_with_pending(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase == "rollback-anchor-removed":
        artifact_is_old = _pending_identity(
            pending.artifact, pending.live_dev, pending.live_ino
        )
        backup_is_old = _pending_identity(
            pending.backup, pending.backup_dev, pending.backup_ino
        )
        if not artifact_is_old:
            if _lexists(pending.artifact):
                raise InstallError(
                    "cannot restore OpenCode upgrade over a foreign artifact"
                )
            if not backup_is_old:
                raise InstallError("OpenCode upgrade rollback lost its frozen backup")
            _rename_noreplace(pending.backup, pending.artifact)
        pending = replace(pending, phase="rollback-restored")
        receipt = _receipt_with_pending(receipt, pending)
        _write_receipt(receipt_path, receipt)
    if pending.phase != "rollback-restored":
        raise InstallError("OpenCode upgrade rollback phase is not recoverable")
    receipt = _receipt_with_pending(receipt, None)
    _write_receipt(receipt_path, receipt)
    return receipt


def _recover_pending_swap(
    repo_root: Path, state_home: Path, receipt: _Receipt | None
) -> _Receipt | None:
    """Recover a pending swap only after independent filesystem validation."""

    receipt_path = _opencode_receipt_path(state_home)
    if receipt is None or receipt.pending_swap is None:
        return receipt
    pending = receipt.pending_swap
    if pending.phase.startswith("rollback-"):
        return _rollback_pending_swap_candidate(receipt_path, receipt)
    permitted_receipt_identities = {(pending.live_dev, pending.live_ino)}
    if pending.phase in {"published", "old-artifact-removed", "old-anchor-removed"}:
        permitted_receipt_identities.add((pending.candidate_dev, pending.candidate_ino))
    if (
        receipt.artifact_dev is None
        or receipt.artifact_ino is None
        or (receipt.artifact_dev, receipt.artifact_ino)
        not in permitted_receipt_identities
    ):
        raise InstallError(
            "pending OpenCode swap lacks a committed live artifact identity"
        )
    expected_artifact = _lexical_absolute(receipt_path.parent / OPENCODE_ARTIFACT_DIRECTORY)
    if (
        pending.artifact != expected_artifact
        or pending.candidate.parent != expected_artifact.parent
        or pending.backup.parent != expected_artifact.parent
        or not pending.candidate.name.startswith(f".{OPENCODE_ARTIFACT_DIRECTORY}.next-")
        or not pending.backup.name.startswith(f".{OPENCODE_ARTIFACT_DIRECTORY}.old-")
        or pending.candidate == pending.backup
        or pending.phase not in {
            "prepared",
            "anchor-recorded",
            "backup-created",
            "published",
            "old-artifact-removed",
            "old-anchor-removed",
        }
        or min(
            pending.candidate_dev, pending.candidate_ino, pending.backup_dev,
            pending.backup_ino, pending.live_dev, pending.live_ino,
        ) <= 0
    ):
        raise InstallError(f"receipt pending swap path or identity is invalid: {receipt_path}")
    candidate_exists = _pending_identity(
        pending.candidate, pending.candidate_dev, pending.candidate_ino
    )
    backup_exists = _pending_identity(pending.backup, pending.backup_dev, pending.backup_ino)
    artifact_exists = _lexists(pending.artifact)
    artifact_is_candidate = _pending_identity(
        pending.artifact, pending.candidate_dev, pending.candidate_ino
    )
    artifact_is_live = _pending_identity(
        pending.artifact, pending.live_dev, pending.live_ino
    )
    if (
        candidate_exists
        and pending.candidate_anchor is not None
        and pending.candidate_anchor_dev is not None
        and pending.candidate_anchor_ino is not None
        and not _artifact_anchor_matches(
            pending.candidate,
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        )
    ):
        anchor_token = pending.candidate_anchor.name.removeprefix(
            OPENCODE_ARTIFACT_ANCHOR_PREFIX
        )
        adopted = _create_artifact_anchor(pending.candidate, anchor_token)
        if adopted != (
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        ):
            raise InstallError("pending OpenCode candidate anchor identity changed")
    candidate_proven = candidate_exists and _artifact_anchor_matches(
        pending.candidate,
        pending.candidate_anchor,
        pending.candidate_anchor_dev,
        pending.candidate_anchor_ino,
    ) and (
        _artifact_matches_evidence(pending.candidate, pending.candidate_digest)
        if pending.candidate_digest is not None
        else _artifact_matches_sources(repo_root, pending.candidate)
    )
    backup_proven = backup_exists and (
        _artifact_anchor_identity_is_live(
            pending.old_anchor,
            pending.old_anchor_dev,
            pending.old_anchor_ino,
        )
        if pending.phase
        in {"published", "old-artifact-removed", "old-anchor-removed"}
        else _artifact_anchor_matches(
            pending.backup,
            pending.old_anchor,
            pending.old_anchor_dev,
            pending.old_anchor_ino,
        )
    )
    artifact_candidate_owned = artifact_is_candidate and _artifact_anchor_matches(
        pending.artifact,
        pending.candidate_anchor,
        pending.candidate_anchor_dev,
        pending.candidate_anchor_ino,
    )
    artifact_candidate_proven = artifact_candidate_owned and (
        _artifact_matches_evidence(pending.artifact, pending.candidate_digest)
        if pending.candidate_digest is not None
        else _artifact_matches_sources(repo_root, pending.artifact)
    )
    artifact_live_proven = artifact_is_live and _artifact_anchor_matches(
        pending.artifact,
        pending.old_anchor,
        pending.old_anchor_dev,
        pending.old_anchor_ino,
    )
    if artifact_exists and not artifact_is_candidate and not artifact_is_live:
        raise InstallError("OpenCode artifact was replaced by an unproven directory; preserving it")
    if pending.phase in {"prepared", "anchor-recorded"}:
        if artifact_is_live and not backup_exists:
            # Crash before the first rename: the candidate is private and the
            # old live artifact remains authoritative.  Remove only exact
            # candidate identity and clear the pending record.
            if candidate_exists and not candidate_proven:
                raise InstallError(
                    "pending OpenCode candidate lacks independent ownership evidence"
                )
            if candidate_proven:
                _remove_candidate_artifact_exact(
                    pending.candidate,
                    pending.candidate_dev,
                    pending.candidate_ino,
                    pending.candidate_anchor,
                    pending.candidate_anchor_dev,
                    pending.candidate_anchor_ino,
                )
                assert pending.candidate_anchor is not None
                assert pending.candidate_anchor_dev is not None
                assert pending.candidate_anchor_ino is not None
                _unlink_artifact_anchor_identity(
                    pending.candidate_anchor,
                    pending.candidate_anchor_dev,
                    pending.candidate_anchor_ino,
                )
            elif (
                pending.candidate_anchor is not None
                and pending.candidate_anchor_dev is not None
                and pending.candidate_anchor_ino is not None
            ):
                _unlink_artifact_anchor_identity(
                    pending.candidate_anchor,
                    pending.candidate_anchor_dev,
                    pending.candidate_anchor_ino,
                )
            receipt = _receipt_with_pending(receipt, None)
            _write_receipt(receipt_path, receipt)
            return receipt
        if artifact_is_live and backup_exists:
            # The first rename happened even though its status rewrite did not.
            pending = _PendingSwap(**{**pending.__dict__, "phase": "backup-created"})
        elif not artifact_exists and backup_exists:
            pending = _PendingSwap(**{**pending.__dict__, "phase": "backup-created"})
    if not artifact_exists and backup_exists:
        if not backup_proven:
            raise InstallError(
                "pending OpenCode backup lacks independent ownership evidence"
            )
        if candidate_exists:
            if not candidate_proven:
                raise InstallError(
                    "pending OpenCode candidate lacks independent ownership evidence"
                )
            # The durable status is still pre-publication.  Preserve the
            # previous install rather than adopting an uncommitted candidate.
            _remove_candidate_artifact_exact(
                pending.candidate,
                pending.candidate_dev,
                pending.candidate_ino,
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
            assert pending.candidate_anchor is not None
            assert pending.candidate_anchor_dev is not None
            assert pending.candidate_anchor_ino is not None
            _unlink_artifact_anchor_identity(
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
            _rename_noreplace(pending.backup, pending.artifact)
            artifact_is_live = True
            backup_exists = False
            receipt = _receipt_with_pending(receipt, None)
            _write_receipt(receipt_path, receipt)
        else:
            _rename_noreplace(pending.backup, pending.artifact)
            artifact_is_live = True
            backup_exists = False
            receipt = _receipt_with_pending(receipt, None)
            _write_receipt(receipt_path, receipt)
        return receipt
    elif (
        artifact_is_candidate
        and backup_exists
        and pending.phase in {"prepared", "anchor-recorded", "backup-created"}
    ):
        if not artifact_candidate_proven or not backup_proven:
            raise InstallError(
                "pending OpenCode swap lacks independent ownership evidence"
            )
        # Candidate publication is not committed until the published receipt
        # write is durable.  Persist rollback intent before retiring either
        # candidate or anchor, then reuse the closed rollback state machine.
        receipt = _receipt_with_pending(
            receipt, replace(pending, phase="rollback-prepared")
        )
        _write_receipt(receipt_path, receipt)
        return _rollback_pending_swap_candidate(receipt_path, receipt)
    elif artifact_is_candidate and backup_exists:
        if not artifact_candidate_proven or not backup_proven:
            raise InstallError(
                "pending OpenCode swap lacks independent ownership evidence"
            )
        # Candidate was published before receipt status rewrite.
        pass
    elif artifact_is_candidate and not backup_exists:
        if not artifact_candidate_proven:
            raise InstallError(
                "pending OpenCode artifact lacks independent ownership evidence"
            )
        receipt = replace(
            receipt,
            artifact_dev=pending.candidate_dev,
            artifact_ino=pending.candidate_ino,
            artifact_digest=pending.candidate_digest,
            artifact_anchor=pending.candidate_anchor,
            artifact_anchor_dev=pending.candidate_anchor_dev,
            artifact_anchor_ino=pending.candidate_anchor_ino,
            pending_swap=replace(pending, phase="old-artifact-removed"),
        )
        _write_receipt(receipt_path, receipt)
        return receipt
    elif artifact_is_live and not backup_exists:
        if not artifact_live_proven:
            raise InstallError(
                "pending OpenCode live artifact lacks independent ownership evidence"
            )
        receipt = _receipt_with_pending(receipt, None)
        _write_receipt(receipt_path, receipt)
        return receipt
    elif backup_exists and not artifact_is_candidate:
        raise InstallError("OpenCode swap has an unproven live occupant; preserving state")
    published = replace(pending, phase="published")
    needs_commit = (
        (receipt.artifact_dev, receipt.artifact_ino)
        != (pending.candidate_dev, pending.candidate_ino)
        or receipt.artifact_anchor != pending.candidate_anchor
        or pending.phase != "published"
    )
    receipt = replace(
        receipt,
        artifact_dev=pending.candidate_dev,
        artifact_ino=pending.candidate_ino,
        artifact_digest=pending.candidate_digest,
        artifact_anchor=pending.candidate_anchor,
        artifact_anchor_dev=pending.candidate_anchor_dev,
        artifact_anchor_ino=pending.candidate_anchor_ino,
        pending_swap=published,
    )
    if needs_commit:
        _write_receipt(receipt_path, receipt)
    return receipt


def _garbage_collect_opencode_backups(
    state_home: Path,
    receipt: _Receipt | None = None,
    repo_root: Path | None = None,
) -> _Receipt | None:
    """Best-effort cleanup of the one receipt-owned pending backup."""

    receipt_path = _opencode_receipt_path(state_home)
    if receipt is None or receipt.pending_swap is None:
        return receipt
    pending = receipt.pending_swap
    if pending.phase not in {
        "published",
        "old-artifact-removed",
        "old-anchor-removed",
    }:
        return receipt
    # Old-state destruction starts only after the receipt durably commits the
    # candidate directory and its permanent anchor as the live generation.
    if (
        (receipt.artifact_dev, receipt.artifact_ino)
        != (pending.candidate_dev, pending.candidate_ino)
        or (
            receipt.artifact_anchor,
            receipt.artifact_anchor_dev,
            receipt.artifact_anchor_ino,
        )
        != (
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        )
        or not _pending_identity(
            pending.artifact, pending.candidate_dev, pending.candidate_ino
        )
        or not _artifact_anchor_matches(
            pending.artifact,
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        )
    ):
        return receipt

    if pending.phase == "published":
        if _pending_identity(pending.backup, pending.backup_dev, pending.backup_ino):
            # ``published`` durably commits retirement authority.  The old
            # directory and old anchor are now independent frozen objects; a
            # prior partial tree deletion may already have severed their
            # package.json hard-link relationship.
            if not _artifact_anchor_identity_is_live(
                pending.old_anchor,
                pending.old_anchor_dev,
                pending.old_anchor_ino,
            ):
                return receipt
            try:
                _remove_opencode_artifact_exact(
                    pending.backup, pending.backup_dev, pending.backup_ino
                )
            except (InstallError, OSError, TypeError, ValueError):
                return receipt
        # Absence or a foreign replacement at the backup pathname is done;
        # neither grants authority to touch that replacement.
        pending = replace(pending, phase="old-artifact-removed")
        receipt = _receipt_with_pending(receipt, pending)
        _write_receipt(receipt_path, receipt)

    if pending.phase == "old-artifact-removed":
        if (
            pending.old_anchor is None
            or pending.old_anchor_dev is None
            or pending.old_anchor_ino is None
        ):
            return receipt
        try:
            _unlink_artifact_anchor_identity(
                pending.old_anchor,
                pending.old_anchor_dev,
                pending.old_anchor_ino,
            )
        except (InstallError, OSError, TypeError, ValueError):
            return receipt
        pending = replace(pending, phase="old-anchor-removed")
        receipt = _receipt_with_pending(receipt, pending)
        _write_receipt(receipt_path, receipt)

    if pending.phase == "old-anchor-removed":
        receipt = _receipt_with_pending(receipt, None)
        _write_receipt(receipt_path, receipt)
    return receipt


def _remove_new_opencode_state(
    receipt_directory: Path,
    existed_before: bool,
    state_home: Path,
    state_home_existed_before: bool,
) -> None:
    """Intentionally retain newly-created empty state directories on failure.

    Without a durable receipt they have no retirement authority.  Empty state
    directories are harmless and are preferable to inventing a second cleanup
    protocol outside the receipt journal.
    """


def _install_source_inventory(root: Path) -> list[tuple[str, Path]]:
    """Independent source walk for install-time artifact verification."""

    package_root = root / "packages" / "expskill"
    result: list[tuple[str, Path]] = []

    def walk(directory: Path) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            metadata = os.lstat(child)
            if stat.S_ISLNK(metadata.st_mode):
                raise OSError(f"source entry is a symlink: {child}")
            if stat.S_ISDIR(metadata.st_mode):
                walk(child)
            elif stat.S_ISREG(metadata.st_mode):
                if "__pycache__" not in child.parts and child.suffix not in {".pyc", ".pyo"}:
                    result.append((child.relative_to(root).as_posix(), child))
            else:
                raise OSError(f"source entry is not regular: {child}")

    for tree in COPY_TREES:
        walk(package_root / tree)
    for relative in COPY_FILES:
        path = package_root / relative
        result.append((path.relative_to(root).as_posix(), path))
    walk(package_root / COPY_LICENSES)
    platform_root = package_root / "opencode"
    for name in PLATFORM_FILES:
        path = platform_root / name
        result.append((path.relative_to(root).as_posix(), path))
    walk(platform_root / PLATFORM_PLUGIN_DIRECTORY)
    walk(package_root / "assets" / "agents")
    for relative in (
        Path("scripts/artifact_contract.py"),
        Path("scripts/build_opencode_package.py"),
        Path("scripts/render_opencode.py"),
    ):
        path = root / relative
        result.append((relative.as_posix(), path))
    return sorted(result, key=lambda item: item[0])


def _artifact_matches_sources(repo_root: Path, artifact: Path) -> bool:
    """Check exact artifact inventory and bytes against the current sources."""

    expected: dict[str, bytes] = {}
    try:
        _reject_symlink_components(
            repo_root / "packages" / "expskill", "canonical package"
        )
        package_root = repo_root / "packages" / "expskill"
        platform_root = package_root / "opencode"
        for name in ("agents.json", "package.json", "README.md", "LICENSE", "index.js"):
            expected[name] = (platform_root / name).read_bytes()
        for name in ("execution-policy.js", "unslop.js"):
            expected[f"plugins/{name}"] = (platform_root / "plugins" / name).read_bytes()
        for relative, path in _install_source_inventory(repo_root):
            source_relative = Path(relative)
            package_marker = Path("packages") / "expskill"
            if source_relative.parts[:2] == package_marker.parts:
                within = Path(*source_relative.parts[2:])
                if within.parts and within.parts[0] in {"skills", "scripts"}:
                    expected[within.as_posix()] = path.read_bytes()
                elif within == Path("assets/execution-policy.json"):
                    expected[within.as_posix()] = path.read_bytes()
                elif within.parts[:2] == ("third-party", "licenses"):
                    expected[within.as_posix()] = path.read_bytes()
                elif within.parts[:2] == ("opencode", "plugins"):
                    expected[Path(*within.parts[1:]).as_posix()] = path.read_bytes()
            elif source_relative.parts[:1] == ("scripts",):
                # Builder and renderer scripts are provenance inputs only.
                continue
        rendered = _render_opencode_all(repo_root)
        expected.update({relative: contents.encode("utf-8") for relative, contents in rendered.items()})
        provenance_payload = json.loads((artifact / "provenance.json").read_text(encoding="utf-8"))
        inputs = provenance_payload.get("inputs")
        expected_inputs = [
            {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for relative, path in _install_source_inventory(repo_root)
        ]
        if (
            provenance_payload.get("schema_version") != "opencode-provenance.v1"
            or inputs != expected_inputs
        ):
            return False
        expected["provenance.json"] = canonical_provenance(expected_inputs)
        actual: dict[str, bytes] = {}
        actual_entries: set[str] = set()
        for path in artifact.rglob("*"):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                return False
            actual_entries.add(path.relative_to(artifact).as_posix())
            if path.is_file():
                actual[path.relative_to(artifact).as_posix()] = path.read_bytes()
        expected_entries: set[str] = set(expected)
        for relative in expected:
            parent = Path(relative).parent
            while parent != Path("."):
                expected_entries.add(parent.as_posix())
                parent = parent.parent
        return actual == expected and actual_entries == expected_entries
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, RuntimeError):
        return False


def _artifact_evidence(artifact: Path) -> str | None:
    """Return stable evidence for one exact, symlink-free artifact tree."""

    try:
        before = _state_lstat(artifact)
        if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
            return None
        digest = hashlib.sha256(b"opencode-artifact-evidence.v1\0")
        for path in sorted(
            artifact.rglob("*"), key=lambda item: item.relative_to(artifact).as_posix()
        ):
            metadata = os.lstat(path)
            relative = path.relative_to(artifact).as_posix().encode("utf-8")
            if stat.S_ISDIR(metadata.st_mode):
                digest.update(b"D\0" + relative + b"\0")
            elif stat.S_ISREG(metadata.st_mode):
                contents = path.read_bytes()
                digest.update(
                    b"F\0"
                    + relative
                    + b"\0"
                    + len(contents).to_bytes(8, "big")
                    + contents
                )
            else:
                return None
        after = _state_lstat(artifact)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            return None
        return digest.hexdigest()
    except (OSError, RuntimeError, ValueError):
        return None


def _artifact_matches_evidence(artifact: Path, expected: str | None) -> bool:
    return expected is not None and _artifact_evidence(artifact) == expected


def _receipt_has_complete_live_artifact_links(
    receipt: _Receipt, artifact: Path
) -> bool:
    """Require the exact planned artifact-link inventory to be live."""

    if not receipt.links:
        return False
    config_roots = {
        _lexical_absolute(link.destination.parent.parent) for link in receipt.links
    }
    if len(config_roots) != 1:
        return False
    config_root = next(iter(config_roots))
    try:
        skill_names = tuple(_skill_inventory(receipt.repository_root))
    except (OSError, RuntimeError, ValueError):
        return False
    expected = {
        ProfileLink(
            _lexical_absolute(artifact / "skills" / name),
            config_root / "skills" / name,
        )
        for name in skill_names
    }
    expected.update(
        ProfileLink(
            _lexical_absolute(artifact / "commands" / f"{name}.md"),
            config_root / "commands" / f"{name}.md",
        )
        for name in skill_names
    )
    expected.update(
        ProfileLink(
            _lexical_absolute(artifact / "agents" / f"{name}.md"),
            config_root / "agents" / f"{name}.md",
        )
        for name in _OPENCODE_AGENT_NAMES
    )
    expected.update(
        ProfileLink(
            _lexical_absolute(artifact / "plugins" / name),
            config_root / "plugins" / name,
        )
        for name in LEGACY_OPENCODE_PLUGINS
    )
    return (
        len(receipt.links) == len(expected)
        and set(receipt.links) == expected
        and all(
            link.destination_dev is not None
            and link.destination_ino is not None
            and _recorded_opencode_link_is_live(link)
            for link in receipt.links
        )
    )


def _receipt_owns_artifact(receipt: _Receipt | None, artifact: Path) -> bool:
    """Require the permanent package anchor and recorded directory identity."""

    if not (
        receipt is not None
        and receipt.artifact_root is not None
        and receipt.artifact_dev is not None
        and receipt.artifact_ino is not None
        and receipt.lineage is not None
        and not receipt.marketplace_added
        and receipt.plugin_installed
        and _lexical_absolute(receipt.artifact_root) == _lexical_absolute(artifact)
    ):
        return False
    try:
        metadata = _state_lstat(artifact)
    except OSError:
        return False
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino)
        != (receipt.artifact_dev, receipt.artifact_ino)
    ):
        return False
    return _artifact_anchor_matches(
        artifact,
        receipt.artifact_anchor,
        receipt.artifact_anchor_dev,
        receipt.artifact_anchor_ino,
    )


def _validate_receipt_artifact(receipt: _Receipt, state_home: Path) -> None:
    if receipt.artifact_root is not None:
        artifact = _fixed_opencode_artifact(state_home, receipt.artifact_root)
        if receipt.artifact_dev is not None and receipt.artifact_ino is not None:
            allowed = [(receipt.artifact_dev, receipt.artifact_ino)]
            if receipt.pending_swap is not None:
                allowed.append(
                    (
                        receipt.pending_swap.candidate_dev,
                        receipt.pending_swap.candidate_ino,
                    )
                )
            if _lexists(artifact) and not any(
                _pending_identity(artifact, dev, ino) for dev, ino in allowed
            ):
                raise InstallError(
                    "OpenCode artifact identity differs from the committed receipt"
                )


def _with_artifact_identity(
    receipt: _Receipt,
    identity: tuple[int, int],
    artifact_digest: str | None = None,
) -> _Receipt:
    return _Receipt(
        repository_root=receipt.repository_root,
        links=receipt.links,
        marketplace_added=receipt.marketplace_added,
        plugin_installed=receipt.plugin_installed,
        artifact_root=receipt.artifact_root,
        artifact_dev=identity[0],
        artifact_ino=identity[1],
        artifact_digest=(
            receipt.artifact_digest if artifact_digest is None else artifact_digest
        ),
        lineage=receipt.lineage,
        artifact_anchor=receipt.artifact_anchor,
        artifact_anchor_dev=receipt.artifact_anchor_dev,
        artifact_anchor_ino=receipt.artifact_anchor_ino,
        teardown_phase=receipt.teardown_phase,
        pending_swap=receipt.pending_swap,
        pending_publish=receipt.pending_publish,
        pending_migration=receipt.pending_migration,
        pending_retirement=receipt.pending_retirement,
    )


def _migrate_artifact_identity(
    repo_root: Path,
    state_home: Path,
    receipt_path: Path,
    receipt: _Receipt,
) -> _Receipt:
    """Add exact identity/evidence to an older committed artifact receipt."""

    if (
        receipt.artifact_root is None
        or receipt.pending_publish is not None
        or receipt.pending_swap is not None
    ):
        return receipt
    if (
        not receipt.pending_migration
        and receipt.artifact_dev is not None
        and receipt.artifact_ino is not None
        and receipt.artifact_digest is not None
        and receipt.artifact_anchor is not None
        and receipt.artifact_anchor_dev is not None
        and receipt.artifact_anchor_ino is not None
    ):
        # Permanent artifact ownership is already complete.  Missing link
        # identities can represent foreign pathnames observed during crash
        # recovery and must never be filled in from the live filesystem.
        return receipt
    artifact = _fixed_opencode_artifact(state_home, receipt.artifact_root)
    if receipt.pending_migration:
        if (
            receipt.artifact_dev is None
            or receipt.artifact_ino is None
            or receipt.artifact_digest is None
            or receipt.lineage is None
            or receipt.artifact_anchor is None
            or receipt.artifact_anchor_dev is None
            or receipt.artifact_anchor_ino is None
            or receipt.artifact_anchor
            != _artifact_anchor_path(artifact.parent, receipt.lineage)
            or not _pending_identity(
                artifact, receipt.artifact_dev, receipt.artifact_ino
            )
            or not _artifact_matches_evidence(artifact, receipt.artifact_digest)
            or not receipt.links
            or not all(
                _recorded_opencode_link_is_live(link) for link in receipt.links
            )
        ):
            raise InstallError(
                "prepared legacy OpenCode migration lost its frozen ownership evidence"
            )
        try:
            anchor_source = _state_lstat(
                artifact / OPENCODE_ARTIFACT_ANCHOR_FILE
            )
        except OSError as error:
            raise InstallError(
                "prepared legacy OpenCode migration lost its anchor source"
            ) from error
        if (
            not stat.S_ISREG(anchor_source.st_mode)
            or (anchor_source.st_dev, anchor_source.st_ino)
            != (receipt.artifact_anchor_dev, receipt.artifact_anchor_ino)
        ):
            raise InstallError(
                "prepared legacy OpenCode migration anchor identity changed"
            )
        anchor = _create_artifact_anchor(artifact, receipt.lineage)
        if anchor != (
            receipt.artifact_anchor,
            receipt.artifact_anchor_dev,
            receipt.artifact_anchor_ino,
        ):
            raise InstallError("legacy OpenCode migration anchor identity changed")
        committed = replace(receipt, pending_migration=False)
        _write_receipt(receipt_path, committed)
        return committed
    has_recorded_identity = (
        receipt.artifact_dev is not None and receipt.artifact_ino is not None
    )
    ownership_proven = (
        _artifact_matches_sources(repo_root, artifact)
        and _receipt_has_complete_live_artifact_links(receipt, artifact)
    )
    if has_recorded_identity:
        metadata = _state_lstat(artifact)
        ownership_proven = ownership_proven and (
            metadata.st_dev,
            metadata.st_ino,
        ) == (receipt.artifact_dev, receipt.artifact_ino)
    if not ownership_proven:
        raise InstallError(
            "legacy OpenCode artifact lacks independent ownership evidence"
        )
    before = _state_lstat(artifact)
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise InstallError(f"opencode artifact is not a regular directory: {artifact}")
    evidence = _artifact_evidence(artifact)
    if evidence is None:
        raise InstallError("legacy OpenCode artifact evidence could not be captured")
    after = _state_lstat(artifact)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise InstallError("legacy OpenCode artifact changed during validation")
    lineage = receipt.lineage or uuid.uuid4().hex
    captured_links = receipt.links
    if not captured_links or not all(
        link.destination_dev is not None
        and link.destination_ino is not None
        and _recorded_opencode_link_is_live(link)
        for link in captured_links
    ):
        raise InstallError("legacy OpenCode link identity changed during migration")
    anchor_source = _state_lstat(artifact / OPENCODE_ARTIFACT_ANCHOR_FILE)
    if not stat.S_ISREG(anchor_source.st_mode):
        raise InstallError("legacy OpenCode anchor source is not a regular file")
    deterministic_anchor = _artifact_anchor_path(artifact.parent, lineage)
    prepared = replace(
        receipt,
        links=captured_links,
        artifact_dev=before.st_dev,
        artifact_ino=before.st_ino,
        artifact_digest=evidence,
        lineage=lineage,
        artifact_anchor=deterministic_anchor,
        artifact_anchor_dev=anchor_source.st_dev,
        artifact_anchor_ino=anchor_source.st_ino,
        teardown_phase="committed",
        pending_migration=True,
    )
    # This is the migration's authority boundary: no live pathname is captured
    # or adopted after the prepared receipt becomes durable.
    _write_receipt(receipt_path, prepared)
    return _migrate_artifact_identity(repo_root, state_home, receipt_path, prepared)


def _restore_opencode_artifact(
    artifact: Path, backup: Path | None, pending: _PendingSwap | None = None
) -> None:
    if backup is None:
        return
    if pending is not None:
        if not _pending_identity(backup, pending.backup_dev, pending.backup_ino):
            raise InstallError(f"cannot restore unproven OpenCode backup: {backup}")
        if _lexists(artifact):
            if not _pending_identity(artifact, pending.candidate_dev, pending.candidate_ino):
                raise InstallError(
                    "cannot restore OpenCode artifact without proving live ownership"
                )
            candidate_anchor_owned = _artifact_anchor_matches(
                artifact,
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
            _remove_candidate_artifact_exact(
                artifact,
                pending.candidate_dev,
                pending.candidate_ino,
                pending.candidate_anchor,
                pending.candidate_anchor_dev,
                pending.candidate_anchor_ino,
            )
            if candidate_anchor_owned:
                assert pending.candidate_anchor is not None
                assert pending.candidate_anchor_dev is not None
                assert pending.candidate_anchor_ino is not None
                _unlink_artifact_anchor_identity(
                    pending.candidate_anchor,
                    pending.candidate_anchor_dev,
                    pending.candidate_anchor_ino,
                )
    elif _lexists(artifact):
        _remove_opencode_artifact(artifact)
    try:
        _rename_noreplace(backup, artifact)
    except OSError as error:
        raise InstallError(f"cannot restore opencode artifact: {artifact}: {error}") from error


def _recover_pending_publish(
    repo_root: Path, state_home: Path, receipt: _Receipt | None
) -> tuple[_Receipt | None, bool]:
    """Recover an initial publication only after exact artifact validation."""

    if receipt is None or receipt.pending_publish is None:
        return receipt, False
    pending = receipt.pending_publish
    receipt_path = _opencode_receipt_path(state_home)
    if pending.phase.startswith("rollback-"):
        return _rollback_pending_publish(receipt_path, receipt), False
    candidate_exists = _pending_identity(
        pending.candidate, pending.candidate_dev, pending.candidate_ino
    )
    artifact_exists = _lexists(pending.artifact)
    artifact_is_candidate = _pending_identity(
        pending.artifact, pending.candidate_dev, pending.candidate_ino
    )
    if (
        candidate_exists
        and pending.candidate_anchor is not None
        and pending.candidate_anchor_dev is not None
        and pending.candidate_anchor_ino is not None
        and not _artifact_anchor_matches(
            pending.candidate,
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        )
    ):
        adopted = _create_artifact_anchor(pending.candidate, pending.lineage)
        if adopted != (
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        ):
            raise InstallError("pending OpenCode publication anchor identity changed")
    candidate_owned = candidate_exists and _artifact_anchor_matches(
        pending.candidate,
        pending.candidate_anchor,
        pending.candidate_anchor_dev,
        pending.candidate_anchor_ino,
    )
    artifact_anchor_owned = artifact_is_candidate and _artifact_anchor_matches(
        pending.artifact,
        pending.candidate_anchor,
        pending.candidate_anchor_dev,
        pending.candidate_anchor_ino,
    )
    artifact_links_owned = (
        artifact_is_candidate
        and pending.planned_links
        and _receipt_has_complete_live_artifact_links(receipt, pending.artifact)
    )
    artifact_owned = artifact_anchor_owned or artifact_links_owned
    candidate_proven = candidate_owned and (
        _artifact_matches_evidence(pending.candidate, pending.candidate_digest)
        if pending.candidate_digest is not None
        else _artifact_matches_sources(repo_root, pending.candidate)
    )
    artifact_proven = artifact_owned and (
        _artifact_matches_evidence(pending.artifact, pending.candidate_digest)
        if pending.candidate_digest is not None
        else _artifact_matches_sources(repo_root, pending.artifact)
    )
    if artifact_exists and not artifact_is_candidate:
        raise InstallError(
            "OpenCode initial publication has an unproven live occupant; preserving it"
        )
    if (
        not candidate_exists
        and not artifact_exists
        and not receipt.links
        and not pending.planned_links
        and not _artifact_anchor_identity_is_live(
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        )
    ):
        # Rollback removed every receipt-owned publication object before a
        # foreign deterministic quarantine collision blocked the final
        # receipt rename.  Retry that same authenticated deletion boundary;
        # no link inventory has been published or can be orphaned here.
        _unlink_state_path(receipt_path)
        return None, False
    if candidate_exists and not artifact_exists and pending.phase in {
        "prepared",
        "anchor-recorded",
    }:
        if not candidate_proven:
            raise InstallError(
                "pending OpenCode publication lacks independent ownership evidence"
            )
        if pending.phase == "prepared":
            pending = replace(pending, phase="anchor-recorded")
            receipt = _receipt_with_pending_publish(receipt, pending)
            _write_receipt(receipt_path, receipt)
        _rename_noreplace(pending.candidate, pending.artifact)
        if not _pending_identity(
            pending.artifact, pending.candidate_dev, pending.candidate_ino
        ):
            raise InstallError("recovered OpenCode candidate identity changed")
        pending = replace(pending, phase="published")
        receipt = _receipt_with_pending_publish(receipt, pending)
        _write_receipt(receipt_path, receipt)
        return receipt, True
    if candidate_exists or not artifact_is_candidate:
        raise InstallError("OpenCode initial publication state is inconsistent")
    if not artifact_proven:
        raise InstallError("recovered OpenCode artifact failed exact evidence validation")
    if not artifact_anchor_owned:
        new_anchor = _create_artifact_anchor(pending.artifact, pending.lineage)
        pending = _PendingPublish(
            **{
                **pending.__dict__,
                "phase": "published",
                "candidate_anchor": new_anchor[0],
                "candidate_anchor_dev": new_anchor[1],
                "candidate_anchor_ino": new_anchor[2],
            }
        )
        receipt = _receipt_with_pending_publish(receipt, pending)
        try:
            _write_receipt(receipt_path, receipt)
        except InstallError:
            try:
                _remove_artifact_anchor_exact(pending.artifact, *new_anchor)
            except InstallError:
                pass
            raise
    elif pending.phase in {"prepared", "anchor-recorded"}:
        pending = _PendingPublish(**{**pending.__dict__, "phase": "published"})
        receipt = _receipt_with_pending_publish(receipt, pending)
        _write_receipt(receipt_path, receipt)
    return receipt, True


def _discard_prepublication_receipt(
    receipt_path: Path, receipt: _Receipt | None
) -> None:
    if receipt is not None and receipt.pending_publish is not None and _lexists(
        receipt_path
    ):
        _unlink_state_path(receipt_path)


def _remove_initial_publication_artifact(
    artifact: Path, receipt: _Receipt | None
) -> None:
    if receipt is None or receipt.pending_publish is None:
        raise InstallError("OpenCode published artifact is missing its exact identity")
    receipt_path = artifact.parent / OPENCODE_RECEIPT_FILENAME
    _rollback_pending_publish(receipt_path, receipt)


def _ensure_opencode_artifact(
    repo_root: Path, state_home: Path, receipt: _Receipt | None = None
) -> tuple[Path, bool, Path | None, _PendingSwap | None, _Receipt | None]:
    """Build and publish a candidate with receipt-owned durable swap state."""

    artifact = _fixed_opencode_artifact(state_home)
    receipt_path = _opencode_receipt_path(state_home)
    had_receipt = receipt is not None
    receipt, recovered_initial = _recover_pending_publish(repo_root, state_home, receipt)
    if recovered_initial:
        return artifact, False, None, None, receipt
    receipt = _recover_pending_swap(repo_root, state_home, receipt)
    # A completed-but-not-cleaned swap is recovered from the receipt and its
    # exact backup is removed before beginning another publication.
    receipt = _garbage_collect_opencode_backups(state_home, receipt, repo_root)
    existing = _lexists(artifact)
    if existing and not _receipt_owns_artifact(receipt, artifact):
        raise InstallError(f"opencode artifact is stale and not receipt-owned: {artifact}")
    if receipt is not None and receipt.pending_swap is not None:
        if (
            existing
            and receipt.teardown_phase == "committed"
            and _artifact_matches_sources(repo_root, artifact)
        ):
            # An identity-preserving reinstall may retry exact retirement and
            # link repair while retaining the original journal.  Changed
            # sources must wait: a second swap would overwrite the only proof
            # authorizing deletion of the first backup and old anchor.
            return artifact, False, None, None, receipt
        raise InstallError(
            "prior OpenCode artifact retirement is incomplete; preserving its backup and anchors"
        )
    if (
        existing
        and receipt is not None
        and receipt.teardown_phase == "committed"
        and _artifact_matches_sources(repo_root, artifact)
    ):
        # The artifact and its permanent ownership anchor are already exact.
        # Link preflight below may repair a missing destination, but there is
        # no reason to rotate the artifact inode or its anchor.
        return artifact, False, None, None, receipt
    binding = _state_binding(artifact)
    if binding is None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
    else:
        _verify_state_binding(binding)
    candidate = artifact.parent / f".{artifact.name}.next-{uuid.uuid4().hex}"
    backup: Path | None = None
    pending: _PendingSwap | None = None
    candidate_identity: tuple[int, int] | None = None
    candidate_digest: str | None = None
    candidate_anchor: tuple[Path, int, int] | None = None
    live_identity: tuple[int, int] | None = None
    rollback_receipt: _Receipt | None = None
    try:
        build_opencode_package(repo_root, candidate)
        if not _artifact_matches_sources(repo_root, candidate):
            raise InstallError("fresh OpenCode artifact failed exact inventory or byte validation")
        candidate_metadata = _state_lstat(candidate)
        candidate_identity = (candidate_metadata.st_dev, candidate_metadata.st_ino)
        candidate_digest = _artifact_evidence(candidate)
        if candidate_digest is None:
            raise InstallError("fresh OpenCode artifact evidence could not be captured")
        if _lexists(artifact):
            artifact_metadata = _state_lstat(artifact)
            if stat.S_ISLNK(artifact_metadata.st_mode) or not stat.S_ISDIR(
                artifact_metadata.st_mode
            ):
                raise InstallError(f"opencode artifact is not a regular directory: {artifact}")
            if not _receipt_owns_artifact(receipt, artifact) or receipt is None:
                raise InstallError(f"opencode artifact ownership changed: {artifact}")
            live_metadata = artifact_metadata
            live_identity = (live_metadata.st_dev, live_metadata.st_ino)
            live_digest = _artifact_evidence(artifact)
            if live_digest is None:
                raise InstallError("live OpenCode artifact evidence could not be captured")
            if receipt.artifact_digest != live_digest:
                receipt = _with_artifact_identity(receipt, live_identity, live_digest)
                _write_receipt(receipt_path, receipt)
            lineage = receipt.lineage or uuid.uuid4().hex
            receipt_for_swap = _Receipt(
                repository_root=receipt.repository_root,
                links=receipt.links,
                marketplace_added=receipt.marketplace_added,
                plugin_installed=receipt.plugin_installed,
                artifact_root=artifact,
                artifact_dev=receipt.artifact_dev,
                artifact_ino=receipt.artifact_ino,
                artifact_digest=receipt.artifact_digest,
                lineage=lineage,
                artifact_anchor=receipt.artifact_anchor,
                artifact_anchor_dev=receipt.artifact_anchor_dev,
                artifact_anchor_ino=receipt.artifact_anchor_ino,
                teardown_phase="committed",
                pending_swap=None,
            )
            if receipt.lineage is None:
                _write_receipt(receipt_path, receipt_for_swap)
            backup = artifact.parent / f".{artifact.name}.old-{uuid.uuid4().hex}"
            anchor_token = candidate.name.removeprefix(f".{artifact.name}.next-")
            anchor_source = os.lstat(candidate / OPENCODE_ARTIFACT_ANCHOR_FILE)
            if not stat.S_ISREG(anchor_source.st_mode):
                raise InstallError("fresh OpenCode candidate anchor is not a regular file")
            deterministic_anchor = _artifact_anchor_path(candidate.parent, anchor_token)
            pending = _PendingSwap(
                lineage=lineage,
                artifact=artifact,
                candidate=candidate,
                candidate_dev=candidate_identity[0],
                candidate_ino=candidate_identity[1],
                backup=backup,
                backup_dev=live_identity[0],
                backup_ino=live_identity[1],
                live_dev=live_identity[0],
                live_ino=live_identity[1],
                phase="prepared",
                candidate_digest=candidate_digest,
                backup_digest=live_digest,
                candidate_anchor=deterministic_anchor,
                candidate_anchor_dev=anchor_source.st_dev,
                candidate_anchor_ino=anchor_source.st_ino,
                old_anchor=receipt.artifact_anchor,
                old_anchor_dev=receipt.artifact_anchor_dev,
                old_anchor_ino=receipt.artifact_anchor_ino,
            )
            # This write, including directory fsync, is mandatory before the
            # anchor link and the first live->backup rename.
            rollback_receipt = _receipt_with_pending(receipt_for_swap, pending)
            _write_receipt(receipt_path, rollback_receipt)
            candidate_anchor = _create_artifact_anchor(candidate, anchor_token)
            if candidate_anchor != (
                deterministic_anchor,
                anchor_source.st_dev,
                anchor_source.st_ino,
            ):
                raise InstallError("candidate OpenCode anchor identity changed")
            pending = replace(pending, phase="anchor-recorded")
            rollback_receipt = _receipt_with_pending(receipt_for_swap, pending)
            _write_receipt(receipt_path, rollback_receipt)
            if not _pending_identity(artifact, pending.live_dev, pending.live_ino):
                raise InstallError("live OpenCode artifact changed before swap")
            _rename_noreplace(artifact, backup)
            pending = _PendingSwap(**{**pending.__dict__, "phase": "backup-created"})
            rollback_receipt = _receipt_with_pending(receipt_for_swap, pending)
            _write_receipt(receipt_path, rollback_receipt)
            _rename_noreplace(candidate, artifact)
            if not _pending_identity(artifact, pending.candidate_dev, pending.candidate_ino):
                raise InstallError("published OpenCode candidate identity changed")
            pending = _PendingSwap(**{**pending.__dict__, "phase": "published"})
            rollback_receipt = _receipt_with_pending(receipt_for_swap, pending)
            _write_receipt(receipt_path, rollback_receipt)
            # Link staging/repair rewrites occur before the final merged
            # receipt.  Keep the active swap journal attached to every such
            # rewrite so a crash cannot expose the candidate while forgetting
            # the backup and both anchor proofs.
            return (
                artifact,
                False,
                backup,
                pending,
                _receipt_with_pending(receipt_for_swap, pending),
            )
        lineage = uuid.uuid4().hex
        anchor_source = os.lstat(candidate / OPENCODE_ARTIFACT_ANCHOR_FILE)
        if not stat.S_ISREG(anchor_source.st_mode):
            raise InstallError("fresh OpenCode anchor source is not a regular file")
        deterministic_anchor = _artifact_anchor_path(candidate.parent, lineage)
        pending_publish = _PendingPublish(
            lineage=lineage,
            artifact=artifact,
            candidate=candidate,
            candidate_dev=candidate_identity[0],
            candidate_ino=candidate_identity[1],
            phase="prepared",
            candidate_digest=candidate_digest,
            candidate_anchor=deterministic_anchor,
            candidate_anchor_dev=anchor_source.st_dev,
            candidate_anchor_ino=anchor_source.st_ino,
        )
        prepublication_receipt = _Receipt(
            repository_root=repo_root,
            links=receipt.links if receipt is not None else (),
            marketplace_added=False,
            plugin_installed=True,
            artifact_root=artifact,
            lineage=lineage,
            pending_publish=pending_publish,
        )
        rollback_receipt = prepublication_receipt
        _write_receipt(receipt_path, prepublication_receipt)
        candidate_anchor = _create_artifact_anchor(candidate, lineage)
        if candidate_anchor != (
            deterministic_anchor,
            anchor_source.st_dev,
            anchor_source.st_ino,
        ):
            raise InstallError("fresh OpenCode anchor identity changed before publication")
        pending_publish = replace(pending_publish, phase="anchor-recorded")
        prepublication_receipt = _receipt_with_pending_publish(
            prepublication_receipt, pending_publish
        )
        rollback_receipt = prepublication_receipt
        _write_receipt(receipt_path, prepublication_receipt)
        _rename_noreplace(candidate, artifact)
        if not _pending_identity(artifact, candidate_identity[0], candidate_identity[1]):
            raise InstallError("published OpenCode candidate identity changed")
        pending_publish = _PendingPublish(
            **{**pending_publish.__dict__, "phase": "published"}
        )
        prepublication_receipt = _receipt_with_pending_publish(
            prepublication_receipt, pending_publish
        )
        rollback_receipt = prepublication_receipt
        _write_receipt(receipt_path, prepublication_receipt)
        return artifact, True, None, None, prepublication_receipt
    except (OpencodeBuildError, OSError, InstallError) as error:
        if rollback_receipt is not None and _lexists(receipt_path):
            try:
                if rollback_receipt.pending_publish is not None:
                    _rollback_pending_publish(receipt_path, rollback_receipt)
                    rollback_receipt = None
                elif rollback_receipt.pending_swap is not None:
                    rollback_receipt = _rollback_pending_swap_candidate(
                        receipt_path, rollback_receipt
                    )
                else:
                    raise InstallError("OpenCode rollback journal is incomplete")
            except InstallError as cleanup_error:
                raise InstallError(f"{error}; {cleanup_error}") from error
            if isinstance(error, InstallError):
                raise
            raise InstallError(f"cannot build OpenCode artifact: {error}") from error
        candidate_anchor_owned = (
            candidate_anchor is not None
            and _lexists(candidate)
            and _artifact_anchor_matches(candidate, *candidate_anchor)
        )
        if _lexists(candidate) and candidate_identity is not None and _pending_identity(
            candidate, candidate_identity[0], candidate_identity[1]
        ):
            try:
                _remove_opencode_artifact_exact(
                    candidate, candidate_identity[0], candidate_identity[1]
                )
            except InstallError:
                pass
        if candidate_anchor_owned and candidate_anchor is not None:
            try:
                _unlink_artifact_anchor_identity(
                    candidate_anchor[0], candidate_anchor[1], candidate_anchor[2]
                )
            except InstallError:
                pass
        if (
            not had_receipt
            and not _lexists(artifact)
            and _lexists(receipt_path)
        ):
            try:
                _unlink_state_path(receipt_path)
            except InstallError:
                pass
        if pending is not None and _lexists(pending.backup):
            backup_owned = _pending_identity(
                pending.backup, pending.backup_dev, pending.backup_ino
            )
            current_candidate = _pending_identity(
                artifact, pending.candidate_dev, pending.candidate_ino
            )
            current_live = _pending_identity(artifact, pending.live_dev, pending.live_ino)
            if backup_owned and not _lexists(artifact):
                _rename_noreplace(pending.backup, artifact)
                _rewrite_receipt_pending(receipt_path, None)
            elif backup_owned and current_candidate:
                # Remove the live directory only after proving it is exactly
                # the published candidate.  A foreign replacement is left in
                # place and the backup is never moved over it.
                _remove_candidate_artifact_exact(
                    artifact,
                    pending.candidate_dev,
                    pending.candidate_ino,
                    pending.candidate_anchor,
                    pending.candidate_anchor_dev,
                    pending.candidate_anchor_ino,
                )
                assert pending.candidate_anchor is not None
                assert pending.candidate_anchor_dev is not None
                assert pending.candidate_anchor_ino is not None
                _unlink_artifact_anchor_identity(
                    pending.candidate_anchor,
                    pending.candidate_anchor_dev,
                    pending.candidate_anchor_ino,
                )
                _rename_noreplace(pending.backup, artifact)
                _rewrite_receipt_pending(receipt_path, None)
            elif backup_owned and not current_live:
                raise InstallError(
                    "cannot restore OpenCode artifact without proving live ownership"
                ) from error
        if isinstance(error, InstallError):
            raise
        raise InstallError(f"cannot build OpenCode artifact: {error}") from error


def _require_opencode_source(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise InstallError(f"opencode source must not be a symlink: {path}")
    if not path.exists():
        raise InstallError(f"opencode source is missing: {label}: {path}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"opencode source cannot be resolved: {path}: {error}") from error
    return resolved


def _artifact_entries(artifact_root: Path, relative: str, label: str) -> tuple[Path, ...]:
    directory = artifact_root / relative
    if directory.is_symlink() or not directory.is_dir():
        raise InstallError(f"opencode artifact {label} directory is missing: {directory}")
    entries: list[Path] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file() and not path.is_dir():
            raise InstallError(f"opencode artifact {label} entry is unsafe: {path}")
        entries.append(path)
    if not entries:
        raise InstallError(f"opencode artifact {label} inventory is empty: {directory}")
    return tuple(entries)


def _opencode_expected_links(
    repo_root: Path,
    config_dir: Path,
    artifact_root: Path,
) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    canonical_config = _canonical_opencode_config(config_dir)
    artifact_path = Path(artifact_root).expanduser()
    _assert_no_symlink_components(artifact_path, "opencode artifact")
    if artifact_path.is_symlink():
        raise InstallError(f"opencode artifact must not be a symlink: {artifact_path}")
    artifact_root = artifact_path.resolve(strict=True)
    if not artifact_root.is_dir():
        raise InstallError(f"opencode artifact is not a regular directory: {artifact_root}")
    links: list[ProfileLink] = []
    skills = _artifact_entries(artifact_root, "skills", "shared skills")
    for skill in skills:
        name = skill.name
        source = _require_opencode_source(skill, f"shared skill {name!r}")
        if not source.is_dir():
            raise InstallError(f"shared skill source is not a directory: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "skills" / name))
    commands = _artifact_entries(artifact_root, "commands", "commands")
    for path in commands:
        if path.suffix != ".md":
            raise InstallError(f"opencode command source must be Markdown: {path}")
        name = path.stem
        source = _require_opencode_source(path, f"command {name!r}")
        if not source.is_file():
            raise InstallError(f"command source is not a regular file: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "commands" / f"{name}.md"))
    agents = _artifact_entries(artifact_root, "agents", "agents")
    for path in agents:
        if path.suffix != ".md":
            raise InstallError(f"opencode agent source must be Markdown: {path}")
        name = path.stem
        source = _require_opencode_source(path, f"agent {name!r}")
        if not source.is_file():
            raise InstallError(f"agent source is not a regular file: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "agents" / f"{name}.md"))
    plugins = _artifact_entries(artifact_root, "plugins", "plugins")
    for path in plugins:
        name = path.name
        source = _require_opencode_source(path, f"plugin {name!r}")
        if not source.is_file():
            raise InstallError(f"plugin source is not a regular file: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "plugins" / name))
    for link in links:
        _validate_opencode_destination(link.destination, canonical_config)
    return tuple(links)


def preflight_opencode_links(
    repo_root: Path,
    config_dir: Path,
    state_home: Path | None = None,
    *,
    artifact_root: Path | None = None,
    legacy_receipt: _Receipt | None = None,
) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    _validate_repository(canonical_root)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if artifact_root is None:
        # Preflight is read-only.  Even when a state home is supplied, build
        # into a disposable artifact rather than adopting or replacing state.
        temporary = tempfile.TemporaryDirectory(prefix="expskill-opencode-preflight-")
        artifact_root = Path(temporary.name) / "artifact"
        try:
            build_opencode_package(canonical_root, artifact_root)
        except (OpencodeBuildError, OSError) as error:
            temporary.cleanup()
            raise InstallError(f"cannot build OpenCode artifact: {error}") from error
    try:
        links = _opencode_expected_links(canonical_root, config_dir, artifact_root)
        if temporary is not None:
            # The temporary artifact is validation-only.  Remap every source
            # to the fixed receipt-owned path before conflict checks and before
            # returning links to callers, so dry-run output is stable.
            future_artifact = _fixed_opencode_artifact(
                _default_state_home() if state_home is None else state_home
            )
            remapped: list[ProfileLink] = []
            for link in links:
                relative = link.source.relative_to(artifact_root)
                remapped.append(
                    ProfileLink(source=future_artifact / relative, destination=link.destination)
                )
            links = tuple(remapped)
    finally:
        if temporary is not None:
            temporary.cleanup()
    for link in links:
        if not _lexists(link.destination):
            continue
        recorded = next(
            (
                item
                for item in legacy_receipt.links
                if item.destination == link.destination
            ),
            None,
        ) if legacy_receipt is not None else None
        if _same_owned_link(link.destination, link.source):
            # Target equality alone never creates ownership.  A current or
            # interrupted receipt may preserve the pathname while retaining
            # its already-recorded inode authority; a fresh install may not
            # adopt a preexisting same-target symlink.
            if recorded is not None:
                continue
            raise InstallError(
                f"refusing unowned opencode destination: {link.destination}"
            )
        if legacy_receipt is not None and (
            legacy_receipt.artifact_root is None
            or legacy_receipt.pending_publish is not None
        ):
            if recorded is not None and _same_recorded_link(
                link.destination, recorded.source
            ):
                continue
        raise InstallError(f"refusing conflicting opencode destination: {link.destination}")
    return links


def install_opencode(
    repo_root: Path,
    config_dir: Path,
    state_home: Path,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    # Semantic repository validation is intentionally before artifact/state
    # creation, link preflight, or any receipt mutation.
    _validate_repository(canonical_root)
    # Validate the lexical config path before materializing receipt-owned
    # state; resolving a symlinked ancestor here would redirect every output.
    canonical_config = _canonical_opencode_config(config_dir)
    receipt_path_value = _opencode_receipt_path(state_home)
    try:
        config_binding = _open_config_binding(canonical_config, create=True)
    except OSError as error:
        raise InstallError(
            f"cannot bind OpenCode config directory: {canonical_config}: {error}"
        ) from error
    if config_binding is None:
        raise InstallError(f"cannot create OpenCode config directory: {canonical_config}")
    config_key = str(config_binding.directory)
    if config_key in _CONFIG_BINDINGS:
        _close_config_binding(config_binding)
        raise InstallError(
            f"OpenCode config directory is already active: {config_binding.directory}"
        )
    _CONFIG_BINDINGS[config_key] = config_binding
    receipt_directory_existed = receipt_path_value.parent.exists()
    state_home_existed = receipt_path_value.parent.parent.exists()
    try:
        try:
            binding = _open_state_binding(receipt_path_value.parent, create=True)
        except OSError as error:
            raise InstallError(
                f"cannot bind OpenCode state directory: {receipt_path_value.parent}: {error}"
            ) from error
        if binding is None:
            raise InstallError(f"cannot create OpenCode state directory: {receipt_path_value.parent}")
        key = str(binding.directory)
        if key in _STATE_BINDINGS:
            _close_state_binding(binding)
            raise InstallError(f"OpenCode state directory is already active: {binding.directory}")
        _STATE_BINDINGS[key] = binding
        try:
            return _install_opencode_bound(
                canonical_root,
                canonical_config,
                state_home,
                receipt_directory_existed,
                state_home_existed,
            )
        finally:
            _STATE_BINDINGS.pop(key, None)
            _close_state_binding(binding)
    finally:
        _CONFIG_BINDINGS.pop(config_key, None)
        _close_config_binding(config_binding)


def _install_opencode_bound(
    canonical_root: Path,
    config_dir: Path,
    state_home: Path,
    receipt_directory_existed: bool,
    state_home_existed: bool,
) -> InstallResult:
    receipt_path_value = _opencode_receipt_path(state_home)
    receipt_directory = receipt_path_value.parent
    canonical_state_home = receipt_directory.parent
    receipt: _Receipt | None = None
    _recover_receipt_deletion(
        canonical_root, config_dir, state_home, receipt_path_value
    )
    if _lexists(receipt_path_value):
        receipt = _read_opencode_receipt(
            receipt_path_value, canonical_root, config_dir, state_home
        )
        if receipt is None:
            raise InstallError(f"receipt disappeared while reading: {receipt_path_value}")
        if receipt.pending_retirement is not None:
            _recover_pending_retirement(receipt_path_value, receipt)
            receipt = _read_opencode_receipt(
                receipt_path_value, canonical_root, config_dir, state_home
            )
            if receipt is None:
                raise InstallError(
                    f"receipt disappeared after retirement recovery: {receipt_path_value}"
                )
        if receipt.teardown_phase != "committed":
            if receipt.pending_publish is not None or receipt.pending_swap is not None:
                raise InstallError("OpenCode teardown receipt contains publication state")
            _resume_opencode_teardown(receipt_path_value, receipt, state_home)
            receipt = None
        if receipt is not None:
            receipt = _migrate_artifact_identity(
                canonical_root, state_home, receipt_path_value, receipt
            )
            _validate_receipt_artifact(receipt, state_home)
    try:
        (
            artifact_root,
            artifact_created,
            artifact_backup,
            artifact_pending,
            receipt,
        ) = _ensure_opencode_artifact(canonical_root, state_home, receipt)
        if receipt is not None:
            _recover_staged_opencode_links(receipt)
    except Exception:
        _remove_new_opencode_state(
            receipt_directory,
            receipt_directory_existed,
            canonical_state_home,
            state_home_existed,
        )
        raise
    try:
        links = preflight_opencode_links(
            canonical_root,
            config_dir,
            artifact_root=artifact_root,
            legacy_receipt=receipt,
        )
    except Exception:
        if artifact_created:
            _remove_initial_publication_artifact(artifact_root, receipt)
            _discard_prepublication_receipt(receipt_path_value, receipt)
        elif artifact_backup is not None:
            if receipt is None or receipt.pending_swap is None:
                raise InstallError("OpenCode upgrade rollback lacks a durable journal")
            receipt = _rollback_pending_swap_candidate(receipt_path_value, receipt)
        _remove_new_opencode_state(
            receipt_directory,
            receipt_directory_existed,
            canonical_state_home,
            state_home_existed,
        )
        raise
    def record_staged_link(staged: ProfileLink) -> None:
        nonlocal receipt
        if receipt is None:
            raise InstallError(
                "OpenCode link publication lacks a durable receipt"
            )
        updated: list[ProfileLink] = []
        replaced_link = False
        for recorded in receipt.links:
            if recorded.destination == staged.destination:
                updated.append(staged)
                replaced_link = True
            else:
                updated.append(recorded)
        if not replaced_link:
            updated.append(staged)
        receipt = _persist_receipt(
            receipt_path_value,
            receipt,
            links=tuple(updated),
        )

    created_links: list[ProfileLink] = _JournaledLinkList(record_staged_link)
    removed_retired: tuple[ProfileLink, ...] = ()
    preserve_transaction = receipt is not None and receipt.pending_swap is not None
    try:
        if receipt is not None and (
            receipt.artifact_root is None
            or (
                receipt.pending_publish is not None
                and not receipt.pending_publish.planned_links
            )
        ):
            # Legacy link retirement is irreversible pathname progress.  From
            # this point both ordinary and crash exits leave the new artifact
            # receipt in place for exact retry instead of reconstructing old
            # source links.
            if receipt.links:
                preserve_transaction = True
            for old in tuple(receipt.links):
                recorded = old
                if old.destination_dev is None or old.destination_ino is None:
                    if not _lexists(old.destination):
                        continue
                    if not _same_recorded_link(old.destination, old.source):
                        raise InstallError(
                            f"refusing to migrate retargeted legacy link: {old.destination}"
                        )
                    recorded = _capture_opencode_link_identity(old)
                    receipt = _persist_receipt(
                        receipt_path_value,
                        receipt,
                        links=tuple(
                            recorded if item.destination == old.destination else item
                            for item in receipt.links
                        ),
                    )
                assert recorded.destination_dev is not None
                assert recorded.destination_ino is not None
                _unlink_recorded_destination(recorded)
        if receipt is not None and receipt.pending_publish is not None:
            if not receipt.pending_publish.planned_links:
                planned_publish = replace(
                    receipt.pending_publish,
                    phase="planned-links",
                    planned_links=True,
                )
                receipt = _persist_receipt(
                    receipt_path_value,
                    _receipt_with_pending_publish(receipt, planned_publish),
                    links=links,
                )
        _create_links(links, created_links)
        if receipt is not None:
            current_destinations = {link.destination for link in links}
            if any(
                link.destination not in current_destinations
                for link in receipt.links
            ):
                preserve_transaction = True
            _retained, removed_retired = _prune_opencode_retired_links(
                receipt, links, config_dir
            )
        committed_metadata = _state_lstat(artifact_root)
        committed_identity = (committed_metadata.st_dev, committed_metadata.st_ino)
        expected_committed = (
            (artifact_pending.candidate_dev, artifact_pending.candidate_ino)
            if artifact_pending is not None
            else (
                (receipt.pending_publish.candidate_dev, receipt.pending_publish.candidate_ino)
                if receipt is not None and receipt.pending_publish is not None
                else committed_identity
            )
        )
        if committed_identity != expected_committed:
            raise InstallError("published OpenCode artifact identity changed before commit")
        committed_digest = _artifact_evidence(artifact_root)
        expected_digest = (
            artifact_pending.candidate_digest
            if artifact_pending is not None
            else (
                receipt.pending_publish.candidate_digest
                if receipt is not None and receipt.pending_publish is not None
                else committed_digest
            )
        )
        if committed_digest is None or (
            expected_digest is not None and committed_digest != expected_digest
        ):
            raise InstallError("published OpenCode artifact evidence changed before commit")
        if not all(
            _same_recorded_link(link.destination, link.source) for link in links
        ):
            raise InstallError(
                "published OpenCode link inventory changed before commit"
            )
        previous_links = (
            {link.destination: link for link in receipt.links}
            if receipt is not None
            else {}
        )
        committed_links = tuple(
            _committed_opencode_link(
                link,
                previous_links.get(link.destination),
                created=link in created_links,
            )
            for link in links
        )
        if artifact_pending is not None:
            committed_anchor = (
                artifact_pending.candidate_anchor,
                artifact_pending.candidate_anchor_dev,
                artifact_pending.candidate_anchor_ino,
            )
        elif receipt is not None and receipt.pending_publish is not None:
            committed_anchor = (
                receipt.pending_publish.candidate_anchor,
                receipt.pending_publish.candidate_anchor_dev,
                receipt.pending_publish.candidate_anchor_ino,
            )
        elif receipt is not None:
            committed_anchor = (
                receipt.artifact_anchor,
                receipt.artifact_anchor_dev,
                receipt.artifact_anchor_ino,
            )
        else:
            committed_anchor = (None, None, None)
        if not _artifact_anchor_matches(artifact_root, *committed_anchor):
            raise InstallError("committed OpenCode artifact lost its permanent anchor")
        active_pending_swap = (
            artifact_pending
            if artifact_pending is not None
            else (receipt.pending_swap if receipt is not None else None)
        )
        merged_receipt = _Receipt(
            repository_root=canonical_root,
            links=committed_links,
            marketplace_added=False,
            plugin_installed=True,
            artifact_root=artifact_root,
            artifact_dev=committed_identity[0],
            artifact_ino=committed_identity[1],
            artifact_digest=committed_digest,
            lineage=(receipt.lineage if receipt is not None and receipt.lineage else uuid.uuid4().hex),
            artifact_anchor=committed_anchor[0],
            artifact_anchor_dev=committed_anchor[1],
            artifact_anchor_ino=committed_anchor[2],
            teardown_phase="committed",
            # A recovered retirement journal remains authoritative until its
            # exact old directory and anchor reach durable terminal cleanup.
            # The unchanged-artifact fast path deliberately returns no newly
            # created swap, so it must not erase the current receipt's journal.
            pending_swap=active_pending_swap,
        )
        _write_receipt(receipt_path_value, merged_receipt)
    except BaseException as error:
        preserve_exception_type = not isinstance(error, Exception)
        if preserve_exception_type or preserve_transaction:
            # Crash recovery owns every mutation cited by the durable receipt.
            # Restoring retired or migrated links here would mix old-source
            # pathnames with the retained new artifact transaction.
            raise
        journaled_links = (
            tuple(
                link
                for link in receipt.links
                if link.staged_destination is not None
            )
            if receipt is not None
            else ()
        )
        rollback_failures = [
            *(
                f"residual state or rollback failures: {failure}"
                for failure in _rollback_links(
                    tuple(
                        {
                            link.destination: link
                            for link in (*created_links, *journaled_links)
                        }.values()
                    )
                )
            ),
        ]
        for failure in rollback_failures:
            error = InstallError(f"{error}; {failure}")
        if rollback_failures:
            # Compensation did not establish exact link absence.  Keep the
            # artifact, anchor, receipt, and complete final/staged identity
            # inventory together; either entrypoint can retry this closed
            # rollback phase without reconstructing authority from pathnames.
            if artifact_created and receipt is not None and receipt.pending_publish is not None:
                receipt = _receipt_with_pending_publish(
                    receipt,
                    replace(receipt.pending_publish, phase="rollback-prepared"),
                )
                _write_receipt(receipt_path_value, receipt)
            elif artifact_backup is not None and receipt is not None and receipt.pending_swap is not None:
                receipt = _receipt_with_pending(
                    receipt,
                    replace(receipt.pending_swap, phase="rollback-prepared"),
                )
                _write_receipt(receipt_path_value, receipt)
            if isinstance(error, InstallError):
                raise error
            raise InstallError(str(error)) from error
        if artifact_created:
            try:
                _remove_initial_publication_artifact(artifact_root, receipt)
                _discard_prepublication_receipt(receipt_path_value, receipt)
            except InstallError as cleanup_error:
                error = InstallError(f"{error}; {cleanup_error}")
        elif artifact_backup is not None:
            try:
                if receipt is None or receipt.pending_swap is None:
                    raise InstallError(
                        "OpenCode upgrade rollback lacks a durable journal"
                    )
                receipt = _rollback_pending_swap_candidate(
                    receipt_path_value, receipt
                )
            except InstallError as cleanup_error:
                error = InstallError(f"{error}; {cleanup_error}")
        _remove_new_opencode_state(
            receipt_directory,
            receipt_directory_existed,
            canonical_state_home,
            state_home_existed,
        )
        if isinstance(error, InstallError):
            raise error
        raise InstallError(str(error)) from error
    # The receipt now names the successfully committed artifact.  Retry only
    # the one exact backup identity still named by its pending-swap record;
    # foreign ``.old-*`` directories are untouched.
    merged_receipt = _garbage_collect_opencode_backups(
        state_home, merged_receipt, canonical_root
    )
    return InstallResult(
        links=links,
        created_links=tuple(created_links),
        removed_links=removed_retired,
        marketplace_added=False,
        plugin_installed=True,
    )


def _opencode_receipt_links_without_artifact(
    receipt_path: Path,
    config_dir: Path,
    state_home: Path,
    repository_root: Path | None = None,
) -> tuple[ProfileLink, ...]:
    """Reconstruct a structurally constrained prior artifact inventory."""

    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("links"), list):
        raise InstallError(f"receipt links are malformed: {receipt_path}")
    artifact = _fixed_opencode_artifact(state_home)
    artifact_value = payload.get("artifact_root")
    if artifact_value is not None:
        if not isinstance(artifact_value, str):
            raise InstallError(f"receipt artifact root is malformed: {receipt_path}")
        recorded_artifact = Path(artifact_value).expanduser()
        if not recorded_artifact.is_absolute() or _has_dot_components(recorded_artifact):
            raise InstallError(f"receipt artifact root must be an absolute path: {receipt_path}")
        _fixed_opencode_artifact(state_home, recorded_artifact)
    canonical_config = _canonical_opencode_config(config_dir)
    allowed_plugins = set(LEGACY_OPENCODE_PLUGINS)

    def valid_name(value: str) -> bool:
        return bool(value) and value[0].isascii() and value[0].isalnum() and all(
            character.isascii()
            and (character.isalnum() or character in {"-", "_"})
            for character in value
        )

    expected: list[ProfileLink] = []
    seen_destinations: set[Path] = set()
    for entry in payload["links"]:
        if not isinstance(entry, dict):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source_value = entry.get("source")
        destination_value = entry.get("destination")
        if not isinstance(source_value, str) or not isinstance(destination_value, str):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source = Path(source_value).expanduser()
        destination = Path(destination_value).expanduser()
        if not source.is_absolute() or not destination.is_absolute():
            raise InstallError(f"receipt links must be absolute paths: {receipt_path}")
        source = _lexical_absolute(source)
        destination = _lexical_absolute(destination)
        if not _path_is_within(source, artifact):
            raise InstallError(f"receipt artifact source is outside owned state: {receipt_path}")
        if not _path_is_within(destination, canonical_config):
            raise InstallError(f"receipt destination is outside OpenCode config: {receipt_path}")
        try:
            source_relative = source.relative_to(artifact)
            destination_relative = destination.relative_to(canonical_config)
        except ValueError as error:
            raise InstallError(f"receipt link is outside fixed OpenCode layouts: {receipt_path}") from error
        if len(source_relative.parts) != 2 or len(destination_relative.parts) != 2:
            raise InstallError(f"receipt link has an invalid OpenCode layout: {receipt_path}")
        source_group, source_name = source_relative.parts
        destination_group, destination_name = destination_relative.parts
        if source_group == "skills":
            valid = (
                valid_name(source_name)
                and destination_group == "skills"
                and destination_name == source_name
            )
            if valid and _lexists(source) and (source.is_symlink() or not source.is_dir()):
                valid = False
        elif source_group == "commands":
            valid = (
                source_name.endswith(".md")
                and valid_name(source_name[:-3])
                and destination_group == "commands"
                and destination_name == source_name
            )
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        elif source_group == "agents":
            valid = (
                source_name.endswith(".md")
                and source_name[:-3].startswith("expskill-")
                and valid_name(source_name[:-3])
                and destination_group == "agents"
                and destination_name == source_name
            )
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        elif source_group == "plugins":
            valid = source_name in allowed_plugins and destination_group == "plugins" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        else:
            valid = False
        if not valid:
            raise InstallError(f"receipt link is outside fixed OpenCode layouts: {receipt_path}")
        if destination in seen_destinations:
            raise InstallError(f"receipt link is duplicated: {receipt_path}")
        seen_destinations.add(destination)
        _validate_opencode_destination(destination, canonical_config)
        expected.append(ProfileLink(source=source, destination=destination))
    return tuple(expected)


def _read_opencode_receipt(
    receipt_path: Path,
    repository_root: Path,
    config_dir: Path,
    state_home: Path,
) -> _Receipt | None:
    """Read a current artifact receipt or a strictly constrained legacy one."""

    if not _lexists(receipt_path):
        return None
    if not stat.S_ISREG(_state_lstat(receipt_path).st_mode):
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    try:
        payload = json.loads(_read_state_text(receipt_path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    base_keys = {
        "links",
        "marketplace_added",
        "plugin_installed",
        "repository_root",
    }
    if "artifact_root" in payload:
        allowed_keys = base_keys | {"artifact_root", "lineage"}
        if "artifact_dev" in payload or "artifact_ino" in payload:
            allowed_keys.update({"artifact_dev", "artifact_ino"})
        if "artifact_digest" in payload:
            allowed_keys.add("artifact_digest")
        if "artifact_anchor" in payload or "artifact_anchor_dev" in payload or "artifact_anchor_ino" in payload:
            allowed_keys.update(
                {"artifact_anchor", "artifact_anchor_dev", "artifact_anchor_ino"}
            )
        if "teardown_phase" in payload:
            allowed_keys.add("teardown_phase")
        if "pending_swap" in payload:
            allowed_keys.add("pending_swap")
            if "pending_swap_auth" in payload:
                allowed_keys.add("pending_swap_auth")
            if "pending_swap_checksum" in payload:
                allowed_keys.add("pending_swap_checksum")
        if "pending_publish" in payload:
            allowed_keys.add("pending_publish")
        if "pending_migration" in payload:
            allowed_keys.add("pending_migration")
        if "pending_retirement" in payload:
            allowed_keys.add("pending_retirement")
        if "receipt_secret" in payload:
            allowed_keys.add("receipt_secret")
        if set(payload) != allowed_keys:
            raise InstallError(f"current OpenCode receipt is inconsistent: {receipt_path}")
        pending_publish_payload = payload.get("pending_publish")
        links_are_planned = (
            isinstance(pending_publish_payload, dict)
            and pending_publish_payload.get("planned_links") is True
        )
        if (
            "pending_publish" in payload
            and payload.get("links")
            and not links_are_planned
        ):
            expected = _legacy_opencode_expected_links(repository_root, config_dir)
        else:
            expected = _opencode_receipt_links_without_artifact(
                receipt_path, config_dir, state_home, repository_root
            )
    else:
        if frozenset(payload) not in {
            frozenset(base_keys),
            frozenset(base_keys | {"lineage"}),
            frozenset(base_keys | {"receipt_secret"}),
            frozenset(base_keys | {"lineage", "receipt_secret"}),
        }:
            raise InstallError(f"legacy OpenCode receipt is inconsistent: {receipt_path}")
        expected = _legacy_opencode_expected_links(repository_root, config_dir)
    receipt = _read_receipt(
        receipt_path,
        repository_root,
        expected,
        require_retirement_secret=True,
    )
    if receipt is None:
        return None
    if receipt.artifact_root is not None:
        if (
            receipt.lineage is None
            or receipt.marketplace_added
            or not receipt.plugin_installed
        ):
            raise InstallError(f"current OpenCode receipt is incomplete: {receipt_path}")
        if receipt.pending_publish is not None:
            if receipt.pending_publish.planned_links:
                artifact = _fixed_opencode_artifact(
                    state_home, receipt.artifact_root
                )
                canonical_config = _canonical_opencode_config(config_dir)
                skill_names = tuple(_skill_inventory(repository_root))
                expected_publish = {
                    ProfileLink(
                        artifact / "skills" / name,
                        canonical_config / "skills" / name,
                    )
                    for name in skill_names
                }
                expected_publish.update(
                    ProfileLink(
                        artifact / "commands" / f"{name}.md",
                        canonical_config / "commands" / f"{name}.md",
                    )
                    for name in skill_names
                )
                expected_publish.update(
                    ProfileLink(
                        artifact / "agents" / f"{name}.md",
                        canonical_config / "agents" / f"{name}.md",
                    )
                    for name in _OPENCODE_AGENT_NAMES
                )
                expected_publish.update(
                    ProfileLink(
                        artifact / "plugins" / name,
                        canonical_config / "plugins" / name,
                    )
                    for name in LEGACY_OPENCODE_PLUGINS
                )
                links_are_consistent = set(receipt.links) == expected_publish
            else:
                legacy_expected = _legacy_opencode_expected_links(
                    repository_root, config_dir
                )
                links_are_consistent = not receipt.links or set(
                    receipt.links
                ) == set(legacy_expected)
            if (
                receipt.pending_swap is not None
                or not links_are_consistent
            ):
                raise InstallError(
                    f"OpenCode prepublication receipt is inconsistent: {receipt_path}"
                )
    elif set(receipt.links) != set(expected):
        raise InstallError(f"legacy OpenCode receipt is incomplete: {receipt_path}")
    return receipt


def _receipt_deletion_quarantine_identity(
    receipt_path: Path, name: str, *, suffix: str = ".delete"
) -> tuple[int, int, str, str, str] | None:
    prefix = f".{receipt_path.name}."
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    encoded = name[len(prefix) : -len(suffix)]
    try:
        identity, token, pending_phase, current_phase = encoded.split(".", 3)
        dev_text, ino_text = identity.split("-", 1)
        dev = int(dev_text, 16)
        ino = int(ino_text, 16)
    except (ValueError, TypeError):
        return None
    if (
        dev <= 0
        or ino <= 0
        or len(token) != 32
        or any(character not in "0123456789abcdef" for character in token)
        or current_phase not in OPENCODE_RECEIPT_TEARDOWN_PHASES
        or pending_phase not in OPENCODE_RECEIPT_PENDING_PHASES
    ):
        return None
    return dev, ino, token, current_phase, pending_phase


def _recover_receipt_deletion_legacy(
    repository_root: Path,
    config_dir: Path,
    state_home: Path,
    receipt_path: Path,
) -> None:
    """Resume only an inode-, lineage-, and phase-bound receipt deletion."""

    binding = _state_binding(receipt_path)
    if binding is None:
        return
    _verify_state_binding(binding)
    names = tuple(os.listdir(binding.directory_fd))
    # A process may have stopped after the exchange inside exact receipt
    # retirement.  Its identity-bearing private name is itself the durable
    # recovery record; validate the receipt contents and encoded phase before
    # allowing the locked retry to reclaim it.
    for name in names:
        retirement = _retirement_record_descriptor(name)
        if retirement is None:
            continue
        base_name, identity, _placeholder_identity = retirement
        encoded = _receipt_deletion_quarantine_identity(receipt_path, base_name)
        if encoded is None:
            encoded = _receipt_deletion_quarantine_identity(
                receipt_path, base_name, suffix=".pin"
            )
        if encoded is None or encoded[:2] != identity:
            continue
        try:
            exact = os.stat(
                name, dir_fd=binding.directory_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(exact.st_mode) or (
            exact.st_dev,
            exact.st_ino,
        ) != identity:
            continue
        terminal = _read_opencode_receipt(
            receipt_path.parent / name,
            repository_root,
            config_dir,
            state_home,
        )
        if terminal is None or terminal.lineage is None:
            continue
        current_phase, pending_phase = _receipt_deletion_phase(terminal)
        if (current_phase, pending_phase) != (encoded[3], encoded[4]):
            continue
        expected_quarantine = _receipt_deletion_quarantine_path(
            receipt_path,
            *identity,
            terminal.lineage,
            current_phase,
            pending_phase,
        )
        expected_pin = _receipt_deletion_pin_path(
            receipt_path,
            *identity,
            terminal.lineage,
            current_phase,
            pending_phase,
        )
        if base_name not in {expected_quarantine.name, expected_pin.name}:
            raise InstallError("receipt deletion token is not lineage-bound")
        _unlink_exact_leaf_via_exchange(
            binding.directory_fd,
            base_name,
            identity,
            "receipt retirement",
        )
        os.fsync(binding.directory_fd)
    names = tuple(os.listdir(binding.directory_fd))
    for name in names:
        encoded = _receipt_deletion_quarantine_identity(receipt_path, name)
        if encoded is None:
            encoded = _receipt_deletion_quarantine_identity(
                receipt_path, name, suffix=".pin"
            )
        if encoded is None:
            continue
        identity = encoded[:2]
        encoded_token, encoded_current_phase, encoded_pending_phase = encoded[2:]
        try:
            metadata = os.stat(
                name, dir_fd=binding.directory_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != identity
        ):
            continue
        source = receipt_path.parent / name
        try:
            terminal = _read_opencode_receipt(
                source, repository_root, config_dir, state_home
            )
        except InstallError:
            continue
        if terminal is None or terminal.lineage is None:
            continue
        current_phase, pending_phase = _receipt_deletion_phase(terminal)
        if (
            current_phase != encoded_current_phase
            or pending_phase != encoded_pending_phase
        ):
            continue
        quarantine = _receipt_deletion_quarantine_path(
            receipt_path,
            *identity,
            terminal.lineage,
            current_phase,
            pending_phase,
        )
        pin = _receipt_deletion_pin_path(
            receipt_path,
            *identity,
            terminal.lineage,
            current_phase,
            pending_phase,
        )
        if source not in {quarantine, pin}:
            raise InstallError("receipt deletion token is not lineage-bound")
        source_fd = -1
        try:
            # The retry's first barrier makes a preceding rename durable even
            # when the process stopped before the original caller could fsync.
            os.fsync(binding.directory_fd)
            source_fd = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=binding.directory_fd,
            )
            source_opened = os.fstat(source_fd)
            exact = os.stat(name, dir_fd=binding.directory_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(exact.st_mode)
                or (exact.st_dev, exact.st_ino) != identity
                or not stat.S_ISREG(source_opened.st_mode)
                or (source_opened.st_dev, source_opened.st_ino) != identity
            ):
                continue
            pinned = None
            try:
                pinned = os.stat(pin.name, dir_fd=binding.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            if pinned is None:
                _link_open_descriptor(source_fd, binding.directory_fd, pin.name)
                pinned = os.stat(
                    pin.name, dir_fd=binding.directory_fd, follow_symlinks=False
                )
                if (
                    not stat.S_ISREG(pinned.st_mode)
                    or (pinned.st_dev, pinned.st_ino) != identity
                ):
                    os.fsync(binding.directory_fd)
                    raise InstallError(
                        f"receipt quarantine identity changed while pinning: {quarantine}"
                    )
                os.fsync(binding.directory_fd)
            elif (
                not stat.S_ISREG(pinned.st_mode)
                or (pinned.st_dev, pinned.st_ino) != identity
            ):
                continue

            canonical = None
            try:
                canonical = os.stat(
                    receipt_path.name,
                    dir_fd=binding.directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            if canonical is not None and (
                not stat.S_ISREG(canonical.st_mode)
                or (canonical.st_dev, canonical.st_ino) != identity
            ):
                raise InstallError(
                    f"canonical receipt is occupied during deletion recovery: {receipt_path}"
                )
            if canonical is None:
                quarantined = None
                try:
                    quarantined = os.stat(
                        quarantine.name,
                        dir_fd=binding.directory_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                if quarantined is None:
                    _link_open_descriptor(
                        source_fd, binding.directory_fd, receipt_path.name
                    )
                elif (
                    stat.S_ISREG(quarantined.st_mode)
                    and (quarantined.st_dev, quarantined.st_ino) == identity
                ):
                    _renameat_noreplace(
                        binding.directory_fd,
                        quarantine.name,
                        binding.directory_fd,
                        receipt_path.name,
                    )
                    restored = os.stat(
                        receipt_path.name,
                        dir_fd=binding.directory_fd,
                        follow_symlinks=False,
                    )
                    if (restored.st_dev, restored.st_ino) != identity:
                        try:
                            _renameat_noreplace(
                                binding.directory_fd,
                                receipt_path.name,
                                binding.directory_fd,
                                quarantine.name,
                            )
                            os.fsync(binding.directory_fd)
                        except OSError:
                            pass
                        raise InstallError(
                            f"receipt quarantine identity changed during recovery: {quarantine}"
                        )
                else:
                    if (
                        current_phase == "anchor-removed"
                        and pending_phase == "none"
                    ):
                        # The exact terminal receipt remains independently
                        # pinned. Its canonical name is durably absent, so a
                        # foreign object at the old quarantine pathname is no
                        # deletion authority and must simply survive.
                        _unlink_exact_leaf_via_exchange(
                            binding.directory_fd,
                            pin.name,
                            identity,
                            "receipt deletion pin",
                        )
                        os.fsync(binding.directory_fd)
                        binding.validated_leaves.pop(pin.name, None)
                        _verify_state_binding(binding)
                        continue
                    raise InstallError(
                        f"receipt quarantine is occupied during recovery: {quarantine}"
                    )
                os.fsync(binding.directory_fd)
            binding.validated_leaves[receipt_path.name] = identity
            _unlink_state_path(receipt_path)
        except OSError as error:
            raise InstallError(
                f"cannot recover receipt deletion quarantine: {quarantine}: {error}"
            ) from error
        finally:
            if source_fd >= 0:
                os.close(source_fd)
        binding.validated_leaves.pop(receipt_path.name, None)
        binding.validated_leaves.pop(quarantine.name, None)
        binding.validated_leaves.pop(pin.name, None)
        _verify_state_binding(binding)
    # Recovery also supplies an absence-confirmation barrier when the exact
    # quarantine disappeared after its durable unlink.
    os.fsync(binding.directory_fd)
    _verify_state_binding(binding)


def _recover_receipt_deletion(
    repository_root: Path,
    config_dir: Path,
    state_home: Path,
    receipt_path: Path,
) -> None:
    """Recover authenticated receipt sidecars before either entrypoint reads state."""

    binding = _state_binding(receipt_path)
    if binding is None:
        return
    _verify_state_binding(binding)
    # Accept recovery records written by the immediately preceding release.
    # New deletions never create them, but an upgrade must not strand an exact
    # receipt that was already moved into the old closed protocol.
    _recover_receipt_deletion_legacy(
        repository_root, config_dir, state_home, receipt_path
    )
    prefix = f".{receipt_path.name}."
    for name in tuple(os.listdir(binding.directory_fd)):
        if not name.startswith(prefix) or not name.endswith(".journal"):
            continue
        sidecar_path = receipt_path.parent / name
        metadata = _state_metadata(binding, name)
        if metadata is None:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            # A foreign sidecar replacement cannot become deletion authority.
            continue
        try:
            value = json.loads(_read_state_text(sidecar_path))
            payload = _receipt_retirement_from_payload(value, sidecar_path)
        except (OSError, json.JSONDecodeError, InstallError) as error:
            # Every matching journal pathname is in the closed private
            # namespace.  Treat malformed or unauthenticated bytes as a
            # tamper/replacement failure; silently skipping them would leave
            # the canonical receipt permanently stranded behind an orphan.
            raise InstallError(
                f"receipt retirement sidecar is malformed: {sidecar_path}"
            ) from error
        if Path(str(payload["source"])) != _lexical_absolute(receipt_path):
            raise InstallError(
                f"receipt retirement sidecar names another source: {sidecar_path}"
            )
        canonical = _state_metadata(binding, receipt_path.name)
        expected = (
            int(payload["expected_dev"]),
            int(payload["expected_ino"]),
        )
        if canonical is not None and (
            canonical.st_dev,
            canonical.st_ino,
        ) == expected:
            try:
                canonical_payload = json.loads(_read_state_text(receipt_path))
            except (OSError, json.JSONDecodeError) as error:
                raise InstallError(
                    f"cannot authenticate receipt retirement sidecar: {sidecar_path}"
                ) from error
            if (
                not isinstance(canonical_payload, dict)
                or canonical_payload.get("receipt_secret") != payload["secret"]
            ):
                raise InstallError(
                    f"receipt retirement sidecar lacks receipt authority: {sidecar_path}"
                )
        # Sidecar phase writes may have left a previous generation at the
        # deterministic stage name.  It is safe to clear only a regular file
        # that is not the live sidecar inode; a foreign replacement survives.
        stage_name = f".{name}.stage"
        stage = _state_metadata(binding, stage_name)
        if stage is not None and stat.S_ISREG(stage.st_mode):
            if (stage.st_dev, stage.st_ino) != (metadata.st_dev, metadata.st_ino):
                stage_path = receipt_path.parent / stage_name
                try:
                    stage_value = json.loads(_read_state_text(stage_path))
                    stage_payload = _receipt_retirement_from_payload(
                        stage_value, sidecar_path
                    )
                except (OSError, json.JSONDecodeError, InstallError) as error:
                    raise InstallError(
                        f"receipt retirement sidecar stage is malformed: {stage_path}"
                    ) from error
                if any(
                    stage_payload[key] != payload[key]
                    for key in ("source", "private", "secret", "sidecar", "token")
                ):
                    raise InstallError(
                        f"receipt retirement sidecar stage belongs to another record: {stage_path}"
                    )
                _unlink_private_state_inode(
                    binding,
                    stage_name,
                    (stage.st_dev, stage.st_ino),
                    "receipt retirement sidecar generation",
                )
        _resume_receipt_retirement(sidecar_path, payload)
    os.fsync(binding.directory_fd)
    _verify_state_binding(binding)


def _prune_opencode_retired_links(
    receipt: _Receipt,
    current_links: Sequence[ProfileLink],
    config_dir: Path,
) -> tuple[tuple[ProfileLink, ...], tuple[ProfileLink, ...]]:
    """Drop receipt links no longer emitted by the rebuilt artifact."""

    current_destinations = {link.destination for link in current_links}
    retained: list[ProfileLink] = []
    removed: list[ProfileLink] = []
    for link in receipt.links:
        if link.destination in current_destinations:
            retained.append(link)
            continue
        has_identity = (
            link.destination_dev is not None and link.destination_ino is not None
        )
        if not has_identity:
            # Receipt text without a frozen inode grants no deletion authority.
            continue
        assert link.destination_dev is not None and link.destination_ino is not None
        try:
            removed_exact = _unlink_recorded_destination(link)
        except (OSError, InstallError) as error:
            raise InstallError(
                f"cannot remove retired OpenCode link: {link.destination}: {error}"
            ) from error
        if removed_exact:
            removed.append(link)
    return tuple(retained), tuple(removed)


def _anchor_committed_artifact_for_uninstall(
    receipt_path: Path,
    receipt: _Receipt,
    artifact: Path,
    artifact_identity: tuple[int, int],
) -> _Receipt:
    """Persist exact artifact ownership before committed links are removed."""

    if receipt.pending_publish is not None:
        return receipt
    if receipt.lineage is None:
        raise InstallError("committed OpenCode artifact lacks an ownership lineage")
    anchor = _create_artifact_anchor(artifact, receipt.lineage)
    pending = _PendingPublish(
        lineage=receipt.lineage,
        artifact=artifact,
        candidate=artifact.parent / f".{artifact.name}.next-{receipt.lineage}",
        candidate_dev=artifact_identity[0],
        candidate_ino=artifact_identity[1],
        phase="published",
        candidate_digest=receipt.artifact_digest,
        candidate_anchor=anchor[0],
        candidate_anchor_dev=anchor[1],
        candidate_anchor_ino=anchor[2],
        planned_links=True,
    )
    anchored = _receipt_with_pending_publish(receipt, pending)
    try:
        _write_receipt(receipt_path, anchored)
    except InstallError:
        try:
            _remove_artifact_anchor_exact(artifact, *anchor)
        except InstallError:
            pass
        raise
    return anchored


def _uninstall_legacy_opencode_receipt(
    receipt_path: Path, receipt: _Receipt
) -> tuple[ProfileLink, ...]:
    """Retire the closed historical roster after freezing every live inode."""

    frozen: list[ProfileLink] = []
    for link in receipt.links:
        if link.destination_dev is not None and link.destination_ino is not None:
            frozen.append(link)
        elif _same_recorded_link(link.destination, link.source):
            frozen.append(_capture_opencode_link_identity(link))
        else:
            # Missing and retargeted legacy names grant no deletion authority.
            frozen.append(link)
    current = replace(
        receipt,
        links=tuple(frozen),
        lineage=receipt.lineage or uuid.uuid4().hex,
    )
    _write_receipt(receipt_path, current)
    removed: list[ProfileLink] = []
    failures: list[str] = []
    for link in current.links:
        if link.destination_dev is None or link.destination_ino is None:
            continue
        try:
            if _unlink_recorded_destination(link):
                removed.append(link)
        except (OSError, InstallError) as error:
            failures.append(f"link {link.destination}: {error}")
    if failures:
        raise InstallError(
            "legacy OpenCode link cleanup failed: " + "; ".join(failures)
        )
    _unlink_state_path(receipt_path)
    return tuple(removed)


def uninstall_opencode(
    repo_root: Path,
    config_dir: Path,
    state_home: Path,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    canonical_config = _canonical_opencode_config(config_dir)
    receipt_path_value = _opencode_receipt_path(state_home)
    try:
        binding = _open_state_binding(receipt_path_value.parent, create=False)
    except OSError as error:
        raise InstallError(
            f"cannot bind OpenCode state directory: {receipt_path_value.parent}: {error}"
        ) from error
    if binding is None:
        return InstallResult()
    key = str(binding.directory)
    if key in _STATE_BINDINGS:
        _close_state_binding(binding)
        raise InstallError(f"OpenCode state directory is already active: {binding.directory}")
    _STATE_BINDINGS[key] = binding
    try:
        config_binding = _open_config_binding(canonical_config, create=True)
    except OSError as error:
        _STATE_BINDINGS.pop(key, None)
        _close_state_binding(binding)
        raise InstallError(
            f"cannot bind OpenCode config directory: {canonical_config}: {error}"
        ) from error
    if config_binding is None:
        _STATE_BINDINGS.pop(key, None)
        _close_state_binding(binding)
        raise InstallError(f"cannot create OpenCode config directory: {canonical_config}")
    config_key = str(config_binding.directory)
    if config_key in _CONFIG_BINDINGS:
        _STATE_BINDINGS.pop(key, None)
        _close_state_binding(binding)
        _close_config_binding(config_binding)
        raise InstallError(
            f"OpenCode config directory is already active: {config_binding.directory}"
        )
    _CONFIG_BINDINGS[config_key] = config_binding
    try:
        return _uninstall_opencode_bound(
            canonical_root, canonical_config, state_home, receipt_path_value
        )
    finally:
        _CONFIG_BINDINGS.pop(config_key, None)
        _close_config_binding(config_binding)
        _STATE_BINDINGS.pop(key, None)
        _close_state_binding(binding)


def _uninstall_opencode_bound(
    canonical_root: Path,
    config_dir: Path,
    state_home: Path,
    receipt_path_value: Path,
) -> InstallResult:
    _recover_receipt_deletion(
        canonical_root, config_dir, state_home, receipt_path_value
    )
    if not _lexists(receipt_path_value):
        return InstallResult()
    receipt = _read_opencode_receipt(
        receipt_path_value, canonical_root, config_dir, state_home
    )
    if receipt is None:
        return InstallResult()
    if receipt.pending_retirement is not None:
        _recover_pending_retirement(receipt_path_value, receipt)
        receipt = _read_opencode_receipt(
            receipt_path_value, canonical_root, config_dir, state_home
        )
        if receipt is None:
            raise InstallError(
                f"receipt disappeared after retirement recovery: {receipt_path_value}"
            )
    if receipt.artifact_root is None:
        links = receipt.links
        removed = _uninstall_legacy_opencode_receipt(receipt_path_value, receipt)
        return InstallResult(
            links=links,
            removed_links=removed,
            marketplace_added=False,
            plugin_installed=True,
        )
    receipt, recovered_initial = _recover_pending_publish(
        canonical_root, state_home, receipt
    )
    receipt = _recover_pending_swap(canonical_root, state_home, receipt)
    receipt = _garbage_collect_opencode_backups(
        state_home, receipt, canonical_root
    )
    if receipt is None:
        return InstallResult()
    for link in receipt.links:
        _remove_recorded_opencode_staging(link)
    if receipt.pending_swap is not None or (
        receipt.pending_publish is not None and not recovered_initial
    ):
        raise InstallError(
            "OpenCode interrupted publication could not be proven safe to recover"
        )
    if recovered_initial:
        pending_publish = receipt.pending_publish
        if pending_publish is None:
            raise InstallError("recovered OpenCode publication lost its ownership proof")
        artifact = _fixed_opencode_artifact(state_home, receipt.artifact_root)
        if not _pending_identity(
            artifact,
            pending_publish.candidate_dev,
            pending_publish.candidate_ino,
        ) or not _artifact_anchor_matches(
            artifact,
            pending_publish.candidate_anchor,
            pending_publish.candidate_anchor_dev,
            pending_publish.candidate_anchor_ino,
        ):
            raise InstallError("recovered OpenCode artifact lost its frozen identity")
        receipt = replace(
            receipt,
            artifact_dev=pending_publish.candidate_dev,
            artifact_ino=pending_publish.candidate_ino,
            artifact_digest=pending_publish.candidate_digest,
            artifact_anchor=pending_publish.candidate_anchor,
            artifact_anchor_dev=pending_publish.candidate_anchor_dev,
            artifact_anchor_ino=pending_publish.candidate_anchor_ino,
            teardown_phase="committed",
            pending_publish=None,
        )
        _write_receipt(receipt_path_value, receipt)
    else:
        receipt = _migrate_artifact_identity(
            canonical_root, state_home, receipt_path_value, receipt
        )
    links = receipt.links
    removed = _resume_opencode_teardown(
        receipt_path_value, receipt, state_home
    )
    return InstallResult(
        links=links,
        removed_links=removed,
        marketplace_added=False,
        plugin_installed=True,
    )


def _resume_opencode_teardown(
    receipt_path_value: Path,
    receipt: _Receipt,
    state_home: Path,
) -> tuple[ProfileLink, ...]:
    """Advance the closed teardown state machine using only frozen identities."""

    if receipt.artifact_root is None:
        raise InstallError("OpenCode teardown receipt lacks an artifact")
    current = receipt
    if current.teardown_phase == "committed":
        frozen_links: list[ProfileLink] = []
        for link in current.links:
            if link.destination_dev is not None and link.destination_ino is not None:
                frozen_links.append(link)
            else:
                # Planned-but-uncommitted, missing, and foreign destinations
                # have no deletion authority.
                frozen_links.append(link)
        current = replace(
            current,
            links=tuple(frozen_links),
            teardown_phase="removing-links",
        )
        # Intent and the full immutable inventory are durable before unlink(2).
        _write_receipt(receipt_path_value, current)

    removed: list[ProfileLink] = []
    if current.teardown_phase == "removing-links":
        failures: list[str] = []
        for link in current.links:
            try:
                _remove_recorded_opencode_staging(link)
            except (OSError, InstallError) as error:
                raise InstallError(
                    f"owned opencode staged-link cleanup failed: {error}"
                ) from error
            if link.destination_dev is None or link.destination_ino is None:
                continue
            try:
                # Preserve the target-and-inode proof seam before exact
                # descriptor-bound deletion.  Recovery does not depend on the
                # result because the inode may already be quarantined.
                _recorded_opencode_link_is_live(link)
                removed_exact = _unlink_recorded_destination(link)
            except (OSError, InstallError) as error:
                raise InstallError(
                    f"owned opencode link cleanup failed: {error}"
                ) from error
            if removed_exact:
                removed.append(link)
        if failures:
            raise InstallError(
                "owned opencode link cleanup failed: " + "; ".join(failures)
            )

        artifact = _fixed_opencode_artifact(state_home, current.artifact_root)
        if current.artifact_dev is None or current.artifact_ino is None:
            raise InstallError("OpenCode teardown lacks a frozen artifact identity")
        if _pending_identity(artifact, current.artifact_dev, current.artifact_ino):
            if not _artifact_anchor_identity_is_live(
                current.artifact_anchor,
                current.artifact_anchor_dev,
                current.artifact_anchor_ino,
            ):
                raise InstallError(
                    "OpenCode teardown lost its frozen standalone anchor"
                )
            # ``removing-links`` is already a durable destructive boundary.
            # A prior attempt may have removed package.json before failing, so
            # the exact directory identity is now the complete authority for
            # finishing directory removal.  The standalone anchor has its own
            # independent identity and is retired in the next phase.
            _remove_opencode_artifact_exact(
                artifact, current.artifact_dev, current.artifact_ino
            )
        # Missing and foreign replacements mean the frozen pathname no longer
        # names our inode.  They are preserved and ownership at that name is done.
        current = replace(current, teardown_phase="artifact-removed")
        _write_receipt(receipt_path_value, current)

    if current.teardown_phase == "artifact-removed":
        if (
            current.artifact_anchor is None
            or current.artifact_anchor_dev is None
            or current.artifact_anchor_ino is None
        ):
            raise InstallError("OpenCode teardown lacks a frozen anchor identity")
        _unlink_artifact_anchor_identity(
            current.artifact_anchor,
            current.artifact_anchor_dev,
            current.artifact_anchor_ino,
        )
        # A foreign pathname replacement is intentionally preserved.
        current = replace(current, teardown_phase="anchor-removed")
        _write_receipt(receipt_path_value, current)

    if current.teardown_phase != "anchor-removed":
        raise InstallError("OpenCode teardown phase is not recoverable")
    if not stat.S_ISREG(_state_lstat(receipt_path_value).st_mode):
        raise InstallError(f"receipt path is not a regular file: {receipt_path_value}")
    _unlink_state_path(receipt_path_value)
    return tuple(removed)


def _print_opencode_dry_run(
    repo_root: Path, config_dir: Path, state_home: Path | None = None
) -> None:
    receipt_state_home = _default_state_home() if state_home is None else state_home
    # A dry run must not adopt or rebuild receipt-owned state.  Build the
    # planned artifact in the preflight temporary directory instead.
    existing_receipt: _Receipt | None = None
    receipt_path = _opencode_receipt_path(receipt_state_home)
    if state_home is not None and _lexists(receipt_path):
        existing_receipt = _read_opencode_receipt(
            receipt_path,
            _canonical_repository_root(repo_root),
            _canonical_opencode_config(config_dir),
            receipt_state_home,
        )
    links = preflight_opencode_links(
        repo_root,
        config_dir,
        receipt_state_home,
        legacy_receipt=existing_receipt,
    )
    for link in links:
        print(f"link {link.destination} -> {link.source}")
    print(f"opencode {OPENCODE_PACKAGE_NAME} receipt {_opencode_receipt_path(receipt_state_home)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the expskill marketplace and profiles.")
    parser.add_argument("--target", choices=("codex", "opencode"), default="codex")
    parser.add_argument(
        "--agents-only",
        action="store_true",
        help="codex target only: link agent profiles without touching plugin CLI state",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    arguments = parser.parse_args(argv)
    repository_root = Path(__file__).absolute().parents[1]
    state_home = _default_state_home()
    try:
        if arguments.target == "opencode":
            config_dir = _default_opencode_config_dir()
            if arguments.dry_run:
                _print_opencode_dry_run(repository_root, config_dir, state_home)
            elif arguments.uninstall:
                uninstall_opencode(repository_root, config_dir, state_home)
            else:
                install_opencode(repository_root, config_dir, state_home)
            return 0
        codex_home = _default_codex_home()
        if arguments.agents_only and arguments.target != "codex":
            print("install error: --agents-only applies to the codex target only", file=sys.stderr)
            return 1
        if arguments.dry_run:
            _print_dry_run(repository_root, codex_home, arguments.agents_only)
        elif arguments.uninstall:
            uninstall(
                repository_root, codex_home, state_home, _subprocess_runner, arguments.agents_only
            )
        else:
            install(
                repository_root, codex_home, state_home, _subprocess_runner, arguments.agents_only
            )
    except InstallError as error:
        print(f"install error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
