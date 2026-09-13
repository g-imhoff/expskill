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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# This module is also a documented command-line entry point.  Set the flag
# before importing any local modules so direct invocations cannot populate a
# checkout with Python bytecode caches.
sys.dont_write_bytecode = True

try:
    from scripts.render_opencode import RenderError, render_all
    from scripts.artifact_contract import (
        ARTIFACT_DIRECTORY_MODE,
        ARTIFACT_FILE_MODE,
        ARTIFACT_MTIME,
        COPY_FILES,
        COPY_LICENSES,
        COPY_TREES,
        PLATFORM_FILES,
        PLATFORM_PLUGIN_DIRECTORY,
        PLATFORM_PLUGIN_FILES,
        PROVENANCE_SCHEMA_VERSION,
        canonical_provenance,
    )
except ModuleNotFoundError:
    from render_opencode import RenderError, render_all
    from artifact_contract import (
        ARTIFACT_DIRECTORY_MODE,
        ARTIFACT_FILE_MODE,
        ARTIFACT_MTIME,
        COPY_FILES,
        COPY_LICENSES,
        COPY_TREES,
        PLATFORM_FILES,
        PLATFORM_PLUGIN_DIRECTORY,
        PLATFORM_PLUGIN_FILES,
        PROVENANCE_SCHEMA_VERSION,
        canonical_provenance,
    )


PYTHON_CACHE_SUFFIXES = {".pyc", ".pyo"}


class BuildError(RuntimeError):
    """Raised when package inputs or the explicit output target are unsafe."""


@dataclass(frozen=True)
class _OutputBinding:
    """An output parent bound to an open directory descriptor."""

    parent: Path
    parent_fd: int
    parent_identity: tuple[int, int]
    staging_name: str
    staging_identity: tuple[int, int]
    staging_fd: int
    require_lexical_parent: bool


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


def _bound_output_path(value: Path | str) -> Path:
    """Normalize only the label for an output whose parent is already bound."""

    if value is None:  # type: ignore[comparison-overlap]
        raise BuildError("an explicit output directory is required")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    output = Path(os.path.abspath(os.fspath(candidate)))
    if not output.name:
        raise BuildError("descriptor-bound output must name one child directory")
    return output


