from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.build_opencode_package as build_module
from scripts.build_opencode_package import BuildError, build_opencode_package

ROOT = Path(__file__).resolve().parents[1]
CODEX_ROOT = ROOT / "packages" / "expskill"


class ProbeBase(BaseException):
    """Fault-injection exception that is outside the normal exception tree."""


def fd_snapshot() -> set[int]:
    return {int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()}


NODE = shutil.which("node")
NPM = shutil.which("npm")
needs_node_and_npm = unittest.skipUnless(
    NODE and NPM, "node and npm are required for the opencode package smoke test"
)

EXPECTED_EXPORTS = ("ExecutionPolicyPlugin", "UnslopPlugin")
SHARED_HELPERS = ("design_state.py", "plan_graph.py", "worktrees.py")
THIRD_PARTY_LICENSES = ("mattpocock-skills-MIT.txt", "pstack-MIT.txt")


def is_python_cache(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def run(
    command: list[str], cwd: Path, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class OpencodePackageTests(unittest.TestCase):
    def test_direct_replace_rejects_same_staging_and_output_without_touching_marker(self) -> None:
        """The public seam must reject an identical normalized source and target."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            staging = parent / ".artifact.staging"
            marker = staging / "must-survive.txt"
            staging.mkdir()
            marker.write_text("must survive\n", encoding="utf-8")
            before = {
                entry.name: entry.stat(follow_symlinks=False)
                for entry in parent.iterdir()
            }
            before_fds = fd_snapshot()

            with self.assertRaises(BuildError):
                build_module._replace_output(
                    staging,
                    parent / "nested" / ".." / staging.name,
                )

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")
            self.assertEqual(
                {
                    entry.name: entry.stat(follow_symlinks=False)
                    for entry in parent.iterdir()
                },
                before,
            )
            self.assertEqual(
                {entry.name for entry in staging.iterdir()},
                {marker.name},
            )
            self.assertEqual(fd_snapshot(), before_fds)

    def test_direct_replace_rejects_cross_parent_staging_leaf_without_touching_either(self) -> None:
        """The public seam must bind staging and output to one lexical parent."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            staging_name = ".artifact.staging"
            left_staging = left / staging_name
            right_staging = right / staging_name
            left_staging.mkdir()
            right_staging.mkdir()
            (left_staging / "left-marker").write_text("left\n", encoding="utf-8")
            (right_staging / "foreign-marker").write_text("foreign\n", encoding="utf-8")

            with self.assertRaises(BuildError):
                build_module._replace_output(left_staging, right / "artifact")

            self.assertTrue((left_staging / "left-marker").is_file())
            self.assertTrue((right_staging / "foreign-marker").is_file())
            self.assertFalse((right / "artifact").exists())
            self.assertEqual(
                {entry.name for entry in left.iterdir()},
                {staging_name},
            )
            self.assertEqual(
                {entry.name for entry in right.iterdir()},
                {staging_name},
            )

    def test_placeholder_allocator_preserves_first_identity_after_replacement(self) -> None:
        """A retry must not bind or reclaim a foreign replacement."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            prefix = ".artifact.rollback-"
            moved_owned = parent / "owned-away"
            injected = False
            descriptor = -1
            real_open = build_module.os.open

            def replace_after_first_observation(
                path: object, flags: int, *args: object, **kwargs: object
            ) -> int:
                nonlocal injected
                if (
                    not injected
                    and isinstance(path, str)
                    and path.startswith(prefix)
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    candidate = parent / path
                    candidate.rename(moved_owned)
                    candidate.mkdir()
                    (candidate / "foreign-marker").write_text("foreign\n", encoding="utf-8")
                    injected = True
                    raise OSError("injected open failure after first identity observation")
                return real_open(path, flags, *args, **kwargs)

            try:
                with mock.patch.object(
                    build_module.os,
                    "open",
                    side_effect=replace_after_first_observation,
                ):
                    with self.assertRaises(BuildError):
                        build_module._allocate_empty_directory(parent_fd, prefix)
            finally:
                os.close(parent_fd)

            self.assertTrue(injected)
            foreign = next(name for name in os.listdir(parent) if name.startswith(prefix))
            self.assertTrue((parent / foreign / "foreign-marker").is_file())
            self.assertFalse(moved_owned.exists())

    def test_placeholder_allocator_uses_open_fstat_when_stat_persistently_fails(self) -> None:
        """Persistent name-stat failures do not block descriptor identity capture."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            prefix = ".artifact.rollback-"
            descriptor = -1
            real_stat = build_module.os.stat

            def fail_name_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
                if (
                    isinstance(path, str)
                    and path.startswith(prefix)
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    raise OSError("persistent post-mkdir stat failure")
                return real_stat(path, *args, **kwargs)

            try:
                with mock.patch.object(build_module.os, "stat", side_effect=fail_name_stat):
                    name, identity, descriptor = build_module._allocate_empty_directory(
                        parent_fd, prefix
                    )
                opened = os.fstat(descriptor)
                self.assertEqual(identity, (opened.st_dev, opened.st_ino))
                self.assertTrue((parent / name).is_dir())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                for entry in parent.iterdir():
                    if entry.name.startswith(prefix):
                        shutil.rmtree(entry)
                os.close(parent_fd)

    def test_reclaim_staging_survives_unobservable_placeholder(self) -> None:
        """An unobservable placeholder cannot hide visible staging cleanup."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            staging = parent / ".artifact.staging"
            placeholder = parent / ".artifact.rollback-placeholder"
            staging.mkdir()
            placeholder.mkdir()
            identity = (staging.stat().st_dev, staging.stat().st_ino)
            real_stat = build_module.os.stat

            def fail_unrelated_stat(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                if path == placeholder.name and kwargs.get("dir_fd") == parent_fd:
                    raise OSError("injected placeholder stat failure")
                return real_stat(path, *args, **kwargs)

            try:
                with mock.patch.object(build_module.os, "stat", side_effect=fail_unrelated_stat):
                    self.assertTrue(
                        build_module._reclaim_directory_identity(
                            parent_fd, identity, (staging.name,)
                        )
                    )
            finally:
                os.close(parent_fd)

            self.assertFalse(staging.exists())
            self.assertTrue(placeholder.is_dir())

    def test_reclaim_reports_target_that_survives_bounded_attempts(self) -> None:
        """A visible target after the retry bound is a cleanup failure."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            target = parent / "target"
            target.mkdir()
            identity = (target.stat().st_dev, target.stat().st_ino)
            try:
                with mock.patch.object(build_module, "_remove_tree_at"):
                    self.assertFalse(
                        build_module._reclaim_directory_identity(
                            parent_fd, identity, (target.name,)
                        )
                    )
            finally:
                os.close(parent_fd)
            self.assertTrue(target.is_dir())

    def test_render_baseexceptions_reclaim_staging_and_placeholder(self) -> None:
        """Every render failure class gets the same prepublication cleanup."""

        for failure in (RuntimeError("runtime render failure"), ProbeBase("base render failure")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = root / "repository"
                shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
                workspace = root / "workspace"
                workspace.mkdir()
                with mock.patch.object(build_module, "render_all", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        build_opencode_package(repo, workspace / "artifact")
                self.assertEqual(tuple(workspace.iterdir()), ())

    def test_snapshot_reader_runtimeerror_removes_private_snapshot(self) -> None:
        """A source-reader exception cannot strand its private snapshot tree."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            created: list[Path] = []
            real_mkdtemp = build_module.tempfile.mkdtemp

            def track_mkdtemp(*args: object, **kwargs: object) -> str:
                path = real_mkdtemp(*args, **kwargs)
                created.append(Path(path))
                return path

            with (
                mock.patch.object(build_module.tempfile, "mkdtemp", side_effect=track_mkdtemp),
                mock.patch.object(Path, "read_bytes", side_effect=RuntimeError("reader failed")),
            ):
                with self.assertRaises(RuntimeError):
                    build_module._snapshot_sources(repo)

            self.assertEqual(len(created), 1)
            self.assertFalse(created[0].exists())

    def test_mkdir_at_closes_child_when_close_raises_baseexception(self) -> None:
        """A post-acquisition BaseException cannot leak a nested descriptor."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / "nested" / "deeper").mkdir(parents=True)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            before = fd_snapshot()
            real_open = build_module.os.open
            real_close = build_module.os.close
            nested_fd: int | None = None

            def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                nonlocal nested_fd
                descriptor = real_open(path, flags, *args, **kwargs)
                if path == "nested":
                    nested_fd = descriptor
                return descriptor

            def fail_nested_close(descriptor: int) -> None:
                if descriptor == nested_fd:
                    real_close(descriptor)
                    raise ProbeBase("injected close failure")
                real_close(descriptor)

            try:
                with (
                    mock.patch.object(build_module.os, "open", side_effect=record_open),
                    mock.patch.object(build_module.os, "close", side_effect=fail_nested_close),
                ):
                    with self.assertRaises(ProbeBase):
                        build_module._mkdir_at(parent_fd, Path("nested/deeper"))
                self.assertEqual(fd_snapshot(), before)
            finally:
                os.close(parent_fd)

    def test_open_parent_and_retain_parent_close_descriptors_on_baseexception(self) -> None:
        """Identity validation compensates descriptor acquisition on BaseException."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "parent"
            parent.mkdir()
            caller_fd = os.open(parent, build_module._directory_open_flags())
            before = fd_snapshot()
            real_fstat = build_module.os.fstat
            injected = False

            def fail_fstat(descriptor: int) -> os.stat_result:
                nonlocal injected
                if not injected:
                    injected = True
                    raise ProbeBase("injected identity failure")
                return real_fstat(descriptor)

            try:
                with mock.patch.object(build_module.os, "fstat", side_effect=fail_fstat):
                    with self.assertRaises(ProbeBase):
                        build_module._open_output_parent(parent)
                self.assertEqual(fd_snapshot(), before)
            finally:
                os.fstat(caller_fd)

            before = fd_snapshot()
            injected = False
            try:
                with mock.patch.object(build_module.os, "fstat", side_effect=fail_fstat):
                    with self.assertRaises(ProbeBase):
                        build_module._retain_output_parent(caller_fd)
                self.assertEqual(fd_snapshot(), before)
            finally:
                os.fstat(caller_fd)
                os.close(caller_fd)

    def test_open_directory_chain_closes_children_on_baseexception(self) -> None:
        """A chain-transfer fault closes both the old and newly opened child."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "nested" / "deeper"
            target.mkdir(parents=True)
            before = fd_snapshot()
            real_open = build_module.os.open
            real_close = build_module.os.close
            nested_fd: int | None = None

            def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                nonlocal nested_fd
                descriptor = real_open(path, flags, *args, **kwargs)
                if path == "nested":
                    nested_fd = descriptor
                return descriptor

            def fail_nested_close(descriptor: int) -> None:
                if descriptor == nested_fd:
                    real_close(descriptor)
                    raise ProbeBase("injected chain transfer failure")
                real_close(descriptor)

            with (
                mock.patch.object(build_module.os, "open", side_effect=record_open),
                mock.patch.object(build_module.os, "close", side_effect=fail_nested_close),
            ):
                with self.assertRaises(ProbeBase):
                    build_module._open_directory_chain(target, create=False)
            self.assertEqual(fd_snapshot(), before)

    def test_descriptor_bound_setup_closes_retained_parent_on_baseexception(self) -> None:
        """Descriptor-bound validation compensates its retained parent on fault."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            workspace = root / "workspace"
            workspace.mkdir()
            caller_fd = os.open(workspace, build_module._directory_open_flags())
            before = fd_snapshot()
            try:
                with mock.patch.object(
                    build_module,
                    "_validate_descriptor_bound_parent",
                    side_effect=ProbeBase("injected bound-parent validation failure"),
                ):
                    with self.assertRaises(ProbeBase):
                        build_opencode_package(
                            repo,
                            workspace / "artifact",
                            output_parent_fd=caller_fd,
                        )
                self.assertEqual(fd_snapshot(), before)
                os.fstat(caller_fd)
            finally:
                os.close(caller_fd)

    def test_direct_seam_and_initial_staging_close_descriptors_on_baseexception(self) -> None:
        """Direct and builder staging probes preserve the original fault and fd set."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent"
            parent.mkdir()
            staging = parent / ".artifact.staging"
            staging.mkdir()
            before = fd_snapshot()
            real_stat = build_module.os.stat

            def fail_direct_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
                if path == staging.name and kwargs.get("dir_fd") is not None:
                    raise ProbeBase("injected direct seam stat failure")
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(build_module.os, "stat", side_effect=fail_direct_stat):
                with self.assertRaises(ProbeBase):
                    build_module._replace_output(staging, parent / "artifact")
            self.assertEqual(fd_snapshot(), before)
            self.assertTrue(staging.is_dir())

            real_open = build_module.os.open

            def fail_direct_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if path == staging.name and kwargs.get("dir_fd") is not None:
                    raise ProbeBase("injected direct seam open failure")
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(build_module.os, "open", side_effect=fail_direct_open):
                with self.assertRaises(ProbeBase):
                    build_module._replace_output(staging, parent / "artifact")
            self.assertEqual(fd_snapshot(), before)
            self.assertTrue(staging.is_dir())

            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            workspace = root / "workspace"
            workspace.mkdir()
            before = fd_snapshot()

            def fail_initial_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal injected_initial_stat
                if (
                    not injected_initial_stat
                    and isinstance(path, str)
                    and path.startswith(".artifact.")
                    and kwargs.get("dir_fd") is not None
                ):
                    injected_initial_stat = True
                    raise ProbeBase("injected initial staging stat failure")
                return real_stat(path, *args, **kwargs)

            injected_initial_stat = False
            with mock.patch.object(build_module.os, "stat", side_effect=fail_initial_stat):
                with self.assertRaises(ProbeBase):
                    build_opencode_package(repo, workspace / "artifact")
            self.assertEqual(fd_snapshot(), before)
            self.assertEqual(tuple(workspace.iterdir()), ())

            before = fd_snapshot()
            real_open = build_module.os.open
            injected_initial_open = False

            def fail_initial_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                nonlocal injected_initial_open
                if (
                    not injected_initial_open
                    and isinstance(path, str)
                    and path.startswith(".artifact-2.")
                    and kwargs.get("dir_fd") is not None
                ):
                    injected_initial_open = True
                    raise ProbeBase("injected initial staging open failure")
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(build_module.os, "open", side_effect=fail_initial_open):
                with self.assertRaises(ProbeBase):
                    build_opencode_package(repo, workspace / "artifact-2")
            self.assertEqual(fd_snapshot(), before)
            self.assertEqual(tuple(workspace.iterdir()), ())

    def test_rollback_does_not_exchange_dual_foreign_occupants(self) -> None:
        """Foreign public and private occupants stay under their own names."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            staging_name = ".artifact.staging"
            placeholder_name = ".artifact.rollback"
            staging = parent / staging_name
            placeholder = parent / placeholder_name
            staging.mkdir()
            placeholder.mkdir()
            staging_identity = (staging.stat().st_dev, staging.stat().st_ino)
            placeholder_identity = (placeholder.stat().st_dev, placeholder.stat().st_ino)
            output = parent / "artifact"
            staging.rename(output)
            output.rename(parent / "owned-away")
            output.mkdir()
            (output / "public-foreign").write_text("foreign\n", encoding="utf-8")
            placeholder.rename(parent / "placeholder-owned-away")
            placeholder.mkdir()
            (placeholder / "private-foreign").write_text("foreign\n", encoding="utf-8")
            binding = build_module._OutputBinding(
                parent,
                parent_fd,
                (parent.stat().st_dev, parent.stat().st_ino),
                staging_name,
                staging_identity,
                -1,
                False,
                (),
                placeholder_name,
                placeholder_identity,
                -1,
            )
            try:
                with mock.patch.object(
                    build_module,
                    "_renameat2_exchange",
                    wraps=build_module._renameat2_exchange,
                ) as exchange:
                    build_module._rollback_published_output(binding, output.name)
            finally:
                os.close(parent_fd)
            exchange.assert_not_called()
            self.assertTrue((output / "public-foreign").is_file())
            self.assertTrue((placeholder / "private-foreign").is_file())
            self.assertFalse((parent / "owned-away").exists())
            self.assertFalse((parent / "placeholder-owned-away").exists())

    def test_reclaim_rescans_after_each_attempt_for_renamed_identity(self) -> None:
        """Reclamation finds an owned directory moved after its first attempt."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            owned = parent / "preferred"
            late = parent / "late-name"
            owned.mkdir()
            identity = (owned.stat().st_dev, owned.stat().st_ino)
            calls: list[str] = []

            def move_then_reclaim(
                _parent_fd: int, name: str, _expected: object = None
            ) -> None:
                calls.append(name)
                if name == owned.name:
                    owned.rename(late)
                    return
                shutil.rmtree(late)

            try:
                with mock.patch.object(
                    build_module,
                    "_remove_tree_at",
                    side_effect=move_then_reclaim,
                ):
                    reclaimed = build_module._reclaim_directory_identity(
                        parent_fd, identity, (owned.name,)
                    )
            finally:
                os.close(parent_fd)
            self.assertTrue(reclaimed)
            self.assertEqual(calls, [owned.name, late.name])
            self.assertFalse(late.exists())

    def test_directory_inventory_falls_back_to_open_fstat_for_persistent_stat_failure(self) -> None:
        """Inventory can identify an owned directory through its no-follow descriptor."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            owned = parent / "owned"
            unrelated = parent / "unrelated"
            owned.mkdir()
            unrelated.mkdir()
            (unrelated / "must-survive.txt").write_text("foreign\n", encoding="utf-8")
            expected = (owned.stat().st_dev, owned.stat().st_ino)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            real_stat = build_module.os.stat
            before = fd_snapshot()

            def fail_name_stat(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                if (
                    isinstance(path, str)
                    and path == owned.name
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    raise OSError(errno.EACCES, "persistent owned-name stat failure")
                return real_stat(path, *args, **kwargs)

            try:
                with mock.patch.object(build_module.os, "stat", side_effect=fail_name_stat):
                    matches, unobservable = build_module._directory_inventory_with_errors(
                        parent_fd, expected
                    )
                self.assertEqual(matches, [owned.name])
                self.assertEqual(unobservable, set())
                self.assertEqual(fd_snapshot(), before)
            finally:
                os.close(parent_fd)

            self.assertTrue((unrelated / "must-survive.txt").is_file())

    def test_directory_inventory_fallback_closes_fd_when_fstat_raises_baseexception(self) -> None:
        """A fallback fstat fault cannot leak its newly opened descriptor."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            owned = parent / "owned"
            owned.mkdir()
            expected = (owned.stat().st_dev, owned.stat().st_ino)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            before = fd_snapshot()
            real_stat = build_module.os.stat
            real_open = build_module.os.open
            opened: list[int] = []

            def fail_name_stat(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                if (
                    isinstance(path, str)
                    and path == owned.name
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    raise OSError(errno.EACCES, "persistent owned-name stat failure")
                return real_stat(path, *args, **kwargs)

            def record_open(
                path: object, flags: int, *args: object, **kwargs: object
            ) -> int:
                descriptor = real_open(path, flags, *args, **kwargs)
                if path == owned.name and kwargs.get("dir_fd") == parent_fd:
                    opened.append(descriptor)
                return descriptor

            def fail_fstat(descriptor: int) -> os.stat_result:
                if descriptor in opened:
                    raise ProbeBase("injected fallback fstat failure")
                return os.fstat(descriptor)

            try:
                with (
                    mock.patch.object(build_module.os, "stat", side_effect=fail_name_stat),
                    mock.patch.object(build_module.os, "open", side_effect=record_open),
                    mock.patch.object(build_module.os, "fstat", side_effect=fail_fstat),
                ):
                    with self.assertRaises(ProbeBase):
                        build_module._directory_inventory_with_errors(parent_fd, expected)
                self.assertEqual(fd_snapshot(), before)
            finally:
                os.close(parent_fd)

    def test_directory_inventory_fallback_closes_fd_before_later_identity_observation(self) -> None:
        """A post-fstat identity fault cannot retain the fallback descriptor."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            owned = parent / "owned"
            owned.mkdir()
            expected = (owned.stat().st_dev, owned.stat().st_ino)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            before = fd_snapshot()
            real_stat = build_module.os.stat

            def fail_name_stat(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                if (
                    isinstance(path, str)
                    and path == owned.name
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    raise OSError(errno.EACCES, "persistent owned-name stat failure")
                return real_stat(path, *args, **kwargs)

            def fail_identity(_metadata: os.stat_result) -> tuple[int, int]:
                raise ProbeBase("injected later identity observation failure")

            try:
                with (
                    mock.patch.object(build_module.os, "stat", side_effect=fail_name_stat),
                    mock.patch.object(
                        build_module,
                        "_entry_identity",
                        side_effect=fail_identity,
                    ),
                ):
                    with self.assertRaises(ProbeBase):
                        build_module._directory_inventory_with_errors(parent_fd, expected)
                self.assertEqual(fd_snapshot(), before)
            finally:
                os.close(parent_fd)

    def test_render_runtimeerror_reclaims_relocated_staging_with_stat_failure(self) -> None:
        """A relocated owned directory is reclaimed through open/fstat identity."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            workspace = root / "workspace"
            workspace.mkdir()
            unrelated = workspace / "unrelated"
            unrelated.mkdir()
            foreign_marker = unrelated / "must-survive.txt"
            foreign_marker.write_text("foreign\n", encoding="utf-8")
            output = workspace / "artifact"
            relocated = workspace / ".relocated-staging"

            def relocate_then_fail(_source: Path) -> dict[str, str]:
                staging = next(
                    path
                    for path in workspace.iterdir()
                    if path.name.startswith(".artifact.")
                    and not path.name.startswith(".artifact.rollback-")
                )
                (staging / "owned-marker.txt").write_text("owned\n", encoding="utf-8")
                staging.rename(relocated)
                raise RuntimeError("injected render failure")

            real_stat = build_module.os.stat

            def fail_relocated_stat(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                if (
                    isinstance(path, str)
                    and path == relocated.name
                    and kwargs.get("dir_fd") is not None
                ):
                    raise OSError(errno.EACCES, "persistent relocated-name stat failure")
                return real_stat(path, *args, **kwargs)

            with (
                mock.patch.object(build_module, "render_all", side_effect=relocate_then_fail),
                mock.patch.object(build_module.os, "stat", side_effect=fail_relocated_stat),
            ):
                with self.assertRaises(RuntimeError):
                    build_opencode_package(repo, output)

            self.assertFalse(relocated.exists())
            self.assertTrue(foreign_marker.is_file())
            self.assertFalse(output.exists())
            self.assertEqual({entry.name for entry in workspace.iterdir()}, {unrelated.name})

    def test_placeholder_allocation_failure_reclaims_staging_and_closes_fds(self) -> None:
        """A failed placeholder allocation does not strand staging or descriptors."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            workspace = root / "workspace"
            workspace.mkdir()
            caller_fd = os.open(workspace, build_module._directory_open_flags())
            before = fd_snapshot()

            def fail_placeholder(parent_fd: int, prefix: str) -> tuple[str, tuple[int, int], int]:
                self.assertTrue(prefix.startswith(".artifact.rollback-"))
                raise OSError("injected placeholder allocation failure")

            try:
                with mock.patch.object(
                    build_module, "_allocate_empty_directory", side_effect=fail_placeholder
                ):
                    with self.assertRaises(BuildError):
                        build_opencode_package(repo, workspace / "artifact", output_parent_fd=caller_fd)
            finally:
                after = fd_snapshot()
                self.assertEqual(after, before)
                os.fstat(caller_fd)
                os.close(caller_fd)
            self.assertEqual(tuple(workspace.iterdir()), ())

    def test_placeholder_allocation_cleanup_failure_still_closes_fds(self) -> None:
        """Cleanup errors after placeholder failure are reported as BuildError."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            workspace = root / "workspace"
            workspace.mkdir()
            caller_fd = os.open(workspace, build_module._directory_open_flags())
            before = fd_snapshot()

            def fail_placeholder(_parent_fd: int, _prefix: str) -> tuple[str, tuple[int, int], int]:
                raise OSError("injected placeholder allocation failure")

            try:
                with (
                    mock.patch.object(
                        build_module, "_allocate_empty_directory", side_effect=fail_placeholder
                    ),
                    mock.patch.object(
                        build_module,
                        "_reclaim_directory_identity",
                        side_effect=OSError("injected cleanup failure"),
                    ),
                ):
                    with self.assertRaises(BuildError):
                        build_opencode_package(repo, workspace / "artifact", output_parent_fd=caller_fd)
            finally:
                after = fd_snapshot()
                self.assertEqual(after, before)
                os.fstat(caller_fd)
                os.close(caller_fd)

    def test_placeholder_allocation_retries_after_post_mkdir_stat_failure(self) -> None:
        """A transient post-mkdir stat error does not strand the placeholder."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            prefix = ".artifact.rollback-"
            failed = False
            descriptor = -1
            real_stat = build_module.os.stat

            def fail_first_stat(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                nonlocal failed
                if (
                    not failed
                    and isinstance(path, str)
                    and path.startswith(prefix)
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    failed = True
                    raise OSError("injected post-mkdir stat failure")
                return real_stat(path, *args, **kwargs)

            try:
                with mock.patch.object(build_module.os, "stat", side_effect=fail_first_stat):
                    name, identity, descriptor = build_module._allocate_empty_directory(
                        parent_fd, prefix
                    )
                self.assertTrue(failed)
                self.assertTrue((parent / name).is_dir())
                self.assertEqual(
                    identity,
                    (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino),
                )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                for entry in parent.iterdir():
                    if entry.name.startswith(prefix):
                        shutil.rmtree(entry)
                os.close(parent_fd)

    def test_placeholder_allocation_retries_after_post_mkdir_open_failure(self) -> None:
        """A transient post-mkdir open error does not strand the placeholder."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            prefix = ".artifact.rollback-"
            failed = False
            descriptor = -1
            real_open = build_module.os.open

            def fail_first_open(
                path: object, flags: int, *args: object, **kwargs: object
            ) -> int:
                nonlocal failed
                if (
                    not failed
                    and isinstance(path, str)
                    and path.startswith(prefix)
                    and kwargs.get("dir_fd") == parent_fd
                ):
                    failed = True
                    raise OSError("injected post-mkdir open failure")
                return real_open(path, flags, *args, **kwargs)

            try:
                with mock.patch.object(build_module.os, "open", side_effect=fail_first_open):
                    name, identity, descriptor = build_module._allocate_empty_directory(
                        parent_fd, prefix
                    )
                self.assertTrue(failed)
                self.assertTrue((parent / name).is_dir())
                self.assertEqual(
                    identity,
                    (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino),
                )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                for entry in parent.iterdir():
                    if entry.name.startswith(prefix):
                        shutil.rmtree(entry)
                os.close(parent_fd)

    def test_render_failure_reclaims_relocated_staging_and_placeholder(self) -> None:
        """Render-time relocation still cleans both builder-owned directories."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repository"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            workspace = root / "workspace"
            workspace.mkdir()
            output = workspace / "artifact"
            relocated = workspace / ".relocated-staging"

            def relocate_then_fail(_source: Path) -> dict[str, str]:
                staging = next(
                    path
                    for path in workspace.iterdir()
                    if path.name.startswith(".artifact.")
                    and not path.name.startswith(".artifact.rollback-")
                )
                staging.rename(relocated)
                raise build_module.RenderError("injected render failure")

            with mock.patch.object(build_module, "render_all", side_effect=relocate_then_fail):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, output)
            self.assertFalse(output.exists())
            self.assertEqual(tuple(workspace.iterdir()), ())

    def test_write_bytes_closes_nested_directory_when_leaf_open_fails(self) -> None:
        """A leaf allocation error cannot leak the descriptor for its parent."""

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent_fd = os.open(parent, build_module._directory_open_flags())
            before = fd_snapshot()
            real_open = build_module.os.open

            def fail_leaf(path: object, flags: int, *args: object, **kwargs: object) -> int:
                if path == "leaf.txt":
                    raise OSError("injected leaf allocation failure")
                return real_open(path, flags, *args, **kwargs)

            try:
                with mock.patch.object(build_module.os, "open", side_effect=fail_leaf):
                    with self.assertRaises(OSError):
                        build_module._write_bytes_at(parent_fd, Path("nested/leaf.txt"), b"x")
                after = fd_snapshot()
                self.assertEqual(after, before)
            finally:
                os.close(parent_fd)

    def test_post_publish_fsync_failure_rolls_back_after_parent_relocation(self) -> None:
        """A failure after rename must not strand output under a source root."""

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            copied_repo = temporary_root / "repository"
            shutil.copytree(
                ROOT,
                copied_repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            workspace = temporary_root / "workspace"
            workspace.mkdir()
            descriptor = os.open(workspace, build_module._directory_open_flags())
            moved_workspace = copied_repo / "moved-workspace"
            lexical_output = workspace / "artifact"
            published = False
            fsync_failed = False
            real_rename = build_module._renameat2_noreplace
            real_fsync = build_module.os.fsync

            def publish_then_mark(
                parent_fd: int, source_name: str, destination_name: str
            ) -> bool:
                nonlocal published
                result = real_rename(parent_fd, source_name, destination_name)
                if destination_name == lexical_output.name:
                    published = True
                return result

            def relocate_then_fsync(parent_fd: int) -> None:
                nonlocal fsync_failed
                if published and not fsync_failed:
                    workspace.rename(moved_workspace)
                    fsync_failed = True
                    raise OSError("injected parent fsync failure")
                real_fsync(parent_fd)

            try:
                with (
                    mock.patch.object(
                        build_module,
                        "_renameat2_noreplace",
                        side_effect=publish_then_mark,
                    ),
                    mock.patch.object(build_module.os, "fsync", side_effect=relocate_then_fsync),
                ):
                    with self.assertRaises(BuildError):
                        build_opencode_package(
                            copied_repo,
                            lexical_output,
                            output_parent_fd=descriptor,
                        )
            finally:
                os.close(descriptor)

            self.assertTrue(published)
            self.assertTrue(fsync_failed)
            self.assertFalse(lexical_output.exists())
            self.assertTrue(moved_workspace.is_dir())
            self.assertEqual(tuple(moved_workspace.iterdir()), ())

    def test_publication_exception_after_real_rename_rolls_back(self) -> None:
        """An exception after the real rename is treated as an ambiguous publish."""

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "artifact"
            real_rename = build_module._renameat2_noreplace
            renamed = False

            def publish_then_raise(
                parent_fd: int, source_name: str, destination_name: str
            ) -> bool:
                nonlocal renamed
                published = real_rename(parent_fd, source_name, destination_name)
                if destination_name == output.name:
                    self.assertTrue(published)
                    renamed = True
                    raise RuntimeError("injected ambiguous publication result")
                return published

            with mock.patch.object(
                build_module,
                "_renameat2_noreplace",
                side_effect=publish_then_raise,
            ):
                with self.assertRaises(RuntimeError):
                    build_opencode_package(ROOT, output)

            self.assertTrue(renamed)
            self.assertFalse(output.exists())
            self.assertEqual(tuple(output.parent.iterdir()), ())

    def test_rollback_observation_failure_reclaims_both_identities(self) -> None:
        """Exchange observation failure still reclaims both identities in each seam."""

        for seam in ("descriptor-bound", "temporary"):
            with self.subTest(seam=seam), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "artifact"
                published = False
                observation_failed = False
                real_rename = build_module._renameat2_noreplace
                real_stat = build_module.os.stat
                real_fsync = build_module.os.fsync
                reclaim_calls: list[tuple[str, ...]] = []
                real_reclaim = build_module._reclaim_directory_identity

                def publish_then_mark(
                    parent_fd: int, source_name: str, destination_name: str
                ) -> bool:
                    nonlocal published
                    result = real_rename(parent_fd, source_name, destination_name)
                    if destination_name == output.name:
                        published = result
                    return result

                def fail_exchange_observation_once(
                    path: object, *args: object, **kwargs: object
                ) -> os.stat_result:
                    nonlocal observation_failed
                    if (
                        published
                        and not observation_failed
                        and path == output.name
                        and kwargs.get("dir_fd") is not None
                    ):
                        observation_failed = True
                        raise OSError("injected exchange observation failure")
                    return real_stat(path, *args, **kwargs)

                def fail_after_publish(parent_fd: int) -> None:
                    if published:
                        raise OSError("injected post-publication failure")
                    real_fsync(parent_fd)

                def record_reclaim(
                    parent_fd: int,
                    expected: tuple[int, int],
                    preferred_names: object,
                ) -> bool:
                    names = tuple(preferred_names)  # type: ignore[arg-type]
                    reclaim_calls.append(names)
                    return real_reclaim(parent_fd, expected, names)

                descriptor: int | None = None
                try:
                    if seam == "descriptor-bound":
                        workspace = root / "workspace"
                        workspace.mkdir()
                        descriptor = os.open(workspace, build_module._directory_open_flags())
                        with (
                            mock.patch.object(
                                build_module,
                                "_renameat2_noreplace",
                                side_effect=publish_then_mark,
                            ),
                            mock.patch.object(
                                build_module.os,
                                "stat",
                                side_effect=fail_exchange_observation_once,
                            ),
                            mock.patch.object(
                                build_module.os,
                                "fsync",
                                side_effect=fail_after_publish,
                            ),
                            mock.patch.object(
                                build_module,
                                "_reclaim_directory_identity",
                                side_effect=record_reclaim,
                            ),
                        ):
                            with self.assertRaises(BuildError):
                                build_opencode_package(
                                    ROOT,
                                    workspace / "artifact",
                                    output_parent_fd=descriptor,
                                )
                        parent = workspace
                    else:
                        staging = root / ".artifact.staging"
                        staging.mkdir()
                        with (
                            mock.patch.object(
                                build_module,
                                "_renameat2_noreplace",
                                side_effect=publish_then_mark,
                            ),
                            mock.patch.object(
                                build_module.os,
                                "stat",
                                side_effect=fail_exchange_observation_once,
                            ),
                            mock.patch.object(
                                build_module.os,
                                "fsync",
                                side_effect=fail_after_publish,
                            ),
                            mock.patch.object(
                                build_module,
                                "_reclaim_directory_identity",
                                side_effect=record_reclaim,
                            ),
                        ):
                            with self.assertRaises(BuildError):
                                build_module._replace_output(staging, output)
                        parent = root
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

                self.assertTrue(published)
                self.assertTrue(observation_failed)
                self.assertTrue(
                    any(output.name in names and len(names) == 3 for names in reclaim_calls)
                )
                self.assertTrue(
                    any(output.name in names and len(names) == 2 for names in reclaim_calls)
                )
                self.assertFalse(output.exists())
                self.assertEqual(tuple(parent.iterdir()), ())

    def test_rollback_exchange_preserves_foreign_public_replacement(self) -> None:
        """A public-name replacement cannot be moved into builder-owned staging."""

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            copied_repo = temporary_root / "repository"
            shutil.copytree(
                ROOT,
                copied_repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            workspace = temporary_root / "workspace"
            workspace.mkdir()
            descriptor = os.open(workspace, build_module._directory_open_flags())
            moved_workspace = copied_repo / "moved-workspace"
            lexical_output = workspace / "artifact"
            relocated = False
            foreign_installed = False
            checks = 0
            real_current = build_module._binding_is_current
            real_stat = build_module.os.stat
            real_rename = build_module._renameat2_noreplace

            def relocate_on_post_publish(binding: object) -> bool:
                nonlocal checks, relocated
                checks += 1
                if not relocated and checks >= 3:
                    workspace.rename(moved_workspace)
                    relocated = True
                    return False
                return real_current(binding)  # type: ignore[arg-type]

            def replace_after_observation(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                nonlocal foreign_installed
                observed = real_stat(path, *args, **kwargs)
                if (
                    relocated
                    and not foreign_installed
                    and path == lexical_output.name
                    and kwargs.get("dir_fd") is not None
                ):
                    owned = moved_workspace / lexical_output.name
                    displaced = moved_workspace / "owned-away"
                    owned.rename(displaced)
                    foreign = moved_workspace / lexical_output.name
                    foreign.mkdir()
                    (foreign / "foreign-marker").write_text(
                        "must survive\n", encoding="utf-8"
                    )
                    foreign_installed = True
                return observed

            try:
                with (
                    mock.patch.object(
                        build_module,
                        "_binding_is_current",
                        side_effect=relocate_on_post_publish,
                    ),
                    mock.patch.object(build_module.os, "stat", side_effect=replace_after_observation),
                    mock.patch.object(
                        build_module,
                        "_renameat2_noreplace",
                        side_effect=real_rename,
                    ),
                ):
                    with self.assertRaises(BuildError):
                        build_opencode_package(
                            copied_repo,
                            lexical_output,
                            output_parent_fd=descriptor,
                        )
            finally:
                os.close(descriptor)

            self.assertTrue(relocated)
            self.assertTrue(foreign_installed)
            self.assertTrue(
                (moved_workspace / "artifact" / "foreign-marker").is_file()
            )
            self.assertFalse((moved_workspace / "owned-away").exists())
            self.assertFalse(lexical_output.exists())
            self.assertFalse(
                any(
                    entry.name.startswith(".artifact.")
                    for entry in moved_workspace.iterdir()
                )
            )

    def test_placeholder_final_removal_preserves_late_foreign_replacement(self) -> None:
        """A public placeholder replacement is preserved before final removal."""

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            repo = temporary_root / "repository"
            shutil.copytree(
                ROOT,
                repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            output = temporary_root / "artifact"
            injected = False
            checks = 0
            real_current = build_module._binding_is_current
            real_stat = build_module.os.stat

            def fail_after_publish(binding: object) -> bool:
                nonlocal checks
                checks += 1
                if checks >= 3:
                    return False
                return real_current(binding)  # type: ignore[arg-type]

            def replace_placeholder_before_final_removal(
                path: object, *args: object, **kwargs: object
            ) -> os.stat_result:
                nonlocal injected
                observed = real_stat(path, *args, **kwargs)
                directory_fd = kwargs.get("dir_fd")
                if (
                    not injected
                    and directory_fd is not None
                    and isinstance(path, str)
                    and path == output.name
                    and not any(
                        name.startswith(f".{output.name}.rollback-")
                        for name in os.listdir(directory_fd)
                    )
                ):
                    os.rename(
                        path,
                        f"{path}.displaced",
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                    )
                    os.mkdir(path, dir_fd=directory_fd)
                    marker_fd = os.open(
                        f"{path}/foreign-marker",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    os.close(marker_fd)
                    injected = True
                return observed

            with (
                mock.patch.object(
                    build_module,
                    "_binding_is_current",
                    side_effect=fail_after_publish,
                ),
                mock.patch.object(
                    build_module.os,
                    "stat",
                    side_effect=replace_placeholder_before_final_removal,
                ),
            ):
                try:
                    build_opencode_package(repo, output)
                except BuildError:
                    pass

            self.assertTrue(injected)
            self.assertTrue((output / "foreign-marker").is_file())

    def test_staging_failure_cleanup_closes_duplicated_descriptors(self) -> None:
        """Staging inspection/open failures close all builder-owned descriptors."""

        for failure in ("stat", "open"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                temporary_root = Path(temporary)
                repo = temporary_root / "repository"
                shutil.copytree(
                    ROOT,
                    repo,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"),
                )
                workspace = temporary_root / "workspace"
                workspace.mkdir()
                caller_fd = os.open(workspace, build_module._directory_open_flags())
                before = fd_snapshot()
                real_stat = build_module.os.stat
                real_open = build_module.os.open

                def fail_stat(
                    path: object, *args: object, **kwargs: object
                ) -> os.stat_result:
                    if (
                        failure == "stat"
                        and isinstance(path, str)
                        and path.startswith(".artifact.")
                        and kwargs.get("dir_fd") is not None
                    ):
                        raise OSError("injected staging inspection failure")
                    return real_stat(path, *args, **kwargs)

                def fail_open(
                    path: object, flags: int, *args: object, **kwargs: object
                ) -> int:
                    if (
                        failure == "open"
                        and isinstance(path, str)
                        and path.startswith(".artifact.")
                        and kwargs.get("dir_fd") is not None
                    ):
                        raise OSError("injected staging open failure")
                    return real_open(path, flags, *args, **kwargs)

                try:
                    with (
                        mock.patch.object(build_module.os, "stat", side_effect=fail_stat),
                        mock.patch.object(build_module.os, "open", side_effect=fail_open),
                        mock.patch.object(
                            build_module,
                            "_remove_tree_at",
                            side_effect=OSError("injected cleanup failure"),
                        ),
                    ):
                        with self.assertRaises((BuildError, OSError)):
                            build_opencode_package(
                                repo,
                                workspace / "artifact",
                                output_parent_fd=caller_fd,
                            )
                finally:
                    after = fd_snapshot()
                    self.assertEqual(after, before)
                    os.fstat(caller_fd)
                    os.close(caller_fd)

    def test_prepublication_failure_reclaims_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            repo = temporary_root / "repository"
            shutil.copytree(
                ROOT,
                repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            output = temporary_root / "artifact"
            with mock.patch.object(
                build_module,
                "_verify_live_sources_against_snapshot",
                side_effect=BuildError("injected prepublication failure"),
            ):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, output)
            self.assertFalse(output.exists())
            self.assertEqual(tuple(output.parent.iterdir()), (repo,))

    def _assert_descriptor_bound_source_parent_rejected(
        self,
        actual_parent: Path,
        lexical_output: Path,
        *,
        watched_source_dirs: tuple[Path, ...],
    ) -> None:
        lexical_output.parent.mkdir(parents=True)
        watched_before = {
            directory: {entry.name for entry in directory.iterdir()}
            for directory in (*watched_source_dirs, lexical_output.parent)
        }
        descriptor = os.open(actual_parent, build_module._directory_open_flags())
        try:
            try:
                build_opencode_package(
                    ROOT,
                    lexical_output,
                    output_parent_fd=descriptor,
                )
            except BuildError:
                pass
            else:
                self.fail("descriptor-bound source parent was not rejected")
            for directory, entries in watched_before.items():
                self.assertEqual(
                    {entry.name for entry in directory.iterdir()},
                    entries,
                    f"build created an entry in {directory}",
                )
            self.assertFalse(lexical_output.exists())
            self.assertFalse(lexical_output.is_symlink())
        finally:
            os.close(descriptor)
            # The RED assertion above intentionally leaves a successful build's
            # source-side output behind; remove only this test's unique names so
            # the next test starts from the original checkout.
            output_name = lexical_output.name
            for entry in actual_parent.iterdir():
                if entry.name == output_name or entry.name.startswith(f".{output_name}."):
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
            if lexical_output.is_dir() and not lexical_output.is_symlink():
                shutil.rmtree(lexical_output)
            elif lexical_output.exists() or lexical_output.is_symlink():
                lexical_output.unlink()

    def test_descriptor_bound_output_rejects_canonical_package_parent_fd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            lexical_output = temporary_root / "canonical-decoy" / "artifact"
            self._assert_descriptor_bound_source_parent_rejected(
                CODEX_ROOT,
                lexical_output,
                watched_source_dirs=(ROOT, CODEX_ROOT),
            )

    def test_descriptor_bound_output_rejects_descendant_source_parent_fd(self) -> None:
        source_descendant = CODEX_ROOT / ".descriptor-bound-source-parent"
        source_descendant.mkdir()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                lexical_output = Path(temporary) / "descendant-decoy" / "artifact"
                self._assert_descriptor_bound_source_parent_rejected(
                    source_descendant,
                    lexical_output,
                    watched_source_dirs=(ROOT, CODEX_ROOT, source_descendant),
                )
        finally:
            source_descendant.rmdir()

    def test_descriptor_bound_output_rejects_repository_root_or_ancestor_parent_fd(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            lexical_output = temporary_root / "repository-decoy" / "artifact"
            self._assert_descriptor_bound_source_parent_rejected(
                ROOT,
                lexical_output,
                watched_source_dirs=(ROOT, CODEX_ROOT),
            )
            ancestor_output = temporary_root / "ancestor-decoy" / "artifact"
            self._assert_descriptor_bound_source_parent_rejected(
                ROOT.parent,
                ancestor_output,
                watched_source_dirs=(ROOT, CODEX_ROOT),
            )

    def test_descriptor_bound_output_parent_ignores_replacement_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            descriptor = os.open(workspace, build_module._directory_open_flags())
            detached = root / "detached-workspace"
            workspace.rename(detached)
            workspace.mkdir()
            foreign_output = workspace / "artifact"
            foreign_output.mkdir()
            marker = foreign_output / "foreign.txt"
            marker.write_bytes(b"must survive\n")
            foreign_identity = (
                foreign_output.stat().st_dev,
                foreign_output.stat().st_ino,
            )

            try:
                result = build_opencode_package(
                    ROOT,
                    workspace / "artifact",
                    output_parent_fd=descriptor,
                )
            finally:
                os.close(descriptor)

            self.assertEqual(result, workspace / "artifact")
            self.assertTrue((detached / "artifact/package.json").is_file())
            self.assertEqual(marker.read_bytes(), b"must survive\n")
            self.assertEqual(
                (foreign_output.stat().st_dev, foreign_output.stat().st_ino),
                foreign_identity,
            )

    def test_descriptor_bound_output_writes_stay_bound_after_build_begins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            descriptor = os.open(workspace, build_module._directory_open_flags())
            detached = root / "detached-workspace"
            marker = workspace / "foreign.txt"
            real_snapshot = build_module._snapshot_sources
            replaced = False

            def replace_after_staging(source: Path) -> tuple[Path, Path]:
                nonlocal replaced
                workspace.rename(detached)
                workspace.mkdir()
                marker.write_bytes(b"must survive\n")
                replaced = True
                return real_snapshot(source)

            try:
                with mock.patch.object(
                    build_module,
                    "_snapshot_sources",
                    side_effect=replace_after_staging,
                ):
                    result = build_opencode_package(
                        ROOT,
                        workspace / "artifact",
                        output_parent_fd=descriptor,
                    )
            finally:
                os.close(descriptor)

            self.assertTrue(replaced)
            self.assertEqual(result, workspace / "artifact")
            self.assertTrue((detached / "artifact/package.json").is_file())
            self.assertEqual(tuple(workspace.iterdir()), (marker,))
            self.assertEqual(marker.read_bytes(), b"must survive\n")

    def test_descriptor_bound_output_rejects_workspace_moved_under_repository_after_staging(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            copied_repo = temporary_root / "repository"
            shutil.copytree(
                ROOT,
                copied_repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            workspace = temporary_root / "workspace"
            workspace.mkdir()
            descriptor = os.open(workspace, build_module._directory_open_flags())
            moved_workspace = copied_repo / "moved-workspace"
            lexical_output = workspace / "artifact"
            real_snapshot = build_module._snapshot_sources
            staging_started = False

            def move_after_staging(source: Path) -> tuple[Path, Path]:
                nonlocal staging_started
                self.assertTrue(
                    any(entry.name.startswith(".artifact.") for entry in workspace.iterdir())
                )
                workspace.rename(moved_workspace)
                staging_started = True
                return real_snapshot(source)

            try:
                with mock.patch.object(
                    build_module,
                    "_snapshot_sources",
                    side_effect=move_after_staging,
                ):
                    try:
                        build_opencode_package(
                            copied_repo,
                            lexical_output,
                            output_parent_fd=descriptor,
                        )
                    except BuildError:
                        pass
                    else:
                        self.fail(
                            "publication succeeded after the retained workspace moved "
                            f"under the repository: {moved_workspace / 'artifact'}"
                        )
            finally:
                os.close(descriptor)

            self.assertTrue(staging_started)
            self.assertFalse((moved_workspace / "artifact").exists())
            self.assertFalse(lexical_output.exists())
            self.assertEqual(tuple(moved_workspace.iterdir()), ())

    def test_descriptor_bound_output_rolls_back_workspace_moved_during_rename(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            copied_repo = temporary_root / "repository"
            shutil.copytree(
                ROOT,
                copied_repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            workspace = temporary_root / "workspace"
            workspace.mkdir()
            descriptor = os.open(workspace, build_module._directory_open_flags())
            moved_workspace = copied_repo / "moved-workspace"
            lexical_output = workspace / "artifact"
            real_rename = build_module._renameat2_noreplace
            real_exchange = build_module._renameat2_exchange
            rename_window_entered = False
            rename_calls = 0
            exchange_calls = 0

            def move_during_exclusive_rename(
                parent_fd: int, source_name: str, destination_name: str
            ) -> bool:
                nonlocal rename_calls, rename_window_entered
                rename_calls += 1
                if not rename_window_entered:
                    workspace.rename(moved_workspace)
                    rename_window_entered = True
                return real_rename(parent_fd, source_name, destination_name)

            def count_exchange(
                parent_fd: int, source_name: str, destination_name: str
            ) -> None:
                nonlocal exchange_calls
                exchange_calls += 1
                real_exchange(parent_fd, source_name, destination_name)

            try:
                with mock.patch.object(
                    build_module,
                    "_renameat2_noreplace",
                    side_effect=move_during_exclusive_rename,
                ), mock.patch.object(
                    build_module,
                    "_renameat2_exchange",
                    side_effect=count_exchange,
                ):
                    with self.assertRaises(BuildError):
                        build_opencode_package(
                            copied_repo,
                            lexical_output,
                            output_parent_fd=descriptor,
                        )
            finally:
                os.close(descriptor)

            self.assertTrue(rename_window_entered)
            self.assertEqual(rename_calls, 1)
            self.assertGreaterEqual(exchange_calls, 1)
            self.assertFalse((moved_workspace / "artifact").exists())
            self.assertFalse(lexical_output.exists())
            self.assertEqual(tuple(moved_workspace.iterdir()), ())

    @needs_node_and_npm
    def test_packed_package_is_publishable_installable_and_self_contained(self) -> None:
        assert NODE is not None
        assert NPM is not None
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            package_root = temporary_root / "package"
            build_opencode_package(ROOT, package_root)
            pack_result = run(
                [NPM, "pack", "--json", "--pack-destination", str(temporary_root)],
                package_root,
            )
            self.assertEqual(
                pack_result.returncode,
                0,
                f"npm pack failed:\n{pack_result.stdout}\n{pack_result.stderr}",
            )
            pack_report = json.loads(pack_result.stdout)
            self.assertEqual(len(pack_report), 1, pack_report)
            tarball = temporary_root / pack_report[0]["filename"]
            self.assertTrue(tarball.is_file(), pack_report)

            problems: list[str] = []
            with tarfile.open(tarball, "r:gz") as archive:
                members = {member.name: member for member in archive.getmembers()}
                links = sorted(
                    name
                    for name, member in members.items()
                    if member.issym() or member.islnk()
                )
                if links:
                    problems.append(f"tarball contains links: {links}")
                caches = sorted(
                    name for name in members if is_python_cache(Path(name))
                )
                if caches:
                    problems.append(f"tarball contains Python caches: {caches}")

                def packed_bytes(relative: str) -> bytes | None:
                    name = f"package/{relative}"
                    member = members.get(name)
                    if member is None:
                        problems.append(f"tarball is missing {relative}")
                        return None
                    if not member.isfile():
                        problems.append(f"tarball member is not a regular file: {relative}")
                        return None
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    return extracted.read()

                manifest_bytes = packed_bytes("package.json")
                if manifest_bytes is not None:
                    manifest = json.loads(manifest_bytes)
                    if manifest.get("private") is True:
                        problems.append("package manifest is private")
                    if manifest.get("license") != "MIT":
                        problems.append("package manifest does not declare the MIT license")
                    if manifest.get("exports") != {".": "./index.js"}:
                        problems.append("package manifest has no explicit root export")

                for relative in ("index.js", "LICENSE"):
                    packed_bytes(relative)

                missing_skills: list[str] = []
                changed_skills: list[str] = []
                for source in sorted((CODEX_ROOT / "skills").rglob("*")):
                    relative_path = source.relative_to(CODEX_ROOT / "skills")
                    if is_python_cache(relative_path) or not source.is_file():
                        continue
                    relative = relative_path.as_posix()
                    name = f"package/skills/{relative}"
                    member = members.get(name)
                    if member is None or not member.isfile():
                        missing_skills.append(relative)
                        continue
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    if extracted.read() != source.read_bytes():
                        changed_skills.append(relative)
                if missing_skills:
                    problems.append(
                        f"tarball is missing {len(missing_skills)} shared skill files"
                    )
                if changed_skills:
                    problems.append(
                        f"tarball changed {len(changed_skills)} shared skill files"
                    )

                for helper in SHARED_HELPERS:
                    contents = packed_bytes(f"scripts/{helper}")
                    source = CODEX_ROOT / "scripts" / helper
                    if contents is not None and contents != source.read_bytes():
                        problems.append(f"tarball changed shared helper scripts/{helper}")

                policy = packed_bytes("assets/execution-policy.json")
                policy_source = CODEX_ROOT / "assets" / "execution-policy.json"
                if policy is not None and policy != policy_source.read_bytes():
                    problems.append("tarball changed assets/execution-policy.json")

                for license_name in THIRD_PARTY_LICENSES:
                    contents = packed_bytes(f"third-party/licenses/{license_name}")
                    source = CODEX_ROOT / "third-party" / "licenses" / license_name
                    if contents is not None and contents != source.read_bytes():
                        problems.append(
                            f"tarball changed third-party license {license_name}"
                        )

            project = temporary_root / "consumer"
            project.mkdir()
            (project / "package.json").write_text(
                '{"name":"opencode-package-smoke","private":true,"type":"module"}\n',
                encoding="utf-8",
            )
            install_result = run(
                [
                    NPM,
                    "install",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    "--package-lock=false",
                    str(tarball),
                ],
                project,
            )
            if install_result.returncode != 0:
                problems.append(
                    "clean npm install failed: "
                    + (install_result.stderr.strip() or install_result.stdout.strip())
                )
            else:
                installed = project / "node_modules" / "opencode-expskill"
                installed_links = sorted(
                    path.relative_to(installed).as_posix()
                    for path in installed.rglob("*")
                    if path.is_symlink()
                )
                if installed_links:
                    problems.append(f"installed package contains links: {installed_links}")

                import_script = """
const plugin = await import("opencode-expskill");
const names = Object.keys(plugin).sort();
const expected = ["ExecutionPolicyPlugin", "UnslopPlugin"];
if (JSON.stringify(names) !== JSON.stringify(expected)) {
  throw new Error(`unexpected exports: ${JSON.stringify(names)}`);
}
for (const name of expected) {
  if (typeof plugin[name] !== "function") {
    throw new Error(`${name} is not a plugin function`);
  }
}
const policyHooks = await plugin.ExecutionPolicyPlugin({});
const args = { subagent_type: "expskill-implementer" };
for (let index = 0; index < 30; index++) {
  const input = { tool: "task", sessionID: "packed-policy", callID: `call-${index}` };
  await policyHooks["tool.execute.before"](input, { args });
  await policyHooks["tool.execute.after"]({ ...input, args }, {});
}
let blocked = false;
try {
  await policyHooks["tool.execute.before"](
    { tool: "task", sessionID: "packed-policy", callID: "call-30" }, { args },
  );
} catch (error) {
  blocked = error.message.includes("budget exhausted");
}
if (!blocked) {
  throw new Error("installed ExecutionPolicyPlugin did not enforce its packaged budget");
}
const hooks = await plugin.UnslopPlugin({});
const output = { system: ["base instructions"] };
await hooks["experimental.chat.system.transform"]({ sessionID: "packed" }, output);
if (!output.system[0].includes("<unslop-scope>")) {
  throw new Error("installed UnslopPlugin could not load its packaged skill");
}
console.log(JSON.stringify(names));
"""
                clean_env = dict(os.environ)
                clean_env.pop("EXPSKILL_HOME", None)
                import_result = run(
                    [NODE, "--input-type=module", "--eval", import_script],
                    project,
                    env=clean_env,
                )
                if import_result.returncode != 0:
                    problems.append(
                        "installed package root import failed: "
                        + (import_result.stderr.strip() or import_result.stdout.strip())
                    )
                else:
                    try:
                        exported = tuple(json.loads(import_result.stdout))
                    except json.JSONDecodeError:
                        problems.append(
                            "installed package root returned invalid export evidence: "
                            + import_result.stdout.strip()
                        )
                    else:
                        if exported != EXPECTED_EXPORTS:
                            problems.append(f"installed package exports are {exported!r}")

            self.assertEqual(problems, [], "\n".join(problems))

    @needs_node_and_npm
    def test_npm_pack_omits_python_caches_after_packaged_helper_runs(self) -> None:
        assert NPM is not None
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            package = temporary_root / "package"
            build_opencode_package(ROOT, package)
            helper_env = dict(os.environ)
            helper_env.pop("PYTHONDONTWRITEBYTECODE", None)
            helper_result = run(
                [sys.executable, "-c", "import plan_graph"],
                package / "scripts",
                env=helper_env,
            )
            self.assertEqual(
                helper_result.returncode,
                0,
                f"packaged helper failed:\n{helper_result.stdout}\n{helper_result.stderr}",
            )
            self.assertTrue(
                any(
                    is_python_cache(path.relative_to(package))
                    for path in (package / "scripts").rglob("*")
                ),
                "packaged helper did not leave a Python cache to exercise npm ignores",
            )
            pack_result = run(
                [NPM, "pack", "--json", "--pack-destination", str(temporary_root)],
                package,
            )
            self.assertEqual(
                pack_result.returncode,
                0,
                f"npm pack failed:\n{pack_result.stdout}\n{pack_result.stderr}",
            )
            report = json.loads(pack_result.stdout)
            self.assertEqual(len(report), 1, report)
            tarball = temporary_root / report[0]["filename"]
            with tarfile.open(tarball, "r:gz") as archive:
                caches = sorted(
                    member.name
                    for member in archive.getmembers()
                    if is_python_cache(Path(member.name))
                )
            self.assertEqual(caches, [])


if __name__ == "__main__":
    unittest.main()
