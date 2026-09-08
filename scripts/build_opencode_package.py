"""Build a deterministic, self-contained ``opencode-expskill`` package.

The checked-in OpenCode directory contains only platform-owned source.  This
builder stages the publishable package in an explicit output directory and
materializes the shared Codex assets as regular files.  It never follows
symlink inputs and never emits generated output inside the tracked source
package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterable

try:
    from scripts.render_opencode import RenderError, render_all
except ModuleNotFoundError:
    from render_opencode import RenderError, render_all


PROVENANCE_SCHEMA_VERSION = "opencode-provenance.v1"
PLATFORM_FILES = ("agents.json", "package.json", "README.md", "LICENSE", "index.js")
PLATFORM_PLUGIN_DIRECTORY = "plugins"
COPY_TREES = ("skills", "scripts")
COPY_FILES = (
    Path("assets/execution-policy.json"),
)
COPY_LICENSES = Path("third-party/licenses")
PYTHON_CACHE_SUFFIXES = {".pyc", ".pyo"}


class BuildError(RuntimeError):
    """Raised when package inputs or the explicit output target are unsafe."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_root(value: Path | str | None) -> Path:
    candidate = Path(value).expanduser() if value is not None else repository_root()
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
    candidate = candidate.resolve(strict=False)
    if candidate.exists() and candidate.is_symlink():
        raise BuildError(f"output directory must not be a symlink: {candidate}")
    return candidate


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
    return sorted(sources, key=lambda item: item[0])


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


def _replace_output(staging: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        if output.is_symlink() or not output.is_dir():
            raise BuildError(f"output target is not a regular directory: {output}")
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(output)


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
    _ensure_regular_directory(canonical_root, "canonical package")
    _ensure_regular_directory(platform_root, "OpenCode platform source")
    # Validate the complete set of source trees before staging so a stray
    # symlink cannot hide in an un-copied input directory.
    list(_iter_regular_files(platform_root, "OpenCode platform source"))
    list(
        _iter_regular_files(canonical_root / "assets" / "agents", "canonical agent profiles")
    )
    try:
        output.relative_to(canonical_root)
    except ValueError:
        pass
    else:
        raise BuildError("generated output must be outside packages/expskill")

    staging_parent = output.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=staging_parent))
    try:
        _copy_platform_source(platform_root, staging)
        _copy_canonical_source(canonical_root, staging)
        try:
            rendered = render_all(root)
        except RenderError as error:
            raise BuildError(str(error)) from error
        _write_rendered(staging, rendered)
        _write_provenance(root, staging)
        _replace_output(staging, output)
    except (OSError, BuildError):
        if staging.exists():
            shutil.rmtree(staging)
        raise
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
