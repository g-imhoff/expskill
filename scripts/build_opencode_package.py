"""Build a deterministic, self-contained ``opencode-expskill`` package.

The checked-in OpenCode directory contains only platform-owned source.  This
builder stages the publishable package in an explicit output directory and
materializes the universal source assets as regular files.  It never follows
symlink inputs and never emits generated output inside the tracked source
package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
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
            target.write_bytes(source.read_bytes())
        final = _provenance_sources(root)
        initial_digest = [
            (relative, hashlib.sha256(path.read_bytes()).hexdigest())
            for relative, path in initial
        ]
        final_digest = [
            (relative, hashlib.sha256(path.read_bytes()).hexdigest())
            for relative, path in final
        ]
        if initial_digest != final_digest:
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


def _replace_output(staging: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the final path with mkdir.  Unlike rename/replace, mkdir is an
    # atomic no-replace operation: a target created after preflight can never
    # be clobbered.  Move the already-complete children into that reservation.
    try:
        output.mkdir(mode=ARTIFACT_DIRECTORY_MODE)
    except FileExistsError as error:
        raise BuildError(f"output target must not already exist: {output}") from error
    except OSError as error:
        raise BuildError(f"output target cannot be reserved: {output}: {error}") from error
    try:
        output_identity = os.lstat(output)
    except OSError as error:
        raise BuildError(f"reserved output cannot be inspected: {output}: {error}") from error
    try:
        for child in sorted(staging.iterdir(), key=lambda item: item.name):
            destination = output / child.name
            if os.path.lexists(destination):
                raise BuildError(f"output target was populated during publish: {destination}")
            child.rename(destination)
        staging.rmdir()
        os.chmod(output, ARTIFACT_DIRECTORY_MODE)
        os.utime(output, (ARTIFACT_MTIME, ARTIFACT_MTIME))
    except (OSError, BuildError):
        # The reservation is ours, but never remove it if an unexpected child
        # appeared after reservation; that child may be foreign state.
        try:
            current_identity = os.lstat(output)
        except OSError:
            current_identity = None
        if (
            current_identity is not None
            and current_identity.st_dev == output_identity.st_dev
            and current_identity.st_ino == output_identity.st_ino
        ):
            try:
                children = list(output.iterdir())
            except OSError:
                children = []
            if not children:
                try:
                    output.rmdir()
                except OSError:
                    pass
        raise


def _cleanup_staging(
    staging: Path,
    staging_parent: Path,
    prefix: str,
    identity: os.stat_result | None = None,
) -> None:
    """Remove only the builder-owned staging directory after a failed build."""

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
    staging_prefix = f".{output.name}."
    staging = Path(tempfile.mkdtemp(prefix=staging_prefix, dir=staging_parent))
    staging_identity = os.lstat(staging)
    snapshot_root: Path | None = None
    snapshot_parent: Path | None = None
    try:
        snapshot_root, snapshot_parent = _snapshot_sources(root)
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
