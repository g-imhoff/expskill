"""Build a self-contained Codex package from canonical content.

The Codex CLI copies a plugin into its cache and does not preserve source-tree
symlinks.  This builder therefore emits the regular-file package that the CLI
can install, while keeping that generated package outside the authored
checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Iterable

try:
    from scripts.artifact_contract import (
        ARTIFACT_DIRECTORY_MODE,
        ARTIFACT_FILE_MODE,
        ARTIFACT_MTIME,
    )
    from scripts.render_codex import RenderError, render_all
except ModuleNotFoundError:
    from artifact_contract import (  # type: ignore[no-redef]
        ARTIFACT_DIRECTORY_MODE,
        ARTIFACT_FILE_MODE,
        ARTIFACT_MTIME,
    )
    from render_codex import RenderError, render_all  # type: ignore[no-redef]


PROVENANCE_SCHEMA_VERSION = "codex-provenance.v1"
PYTHON_CACHE_SUFFIXES = {".pyc", ".pyo"}


class BuildError(RuntimeError):
    """Raised when a Codex package input or output is unsafe."""


def _reject_symlink_components(path: Path, label: str) -> None:
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


def _root(repo_root: Path | str | None) -> Path:
    candidate = Path(repo_root).expanduser() if repo_root is not None else Path(__file__).absolute().parents[1]
    _reject_symlink_components(candidate, "repository root")
    try:
        result = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BuildError(f"repository root cannot be resolved: {candidate}: {error}") from error
    if not result.is_dir() or result.is_symlink():
        raise BuildError(f"repository root is not a regular directory: {result}")
    return result


def _output(output_dir: Path | str) -> Path:
    candidate = Path(output_dir).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    _reject_symlink_components(candidate, "output directory")
    if candidate.exists() or candidate.is_symlink():
        raise BuildError(f"output target must not already exist: {candidate}")
    if not candidate.parent.exists() or not candidate.parent.is_dir():
        raise BuildError(f"output parent must be an existing directory: {candidate.parent}")
    return candidate.resolve(strict=False)


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BuildError(f"{label} is not a regular file: {path}")


def _iter_files(root: Path, label: str) -> Iterable[tuple[Path, Path]]:
    if root.is_symlink() or not root.is_dir():
        raise BuildError(f"{label} is not a regular directory: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"{label} entry must not be a symlink: {path}")
        if "__pycache__" in path.parts or path.suffix in PYTHON_CACHE_SUFFIXES:
            continue
        if path.is_file():
            yield path.relative_to(root), path


def _copy_tree(source: Path, target: Path, label: str) -> list[tuple[Path, Path]]:
    copied: list[tuple[Path, Path]] = []
    for relative, path in _iter_files(source, label):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _regular_file(path, label)
        shutil.copyfile(path, destination)
        copied.append((destination, path))
    return copied


def _empty_owned_directory(descriptor: int) -> None:
    """Remove a pinned directory's entries without following pathname swaps."""

    for name in os.listdir(descriptor):
        try:
            child = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(child.st_mode) and not stat.S_ISLNK(child.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            try:
                _empty_owned_directory(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=descriptor)
        elif stat.S_ISREG(child.st_mode) or stat.S_ISLNK(child.st_mode):
            os.unlink(name, dir_fd=descriptor)
        else:
            raise BuildError(f"generated staging entry has an unexpected type: {name}")


def _discard_owned_staging(
    staging_fd: int, staging: Path, expected: tuple[int, int]
) -> None:
    """Reclaim only the staging tree still holding its creation identity.

    A pathname swap after creation is preserved, never reclaimed.  Cleanup is
    best effort and never masks the build fault that caused it (unlike the
    probe/preflight reclaimers, which must abort the operation on mismatch).
    """

    try:
        current = os.lstat(staging)
    except FileNotFoundError:
        return
    except OSError:
        return
    if (
        (current.st_dev, current.st_ino) != expected
        or not stat.S_ISDIR(current.st_mode)
    ):
        return
    try:
        _empty_owned_directory(staging_fd)
        os.fsync(staging_fd)
        current = os.lstat(staging)
        if (current.st_dev, current.st_ino) != expected:
            return
        # Empty-only removal cannot delete data even if the public name is
        # substituted after this final check.
        os.rmdir(staging)
    except (OSError, BuildError):
        return


def _require_staging_identity(
    staging_fd: int, staging: Path, expected: tuple[int, int]
) -> None:
    """Refuse to publish a staging pathname that no longer holds its build."""

    try:
        current = os.lstat(staging)
    except OSError as error:
        raise BuildError(f"Codex package staging candidate changed: {staging}: {error}") from error
    if (
        (current.st_dev, current.st_ino) != expected
        or not stat.S_ISDIR(current.st_mode)
    ):
        raise BuildError(f"Codex package staging candidate changed: {staging}")
    opened = os.fstat(staging_fd)
    if (opened.st_dev, opened.st_ino) != expected:
        raise BuildError(f"Codex package staging candidate changed: {staging}")


def _write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def _write_manifest(package: Path, output: Path) -> None:
    source = package / ".codex-plugin" / "plugin.json"
    _regular_file(source, "Codex bridge manifest")
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildError(f"Codex bridge manifest is invalid: {error}") from error
    if not isinstance(manifest, dict):
        raise BuildError("Codex bridge manifest must contain an object")
    manifest["skills"] = "./skills/"
    manifest["hooks"] = "./hooks/hooks.json"
    _write_text(output / ".codex-plugin" / "plugin.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _write_provenance(sources: list[tuple[str, Path]], output: Path) -> None:
    rows = [
        {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for relative, path in sorted(sources, key=lambda item: item[0])
    ]
    _write_text(
        output / "provenance.json",
        json.dumps(
            {"schema_version": PROVENANCE_SCHEMA_VERSION, "inputs": rows},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _normalize(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        try:
            if path.is_symlink():
                raise BuildError(f"generated output entry must not be a symlink: {path}")
            if path.is_dir():
                os.chmod(path, ARTIFACT_DIRECTORY_MODE)
            elif path.is_file():
                os.chmod(path, ARTIFACT_FILE_MODE)
            os.utime(path, (ARTIFACT_MTIME, ARTIFACT_MTIME), follow_symlinks=False)
        except OSError as error:
            raise BuildError(f"generated output metadata could not be normalized: {path}: {error}") from error
    os.chmod(root, ARTIFACT_DIRECTORY_MODE)
    os.utime(root, (ARTIFACT_MTIME, ARTIFACT_MTIME), follow_symlinks=False)


def build_codex_package(
    repo_root: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> Path:
    if output_dir is None:
        raise BuildError("an explicit output directory is required")
    root = _root(repo_root)
    package = root / "plugins" / "expskill"
    output = _output(output_dir)
    _reject_symlink_components(package, "canonical package")
    content = package / "content"
    codex = package / "codex"
    required = (
        content / "skills",
        content / "agents",
        content / "agents.json",
        content / "scripts",
        content / "policies" / "execution-policy.json",
        content / "policies" / "skills.json",
        content / "policies" / "unslop-runtime.json",
        content / "third-party",
        codex / "skill-adapters",
        codex / "agents.json",
        codex / "hooks",
    )
    for path in required:
        _reject_symlink_components(path, "Codex package input")
    staging = Path(tempfile.mkdtemp(prefix=".codex-build-", dir=output.parent))
    staging_fd: int | None = None
    staging_identity: tuple[int, int] | None = None
    try:
        staging_fd = os.open(
            staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        owned = os.fstat(staging_fd)
        staging_identity = (owned.st_dev, owned.st_ino)
        sources: list[tuple[str, Path]] = []
        _write_manifest(package, staging)
        _copy_tree(content / "skills", staging / "skills", "canonical skills")
        _copy_tree(content / "scripts", staging / "scripts", "canonical shared scripts")
        _copy_tree(
            codex / "skill-adapters",
            staging / "skills",
            "Codex skill adapters",
        )
        _copy_tree(codex / "hooks", staging / "hooks", "Codex hooks")
        _copy_tree(content / "third-party", staging / "third-party", "canonical third-party content")
        policy = content / "policies" / "execution-policy.json"
        skill_policy = content / "policies" / "skills.json"
        unslop_runtime = content / "policies" / "unslop-runtime.json"
        _regular_file(policy, "canonical execution policy")
        _regular_file(skill_policy, "canonical skill policy")
        _regular_file(unslop_runtime, "canonical Unslop runtime policy")
        (staging / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(policy, staging / "assets" / "execution-policy.json")
        shutil.copyfile(skill_policy, staging / "assets" / "skill-policies.json")
        shutil.copyfile(unslop_runtime, staging / "assets" / "unslop-runtime.json")
        rendered = render_all(root)
        for relative, text in sorted(rendered.items()):
            _write_text(staging / relative, text)
        # The generated package is self-contained; its hook is at /hooks.
        hook = staging / "hooks" / "hooks.json"
        hook_text = hook.read_text(encoding="utf-8").replace("${PLUGIN_ROOT}/codex/hooks/", "${PLUGIN_ROOT}/hooks/")
        hook.write_text(hook_text, encoding="utf-8")

        source_paths = [
            package / ".codex-plugin" / "plugin.json",
            codex / "agents.json",
            content / "agents.json",
            root / "scripts" / "render_codex.py",
            root / "scripts" / "build_codex_package.py",
            root / "scripts" / "build_codex_marketplace.py",
            root / "scripts" / "artifact_contract.py",
        ]
        for tree in (
            content / "skills",
            content / "agents",
            content / "scripts",
            content / "policies",
            content / "third-party",
            codex / "skill-adapters",
            codex / "hooks",
        ):
            source_paths.extend(path for _relative, path in _iter_files(tree, f"Codex input {tree}"))
        source_paths = sorted(set(source_paths), key=lambda path: path.as_posix())
        for path in source_paths:
            _regular_file(path, "Codex provenance input")
            if "__pycache__" in path.parts or path.suffix in PYTHON_CACHE_SUFFIXES:
                continue
            sources.append((path.relative_to(root).as_posix(), path))
        _write_provenance(sources, staging)
        _normalize(staging)
        _require_staging_identity(staging_fd, staging, staging_identity)
        staging.rename(output)
    except RenderError as error:
        if staging_fd is not None and staging_identity is not None:
            _discard_owned_staging(staging_fd, staging, staging_identity)
        raise BuildError(str(error)) from error
    except BaseException:
        if staging_fd is not None and staging_identity is not None:
            _discard_owned_staging(staging_fd, staging, staging_identity)
        raise
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
    return output


build = build_codex_package
build_package = build_codex_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the untracked Codex runtime package.")
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=None)
    arguments = parser.parse_args(argv)
    try:
        build_codex_package(arguments.root, arguments.output)
    except BuildError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
