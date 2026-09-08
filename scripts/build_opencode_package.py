"""Build a deterministic, self-contained ``opencode-expskill`` package.

The checked-in OpenCode directory contains only platform-owned source.  This
builder stages the publishable package in an explicit output directory and
materializes the universal source assets as regular files.  It never follows
symlink inputs and never emits generated output inside the tracked source
package.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# This module is also a documented command-line entry point.  Set the flag
# before importing any local modules so direct invocations cannot populate a
# checkout with Python bytecode caches.
sys.dont_write_bytecode = True

try:
    from scripts.render_opencode import RenderError, render_all
except ModuleNotFoundError:
    from render_opencode import RenderError, render_all


PROVENANCE_SCHEMA_VERSION = "opencode-provenance.v1"
PLATFORM_FILES = ("agents.json", "package.json", "README.md", "LICENSE", "index.js")
PLATFORM_PLUGIN_DIRECTORY = "plugins"
PLATFORM_PLUGIN_FILES = ("execution-policy.js", "unslop.js")
COPY_TREES = ("skills", "scripts")
COPY_FILES = (
    Path("assets/execution-policy.json"),
)
COPY_LICENSES = Path("third-party/licenses")
PYTHON_CACHE_SUFFIXES = {".pyc", ".pyo"}
ARTIFACT_DIRECTORY_MODE = 0o755
ARTIFACT_FILE_MODE = 0o644
ARTIFACT_MTIME = 0


class BuildError(RuntimeError):
    """Raised when package inputs or the explicit output target are unsafe."""


@dataclass(frozen=True)
class _OutputBinding:
    """An output parent bound to an open directory descriptor."""

    parent: Path
    parent_fd: int
    parent_identity: tuple[int, int]
    staging_name: str


# ``_replace_output`` deliberately keeps a two-argument public seam for callers
# and tests.  The builder registers the descriptor-bound context immediately
# before calling it, so a caller cannot swap the lexical parent between staging
# and publication and redirect the commit through a symlink.
_OUTPUT_BINDINGS: dict[str, _OutputBinding] = {}


def _reject_symlink_components(path: Path, label: str) -> None:
    """Reject a symlink anywhere in an input path, including its parents."""

    candidate = path.expanduser()
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
            raise BuildError(f"{label} cannot be inspected: {current}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise BuildError(f"{label} path component must not be a symlink: {current}")


def repository_root() -> Path:
    return Path(__file__).absolute().parents[1]


def _resolve_root(value: Path | str | None) -> Path:
    candidate = Path(value).expanduser() if value is not None else repository_root()
    _reject_symlink_components(candidate, "repository root")
    try:
        root = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BuildError(f"repository root cannot be resolved: {candidate}: {error}") from error
    if not root.is_dir() or root.is_symlink():
        raise BuildError(f"repository root is not a regular directory: {root}")
    return root


def _output_path(value: Path | str) -> Path:
    if value is None:  # type: ignore[comparison-overlap]
        raise BuildError("an explicit output directory is required")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    _reject_symlink_components(candidate, "output directory")
    try:
        final_mode = os.lstat(candidate).st_mode
    except FileNotFoundError:
        pass
    except OSError as error:
        raise BuildError(f"output directory cannot be inspected: {candidate}: {error}") from error
    else:
        if stat.S_ISLNK(final_mode):
            raise BuildError(f"output directory must not be a symlink: {candidate}")
    candidate = candidate.resolve(strict=False)
    return candidate


def _validate_output_target(root: Path, canonical_root: Path, output: Path) -> None:
    source_roots = (root.resolve(strict=True), canonical_root.resolve(strict=True))
    for source_root in source_roots:
        if output == source_root:
            raise BuildError("generated output must not be a source root")
        if output in source_root.parents:
            raise BuildError("generated output must not be an ancestor of a source root")
        if source_root in output.parents:
            raise BuildError("generated output must be outside the repository source tree")
    if output.exists() or output.is_symlink():
        raise BuildError(f"output target must not already exist: {output}")


def _ensure_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BuildError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise BuildError(f"{label} is not a regular file: {path}")
    return path


def _ensure_regular_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BuildError(f"{label} must not be a symlink: {path}")
    if not path.is_dir():
        raise BuildError(f"{label} is not a regular directory: {path}")
    return path


def _is_cache(relative: Path) -> bool:
    return "__pycache__" in relative.parts or relative.suffix in PYTHON_CACHE_SUFFIXES


def _iter_regular_files(root: Path, label: str) -> Iterable[tuple[Path, Path]]:
    """Yield ``(relative, absolute)`` files after rejecting unsafe entries."""

    _ensure_regular_directory(root, label)
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
        except OSError as error:
            raise BuildError(f"{label} could not be listed: {error}") from error
        for path in entries:
            relative = path.relative_to(root)
            if path.is_symlink():
                raise BuildError(f"{label} entry must not be a symlink: {path}")
            if path.is_dir():
                pending.append(path)
                continue
            _ensure_regular_file(path, label)
            if not _is_cache(relative):
                yield relative, path


def _safe_copy_file(source: Path, target: Path, label: str) -> None:
    _ensure_regular_file(source, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.is_symlink():
        raise BuildError(f"generated output entry must not be a symlink: {target}")
    shutil.copyfile(source, target)
    if target.is_symlink() or not target.is_file():
        raise BuildError(f"generated output entry is not a regular file: {target}")


def _copy_tree(source: Path, target: Path, label: str) -> list[Path]:
    files: list[Path] = []
    for relative, path in _iter_regular_files(source, label):
        destination = target / relative
        _safe_copy_file(path, destination, label)
        files.append(destination)
    return files


def _copy_platform_source(source_root: Path, output_root: Path) -> list[Path]:
    files: list[Path] = []
    for name in PLATFORM_FILES:
        source = source_root / name
        destination = output_root / name
        _safe_copy_file(source, destination, f"OpenCode platform source {name!r}")
        files.append(destination)
    plugins_source = source_root / PLATFORM_PLUGIN_DIRECTORY
    files.extend(
        _copy_tree(plugins_source, output_root / PLATFORM_PLUGIN_DIRECTORY, "OpenCode plugin source")
    )
    return files


def _validate_platform_source(source_root: Path) -> None:
    """Require the exact checked-in platform source roster.

    Generated OpenCode documents belong in the private build artifact.  An
    extra top-level directory (for example a checked-in ``agents`` or
    ``catalog.json``) is therefore an input defect rather than something the
    builder may silently ignore.
    """

    _ensure_regular_directory(source_root, "OpenCode platform source")
    expected = set(PLATFORM_FILES) | {PLATFORM_PLUGIN_DIRECTORY}
    try:
        entries = {entry.name: entry for entry in source_root.iterdir()}
    except OSError as error:
        raise BuildError(f"OpenCode platform source could not be listed: {error}") from error
    unexpected = sorted(set(entries) - expected)
    missing = sorted(expected - set(entries))
    if unexpected or missing:
        details: list[str] = []
        if unexpected:
            details.append(f"unexpected entries {unexpected!r}")
        if missing:
            details.append(f"missing entries {missing!r}")
        raise BuildError("OpenCode platform source roster is invalid: " + "; ".join(details))
    for name in PLATFORM_FILES:
        _ensure_regular_file(entries[name], f"OpenCode platform source {name!r}")
    plugins = entries[PLATFORM_PLUGIN_DIRECTORY]
    _ensure_regular_directory(plugins, "OpenCode plugin source")
    try:
        plugin_entries = {entry.name: entry for entry in plugins.iterdir()}
    except OSError as error:
        raise BuildError(f"OpenCode plugin source could not be listed: {error}") from error
    if set(plugin_entries) != set(PLATFORM_PLUGIN_FILES):
        raise BuildError(
            "OpenCode plugin source roster is invalid: "
            f"expected {sorted(PLATFORM_PLUGIN_FILES)!r}, found {sorted(plugin_entries)!r}"
        )
    for name in PLATFORM_PLUGIN_FILES:
        _ensure_regular_file(plugin_entries[name], f"OpenCode plugin source {name!r}")


def _copy_canonical_source(canonical_root: Path, output_root: Path) -> list[Path]:
    files: list[Path] = []
    for tree in COPY_TREES:
        files.extend(_copy_tree(canonical_root / tree, output_root / tree, f"canonical {tree}"))
    for relative in COPY_FILES:
        source = canonical_root / relative
        target = output_root / relative
        _safe_copy_file(source, target, f"canonical package asset {relative}")
        files.append(target)
    files.extend(_copy_tree(canonical_root / COPY_LICENSES, output_root / COPY_LICENSES, "canonical licenses"))
    return files


def _provenance_sources(root: Path) -> list[tuple[str, Path]]:
    canonical_root = root / "packages" / "expskill"
    platform_root = canonical_root / "opencode"
    sources: list[tuple[str, Path]] = []

    def add_file(path: Path) -> None:
        _reject_symlink_components(path, "provenance input")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as error:
            raise BuildError(f"provenance input is outside repository root: {path}") from error
        _ensure_regular_file(path, "provenance input")
        sources.append((relative, path))

    for tree in COPY_TREES:
        tree_root = canonical_root / tree
        for relative, path in _iter_regular_files(tree_root, f"canonical {tree}"):
            add_file(path)
    for relative in COPY_FILES:
        add_file(canonical_root / relative)
    for relative, path in _iter_regular_files(canonical_root / COPY_LICENSES, "canonical licenses"):
        add_file(path)
    for name in PLATFORM_FILES:
        add_file(platform_root / name)
    for _relative, path in _iter_regular_files(platform_root / PLATFORM_PLUGIN_DIRECTORY, "OpenCode plugin source"):
        add_file(path)
    for _relative, path in _iter_regular_files(
        canonical_root / "assets" / "agents", "canonical agent profiles"
    ):
        add_file(path)
    for relative in (Path("scripts/build_opencode_package.py"), Path("scripts/render_opencode.py")):
        add_file(root / relative)
    return sorted(sources, key=lambda item: item[0])


def _snapshot_sources(root: Path) -> tuple[Path, Path]:
    """Copy all build inputs into one private, verified source snapshot.

    Rendering, copying, and provenance hashing all consume this snapshot.  A
    second inventory/digest pass over the live checkout detects edits during
    snapshot creation and aborts before publishing an inconsistent artifact.
    """

    initial = _provenance_sources(root)
    snapshot_parent = Path(tempfile.mkdtemp(prefix=".opencode-source-snapshot-"))
    snapshot_root = snapshot_parent / "root"
    try:
        for relative, source in initial:
            target = snapshot_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Read once and derive the digest from the exact bytes written to
            # the private snapshot.  A second read of the live path here could
            # describe a different byte set than the one rendered below.
            contents = source.read_bytes()
            target.write_bytes(contents)
        snapshot_digest = [
            (relative, hashlib.sha256(path.read_bytes()).hexdigest())
            for relative, path in _provenance_sources(snapshot_root)
        ]
        final_sources = _provenance_sources(root)
        final_digest = [
            (relative, hashlib.sha256(path.read_bytes()).hexdigest())
            for relative, path in final_sources
        ]
        if snapshot_digest != final_digest:
            raise BuildError("repository sources changed while creating a private OpenCode snapshot")
    except (OSError, BuildError):
        shutil.rmtree(snapshot_parent, ignore_errors=True)
        raise
    return snapshot_root, snapshot_parent


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_rendered(output_root: Path, rendered: dict[str, str]) -> list[Path]:
    files: list[Path] = []
    for relative, contents in sorted(rendered.items()):
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        if target.is_symlink() or not target.is_file():
            raise BuildError(f"rendered output is not a regular file: {target}")
        files.append(target)
    return files


def _write_provenance(root: Path, output_root: Path) -> Path:
    inputs = []
    for relative, path in _provenance_sources(root):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        inputs.append({"path": relative, "sha256": digest})
    target = output_root / "provenance.json"
    _write_json(target, {"schema_version": PROVENANCE_SCHEMA_VERSION, "inputs": inputs})
    return target


def _snapshot_digest_manifest(root: Path) -> list[tuple[str, str]]:
    """Return the digest inventory for an already-created private snapshot."""

    return [
        (relative, hashlib.sha256(path.read_bytes()).hexdigest())
        for relative, path in _provenance_sources(root)
    ]


def _verify_live_sources_against_snapshot(root: Path, snapshot_root: Path) -> None:
    """Reject any live source edit observed after the snapshot was accepted."""

    if _snapshot_digest_manifest(root) != _snapshot_digest_manifest(snapshot_root):
        raise BuildError("repository sources changed before OpenCode publication")


def _normalize_artifact_modes(output_root: Path) -> None:
    """Make artifact permissions and timestamps independent of umask and clock."""

    paths = sorted(output_root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    paths.append(output_root)
    for path in paths:
        try:
            metadata = os.lstat(path)
        except OSError as error:
            raise BuildError(f"artifact entry cannot be inspected: {path}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise BuildError(f"artifact entry must not be a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            mode = ARTIFACT_DIRECTORY_MODE
        elif stat.S_ISREG(metadata.st_mode):
            mode = ARTIFACT_FILE_MODE
        else:
            raise BuildError(f"artifact entry is not a regular file or directory: {path}")
        try:
            os.chmod(path, mode)
            os.utime(path, (ARTIFACT_MTIME, ARTIFACT_MTIME))
        except OSError as error:
            raise BuildError(f"artifact metadata cannot be normalized: {path}: {error}") from error


def _same_directory_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_output_parent(parent: Path) -> tuple[int, tuple[int, int]]:
    """Open and identity-check the already validated output parent."""

    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as error:
        raise BuildError(f"output parent cannot be opened safely: {parent}: {error}") from error
    try:
        bound = os.fstat(descriptor)
        observed = os.stat(parent, follow_symlinks=False)
        if not stat.S_ISDIR(bound.st_mode) or not _same_directory_identity(bound, observed):
            raise BuildError(f"output parent changed during validation: {parent}")
        return descriptor, (bound.st_dev, bound.st_ino)
    except Exception:
        os.close(descriptor)
        raise


def _binding_is_current(binding: _OutputBinding) -> bool:
    try:
        bound = os.fstat(binding.parent_fd)
        observed = os.stat(binding.parent, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(bound.st_mode)
        and _same_directory_identity(bound, observed)
        and (bound.st_dev, bound.st_ino) == binding.parent_identity
    )


def _renameat2_noreplace(
    parent_fd: int, source_name: str, destination_name: str
) -> bool:
    """Atomically rename without replacing a target where Linux supports it."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.renameat2
    except (AttributeError, OSError):
        return False
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(
        parent_fd,
        os.fsencode(source_name),
        parent_fd,
        os.fsencode(destination_name),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return True
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise BuildError(f"output target must not already exist: {destination_name}")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
        return False
    raise OSError(error_number, os.strerror(error_number))


def _fallback_publish_no_replace(binding: _OutputBinding, output_name: str) -> None:
    """Portable complete-or-absent fallback when renameat2 is unavailable."""

    parent_fd = binding.parent_fd
    staging_name = binding.staging_name
    try:
        os.mkdir(output_name, mode=ARTIFACT_DIRECTORY_MODE, dir_fd=parent_fd)
    except FileExistsError as error:
        raise BuildError(f"output target must not already exist: {binding.parent / output_name}") from error
    except OSError as error:
        raise BuildError(f"output target cannot be reserved: {binding.parent / output_name}: {error}") from error
    output_fd: int | None = None
    staging_fd: int | None = None
    moved: list[str] = []
    try:
        output_fd = os.open(
            output_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        staging_fd = os.open(
            staging_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        staging_path = Path(f"/proc/self/fd/{staging_fd}")
        for child in sorted(staging_path.iterdir(), key=lambda item: item.name):
            try:
                os.stat(child.name, dir_fd=output_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BuildError(
                    f"output target was populated during publish: {binding.parent / output_name / child.name}"
                )
            os.rename(
                child.name,
                child.name,
                src_dir_fd=staging_fd,
                dst_dir_fd=output_fd,
            )
            moved.append(child.name)
        os.rmdir(staging_name, dir_fd=parent_fd)
    except (OSError, BuildError):
        # The output reservation is ours only while it remains empty or contains
        # exactly the children moved by this operation.  Roll every moved entry
        # back before removing it; a foreign child is never deleted.
        if output_fd is not None:
            for name in reversed(moved):
                try:
                    os.rename(name, name, src_dir_fd=output_fd, dst_dir_fd=staging_fd)
                except OSError:
                    pass
        try:
            if staging_fd is not None:
                os.close(staging_fd)
                staging_fd = None
            if output_fd is not None:
                os.close(output_fd)
                output_fd = None
            if not os.listdir(Path(f"/proc/self/fd/{parent_fd}") / output_name):
                os.rmdir(output_name, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        if output_fd is not None:
            os.close(output_fd)


def _replace_output(staging: Path, output: Path) -> None:
    """Publish one complete artifact with no target replacement."""

    key = os.path.abspath(os.fspath(staging))
    binding = _OUTPUT_BINDINGS.get(key)
    temporary_binding = False
    if binding is None:
        parent_fd, identity = _open_output_parent(output.parent)
        binding = _OutputBinding(output.parent, parent_fd, identity, staging.name)
        temporary_binding = True
    try:
        if not _binding_is_current(binding):
            raise BuildError(f"output parent changed during publication: {output.parent}")
        output_name = output.name
        # ``renameat2`` is the preferred one-operation complete publication.
        # Its fallback reserves the name and rolls back every moved child on any
        # error, preserving the same no-clobber and complete-or-absent result.
        try:
            published = _renameat2_noreplace(binding.parent_fd, binding.staging_name, output_name)
        except OSError as error:
            raise BuildError(f"cannot publish OpenCode artifact: {error}") from error
        if not published:
            try:
                _fallback_publish_no_replace(binding, output_name)
            except OSError as error:
                raise BuildError(f"cannot publish OpenCode artifact: {error}") from error
        if not _binding_is_current(binding):
            # The path moved after the commit.  Undo through the still-open
            # descriptor, never through the potentially replaced lexical path.
            try:
                os.rename(output_name, binding.staging_name, src_dir_fd=binding.parent_fd, dst_dir_fd=binding.parent_fd)
            except OSError:
                pass
            raise BuildError(f"output parent changed during publication: {output.parent}")
    finally:
        if temporary_binding:
            os.close(binding.parent_fd)


def _cleanup_staging(
    staging: Path,
    staging_parent: Path,
    prefix: str,
    identity: os.stat_result | None = None,
) -> None:
    """Remove only the builder-owned staging directory after a failed build."""

    binding = _OUTPUT_BINDINGS.get(os.path.abspath(os.fspath(staging)))
    if binding is not None:
        try:
            metadata = os.stat(
                binding.staging_name,
                dir_fd=binding.parent_fd,
                follow_symlinks=False,
            )
        except OSError:
            return
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return
        if identity is not None:
            if metadata.st_dev != identity.st_dev or metadata.st_ino != identity.st_ino:
                return
        try:
            shutil.rmtree(Path(f"/proc/self/fd/{binding.parent_fd}") / binding.staging_name)
        except OSError:
            pass
        return

    if staging.parent != staging_parent or not staging.name.startswith(prefix):
        return
    try:
        metadata = os.lstat(staging)
    except FileNotFoundError:
        return
    except OSError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return
    if identity is not None:
        if metadata.st_dev != identity.st_dev or metadata.st_ino != identity.st_ino:
            return
    try:
        shutil.rmtree(staging)
    except OSError:
        return


def build_opencode_package(
    repo_root: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> Path:
    """Build the package into an explicit output directory and return it."""

    if output_dir is None:
        raise BuildError("an explicit output directory is required")
    root = _resolve_root(repo_root)
    output = _output_path(output_dir)
    canonical_root = root / "packages" / "expskill"
    platform_root = canonical_root / "opencode"
    _reject_symlink_components(canonical_root, "canonical package")
    _reject_symlink_components(platform_root, "OpenCode platform source")
    for relative in (
        *COPY_TREES,
        *COPY_FILES,
        COPY_LICENSES,
        Path("assets") / "agents",
        Path("opencode") / PLATFORM_PLUGIN_DIRECTORY,
    ):
        source = canonical_root / relative
        _reject_symlink_components(source, f"canonical input {relative}")
    _ensure_regular_directory(canonical_root, "canonical package")
    _ensure_regular_directory(platform_root, "OpenCode platform source")
    _validate_platform_source(platform_root)
    # Validate the complete set of source trees before staging so a stray
    # symlink cannot hide in an un-copied input directory.
    list(_iter_regular_files(platform_root, "OpenCode platform source"))
    list(
        _iter_regular_files(canonical_root / "assets" / "agents", "canonical agent profiles")
    )
    try:
        canonical_root_resolved = canonical_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BuildError(f"canonical package cannot be resolved: {canonical_root}: {error}") from error
    _validate_output_target(root, canonical_root_resolved, output)

    staging_parent = output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    parent_fd, parent_identity = _open_output_parent(staging_parent)
    staging_prefix = f".{output.name}."
    descriptor_parent = Path(f"/proc/self/fd/{parent_fd}")
    staging_name = Path(tempfile.mkdtemp(prefix=staging_prefix, dir=descriptor_parent)).name
    staging = descriptor_parent / staging_name
    staging_identity = os.lstat(staging)
    binding = _OutputBinding(staging_parent, parent_fd, parent_identity, staging_name)
    binding_key = os.path.abspath(os.fspath(staging))
    _OUTPUT_BINDINGS[binding_key] = binding
    snapshot_root: Path | None = None
    snapshot_parent: Path | None = None
    try:
        snapshot_root, snapshot_parent = _snapshot_sources(root)
        # Revalidate the live inventory at the handoff from snapshot capture to
        # rendering.  Later edits during rendering are intentionally harmless:
        # every copied/rendered byte and provenance digest still comes solely
        # from the accepted private snapshot.
        _verify_live_sources_against_snapshot(root, snapshot_root)
        snapshot_canonical = snapshot_root / "packages" / "expskill"
        snapshot_platform = snapshot_canonical / "opencode"
        _copy_platform_source(snapshot_platform, staging)
        _copy_canonical_source(snapshot_canonical, staging)
        try:
            rendered = render_all(snapshot_root)
        except RenderError as error:
            raise BuildError(str(error)) from error
        _write_rendered(staging, rendered)
        _write_provenance(snapshot_root, staging)
        _normalize_artifact_modes(staging)
        _replace_output(staging, output)
    except (OSError, BuildError):
        _cleanup_staging(staging, staging_parent, staging_prefix, staging_identity)
        raise
    finally:
        if snapshot_parent is not None:
            shutil.rmtree(snapshot_parent, ignore_errors=True)
        _OUTPUT_BINDINGS.pop(binding_key, None)
        os.close(parent_fd)
    return output


# Stable short aliases for callers that import the builder as a library.
build = build_opencode_package
build_package = build_opencode_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained OpenCode package.")
    parser.add_argument(
        "output_directory",
        nargs="?",
        type=Path,
        help="explicit package output directory",
    )
    parser.add_argument(
        "--root",
        "--repo-root",
        dest="root",
        type=Path,
        default=None,
        help="repository root (defaults to this checkout)",
    )
    parser.add_argument(
        "--output-dir",
        "--output",
        dest="output_dir",
        type=Path,
        default=None,
        help="explicit package output directory",
    )
    arguments = parser.parse_args(argv)
    output_dir = arguments.output_dir or arguments.output_directory
    if output_dir is None:
        parser.error("an explicit output directory is required")
    try:
        output = build_opencode_package(arguments.root, output_dir)
    except (BuildError, OSError) as error:
        print(f"build error: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
