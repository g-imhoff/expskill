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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class InstallResult:
    links: tuple[ProfileLink, ...] = ()
    created_links: tuple[ProfileLink, ...] = ()
    removed_links: tuple[ProfileLink, ...] = ()
    marketplace_added: bool = False
    plugin_installed: bool = False


@dataclass(frozen=True)
class _Receipt:
    repository_root: Path
    links: tuple[ProfileLink, ...]
    marketplace_added: bool
    plugin_installed: bool
    artifact_root: Path | None = None
    lineage: str | None = None
    pending_swap: "_PendingSwap | None" = None
    pending_publish: "_PendingPublish | None" = None


@dataclass(frozen=True)
class _PendingPublish:
    """Identity-bound state written before an initial artifact publication."""

    lineage: str
    artifact: Path
    candidate: Path
    candidate_dev: int
    candidate_ino: int
    phase: str


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


def _pending_swap_payload(pending: _PendingSwap) -> dict[str, object]:
    return {
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


def _pending_swap_auth(lineage: str, payload: Mapping[str, object]) -> str:
    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        b"opencode-pending-swap.v1\0" + lineage.encode() + b"\0" + canonical.encode()
    ).hexdigest()


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _StateBinding:
    """A state directory retained through one installer transaction."""

    directory: Path
    directory_fd: int
    identity: tuple[int, int]


_STATE_BINDINGS: dict[str, _StateBinding] = {}


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_state_binding(directory: Path, *, create: bool) -> _StateBinding | None:
    """Open every ancestor without following links and optionally create it."""

    absolute = _lexical_absolute(directory)
    descriptor = os.open(absolute.anchor, _directory_open_flags())
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                os.mkdir(component, mode=0o755, dir_fd=descriptor)
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        binding = _StateBinding(
            directory=absolute,
            directory_fd=descriptor,
            identity=(metadata.st_dev, metadata.st_ino),
        )
        _verify_state_binding(binding)
        return binding
    except Exception:
        os.close(descriptor)
        raise


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


def _unlink_state_path(path: Path) -> None:
    binding = _state_binding(path)
    if binding is None:
        path.unlink()
        return
    _verify_state_binding(binding)
    os.unlink(path.name, dir_fd=binding.directory_fd)
    os.fsync(binding.directory_fd)
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
        seen_pairs.add(pair)
        seen_destinations.add(lexical_destination)
        links.append(expected[pair])
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
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            contents = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _verify_state_binding(binding)
    return contents


