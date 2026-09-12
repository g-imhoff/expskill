from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
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
    }
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


def _receipt_deletion_descriptor(path: Path) -> tuple[str, str, str]:
    """Parse the receipt fields required to create its deletion capability."""

    if path.name != OPENCODE_RECEIPT_FILENAME:
        raise InstallError(f"unsupported receipt deletion path: {path}")
    try:
        payload = json.loads(_read_state_text(path))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {path}")
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


def _unlink_state_path(path: Path) -> None:
    binding = _state_binding(path)
    if binding is None:
        path.unlink()
        return
    _verify_state_binding(binding)
    expected = binding.validated_leaves.get(path.name)
    if expected is None:
        metadata = os.stat(
            path.name, dir_fd=binding.directory_fd, follow_symlinks=False
        )
        expected = (metadata.st_dev, metadata.st_ino)
    lineage, current_phase, pending_phase = _receipt_deletion_descriptor(path)
    quarantine_path = _receipt_deletion_quarantine_path(
        path,
        *expected,
        lineage,
        current_phase,
        pending_phase,
    )
    quarantine = quarantine_path.name

    def metadata(name: str) -> os.stat_result | None:
        try:
            return os.stat(
                name, dir_fd=binding.directory_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            return None

    try:
        original = metadata(path.name)
        quarantined = metadata(quarantine)
        original_is_exact = original is not None and (
            original.st_dev,
            original.st_ino,
        ) == expected
        quarantine_is_exact = quarantined is not None and (
            quarantined.st_dev,
            quarantined.st_ino,
        ) == expected
        if original is not None and (
            not original_is_exact or not stat.S_ISREG(original.st_mode)
        ):
            raise InstallError(
                f"receipt path identity changed before deletion: {path}"
            )
        if quarantine_is_exact and not stat.S_ISREG(quarantined.st_mode):
            raise InstallError(
                f"receipt quarantine identity changed before deletion: {quarantine_path}"
            )
        if quarantined is not None and not quarantine_is_exact:
            raise InstallError(
                f"receipt quarantine is occupied by a foreign object: {quarantine_path}"
            )
        if original_is_exact and quarantine_is_exact:
            # An authenticated hard-link alias still does not permit bypassing
            # the rename boundary by directly unlinking the canonical name.
            # Retire the redundant alias first, then run the same
            # canonical->quarantine protocol as every other deletion.
            os.unlink(quarantine, dir_fd=binding.directory_fd)
            os.fsync(binding.directory_fd)
            if metadata(quarantine) is not None:
                raise InstallError(
                    f"receipt quarantine was replaced during deletion: {quarantine_path}"
                )
            quarantined = None
            quarantine_is_exact = False
        if original_is_exact and quarantined is None:
            _renameat_noreplace(
                binding.directory_fd,
                path.name,
                binding.directory_fd,
                quarantine,
            )
            os.fsync(binding.directory_fd)
            moved = metadata(quarantine)
            if (
                moved is None
                or not stat.S_ISREG(moved.st_mode)
                or (moved.st_dev, moved.st_ino) != expected
            ):
                raise InstallError(
                    f"receipt path identity changed before deletion: {path}"
                )
            quarantine_is_exact = True
            original_is_exact = False
        if quarantine_is_exact:
            os.unlink(quarantine, dir_fd=binding.directory_fd)
            os.fsync(binding.directory_fd)
            if metadata(quarantine) is not None:
                raise InstallError(
                    f"receipt quarantine was replaced during deletion: {quarantine_path}"
                )
        # A retry that observes absence still supplies the durability barrier
        # for the preceding rename/unlink attempt.
        os.fsync(binding.directory_fd)
    except OSError as error:
        raise InstallError(f"cannot conditionally remove receipt: {path}: {error}") from error
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


def _deletion_quarantine_path(path: Path, dev: int, ino: int) -> Path:
    """Return a receipt-recoverable deletion name for one exact inode."""

    return path.parent / f".{path.name}.{dev:x}-{ino:x}.delete"


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
            if staged_destination != _opencode_link_staging_path(
                canonical_source, lexical_destination
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


def _write_state_payload(path: Path, payload: Mapping[str, object]) -> bool:
    """Write through the retained state descriptor; return false if unbound."""

    binding = _state_binding(path)
    if binding is None:
        return False
    _verify_state_binding(binding)
    current_identity: tuple[int, int] | None = None
    try:
        current = os.stat(path.name, dir_fd=binding.directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(current.st_mode):
            raise InstallError(f"receipt path is not a regular file: {path}")
        current_identity = (current.st_dev, current.st_ino)
        validated = binding.validated_leaves.get(path.name)
        if validated is not None and current_identity != validated:
            raise InstallError(f"receipt path identity changed before write: {path}")
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    published = False
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=binding.directory_fd,
        )
        encoded = (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode()
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        temporary_metadata = os.fstat(descriptor)
        temporary_identity = (temporary_metadata.st_dev, temporary_metadata.st_ino)
        os.close(descriptor)
        descriptor = -1
        if current_identity is None:
            _renameat_noreplace(
                binding.directory_fd,
                temporary_name,
                binding.directory_fd,
                path.name,
            )
        else:
            _renameat_exchange(
                binding.directory_fd,
                temporary_name,
                binding.directory_fd,
                path.name,
            )
            displaced = os.stat(
                temporary_name,
                dir_fd=binding.directory_fd,
                follow_symlinks=False,
            )
            if (displaced.st_dev, displaced.st_ino) != current_identity:
                try:
                    _renameat_exchange(
                        binding.directory_fd,
                        temporary_name,
                        binding.directory_fd,
                        path.name,
                    )
                except (OSError, InstallError) as reverse_error:
                    # After the first exchange the installer-created inode is
                    # live and the foreign replacement occupies our temporary
                    # name.  If exchange itself cannot put them back, rotate
                    # only the exact installer inode out of the way, then
                    # restore the displaced foreign inode without overwrite.
                    live = os.stat(
                        path.name,
                        dir_fd=binding.directory_fd,
                        follow_symlinks=False,
                    )
                    if (live.st_dev, live.st_ino) != temporary_identity:
                        raise InstallError(
                            f"receipt recovery found an unproven live object: {path}"
                        ) from reverse_error
                    recovery_name = f".{path.name}.{uuid.uuid4().hex}.rollback"
                    _renameat_noreplace(
                        binding.directory_fd,
                        path.name,
                        binding.directory_fd,
                        recovery_name,
                    )
                    try:
                        _renameat_noreplace(
                            binding.directory_fd,
                            temporary_name,
                            binding.directory_fd,
                            path.name,
                        )
                    except (OSError, InstallError) as restore_error:
                        # Both objects retain recoverable names and neither is
                        # deleted when the foreign inode cannot be restored.
                        os.fsync(binding.directory_fd)
                        raise InstallError(
                            f"receipt replacement recovery failed: {path}: {restore_error}"
                        ) from reverse_error
                    recovered = os.stat(
                        recovery_name,
                        dir_fd=binding.directory_fd,
                        follow_symlinks=False,
                    )
                    if (recovered.st_dev, recovered.st_ino) != temporary_identity:
                        raise InstallError(
                            f"receipt recovery object identity changed: {path}"
                        ) from reverse_error
                    os.unlink(recovery_name, dir_fd=binding.directory_fd)
                    os.fsync(binding.directory_fd)
                raise InstallError(f"receipt path identity changed during write: {path}")
            os.unlink(temporary_name, dir_fd=binding.directory_fd)
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
        if descriptor >= 0:
            os.close(descriptor)
        if not published and temporary_identity is not None:
            try:
                remaining = os.stat(
                    temporary_name,
                    dir_fd=binding.directory_fd,
                    follow_symlinks=False,
                )
                if (remaining.st_dev, remaining.st_ino) == temporary_identity:
                    os.unlink(temporary_name, dir_fd=binding.directory_fd)
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
            expected_staging = _opencode_link_staging_path(
                link.source, link.destination
            )
            if link.staged_destination != expected_staging:
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
    """Return only the historical parent-installer link roster."""

    canonical_root = _canonical_repository_root(repo_root)
    canonical_config = _canonical_opencode_config(config_dir)
    package_root = canonical_root / "packages" / "expskill"
    links: list[ProfileLink] = []
    for name in LEGACY_OPENCODE_SKILLS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "skills" / name),
                destination=canonical_config / "skills" / name,
            )
        )
    for name in LEGACY_OPENCODE_SKILLS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "opencode" / "commands" / f"{name}.md"),
                destination=canonical_config / "commands" / f"{name}.md",
            )
        )
    for name in LEGACY_OPENCODE_AGENTS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "opencode" / "agents" / f"{name}.md"),
                destination=canonical_config / "agents" / f"{name}.md",
            )
        )
    for name in LEGACY_OPENCODE_PLUGINS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "opencode" / "plugins" / name),
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
        os.symlink(os.fspath(source), temporary_name, dir_fd=parent_fd)
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
        os.fsync(parent_fd)
        if record_staged is not None:
            record_staged(
                temporary_path,
                temporary_identity[0],
                temporary_identity[1],
            )
            journaled = True
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
        if not journaled and not published and temporary_identity is not None:
            try:
                remaining = os.stat(
                    temporary_name, dir_fd=parent_fd, follow_symlinks=False
                )
                if (remaining.st_dev, remaining.st_ino) == temporary_identity:
                    os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
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
    # A quarantine match is independent filesystem evidence for the exact
    # recorded symlink; prime the original key used by the stable unlink seam.
    binding.validated_leaves[_lexical_absolute(destination)] = identity
    return _unlink_destination(destination)


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
            os.unlink(quarantine, dir_fd=parent_fd)
            removed = True
            if original_is_exact:
                os.unlink(destination.name, dir_fd=parent_fd)
        elif quarantined is not None:
            if original_is_exact:
                # The collision is foreign and survives.  The independently
                # matched original can still be unlinked without reusing the
                # occupied quarantine name.
                os.unlink(destination.name, dir_fd=parent_fd)
                removed = True
        elif original_is_exact:
            _renameat_noreplace(
                parent_fd, destination.name, parent_fd, quarantine
            )
            moved = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
            if (moved.st_dev, moved.st_ino) != expected:
                raise InstallError(
                    f"OpenCode config destination identity changed: {destination}"
                )
            os.unlink(quarantine, dir_fd=parent_fd)
            removed = True
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
            raise InstallError(f"opencode artifact is not a regular directory: {expected}")
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
    """Remove an opened exact directory without reopening a replaced name."""

    parent_fd: int | None = None
    target_fd: int | None = None
    try:
        binding = _state_binding(path)
        if binding is not None:
            _verify_state_binding(binding)
            parent_fd = os.dup(binding.directory_fd)
        else:
            parent_fd = os.open(
                path.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        target_fd = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(target_fd)
        if opened.st_dev != dev or opened.st_ino != ino:
            raise InstallError(f"OpenCode artifact identity changed: {path}")

        def clear_directory(directory_fd: int) -> None:
            for entry in list(os.scandir(directory_fd)):
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                    child_fd = os.open(
                        entry.name,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    try:
                        child_opened = os.fstat(child_fd)
                        if (
                            child_opened.st_dev != metadata.st_dev
                            or child_opened.st_ino != metadata.st_ino
                        ):
                            continue
                        clear_directory(child_fd)
                    finally:
                        os.close(child_fd)
                    try:
                        current = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                        if (
                            current.st_dev == metadata.st_dev
                            and current.st_ino == metadata.st_ino
                        ):
                            os.rmdir(entry.name, dir_fd=directory_fd)
                    except OSError:
                        pass
                elif stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    try:
                        current = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                        if (
                            current.st_dev == metadata.st_dev
                            and current.st_ino == metadata.st_ino
                        ):
                            os.unlink(entry.name, dir_fd=directory_fd)
                    except OSError:
                        pass

        clear_directory(target_fd)
        # If the lexical name was replaced while contents were being cleared,
        # leave the replacement untouched.  The opened candidate is already
        # detached from that foreign name.
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if current.st_dev != dev or current.st_ino != ino:
            raise InstallError(f"OpenCode artifact identity changed: {path}")
        os.rmdir(path.name, dir_fd=parent_fd)
        if binding is not None:
            os.fsync(parent_fd)
            _verify_state_binding(binding)
    except FileNotFoundError:
        return
    except OSError as error:
        raise InstallError(f"cannot remove exact OpenCode artifact: {path}: {error}") from error
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _pending_identity(path: Path, dev: int, ino: int) -> bool:
    try:
        metadata = _state_lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_dev == dev
        and metadata.st_ino == ino
    )


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
                current = os.stat(
                    anchor.name,
                    dir_fd=binding.directory_fd,
                    follow_symlinks=False,
                )
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(anchor.name, dir_fd=binding.directory_fd)
                    os.fsync(binding.directory_fd)
            except OSError:
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


def _unlink_artifact_anchor_identity(anchor: Path, anchor_dev: int, anchor_ino: int) -> None:
    """Durably unlink or confirm absence of one receipt-recorded anchor."""

    binding = _state_binding(anchor)
    if binding is None:
        raise InstallError("OpenCode artifact anchor is outside bound owned state")
    expected = (anchor_dev, anchor_ino)
    quarantine = _deletion_quarantine_path(anchor, *expected).name

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
        quarantined = metadata(quarantine)
        original = metadata(anchor.name)
        quarantine_is_exact = quarantined is not None and (
            quarantined.st_dev,
            quarantined.st_ino,
        ) == expected
        original_is_exact = original is not None and (
            original.st_dev,
            original.st_ino,
        ) == expected
        if quarantine_is_exact:
            os.unlink(quarantine, dir_fd=binding.directory_fd)
            if original_is_exact:
                os.unlink(anchor.name, dir_fd=binding.directory_fd)
        elif quarantined is not None:
            if original_is_exact:
                os.unlink(anchor.name, dir_fd=binding.directory_fd)
        elif original_is_exact:
            _renameat_noreplace(
                binding.directory_fd,
                anchor.name,
                binding.directory_fd,
                quarantine,
            )
            moved = os.stat(
                quarantine,
                dir_fd=binding.directory_fd,
                follow_symlinks=False,
            )
            if (moved.st_dev, moved.st_ino) != expected:
                raise InstallError(
                    "OpenCode artifact anchor identity changed during cleanup"
                )
            os.unlink(quarantine, dir_fd=binding.directory_fd)
        # A retry that sees neither exact name still performs the durability
        # barrier whose earlier attempt may have failed after unlink(2).
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
    receipt_directory = receipt_path.parent
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{receipt_path.name}.", suffix=".tmp", dir=receipt_directory
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, indent=2, sort_keys=True)
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
        raise InstallError(f"cannot durably write receipt: {receipt_path}: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _recover_pending_swap(
    repo_root: Path, state_home: Path, receipt: _Receipt | None
) -> _Receipt | None:
    """Recover a pending swap only after independent filesystem validation."""

    receipt_path = _opencode_receipt_path(state_home)
    if receipt is None or receipt.pending_swap is None:
        return receipt
    pending = receipt.pending_swap
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
                _remove_opencode_artifact_exact(
                    pending.candidate, pending.candidate_dev, pending.candidate_ino
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
            _remove_opencode_artifact_exact(
                pending.candidate, pending.candidate_dev, pending.candidate_ino
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
    """Remove only an empty receipt directory created by this install."""

    binding = _STATE_BINDINGS.get(str(_lexical_absolute(receipt_directory)))
    if binding is None:
        return

    def remove_bound_empty(directory: Path) -> None:
        created = next(
            (
                item
                for item in binding.created_directories
                if item.directory == _lexical_absolute(directory)
            ),
            None,
        )
        if created is None:
            return
        try:
            opened = os.fstat(created.directory_fd)
            named = os.stat(
                created.directory.name,
                dir_fd=created.parent_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != created.identity
                or (named.st_dev, named.st_ino) != created.identity
                or os.listdir(created.directory_fd)
            ):
                return
            os.rmdir(created.directory.name, dir_fd=created.parent_fd)
            os.fsync(created.parent_fd)
        except OSError:
            pass

    if not existed_before:
        remove_bound_empty(receipt_directory)
    if not state_home_existed_before:
        remove_bound_empty(state_home)


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
            _same_recorded_link(link.destination, link.source)
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
    captured_links = tuple(_capture_opencode_link_identity(link) for link in receipt.links)
    if not captured_links or not all(
        _recorded_opencode_link_is_live(link) for link in captured_links
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
            _remove_opencode_artifact_exact(
                artifact, pending.candidate_dev, pending.candidate_ino
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
    pending = receipt.pending_publish
    anchor_owned = _artifact_anchor_matches(
        artifact,
        pending.candidate_anchor,
        pending.candidate_anchor_dev,
        pending.candidate_anchor_ino,
    )
    if not anchor_owned and pending.candidate_anchor is not None and _lexists(
        pending.candidate_anchor
    ):
        raise InstallError("OpenCode publication anchor was replaced; preserving state")
    _remove_opencode_artifact_exact(
        artifact, pending.candidate_dev, pending.candidate_ino
    )
    if anchor_owned:
        assert pending.candidate_anchor is not None
        assert pending.candidate_anchor_dev is not None
        assert pending.candidate_anchor_ino is not None
        _unlink_artifact_anchor_identity(
            pending.candidate_anchor,
            pending.candidate_anchor_dev,
            pending.candidate_anchor_ino,
        )


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
            _write_receipt(receipt_path, _receipt_with_pending(receipt_for_swap, pending))
            candidate_anchor = _create_artifact_anchor(candidate, anchor_token)
            if candidate_anchor != (
                deterministic_anchor,
                anchor_source.st_dev,
                anchor_source.st_ino,
            ):
                raise InstallError("candidate OpenCode anchor identity changed")
            pending = replace(pending, phase="anchor-recorded")
            _write_receipt(
                receipt_path, _receipt_with_pending(receipt_for_swap, pending)
            )
            if not _pending_identity(artifact, pending.live_dev, pending.live_ino):
                raise InstallError("live OpenCode artifact changed before swap")
            _rename_noreplace(artifact, backup)
            pending = _PendingSwap(**{**pending.__dict__, "phase": "backup-created"})
            _write_receipt(receipt_path, _receipt_with_pending(receipt_for_swap, pending))
            _rename_noreplace(candidate, artifact)
            if not _pending_identity(artifact, pending.candidate_dev, pending.candidate_ino):
                raise InstallError("published OpenCode candidate identity changed")
            pending = _PendingSwap(**{**pending.__dict__, "phase": "published"})
            _write_receipt(receipt_path, _receipt_with_pending(receipt_for_swap, pending))
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
        _write_receipt(receipt_path, prepublication_receipt)
        return artifact, True, None, None, prepublication_receipt
    except (OpencodeBuildError, OSError, InstallError) as error:
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
                _remove_opencode_artifact_exact(
                    artifact, pending.candidate_dev, pending.candidate_ino
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
            _restore_opencode_artifact(artifact_root, artifact_backup, artifact_pending)
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
        if artifact_created:
            try:
                _remove_initial_publication_artifact(artifact_root, receipt)
                _discard_prepublication_receipt(receipt_path_value, receipt)
            except InstallError as cleanup_error:
                error = InstallError(f"{error}; {cleanup_error}")
        elif artifact_backup is not None:
            try:
                _restore_opencode_artifact(artifact_root, artifact_backup, artifact_pending)
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
        if set(payload) != base_keys:
            raise InstallError(f"legacy OpenCode receipt is inconsistent: {receipt_path}")
        expected = _legacy_opencode_expected_links(repository_root, config_dir)
    receipt = _read_receipt(receipt_path, repository_root, expected)
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
    receipt_path: Path, name: str
) -> tuple[int, int, str, str] | None:
    prefix = f".{receipt_path.name}."
    suffix = ".delete"
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
    return dev, ino, current_phase, pending_phase


def _recover_receipt_deletion(
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
    for name in tuple(os.listdir(binding.directory_fd)):
        encoded = _receipt_deletion_quarantine_identity(receipt_path, name)
        if encoded is None:
            continue
        identity = encoded[:2]
        encoded_current_phase, encoded_pending_phase = encoded[2:]
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
        quarantine = receipt_path.parent / name
        try:
            terminal = _read_opencode_receipt(
                quarantine, repository_root, config_dir, state_home
            )
        except InstallError:
            continue
        if terminal is None or terminal.lineage is None:
            continue
        current_phase, pending_phase = _receipt_deletion_phase(terminal)
        if (
            current_phase != encoded_current_phase
            or pending_phase != encoded_pending_phase
            or quarantine
            != _receipt_deletion_quarantine_path(
                receipt_path,
                *identity,
                terminal.lineage,
                current_phase,
                pending_phase,
            )
        ):
            continue
        try:
            # The retry's first barrier makes a preceding rename durable even
            # when the process stopped before the original caller could fsync.
            os.fsync(binding.directory_fd)
            exact = os.stat(
                name, dir_fd=binding.directory_fd, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(exact.st_mode)
                or (exact.st_dev, exact.st_ino) != identity
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
                canonical.st_dev,
                canonical.st_ino,
            ) == identity:
                if not stat.S_ISREG(canonical.st_mode):
                    continue
                os.unlink(receipt_path.name, dir_fd=binding.directory_fd)
                os.fsync(binding.directory_fd)
            os.unlink(name, dir_fd=binding.directory_fd)
            os.fsync(binding.directory_fd)
            try:
                os.stat(name, dir_fd=binding.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise InstallError(
                    f"receipt quarantine was replaced during recovery: {quarantine}"
                )
            # A separate barrier records the confirmed absence before this
            # entrypoint creates or removes any new canonical receipt state.
            os.fsync(binding.directory_fd)
        except OSError as error:
            raise InstallError(
                f"cannot recover receipt deletion quarantine: {quarantine}: {error}"
            ) from error
        binding.validated_leaves.pop(receipt_path.name, None)
        binding.validated_leaves.pop(name, None)
        _verify_state_binding(binding)
    # Recovery also supplies an absence-confirmation barrier when the exact
    # quarantine disappeared after its durable unlink.
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
                failures.append(f"staged link {link.staged_destination}: {error}")
                continue
            if link.destination_dev is None or link.destination_ino is None:
                continue
            try:
                # Preserve the target-and-inode proof seam before exact
                # descriptor-bound deletion.  Recovery does not depend on the
                # result because the inode may already be quarantined.
                _recorded_opencode_link_is_live(link)
                removed_exact = _unlink_recorded_destination(link)
            except (OSError, InstallError) as error:
                failures.append(f"link {link.destination}: {error}")
                continue
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
