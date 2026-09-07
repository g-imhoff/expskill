"""Materialize generated npm assets from the canonical Codex package.

The OpenCode npm tarball cannot retain a directory symlink that points outside
its package root. This generator keeps the checked-in package mirror complete
and byte-identical to its canonical sources. Regenerate it with:

    python3 scripts/sync_opencode_package.py
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


TREE_MAPPINGS = (
    (Path("packages/codex/skills"), Path("skills")),
    (Path("packages/codex/scripts"), Path("scripts")),
)
FILE_MAPPINGS = (
    (
        Path("packages/codex/assets/execution-policy.json"),
        Path("assets/execution-policy.json"),
    ),
    (
        Path("packages/codex/third-party/licenses/mattpocock-skills-MIT.txt"),
        Path("third-party/licenses/mattpocock-skills-MIT.txt"),
    ),
    (
        Path("packages/codex/third-party/licenses/pstack-MIT.txt"),
        Path("third-party/licenses/pstack-MIT.txt"),
    ),
)
MANAGED_ROOTS = tuple(target for _source, target in TREE_MAPPINGS) + (
    Path("assets"),
    Path("third-party"),
)


class SyncError(RuntimeError):
    pass


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolved_root(repo_root: Path | None) -> Path:
    candidate = Path(repo_root).expanduser() if repo_root is not None else _repository_root()
    try:
        root = candidate.resolve(strict=True)
    except OSError as error:
        raise SyncError(f"repository root cannot be resolved: {candidate}: {error}") from error
    package_root = root / "packages" / "opencode"
    if package_root.is_symlink() or not package_root.is_dir():
        raise SyncError(
            f"opencode package root is not a regular directory: {package_root}"
        )
    return root


def _regular_source(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise SyncError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise SyncError(f"{label} is not a regular file: {path}")
    return path


def _is_python_cache(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def generated_asset_sources(repo_root: Path | None = None) -> dict[Path, Path]:
    root = _resolved_root(repo_root)
    generated: dict[Path, Path] = {}
    for source_relative, target_prefix in TREE_MAPPINGS:
        source_root = root / source_relative
        if source_root.is_symlink() or not source_root.is_dir():
            raise SyncError(f"canonical asset tree is not a regular directory: {source_root}")
        for source in sorted(source_root.rglob("*")):
            relative = source.relative_to(source_root)
            if _is_python_cache(relative):
                continue
            if source.is_symlink():
                raise SyncError(f"canonical asset entry must not be a symlink: {source}")
            if source.is_dir():
                continue
            _regular_source(source, "canonical asset entry")
            generated[target_prefix / relative] = source
    for source_relative, target_relative in FILE_MAPPINGS:
        generated[target_relative] = _regular_source(
            root / source_relative, "canonical package asset"
        )
    return generated


def _expected_directories(paths: set[Path]) -> set[Path]:
    expected: set[Path] = set(MANAGED_ROOTS)
    for path in paths:
        parent = path.parent
        while parent != Path("."):
            expected.add(parent)
            parent = parent.parent
    return expected


def check_generated_assets(repo_root: Path | None = None) -> list[str]:
    root = _resolved_root(repo_root)
    package_root = root / "packages" / "opencode"
    expected = generated_asset_sources(root)
    expected_files = set(expected)
    expected_directories = _expected_directories(expected_files)
    actual_files: set[Path] = set()
    actual_directories: set[Path] = set()
    problems: list[str] = []

    for managed_root in MANAGED_ROOTS:
        target_root = package_root / managed_root
        if target_root.is_symlink():
            problems.append(f"generated opencode package entry is a symlink: {managed_root}")
            continue
        if not target_root.is_dir():
            problems.append(
                f"generated opencode package directory is missing: {managed_root}"
            )
            continue
        actual_directories.add(managed_root)
        for target in sorted(target_root.rglob("*")):
            relative = target.relative_to(package_root)
            if target.is_symlink():
                problems.append(
                    f"generated opencode package entry is a symlink: {relative}"
                )
            elif target.is_dir():
                actual_directories.add(relative)
            elif target.is_file():
                actual_files.add(relative)
            else:
                problems.append(
                    f"generated opencode package entry is not a regular file: {relative}"
                )

    missing_files = sorted(expected_files - actual_files)
    unexpected_files = sorted(actual_files - expected_files)
    missing_directories = sorted(expected_directories - actual_directories)
    unexpected_directories = sorted(actual_directories - expected_directories)
    if missing_files:
        problems.append(
            "generated opencode package files are missing: "
            + ", ".join(str(path) for path in missing_files)
        )
    if unexpected_files:
        problems.append(
            "generated opencode package files are unexpected: "
            + ", ".join(str(path) for path in unexpected_files)
        )
    if missing_directories:
        problems.append(
            "generated opencode package directories are missing: "
            + ", ".join(str(path) for path in missing_directories)
        )
    if unexpected_directories:
        problems.append(
            "generated opencode package directories are unexpected: "
            + ", ".join(str(path) for path in unexpected_directories)
        )

    for relative in sorted(expected_files & actual_files):
        target = package_root / relative
        try:
            current = target.read_bytes()
            canonical = expected[relative].read_bytes()
        except OSError as error:
            problems.append(
                f"generated opencode package asset cannot be read: {relative}: {error}"
            )
            continue
        if current != canonical:
            problems.append(f"generated opencode package asset differs: {relative}")
    return problems


def _remove_managed_root(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def sync(repo_root: Path | None = None) -> tuple[Path, ...]:
    root = _resolved_root(repo_root)
    generated_asset_sources(root)
    package_root = root / "packages" / "opencode"
    for managed_root in MANAGED_ROOTS:
        _remove_managed_root(package_root / managed_root)
    for source_relative, target_relative in TREE_MAPPINGS:
        shutil.copytree(root / source_relative, package_root / target_relative)
    for source_relative, target_relative in FILE_MAPPINGS:
        target = package_root / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / source_relative, target)
    problems = check_generated_assets(root)
    if problems:
        raise SyncError("generated package validation failed: " + "; ".join(problems))
    return tuple(package_root / target for target in MANAGED_ROOTS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize generated OpenCode npm package assets."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when generated package assets differ from their canonical sources",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.check:
            problems = check_generated_assets()
            if problems:
                for problem in problems:
                    print(problem)
                return 1
            print("opencode package assets match their canonical sources")
            return 0
        synced = sync()
        for path in synced:
            print(f"synchronized {path}")
    except (OSError, SyncError) as error:
        print(f"sync error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