def _write_state_payload(path: Path, payload: Mapping[str, object]) -> bool:
    """Write through the retained state descriptor; return false if unbound."""

    binding = _state_binding(path)
    if binding is None:
        return False
    _verify_state_binding(binding)
    try:
        current = os.stat(path.name, dir_fd=binding.directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(current.st_mode):
            raise InstallError(f"receipt path is not a regular file: {path}")
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = -1
    published = False
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
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=binding.directory_fd,
            dst_dir_fd=binding.directory_fd,
        )
        published = True
        os.fsync(binding.directory_fd)
        _verify_state_binding(binding)
        return True
    except OSError as error:
        raise InstallError(f"cannot durably write receipt: {path}: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary_name, dir_fd=binding.directory_fd)
            except OSError:
                pass


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
    links = _receipt_links(payload.get("links"), receipt_path, expected_links)
    lineage_value = payload.get("lineage")
    lineage: str | None
    if lineage_value is None:
        lineage = None
    elif isinstance(lineage_value, str) and len(lineage_value) >= 32:
        lineage = lineage_value
    else:
        raise InstallError(f"receipt lineage is malformed: {receipt_path}")
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
        if set(pending_value) != required:
            raise InstallError(f"receipt pending swap is malformed: {receipt_path}")
        if pending_value.get("lineage") != lineage:
            raise InstallError(f"receipt pending swap lineage mismatch: {receipt_path}")
        auth_value = payload.get("pending_swap_auth")
        if (
            not isinstance(auth_value, str)
            or auth_value != _pending_swap_auth(lineage, pending_value)
        ):
            raise InstallError(f"receipt pending swap authentication failed: {receipt_path}")
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
            or pending_value.get("phase") not in {"prepared", "backup-created", "published"}
        ):
            raise InstallError(f"receipt pending swap is malformed: {receipt_path}")
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
        if (
            not isinstance(publish_value, dict)
            or set(publish_value) != required_publish
            or lineage is None
            or pending is not None
            or publish_value.get("lineage") != lineage
            or publish_value.get("phase") not in {"prepared", "published"}
        ):
            raise InstallError(f"receipt pending publish is malformed: {receipt_path}")
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
        )
    return _Receipt(
        repository_root=recorded_root,
        links=links,
        marketplace_added=marketplace_added,
        plugin_installed=plugin_installed,
        artifact_root=artifact_root,
        lineage=lineage,
        pending_swap=pending,
        pending_publish=pending_publish,
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
    payload = {
        "links": [
            {"destination": str(link.destination), "source": str(link.source)}
            for link in sorted(receipt.links, key=lambda item: str(item.destination))
        ],
        "marketplace_added": receipt.marketplace_added,
        "plugin_installed": receipt.plugin_installed,
        "repository_root": str(receipt.repository_root),
    }
    if receipt.artifact_root is not None:
        payload["artifact_root"] = str(receipt.artifact_root)
    if receipt.lineage is not None:
        payload["lineage"] = receipt.lineage
    if receipt.pending_swap is not None:
        pending = receipt.pending_swap
        pending_payload = _pending_swap_payload(pending)
        payload["pending_swap"] = pending_payload
        payload["pending_swap_auth"] = _pending_swap_auth(pending.lineage, pending_payload)
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
            link.destination.symlink_to(link.source)
        except OSError as error:
            raise InstallError(f"cannot create agent link: {link.destination}: {error}") from error
        created.append(link)


def _rollback_links(links: Sequence[ProfileLink]) -> list[str]:
    failures: list[str] = []
    for link in reversed(tuple(links)):
        if not _lexists(link.destination):
            continue
        if not _same_recorded_link(link.destination, link.source):
            failures.append(f"link preserved because ownership changed: {link.destination}")
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
    return failures


def _same_recorded_link(destination: Path, source: Path) -> bool:
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
            link.destination.unlink()
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
        lineage=receipt.lineage,
        pending_swap=receipt.pending_swap,
        pending_publish=receipt.pending_publish,
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
            link.destination.unlink()
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


def _rename_noreplace(source: Path, target: Path) -> None:
    """Rename a recovered directory without overwriting a new occupant."""

    if source.parent != target.parent:
        raise InstallError("recovery paths must share one parent")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renameat2
    except (AttributeError, OSError) as error:
        raise InstallError("exclusive recovery rename is unavailable") from error
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
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(
            parent_fd,
            os.fsencode(source.name),
            parent_fd,
            os.fsencode(target.name),
            1,
        )
        if result == 0:
            if binding is not None:
                os.fsync(parent_fd)
                _verify_state_binding(binding)
            return
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise InstallError(f"recovery target was replaced: {target}")
        raise InstallError(
            f"exclusive recovery rename failed: {os.strerror(error_number)}"
        )
    except OSError as error:
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
        lineage=receipt.lineage,
        pending_swap=pending,
        pending_publish=receipt.pending_publish,
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
        lineage=receipt.lineage,
        pending_swap=receipt.pending_swap,
        pending_publish=pending_publish,
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
    else:
        pending_payload = _pending_swap_payload(pending)
        payload["pending_swap"] = pending_payload
        payload["pending_swap_auth"] = _pending_swap_auth(
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


def _recover_pending_swap(state_home: Path, receipt: _Receipt | None) -> _Receipt | None:
    """Recover exactly the pending swap named by the owned main receipt."""

    receipt_path = _opencode_receipt_path(state_home)
    if receipt is None or receipt.pending_swap is None:
        return receipt
    pending = receipt.pending_swap
    expected_artifact = _lexical_absolute(receipt_path.parent / OPENCODE_ARTIFACT_DIRECTORY)
    if (
        pending.artifact != expected_artifact
        or pending.candidate.parent != expected_artifact.parent
        or pending.backup.parent != expected_artifact.parent
        or not pending.candidate.name.startswith(f".{OPENCODE_ARTIFACT_DIRECTORY}.next-")
        or not pending.backup.name.startswith(f".{OPENCODE_ARTIFACT_DIRECTORY}.old-")
        or pending.candidate == pending.backup
        or pending.phase not in {"prepared", "backup-created", "published"}
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
    if artifact_exists and not artifact_is_candidate and not artifact_is_live:
        raise InstallError("OpenCode artifact was replaced by an unproven directory; preserving it")
    if pending.phase == "prepared":
        if artifact_is_live and not backup_exists:
            # Crash before the first rename: the candidate is private and the
            # old live artifact remains authoritative.  Remove only exact
            # candidate identity and clear the pending record.
            if candidate_exists:
                _remove_opencode_artifact_exact(
                    pending.candidate, pending.candidate_dev, pending.candidate_ino
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
        if candidate_exists:
            # The durable status is still pre-publication.  Preserve the
            # previous install rather than adopting an uncommitted candidate.
            _remove_opencode_artifact_exact(
                pending.candidate, pending.candidate_dev, pending.candidate_ino
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
    elif artifact_is_candidate and backup_exists:
        # Candidate was published before receipt status rewrite.
        pass
    elif artifact_is_candidate and not backup_exists:
        receipt = _receipt_with_pending(receipt, None)
        _write_receipt(receipt_path, receipt)
        return receipt
    elif artifact_is_live and not backup_exists:
        receipt = _receipt_with_pending(receipt, None)
        _write_receipt(receipt_path, receipt)
        return receipt
    elif backup_exists and not artifact_is_candidate:
        raise InstallError("OpenCode swap has an unproven live occupant; preserving state")
    published = _PendingSwap(**{**pending.__dict__, "phase": "published"})
    receipt = _receipt_with_pending(receipt, published)
    if pending.phase != "published":
        _write_receipt(receipt_path, receipt)
    return receipt


def _garbage_collect_opencode_backups(
    state_home: Path, receipt: _Receipt | None = None
) -> _Receipt | None:
    """Best-effort cleanup of the one receipt-owned pending backup."""

    receipt_path = _opencode_receipt_path(state_home)
    if receipt is None or receipt.pending_swap is None:
        return receipt
    pending = receipt.pending_swap
    if pending.phase != "published":
        return receipt
    if not _pending_identity(pending.artifact, pending.candidate_dev, pending.candidate_ino):
        return receipt
    if not _pending_identity(pending.backup, pending.backup_dev, pending.backup_ino):
        if not _lexists(pending.backup):
            receipt = _receipt_with_pending(receipt, None)
            _write_receipt(receipt_path, receipt)
        return receipt
    try:
        _remove_opencode_artifact_exact(
            pending.backup, pending.backup_dev, pending.backup_ino
        )
    except (InstallError, OSError, TypeError, ValueError):
        return receipt
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

    if existed_before or not receipt_directory.is_dir():
        return
    try:
        if not any(receipt_directory.iterdir()):
            receipt_directory.rmdir()
    except OSError:
        pass
    if state_home_existed_before or not state_home.is_dir():
        return
    try:
        if not any(state_home.iterdir()):
            state_home.rmdir()
    except OSError:
        pass


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


def _receipt_owns_artifact(receipt: _Receipt | None, artifact: Path) -> bool:
    """Use only a fully parsed current receipt as artifact ownership proof."""

    return (
        receipt is not None
        and receipt.artifact_root is not None
        and bool(receipt.links)
        and receipt.lineage is not None
        and not receipt.marketplace_added
        and receipt.plugin_installed
        and _lexical_absolute(receipt.artifact_root) == _lexical_absolute(artifact)
    )


def _validate_receipt_artifact(receipt: _Receipt, state_home: Path) -> None:
    if receipt.artifact_root is not None:
        _fixed_opencode_artifact(state_home, receipt.artifact_root)


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
            _remove_opencode_artifact_exact(
                artifact, pending.candidate_dev, pending.candidate_ino
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
    """Recover an authenticated initial publication before ordinary ownership."""

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
    if artifact_exists and not artifact_is_candidate:
        raise InstallError(
            "OpenCode initial publication has an unproven live occupant; preserving it"
        )
    if candidate_exists and not artifact_exists and pending.phase == "prepared":
        _remove_opencode_artifact_exact(
            pending.candidate, pending.candidate_dev, pending.candidate_ino
        )
        if receipt.links:
            restored = _Receipt(
                repository_root=receipt.repository_root,
                links=receipt.links,
                marketplace_added=receipt.marketplace_added,
                plugin_installed=receipt.plugin_installed,
            )
            _write_receipt(receipt_path, restored)
            return restored, False
        _unlink_state_path(receipt_path)
        return None, False
    if candidate_exists or not artifact_is_candidate:
        raise InstallError("OpenCode initial publication state is inconsistent")
    if not _artifact_matches_sources(repo_root, pending.artifact):
        raise InstallError("recovered OpenCode artifact failed exact source validation")
    if pending.phase != "published":
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


def _ensure_opencode_artifact(
    repo_root: Path, state_home: Path, receipt: _Receipt | None = None
) -> tuple[Path, bool, Path | None, _PendingSwap | None, _Receipt | None]:
    """Build and publish a candidate with receipt-owned durable swap state."""

    artifact = _fixed_opencode_artifact(state_home)
    receipt_path = _opencode_receipt_path(state_home)
    receipt, recovered_initial = _recover_pending_publish(repo_root, state_home, receipt)
    if recovered_initial:
        return artifact, True, None, None, receipt
    receipt = _recover_pending_swap(state_home, receipt)
    # A completed-but-not-cleaned swap is recovered from the receipt and its
    # exact backup is removed before beginning another publication.
    receipt = _garbage_collect_opencode_backups(state_home, receipt)
    existing = _lexists(artifact)
    if existing and not _receipt_owns_artifact(receipt, artifact):
        raise InstallError(f"opencode artifact is stale and not receipt-owned: {artifact}")
    binding = _state_binding(artifact)
    if binding is None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
    else:
        _verify_state_binding(binding)
    candidate = artifact.parent / f".{artifact.name}.next-{uuid.uuid4().hex}"
    backup: Path | None = None
    pending: _PendingSwap | None = None
    candidate_identity: tuple[int, int] | None = None
    live_identity: tuple[int, int] | None = None
    try:
        build_opencode_package(repo_root, candidate)
        if not _artifact_matches_sources(repo_root, candidate):
            raise InstallError("fresh OpenCode artifact failed exact inventory or byte validation")
        candidate_metadata = _state_lstat(candidate)
        candidate_identity = (candidate_metadata.st_dev, candidate_metadata.st_ino)
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
            lineage = receipt.lineage or uuid.uuid4().hex
            receipt_for_swap = _Receipt(
                repository_root=receipt.repository_root,
                links=receipt.links,
                marketplace_added=receipt.marketplace_added,
                plugin_installed=receipt.plugin_installed,
                artifact_root=artifact,
                lineage=lineage,
                pending_swap=None,
            )
            if receipt.lineage is None:
                _write_receipt(receipt_path, receipt_for_swap)
            backup = artifact.parent / f".{artifact.name}.old-{uuid.uuid4().hex}"
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
            )
            # This write, including directory fsync, is mandatory before the
            # first live->backup rename.
            _write_receipt(receipt_path, _receipt_with_pending(receipt_for_swap, pending))
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
            return artifact, False, backup, pending, receipt_for_swap
        lineage = uuid.uuid4().hex
        pending_publish = _PendingPublish(
            lineage=lineage,
            artifact=artifact,
            candidate=candidate,
            candidate_dev=candidate_identity[0],
            candidate_ino=candidate_identity[1],
            phase="prepared",
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
        if _lexists(candidate) and candidate_identity is not None and _pending_identity(
            candidate, candidate_identity[0], candidate_identity[1]
        ):
            try:
                _remove_opencode_artifact_exact(
                    candidate, candidate_identity[0], candidate_identity[1]
                )
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
        if not _same_owned_link(link.destination, link.source):
            if legacy_receipt is not None and (
                legacy_receipt.artifact_root is None
                or legacy_receipt.pending_publish is not None
            ):
                legacy = next(
                    (
                        item
                        for item in legacy_receipt.links
                        if item.destination == link.destination
                    ),
                    None,
                )
                if legacy is not None and _same_recorded_link(
                    link.destination, legacy.source
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
    _canonical_opencode_config(config_dir)
    receipt_path_value = _opencode_receipt_path(state_home)
    receipt_directory_existed = receipt_path_value.parent.exists()
    state_home_existed = receipt_path_value.parent.parent.exists()
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
        os.close(binding.directory_fd)
        raise InstallError(f"OpenCode state directory is already active: {binding.directory}")
    _STATE_BINDINGS[key] = binding
    try:
        return _install_opencode_bound(
            canonical_root,
            config_dir,
            state_home,
            receipt_directory_existed,
            state_home_existed,
        )
    finally:
        _STATE_BINDINGS.pop(key, None)
        os.close(binding.directory_fd)


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
    if _lexists(receipt_path_value):
        receipt = _read_opencode_receipt(
            receipt_path_value, canonical_root, config_dir, state_home
        )
        if receipt is None:
            raise InstallError(f"receipt disappeared while reading: {receipt_path_value}")
        _validate_receipt_artifact(receipt, state_home)
    try:
        (
            artifact_root,
            artifact_created,
            artifact_backup,
            artifact_pending,
            receipt,
        ) = _ensure_opencode_artifact(canonical_root, state_home, receipt)
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
            _remove_opencode_artifact(artifact_root)
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
    created_links: list[ProfileLink] = []
    migrated_links: list[ProfileLink] = []
    removed_retired: tuple[ProfileLink, ...] = ()
    try:
        if receipt is not None and (
            receipt.artifact_root is None or receipt.pending_publish is not None
        ):
            for old in receipt.links:
                if not _lexists(old.destination):
                    continue
                if not _same_recorded_link(old.destination, old.source):
                    raise InstallError(
                        f"refusing to migrate retargeted legacy link: {old.destination}"
                    )
                old.destination.unlink()
                migrated_links.append(old)
        _create_links(links, created_links)
        if receipt is not None:
            _retained, removed_retired = _prune_opencode_retired_links(
                receipt, links, config_dir
            )
        merged_receipt = _Receipt(
            repository_root=canonical_root,
            links=tuple(links),
            marketplace_added=False,
            plugin_installed=True,
            artifact_root=artifact_root,
            lineage=(receipt.lineage if receipt is not None and receipt.lineage else uuid.uuid4().hex),
            pending_swap=artifact_pending,
        )
        _write_receipt(receipt_path_value, merged_receipt)
    except Exception as error:
        for failure in _rollback_links(created_links):
            error = InstallError(f"{error}; residual state or rollback failures: {failure}")
        for failure in _restore_opencode_links(removed_retired, config_dir):
            error = InstallError(f"{error}; retired-link rollback: {failure}")
        for failure in _restore_opencode_links(migrated_links, config_dir):
            error = InstallError(f"{error}; legacy-link rollback: {failure}")
        if isinstance(error, InstallError):
            if artifact_created:
                try:
                    _remove_opencode_artifact(artifact_root)
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
            raise error
        if artifact_created:
            try:
                _remove_opencode_artifact(artifact_root)
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
        raise InstallError(str(error)) from error
    if artifact_backup is not None:
        # Commit is complete once links and receipt point at the new artifact.
        # Garbage collection is post-commit and safely retried on a later run.
        try:
            if artifact_pending is None:
                raise InstallError("OpenCode backup is missing its recorded identity")
            _remove_opencode_artifact_exact(
                artifact_backup,
                artifact_pending.backup_dev,
                artifact_pending.backup_ino,
            )
        except (InstallError, OSError):
            pass
    # The receipt now names the successfully committed artifact.  Retry only
    # the one exact backup identity still named by its pending-swap record;
    # foreign ``.old-*`` directories are untouched.
    merged_receipt = _garbage_collect_opencode_backups(state_home, merged_receipt)
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
    """Reconstruct a constrained receipt allow-list when state was removed."""

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
    if repository_root is not None:
        try:
            allowed_skills = set(_skill_inventory(repository_root))
        except Exception as error:
            raise InstallError(f"canonical skill inventory cannot be read: {error}") from error
    else:
        allowed_skills = set(LEGACY_OPENCODE_SKILLS)
    allowed_agents = set(_OPENCODE_AGENT_NAMES)
    allowed_plugins = set(LEGACY_OPENCODE_PLUGINS)
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
            valid = source_name in allowed_skills and destination_group == "skills" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_dir()):
                valid = False
        elif source_group == "commands":
            valid = source_name.endswith(".md") and source_name[:-3] in allowed_skills and destination_group == "commands" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        elif source_group == "agents":
            valid = source_name.endswith(".md") and source_name[:-3] in allowed_agents and destination_group == "agents" and destination_name == source_name
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
        if "pending_swap" in payload:
            allowed_keys.update({"pending_swap", "pending_swap_auth"})
        if "pending_publish" in payload:
            allowed_keys.add("pending_publish")
        if set(payload) != allowed_keys:
            raise InstallError(f"current OpenCode receipt is inconsistent: {receipt_path}")
        if "pending_publish" in payload and payload.get("links"):
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
            legacy_expected = _legacy_opencode_expected_links(repository_root, config_dir)
            if (
                receipt.pending_swap is not None
                or (receipt.links and set(receipt.links) != set(legacy_expected))
            ):
                raise InstallError(
                    f"OpenCode prepublication receipt is inconsistent: {receipt_path}"
                )
        else:
            artifact = _fixed_opencode_artifact(state_home, receipt.artifact_root)
            canonical_config = _canonical_opencode_config(config_dir)
            skill_names = tuple(_skill_inventory(repository_root))
            expected_current = {
                ProfileLink(artifact / "skills" / name, canonical_config / "skills" / name)
                for name in skill_names
            }
            expected_current.update(
                ProfileLink(
                    artifact / "commands" / f"{name}.md",
                    canonical_config / "commands" / f"{name}.md",
                )
                for name in skill_names
            )
            expected_current.update(
                ProfileLink(
                    artifact / "agents" / f"{name}.md",
                    canonical_config / "agents" / f"{name}.md",
                )
                for name in _OPENCODE_AGENT_NAMES
            )
            expected_current.update(
                ProfileLink(
                    artifact / "plugins" / name,
                    canonical_config / "plugins" / name,
                )
                for name in LEGACY_OPENCODE_PLUGINS
            )
            if set(receipt.links) != expected_current:
                raise InstallError(
                    f"current OpenCode receipt link inventory is incomplete: {receipt_path}"
                )
    elif set(receipt.links) != set(expected):
        raise InstallError(f"legacy OpenCode receipt is incomplete: {receipt_path}")
    return receipt


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
        if not _lexists(link.destination) or not _same_recorded_link(
            link.destination, link.source
        ):
            # Missing and retargeted destinations are not ours to remove.
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            for restored in _restore_opencode_links(removed, config_dir):
                error = InstallError(f"{error}; retired-link rollback: {restored}")
            raise InstallError(
                f"cannot remove retired OpenCode link: {link.destination}: {error}"
            ) from error
        removed.append(link)
    return tuple(retained), tuple(removed)


def _restore_opencode_links(links: Sequence[ProfileLink], config_dir: Path) -> list[str]:
    failures: list[str] = []
    for link in reversed(tuple(links)):
        if _lexists(link.destination):
            continue
        try:
            _validate_opencode_destination(link.destination, config_dir)
            link.destination.parent.mkdir(parents=True, exist_ok=True)
            link.destination.symlink_to(link.source)
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
        except InstallError as error:
            failures.append(str(error))
    return failures


def uninstall_opencode(
    repo_root: Path,
    config_dir: Path,
    state_home: Path,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
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
        os.close(binding.directory_fd)
        raise InstallError(f"OpenCode state directory is already active: {binding.directory}")
    _STATE_BINDINGS[key] = binding
    try:
        return _uninstall_opencode_bound(
            canonical_root, config_dir, state_home, receipt_path_value
        )
    finally:
        _STATE_BINDINGS.pop(key, None)
        os.close(binding.directory_fd)


def _uninstall_opencode_bound(
    canonical_root: Path,
    config_dir: Path,
    state_home: Path,
    receipt_path_value: Path,
) -> InstallResult:
    if not _lexists(receipt_path_value):
        return InstallResult()
    receipt = _read_opencode_receipt(
        receipt_path_value, canonical_root, config_dir, state_home
    )
    if receipt is None:
        return InstallResult()
    links = receipt.links
    _validate_receipt_artifact(receipt, state_home)
    current = receipt
    removed: list[ProfileLink] = []
    failures: list[str] = []
    for link in receipt.links:
        if not _lexists(link.destination):
            current = _persist_receipt(receipt_path_value, current, links=tuple(item for item in current.links if item != link))
            continue
        if not _same_recorded_link(link.destination, link.source):
            current = _persist_receipt(receipt_path_value, current, links=tuple(item for item in current.links if item != link))
            continue
        try:
            if link.destination.is_dir() and not link.destination.is_symlink():
                failures.append(f"link {link.destination} is a real directory")
                continue
            link.destination.unlink()
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
            continue
        removed.append(link)
        current = _persist_receipt(receipt_path_value, current, links=tuple(item for item in current.links if item != link))
    if failures:
        raise InstallError("owned opencode link cleanup failed: " + "; ".join(failures))
    if current.links:
        raise InstallError("owned opencode link cleanup did not converge")
    if current.artifact_root is not None:
        artifact = _fixed_opencode_artifact(state_home, current.artifact_root)
        _remove_opencode_artifact(artifact)
    if not stat.S_ISREG(_state_lstat(receipt_path_value).st_mode):
        raise InstallError(f"receipt path is not a regular file: {receipt_path_value}")
    try:
        _unlink_state_path(receipt_path_value)
    except OSError as error:
        raise InstallError(f"cannot remove receipt: {receipt_path_value}: {error}") from error
    return InstallResult(
        links=links,
        removed_links=tuple(removed),
        marketplace_added=False,
        plugin_installed=True,
    )


def _print_opencode_dry_run(
    repo_root: Path, config_dir: Path, state_home: Path | None = None
) -> None:
    receipt_state_home = _default_state_home() if state_home is None else state_home
    # A dry run must not adopt or rebuild receipt-owned state.  Build the
    # planned artifact in the preflight temporary directory instead.
    links = preflight_opencode_links(repo_root, config_dir, receipt_state_home)
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
