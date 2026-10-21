"""Shared build artifact cache for the test suite.

Session-scoped: each ``(builder_name, resolved ROOT)`` pair is built once per
test process and reused. Mutating tests must use :func:`checkout_copy` to get
an isolated writable copy; the shared artifact must never be mutated.

Failure paths stay fresh: a builder exception is never cached, so the next
caller retries a clean build.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

_CACHE: dict[tuple[str, Path], Path] = {}
_COPY_HOLDERS: list[tempfile.TemporaryDirectory[str]] = []


def _resolve_root(root: Path | str | None = None) -> Path:
    if root is None:
        return Path(__file__).resolve().parents[1].resolve()
    return Path(root).expanduser().resolve()


def shared_artifact(builder_name: str, root: Path | str | None = None) -> Path:
    """Return the session-shared artifact for ``builder_name``.

    Backed by module dict :data:`_CACHE` keyed on ``(builder_name, resolved
    ROOT)``. Builds once per test session (per worker under xdist).
    """
    repo = _resolve_root(root)
    key = (builder_name, repo)
    cached = _CACHE.get(key)
    if cached is not None:
        if cached.exists():
            return cached
        del _CACHE[key]
    if builder_name == "opencode":
        from scripts.build_opencode_package import build_opencode_package as builder
    elif builder_name == "codex":
        from scripts.build_codex_package import build_codex_package as builder
    elif builder_name == "hermes":
        from scripts.build_hermes_package import build_hermes_package as builder
    else:
        raise ValueError(f"unknown builder: {builder_name!r}")
    parent = tempfile.mkdtemp(prefix=f"shared-{builder_name}-")
    atexit.register(shutil.rmtree, parent, True)
    target = Path(parent) / "artifact"
    try:
        result = Path(builder(repo, target))
    except BaseException:
        shutil.rmtree(parent, ignore_errors=True)
        raise
    _CACHE[key] = result
    return result


def checkout_copy(source: Path | str, owner=None) -> Path:
    """Return an isolated writable copy of ``source`` via ``shutil.copytree``.

    If ``owner`` is a :class:`unittest.TestCase`, the backing temp dir is
    cleaned up with it; otherwise the copy lives until process exit.
    """
    src = Path(source)
    holder: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(
        prefix="artifact-copy-"
    )
    if owner is not None and hasattr(owner, "addCleanup"):
        owner.addCleanup(holder.cleanup)
    else:
        _COPY_HOLDERS.append(holder)
        atexit.register(holder.cleanup)
    dest = Path(holder.name) / src.name
    shutil.copytree(src, dest, symlinks=False, copy_function=shutil.copy2)
    return dest


try:
    import pytest as _pytest

    @_pytest.fixture(scope="session")
    def shared_artifact_factory():
        """Session-scoped pytest access to :func:`shared_artifact`."""
        return shared_artifact
except ImportError:  # pragma: no cover - pytest is always present in practice
    pass