def _validate_output_target(
    root: Path,
    canonical_root: Path,
    output: Path,
    *,
    output_parent_fd: int | None = None,
) -> None:
    source_roots = (root.resolve(strict=True), canonical_root.resolve(strict=True))
    for source_root in source_roots:
        if output == source_root:
            raise BuildError("generated output must not be a source root")
        if output in source_root.parents:
            raise BuildError("generated output must not be an ancestor of a source root")
        if source_root in output.parents:
            raise BuildError("generated output must be outside the repository source tree")
    if output_parent_fd is None:
        occupied = output.exists() or output.is_symlink()
    else:
        try:
            os.stat(output.name, dir_fd=output_parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            occupied = False
        except OSError as error:
            raise BuildError(
                f"output target cannot be inspected through its bound parent: {error}"
            ) from error
        else:
            occupied = True
    if occupied:
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


def _staging_binding_for(path: Path) -> tuple[_OutputBinding, Path] | None:
    """Return the descriptor binding and relative path for staged output."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    for binding in _OUTPUT_BINDINGS.values():
        staging_root = binding.parent / binding.staging_name
        try:
            return binding, absolute.relative_to(staging_root)
        except ValueError:
            continue
    return None


def _mkdir_at(parent_fd: int, relative: Path) -> int:
    descriptor = parent_fd
    try:
        for component in relative.parts:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(component, mode=ARTIFACT_DIRECTORY_MODE, dir_fd=descriptor)
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            if descriptor != parent_fd:
                os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        if descriptor != parent_fd:
            os.close(descriptor)
        raise


def _write_bytes_at(parent_fd: int, relative: Path, contents: bytes) -> None:
    directory_fd = _mkdir_at(parent_fd, relative.parent)
    descriptor = os.open(
        relative.name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        mode=ARTIFACT_FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(contents)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, ARTIFACT_FILE_MODE)
        os.utime(descriptor, (ARTIFACT_MTIME, ARTIFACT_MTIME))
    finally:
        os.close(descriptor)
        if directory_fd != parent_fd:
            os.close(directory_fd)


def _stat_at(parent_fd: int, relative: Path) -> os.stat_result:
    directory_fd = _mkdir_at(parent_fd, relative.parent)
    try:
        return os.stat(relative.name, dir_fd=directory_fd, follow_symlinks=False)
    finally:
        if directory_fd != parent_fd:
            os.close(directory_fd)


def _safe_copy_file(source: Path, target: Path, label: str) -> None:
    _ensure_regular_file(source, label)
    binding = _staging_binding_for(target)
    if binding is not None:
        output_binding, relative = binding
        try:
            contents = source.read_bytes()
            _write_bytes_at(output_binding.staging_fd, relative, contents)
        except OSError as error:
            raise BuildError(f"cannot write staged output {target}: {error}") from error
        try:
            metadata = _stat_at(output_binding.staging_fd, relative)
        except OSError as error:
            raise BuildError(f"staged output cannot be inspected: {target}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise BuildError(f"generated output entry is not a regular file: {target}")
        return
    else:
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
    for relative in (
        Path("scripts/artifact_contract.py"),
        Path("scripts/build_opencode_package.py"),
        Path("scripts/render_opencode.py"),
    ):
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
        snapshot_manifest: list[tuple[str, str]] = []
        for relative, source in initial:
            target = snapshot_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # Read once and derive the digest from the exact bytes written to
            # the private snapshot.  A second read of the live path here could
            # describe a different byte set than the one rendered below.
            contents = source.read_bytes()
            target.write_bytes(contents)
            # Hash exactly the bytes written above, without a second read of
            # either the live source or the snapshot target.
            snapshot_manifest.append((relative, hashlib.sha256(contents).hexdigest()))
        written_manifest = _snapshot_digest_manifest(snapshot_root)
        final_manifest = _source_digest_manifest(root)
        if snapshot_manifest != written_manifest or snapshot_manifest != final_manifest:
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
        binding = _staging_binding_for(target)
        if binding is not None:
            output_binding, target_relative = binding
            try:
                _write_bytes_at(
                    output_binding.staging_fd,
                    target_relative,
                    contents.encode("utf-8"),
                )
                metadata = _stat_at(output_binding.staging_fd, target_relative)
            except OSError as error:
                raise BuildError(f"rendered output cannot be written: {target}: {error}") from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise BuildError(f"rendered output is not a regular file: {target}")
        else:
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
    binding = _staging_binding_for(target)
    if binding is not None:
        output_binding, relative = binding
        try:
            _write_bytes_at(output_binding.staging_fd, relative, canonical_provenance(inputs))
        except OSError as error:
            raise BuildError(f"provenance cannot be written: {target}: {error}") from error
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_provenance(inputs))
    return target


def _snapshot_digest_manifest(root: Path) -> list[tuple[str, str]]:
    """Return the digest inventory for an already-created private snapshot."""

    return [
        (relative, hashlib.sha256(path.read_bytes()).hexdigest())
        for relative, path in _provenance_sources(root)
    ]


def _source_digest_manifest(root: Path) -> list[tuple[str, str]]:
    """Read each live source once and hash the exact bytes observed."""

    return [
        (relative, hashlib.sha256(path.read_bytes()).hexdigest())
        for relative, path in _provenance_sources(root)
    ]


def _verify_live_sources_against_snapshot(root: Path, snapshot_root: Path) -> None:
    """Reject any live source edit observed after the snapshot was accepted."""

    if _source_digest_manifest(root) != _snapshot_digest_manifest(snapshot_root):
        raise BuildError("repository sources changed before OpenCode publication")


def _normalize_artifact_modes(output_root: Path) -> None:
    """Make artifact permissions and timestamps independent of umask and clock."""

    binding = _staging_binding_for(output_root)
    if binding is not None:
        output_binding, relative = binding
        if relative != Path("."):
            raise BuildError(f"artifact normalization root is not staging root: {output_root}")

        def normalize_fd(directory_fd: int) -> None:
            entries = list(os.scandir(directory_fd))
            for entry in entries:
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise BuildError(f"artifact entry must not be a symlink: {entry.name}")
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(entry.name, _directory_open_flags(), dir_fd=directory_fd)
                    try:
                        opened = os.fstat(child_fd)
                        if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
                            raise BuildError(f"artifact entry changed during normalization: {entry.name}")
                        normalize_fd(child_fd)
                    finally:
                        os.close(child_fd)
                    mode = ARTIFACT_DIRECTORY_MODE
                elif stat.S_ISREG(metadata.st_mode):
                    mode = ARTIFACT_FILE_MODE
                else:
                    raise BuildError(f"artifact entry is not a regular file or directory: {entry.name}")
                try:
                    os.chmod(entry.name, mode, dir_fd=directory_fd, follow_symlinks=False)
                    os.utime(
                        entry.name,
                        (ARTIFACT_MTIME, ARTIFACT_MTIME),
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise BuildError(f"artifact metadata cannot be normalized: {entry.name}: {error}") from error

        normalize_fd(output_binding.staging_fd)
        os.fchmod(output_binding.staging_fd, ARTIFACT_DIRECTORY_MODE)
        os.utime(output_binding.staging_fd, (ARTIFACT_MTIME, ARTIFACT_MTIME))
        return

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


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory_chain(path: Path, *, create: bool) -> int:
    """Open an absolute directory one component at a time from ``/``."""

    candidate = Path(path)
    if not candidate.is_absolute():
        raise BuildError(f"output parent must be absolute: {candidate}")
    descriptor: int | None = None
    try:
        descriptor = os.open(Path(candidate.anchor), _directory_open_flags())
        for component in candidate.parts[1:]:
            try:
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=ARTIFACT_DIRECTORY_MODE, dir_fd=descriptor)
                child = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise BuildError(f"output parent cannot be opened safely: {candidate}: {error}") from error
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _open_output_parent(parent: Path) -> tuple[int, tuple[int, int]]:
    """Open and identity-check the output parent through anchored descriptors."""

    descriptor = _open_directory_chain(parent, create=True)
    try:
        bound = os.fstat(descriptor)
        if not stat.S_ISDIR(bound.st_mode):
            raise BuildError(f"output parent is not a directory: {parent}")
        return descriptor, (bound.st_dev, bound.st_ino)
    except Exception:
        os.close(descriptor)
        raise


def _retain_output_parent(descriptor: int) -> tuple[int, tuple[int, int]]:
    """Duplicate and validate a caller-retained exact output parent."""

    try:
        retained = os.dup(descriptor)
    except OSError as error:
        raise BuildError(f"bound output parent descriptor is invalid: {error}") from error
    try:
        metadata = os.fstat(retained)
        if not stat.S_ISDIR(metadata.st_mode):
            raise BuildError("bound output parent descriptor is not a directory")
        return retained, (metadata.st_dev, metadata.st_ino)
    except Exception:
        os.close(retained)
        raise


def _binding_is_current(binding: _OutputBinding) -> bool:
    try:
        bound = os.fstat(binding.parent_fd)
    except OSError:
        return False
    if not (
        stat.S_ISDIR(bound.st_mode)
        and (bound.st_dev, bound.st_ino) == binding.parent_identity
    ):
        return False
    if not binding.require_lexical_parent:
        return True
    observed_fd: int | None = None
    try:
        observed_fd = _open_directory_chain(binding.parent, create=False)
        observed = os.fstat(observed_fd)
        return _same_directory_identity(bound, observed)
    except (OSError, BuildError):
        return False
    finally:
        if observed_fd is not None:
            os.close(observed_fd)


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


def _remove_tree_at(parent_fd: int, name: str, expected: os.stat_result | None = None) -> None:
    """Remove one directory through its opened descriptor and parent binding."""

    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if expected is not None and (
        metadata.st_dev != expected.st_dev or metadata.st_ino != expected.st_ino
    ):
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return
    try:
        descriptor = os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError:
        return
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
            return
        for entry in list(os.scandir(descriptor)):
            child_metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(child_metadata.st_mode) and not stat.S_ISLNK(child_metadata.st_mode):
                _remove_tree_at(descriptor, entry.name, child_metadata)
            elif stat.S_ISREG(child_metadata.st_mode) or stat.S_ISLNK(child_metadata.st_mode):
                try:
                    current = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                    if (
                        current.st_dev == child_metadata.st_dev
                        and current.st_ino == child_metadata.st_ino
                    ):
                        os.unlink(entry.name, dir_fd=descriptor)
                except OSError:
                    pass
            else:
                # Unknown entries are preserved rather than guessed at.
                continue
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError:
            pass
    finally:
        os.close(descriptor)


def _fsync_tree_at(directory_fd: int) -> None:
    """Durably flush one exact descriptor-bound artifact tree."""

    flags = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    for entry in sorted(os.scandir(directory_fd), key=lambda item: item.name):
        metadata = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            child = os.open(
                entry.name, _directory_open_flags(), dir_fd=directory_fd
            )
            try:
                opened = os.fstat(child)
                if not _same_directory_identity(metadata, opened):
                    raise BuildError(
                        f"artifact directory changed before fsync: {entry.name}"
                    )
                _fsync_tree_at(child)
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            child = os.open(entry.name, os.O_RDONLY | flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(child)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_dev != metadata.st_dev
                    or opened.st_ino != metadata.st_ino
                ):
                    raise BuildError(
                        f"artifact file changed before fsync: {entry.name}"
                    )
                os.fsync(child)
            finally:
                os.close(child)
        else:
            raise BuildError(
                f"artifact entry is not regular before fsync: {entry.name}"
            )
    os.fsync(directory_fd)


def _replace_output(staging: Path, output: Path) -> None:
    """Publish one complete artifact with no target replacement."""

    key = os.path.abspath(os.fspath(staging))
    binding = _OUTPUT_BINDINGS.get(key)
    temporary_binding = False
    if binding is None:
        parent_fd, identity = _open_output_parent(output.parent)
        try:
            staging_metadata = os.stat(
                staging.name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError as error:
            raise BuildError(f"staging directory cannot be inspected: {error}") from error
        try:
            staging_fd = os.open(
                staging.name,
                _directory_open_flags(),
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise BuildError(f"staging directory cannot be opened: {error}") from error
        binding = _OutputBinding(
            output.parent,
            parent_fd,
            identity,
            staging.name,
            (staging_metadata.st_dev, staging_metadata.st_ino),
            staging_fd,
            True,
        )
        temporary_binding = True
    try:
        if not _binding_is_current(binding):
            raise BuildError(f"output parent changed during publication: {output.parent}")
        try:
            staging_metadata = os.stat(
                binding.staging_name,
                dir_fd=binding.parent_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise BuildError(f"staging directory changed during publication: {error}") from error
        if (
            not stat.S_ISDIR(staging_metadata.st_mode)
            or stat.S_ISLNK(staging_metadata.st_mode)
            or (staging_metadata.st_dev, staging_metadata.st_ino) != binding.staging_identity
        ):
            raise BuildError(f"staging directory changed during publication: {staging}")
        output_name = output.name
        # Complete publication requires a platform-supported exclusive
        # directory rename.  A child-by-child publication cannot provide the same
        # complete-or-absent guarantee and is intentionally forbidden.
        try:
            published = _renameat2_noreplace(binding.parent_fd, binding.staging_name, output_name)
        except OSError as error:
            raise BuildError(f"cannot publish OpenCode artifact: {error}") from error
        if not published:
            raise BuildError("exclusive atomic directory publication is unavailable")
        os.fsync(binding.parent_fd)
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
            os.close(binding.staging_fd)
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
        _remove_tree_at(binding.parent_fd, binding.staging_name, metadata)
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
    # This path is used only by the public two-argument seam.  Re-open and
    # re-verify the exact leaf identity immediately before descriptor-bound
    # cleanup; never recursively reopen an untrusted lexical name.
    try:
        parent_fd = _open_directory_chain(staging_parent, create=False)
    except BuildError:
        return
    try:
        _remove_tree_at(parent_fd, staging.name, metadata)
    finally:
        os.close(parent_fd)


def build_opencode_package(
    repo_root: Path | str | None = None,
    output_dir: Path | str | None = None,
    *,
    output_parent_fd: int | None = None,
) -> Path:
    """Build the package into an explicit output directory and return it."""

    if output_dir is None:
        raise BuildError("an explicit output directory is required")
    root = _resolve_root(repo_root)
    output = (
        _output_path(output_dir)
        if output_parent_fd is None
        else _bound_output_path(output_dir)
    )
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
    staging_parent = output.parent
    if output_parent_fd is None:
        _validate_output_target(root, canonical_root_resolved, output)
        parent_fd, parent_identity = _open_output_parent(staging_parent)
        require_lexical_parent = True
    else:
        parent_fd, parent_identity = _retain_output_parent(output_parent_fd)
        require_lexical_parent = False
        try:
            _validate_output_target(
                root,
                canonical_root_resolved,
                output,
                output_parent_fd=parent_fd,
            )
        except Exception:
            os.close(parent_fd)
            raise
    staging_prefix = f".{output.name}."
    staging_name = ""
    staging_identity: os.stat_result | None = None
    for _attempt in range(32):
        candidate_name = f"{staging_prefix}{uuid.uuid4().hex}"
        try:
            os.mkdir(candidate_name, mode=ARTIFACT_DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as error:
            os.close(parent_fd)
            raise BuildError(f"cannot create private staging directory: {error}") from error
        staging_name = candidate_name
        staging_identity = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
        break
    if not staging_name or staging_identity is None:
        os.close(parent_fd)
        raise BuildError("cannot allocate a unique staging directory")
    staging = staging_parent / staging_name
    try:
        staging_fd = os.open(staging_name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError as error:
        _remove_tree_at(parent_fd, staging_name, staging_identity)
        os.close(parent_fd)
        raise BuildError(f"staging directory cannot be opened: {error}") from error
    binding = _OutputBinding(
        staging_parent,
        parent_fd,
        parent_identity,
        staging_name,
        (staging_identity.st_dev, staging_identity.st_ino),
        staging_fd,
        require_lexical_parent,
    )
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
        # Rendering, copying, and provenance generation may execute arbitrary
        # source readers.  Revalidate the complete live source inventory at
        # the publication handoff so a mutation during render cannot publish a
        # candidate that no longer corresponds to the checkout.
        _verify_live_sources_against_snapshot(root, snapshot_root)
        _fsync_tree_at(binding.staging_fd)
        _replace_output(staging, output)
    except (OSError, BuildError):
        _cleanup_staging(staging, staging_parent, staging_prefix, staging_identity)
        raise
    finally:
        if snapshot_parent is not None:
            shutil.rmtree(snapshot_parent, ignore_errors=True)
        _OUTPUT_BINDINGS.pop(binding_key, None)
        os.close(binding.staging_fd)
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
