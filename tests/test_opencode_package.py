from __future__ import annotations

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

        def fd_snapshot() -> set[int]:
            return {int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()}

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
