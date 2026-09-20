from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.build_opencode_package as build_module
import scripts.install as install_module
import scripts.validate as validate_module
from scripts.build_opencode_package import BuildError, build_opencode_package
from scripts.install import InstallError, install_opencode, uninstall_opencode


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
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
AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
PLUGINS = ("unslop.js", "execution-policy.js")


def seed_repository(path: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(ROOT / ".agents", path / ".agents", ignore=ignore)
    shutil.copytree(ROOT / "plugins", path / "plugins", ignore=ignore)
    shutil.copytree(ROOT / "scripts", path / "scripts", ignore=ignore)
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def receipt_path(state: Path) -> Path:
    return state / "expskill" / "install-opencode.json"


def profile_symlinks(config: Path) -> list[Path]:
    """Exclude receipt-owned hard-link anchors from public-link counts."""

    return [
        path
        for path in config.rglob("*")
        if path.is_symlink()
        and not path.name.startswith(install_module.OPENCODE_LINK_ANCHOR_PREFIX)
    ]


def original_feature_legacy_links(repo: Path, config: Path) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for name in SKILLS:
        links.append(
            {
                "source": str(repo / "packages" / "codex" / "skills" / name),
                "destination": str(config / "skills" / name),
            }
        )
    for name in SKILLS:
        links.append(
            {
                "source": str(repo / "packages" / "opencode" / "commands" / f"{name}.md"),
                "destination": str(config / "commands" / f"{name}.md"),
            }
        )
    for name in AGENTS:
        links.append(
            {
                "source": str(repo / "packages" / "opencode" / "agents" / f"{name}.md"),
                "destination": str(config / "agents" / f"{name}.md"),
            }
        )
    for name in PLUGINS:
        links.append(
            {
                "source": str(repo / "packages" / "opencode" / "plugins" / name),
                "destination": str(config / "plugins" / name),
            }
        )
    return links


class FoundationCorrectionTests(unittest.TestCase):
    def test_receipt_reverse_exchange_failure_restores_foreign_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_directory = root / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            displaced_original = state_directory / "displaced-original.json"
            receipt.write_text('{"generation": "original"}\n', encoding="utf-8")
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            real_exchange = install_module._renameat_exchange
            exchanges = 0

            def replace_then_fail_reverse(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal exchanges
                exchanges += 1
                if exchanges == 1:
                    receipt.rename(displaced_original)
                    receipt.write_text("foreign receipt\n", encoding="utf-8")
                    real_exchange(source_fd, source_name, target_fd, target_name)
                    return
                raise OSError("injected reverse exchange failure")

            try:
                with mock.patch.object(
                    install_module,
                    "_renameat_exchange",
                    side_effect=replace_then_fail_reverse,
                ):
                    with self.assertRaises(InstallError):
                        install_module._write_state_payload(
                            receipt, {"generation": "installer"}
                        )
                # The failed reverse syscall leaves both exact identities in
                # their recoverable names.  A lock-held retry restores the
                # displaced public object before rejecting the write.
                with self.assertRaises(InstallError):
                    install_module._write_state_payload(
                        receipt, {"generation": "installer"}
                    )
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

            self.assertEqual(receipt.read_text(encoding="utf-8"), "foreign receipt\n")
            self.assertEqual(
                displaced_original.read_text(encoding="utf-8"),
                '{"generation": "original"}\n',
            )
            self.assertFalse(
                any(path.name.endswith(".tmp") for path in state_directory.iterdir())
            )

    def test_source_changing_upgrade_recovers_old_backup_after_each_publish_rename(
        self,
    ) -> None:
        for crash_point in ("backup", "published"):
            with self.subTest(crash_point=crash_point):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    old_marker = "Cut AI tells"
                    new_marker = "Source-changing recovery marker"
                    skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
                    self.assertIn(old_marker, skill.read_text(encoding="utf-8"))
                    skill.write_text(
                        skill.read_text(encoding="utf-8").replace(
                            old_marker, new_marker
                        ),
                        encoding="utf-8",
                    )
                    real_rename = install_module._rename_noreplace
                    real_publish = install_module._publish_workspace_candidate

                    def crash_after_rename(source: Path, target: Path) -> None:
                        is_backup = (
                            source.name == "opencode-artifact"
                            and target.name.startswith(".opencode-artifact.old-")
                        )
                        real_rename(source, target)
                        if crash_point == "backup" and is_backup:
                            raise SystemExit("injected backup crash")

                    def crash_after_publish(
                        workspace: Path,
                        workspace_identity: tuple[int, int],
                        candidate: Path,
                        candidate_identity: tuple[int, int],
                        artifact: Path,
                    ) -> None:
                        real_publish(
                            workspace,
                            workspace_identity,
                            candidate,
                            candidate_identity,
                            artifact,
                        )
                        if crash_point == "published":
                            raise SystemExit("injected published crash")

                    with (
                        mock.patch.object(
                            install_module,
                            "_rename_noreplace",
                            side_effect=crash_after_rename,
                        ),
                        mock.patch.object(
                            install_module,
                            "_publish_workspace_candidate",
                            side_effect=crash_after_publish,
                        ),
                    ):
                        with self.assertRaises(SystemExit):
                            install_opencode(repo, config, state)

                    if crash_point == "backup":
                        install_opencode(repo, config, state)
                        self.assertIn(
                            new_marker,
                            (config / "commands/unslop.md").read_text(
                                encoding="utf-8"
                            ),
                        )
                    else:
                        uninstall_opencode(repo, config, state)
                        self.assertFalse(receipt_path(state).exists())
                        self.assertFalse(
                            (state / "expskill/opencode-artifact").exists()
                        )
                    self.assertFalse(
                        any(
                            path.name.startswith(".opencode-artifact.old-")
                            or path.name.startswith(".opencode-artifact.next-")
                            for path in (state / "expskill").iterdir()
                        )
                    )

    def test_failed_install_cleans_bound_state_not_redirected_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            moved = root / "moved-state"
            outside = root / "outside"
            outside.mkdir()
            redirected = outside / "expskill"

            def redirect_state_then_fail(
                _source: Path, _candidate: Path, **_kwargs: object
            ) -> Path:
                state.rename(moved)
                state.symlink_to(outside, target_is_directory=True)
                redirected.mkdir()
                raise BuildError("injected build failure after state substitution")

            with mock.patch.object(
                install_module,
                "build_opencode_package",
                side_effect=redirect_state_then_fail,
            ):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertTrue(redirected.is_dir())
            self.assertEqual(list(redirected.iterdir()), [])
            self.assertTrue((moved / "expskill").is_dir())
            retained = tuple((moved / "expskill").iterdir())
            self.assertEqual(
                sum(path.name == "install-opencode.json" for path in retained),
                1,
            )
            self.assertEqual(
                sum(path.name.startswith(".opencode-artifact.txn-") for path in retained),
                1,
            )

    def test_uninstall_recovers_interrupted_initial_artifact_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_publish = install_module._publish_workspace_candidate

            def crash_after_initial_publish(
                workspace: Path,
                workspace_identity: tuple[int, int],
                candidate: Path,
                candidate_identity: tuple[int, int],
                artifact: Path,
            ) -> None:
                real_publish(
                    workspace,
                    workspace_identity,
                    candidate,
                    candidate_identity,
                    artifact,
                )
                raise SystemExit("injected initial publication crash")

            with mock.patch.object(
                install_module,
                "_publish_workspace_candidate",
                side_effect=crash_after_initial_publish,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            anchor = (
                state
                / "expskill"
                / f"{install_module.OPENCODE_ARTIFACT_ANCHOR_PREFIX}{payload['lineage']}"
            )
            self.assertEqual(payload["teardown_phase"], "committed")
            self.assertEqual(payload["pending_publish"]["phase"], "prepared")
            self.assertFalse(anchor.exists())
            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            # Artifact publication crashed before any link was staged.
            self.assertEqual(
                len(profile_symlinks(config)),
                len(payload["links"]),
            )

    def test_uninstall_preserves_unproven_interrupted_initial_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            artifact = state / "expskill/opencode-artifact"
            real_publish = install_module._publish_workspace_candidate

            def crash_after_initial_publish(
                workspace: Path,
                workspace_identity: tuple[int, int],
                candidate: Path,
                candidate_identity: tuple[int, int],
                published_artifact: Path,
            ) -> None:
                real_publish(
                    workspace,
                    workspace_identity,
                    candidate,
                    candidate_identity,
                    published_artifact,
                )
                raise SystemExit("injected initial publication crash")

            with mock.patch.object(
                install_module,
                "_publish_workspace_candidate",
                side_effect=crash_after_initial_publish,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            (artifact / "unexpected.txt").write_text("foreign\n", encoding="utf-8")
            receipt_before = receipt_path(state).read_bytes()
            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)

            self.assertEqual(receipt_path(state).read_bytes(), receipt_before)
            self.assertEqual(
                (artifact / "unexpected.txt").read_text(encoding="utf-8"),
                "foreign\n",
            )

    def test_foreign_matching_backup_survives_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            foreign = state / "expskill" / ".opencode-artifact.old-foreign"
            foreign.mkdir()
            (foreign / "foreign.txt").write_text("must survive\n", encoding="utf-8")
            install_opencode(repo, config, state)
            self.assertEqual((foreign / "foreign.txt").read_text(encoding="utf-8"), "must survive\n")

    def test_interrupted_swap_backup_survives_failed_next_build_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill" / "opencode-artifact"
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "interrupted swap recovery marker"
                ),
                encoding="utf-8",
            )
            def crash_after_live_backup(
                workspace: Path,
                workspace_identity: tuple[int, int],
                candidate: Path,
                candidate_identity: tuple[int, int],
                published_artifact: Path,
            ) -> None:
                raise SystemExit("simulated process crash")

            with mock.patch.object(
                install_module,
                "_publish_workspace_candidate",
                side_effect=crash_after_live_backup,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)
            backups = tuple((state / "expskill").glob(".opencode-artifact.old-*"))
            self.assertEqual(len(backups), 1)
            self.assertFalse(artifact.exists())
            with mock.patch.object(install_module, "build_opencode_package", side_effect=BuildError("failed")):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)
            self.assertTrue(artifact.is_dir())
            self.assertFalse(backups[0].exists())
            install_opencode(repo, config, state)
            self.assertTrue(artifact.is_dir())

    def test_output_parent_move_and_symlink_after_staging_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            parent = root / "output-parent"
            parent.mkdir()
            outside = root / "outside"
            outside.mkdir()
            output = parent / "artifact"
            real_publish = build_module._replace_output

            def move_parent_then_publish(staging: Path, target: Path) -> None:
                moved = root / "moved-parent"
                target.parent.rename(moved)
                target.parent.symlink_to(outside, target_is_directory=True)
                real_publish(staging, target)

            with mock.patch.object(build_module, "_replace_output", side_effect=move_parent_then_publish):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, output)
            self.assertTrue(output.parent.is_symlink())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((outside / "artifact").exists())
            self.assertFalse(any(path.name.startswith(".artifact.") for path in (root / "moved-parent").iterdir()))

    def test_snapshot_mutation_after_read_aborts_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            real_read_bytes = Path.read_bytes
            mutated = False

            def read_then_mutate(path: Path) -> bytes:
                nonlocal mutated
                data = real_read_bytes(path)
                if path == skill and not mutated:
                    mutated = True
                    skill.write_bytes(data + b"\nmutated after snapshot read\n")
                return data

            with mock.patch.object(Path, "read_bytes", autospec=True, side_effect=read_then_mutate):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, root / "artifact")

    def test_snapshot_two_file_old_new_mixture_aborts_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            first = repo / "plugins/expskill/skills/brainstorm/SKILL.md"
            second = repo / "plugins/expskill/skills/design/SKILL.md"
            real_read_bytes = Path.read_bytes
            mutated = False

            def read_then_mutate(path: Path) -> bytes:
                nonlocal mutated
                data = real_read_bytes(path)
                if path == second and not mutated:
                    mutated = True
                    first.write_bytes(real_read_bytes(first) + b"\nmutated between source reads\n")
                return data

            with mock.patch.object(Path, "read_bytes", autospec=True, side_effect=read_then_mutate):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, root / "artifact")

    def test_publish_failure_on_second_operation_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            output = root / "artifact"
            moved = 0
            real_rename = os.rename

            def fail_second(source: str, target: str, **kwargs: object) -> None:
                nonlocal moved
                if kwargs.get("src_dir_fd") is not None and kwargs.get("dst_dir_fd") is not None:
                    moved += 1
                    if moved == 2:
                        raise OSError("injected second publish failure")
                real_rename(source, target, **kwargs)

            with mock.patch.object(build_module, "_renameat2_noreplace", return_value=False):
                with mock.patch.object(build_module.os, "rename", side_effect=fail_second):
                    with self.assertRaises(BuildError):
                        build_opencode_package(repo, output)
            self.assertFalse(output.exists())

    def test_exclusive_publish_unavailable_fails_closed_without_output(self) -> None:
        """The builder must not expose a child-by-child partial artifact."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            output = root / "artifact"
            with mock.patch.object(build_module, "_renameat2_noreplace", return_value=False):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, output)
            self.assertFalse(output.exists())

    def test_builder_does_not_require_procfs_for_staging_or_cleanup(self) -> None:
        """Descriptor-relative publication remains usable when /proc is absent."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            output = root / "artifact"
            real_exists = Path.exists

            def hide_proc(path: Path) -> bool:
                if str(path).startswith("/proc/self/fd"):
                    return False
                return real_exists(path)

            with mock.patch.object(Path, "exists", autospec=True, side_effect=hide_proc):
                artifact = build_opencode_package(repo, output)
            self.assertTrue(artifact.is_dir())

    def test_validator_rejects_non_mapping_provenance_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            for value in ([], None):
                real_build = validate_module.build_opencode_package

                def mutate(source: Path, artifact: Path, value: object = value) -> Path:
                    result = real_build(source, artifact)
                    (result / "provenance.json").write_text(
                        json.dumps(value), encoding="utf-8"
                    )
                    return result

                with mock.patch.object(validate_module, "build_opencode_package", side_effect=mutate):
                    errors = validate_module.validate_repository(
                        repo, include_main=False, include_opencode=True
                    )
                self.assertTrue(any("provenance" in error.lower() for error in errors), errors)

    def test_forged_transaction_record_cannot_authorize_foreign_backup_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            parent = state / "expskill"
            foreign = parent / ".opencode-artifact.old-forged"
            foreign.mkdir()
            (foreign / "foreign.txt").write_text("must survive\n", encoding="utf-8")
            forged = parent / ".opencode-artifact.txn-forged.json"
            forged.write_text(
                json.dumps(
                    {
                        "schema_version": "opencode-artifact-transaction.v1",
                        "phase": "published",
                        "artifact": str(parent / "opencode-artifact"),
                        "backup": str(foreign),
                        "backup_dev": foreign.stat().st_dev,
                        "backup_ino": foreign.stat().st_ino,
                        "artifact_dev": (parent / "opencode-artifact").stat().st_dev,
                        "artifact_ino": (parent / "opencode-artifact").stat().st_ino,
                    }
                ),
                encoding="utf-8",
            )
            install_opencode(repo, config, state)
            self.assertTrue((foreign / "foreign.txt").exists())

    def test_empty_link_receipt_cannot_own_foreign_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            artifact = state / "expskill" / "opencode-artifact"
            artifact.mkdir(parents=True)
            marker = artifact / "foreign.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            receipt_path(state).write_text(
                json.dumps(
                    {
                        "artifact_root": str(artifact),
                        "lineage": "f" * 32,
                        "links": [],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(repo.resolve()),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")

    def test_incomplete_current_receipt_link_inventory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            payload["links"].pop()
            receipt_path(state).write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)

    def test_state_ancestor_replacement_cannot_redirect_receipt_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            moved = root / "moved-state"
            outside = root / "outside"
            outside.mkdir()
            real_write = install_module._write_state_payload
            replaced = False

            def replace_ancestor(path: Path, payload: object) -> bool:
                nonlocal replaced
                if not replaced:
                    replaced = True
                    state.rename(moved)
                    state.symlink_to(outside, target_is_directory=True)
                return real_write(path, payload)

            with mock.patch.object(
                install_module, "_write_state_payload", side_effect=replace_ancestor
            ):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertTrue(replaced)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((outside / "expskill" / "install-opencode.json").exists())

    def test_pending_swap_alone_cannot_authorize_backup_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            parent = state / "expskill"
            artifact = parent / "opencode-artifact"
            backup = parent / ".opencode-artifact.old-forged"
            artifact.mkdir(parents=True)
            backup.mkdir()
            marker = backup / "foreign.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            artifact_metadata = artifact.stat()
            backup_metadata = backup.stat()
            lineage = "f" * 32
            receipt_path(state).write_text(
                json.dumps(
                    {
                        "lineage": lineage,
                        "pending_swap": {
                            "artifact": str(artifact),
                            "backup": str(backup),
                            "backup_dev": backup_metadata.st_dev,
                            "backup_ino": backup_metadata.st_ino,
                            "candidate": str(parent / ".opencode-artifact.next-forged"),
                            "candidate_dev": artifact_metadata.st_dev,
                            "candidate_ino": artifact_metadata.st_ino,
                            "lineage": lineage,
                            "live_dev": artifact_metadata.st_dev,
                            "live_ino": artifact_metadata.st_ino,
                            "phase": "published",
                        },
                    }
                ),
                encoding="utf-8",
            )

            install_module._garbage_collect_opencode_backups(state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")

    def test_forged_pending_swap_in_legitimate_receipt_cannot_delete_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            parent = state / "expskill"
            artifact = parent / "opencode-artifact"
            backup = parent / ".opencode-artifact.old-forged"
            backup.mkdir()
            marker = backup / "foreign.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            artifact_metadata = artifact.stat()
            backup_metadata = backup.stat()
            lineage = payload["lineage"]
            payload["pending_swap"] = {
                "artifact": str(artifact),
                "backup": str(backup),
                "backup_dev": backup_metadata.st_dev,
                "backup_ino": backup_metadata.st_ino,
                "candidate": str(parent / ".opencode-artifact.next-forged"),
                "candidate_dev": artifact_metadata.st_dev,
                "candidate_ino": artifact_metadata.st_ino,
                "lineage": lineage,
                "live_dev": backup_metadata.st_dev,
                "live_ino": backup_metadata.st_ino,
                "phase": "published",
            }
            payload["pending_swap_auth"] = install_module._pending_swap_checksum(
                lineage, payload["pending_swap"]
            )
            receipt_path(state).write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")

    def test_first_publish_preserves_late_foreign_live_occupant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            artifact = state / "expskill" / "opencode-artifact"
            real_matches = install_module._artifact_matches_sources
            foreign_identity: tuple[int, int] | None = None

            def inject_live(source: Path, candidate: Path) -> bool:
                nonlocal foreign_identity
                matches = real_matches(source, candidate)
                artifact.mkdir()
                metadata = artifact.stat()
                foreign_identity = (metadata.st_dev, metadata.st_ino)
                return matches

            with mock.patch.object(install_module, "_artifact_matches_sources", side_effect=inject_live):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertIsNotNone(foreign_identity)
            self.assertEqual((artifact.stat().st_dev, artifact.stat().st_ino), foreign_identity)

    def test_reinstall_preserves_late_foreign_backup_occupant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "late foreign backup marker"
                ),
                encoding="utf-8",
            )
            real_write = install_module._write_receipt
            foreign: Path | None = None
            foreign_identity: tuple[int, int] | None = None

            def inject_backup(path: Path, receipt: object) -> None:
                nonlocal foreign, foreign_identity
                real_write(path, receipt)
                pending = getattr(receipt, "pending_swap", None)
                if foreign is None and pending is not None and pending.phase == "prepared":
                    foreign = pending.backup
                    foreign.mkdir()
                    metadata = foreign.stat()
                    foreign_identity = (metadata.st_dev, metadata.st_ino)

            with mock.patch.object(install_module, "_write_receipt", side_effect=inject_backup):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertIsNotNone(foreign)
            self.assertIsNotNone(foreign_identity)
            assert foreign is not None
            self.assertEqual((foreign.stat().st_dev, foreign.stat().st_ino), foreign_identity)

    def test_backup_cleanup_preserves_identity_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "backup identity replacement marker"
                ),
                encoding="utf-8",
            )
            real_write = install_module._write_receipt
            published_writes = 0
            replacement: Path | None = None
            detached: Path | None = None

            def replace_before_cleanup(path: Path, receipt: object) -> None:
                nonlocal published_writes, replacement, detached
                real_write(path, receipt)
                pending = getattr(receipt, "pending_swap", None)
                if pending is not None and pending.phase == "published":
                    published_writes += 1
                    if published_writes == 2:
                        replacement = pending.backup
                        detached = pending.backup.with_name(pending.backup.name + ".detached")
                        pending.backup.rename(detached)
                        replacement.mkdir()
                        (replacement / "foreign.txt").write_text("must survive\n", encoding="utf-8")

            with mock.patch.object(install_module, "_write_receipt", side_effect=replace_before_cleanup):
                install_opencode(repo, config, state)

            self.assertIsNotNone(replacement)
            self.assertIsNotNone(detached)
            assert replacement is not None and detached is not None
            self.assertEqual((replacement / "foreign.txt").read_text(encoding="utf-8"), "must survive\n")
            self.assertTrue(detached.is_dir())

    def test_recovery_persists_published_state_before_next_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "published recovery marker"
                ),
                encoding="utf-8",
            )
            real_write = install_module._write_receipt

            def crash_before_published_receipt(path: Path, receipt: object) -> None:
                pending = getattr(receipt, "pending_swap", None)
                if pending is not None and pending.phase == "published":
                    raise SystemExit("injected process death")
                real_write(path, receipt)

            with mock.patch.object(
                install_module, "_write_receipt", side_effect=crash_before_published_receipt
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            install_opencode(repo, config, state)

            self.assertFalse(
                any(path.name.startswith(".opencode-artifact.old-") for path in (state / "expskill").iterdir())
            )

    def test_first_publish_crash_recovers_from_prepublication_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"

            with mock.patch.object(
                install_module,
                "preflight_opencode_links",
                side_effect=SystemExit("injected process death"),
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertTrue((state / "expskill" / "opencode-artifact").is_dir())
            install_opencode(repo, config, state)
            self.assertTrue(receipt_path(state).is_file())

    def test_successful_reinstall_clears_pending_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)

            install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            self.assertNotIn("pending_swap", payload)

    def test_receipt_inventory_without_live_links_cannot_own_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill" / "opencode-artifact"
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            artifact_identity = (artifact.stat().st_dev, artifact.stat().st_ino)
            anchor = Path(payload["artifact_anchor"])
            self.assertEqual(
                (anchor.stat().st_dev, anchor.stat().st_ino),
                (
                    (artifact / install_module.OPENCODE_ARTIFACT_ANCHOR_FILE).stat().st_dev,
                    (artifact / install_module.OPENCODE_ARTIFACT_ANCHOR_FILE).stat().st_ino,
                ),
            )
            shutil.rmtree(config)

            install_opencode(repo, config, state)

            self.assertEqual(
                (artifact.stat().st_dev, artifact.stat().st_ino), artifact_identity
            )
            self.assertEqual(
                len(profile_symlinks(config)),
                len(payload["links"]),
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            artifact = Path(payload["artifact_root"])
            anchor = Path(payload["artifact_anchor"])
            displaced_anchor = anchor.with_name("displaced-permanent-anchor")
            anchor.rename(displaced_anchor)
            anchor.write_text("forged anchor\n", encoding="utf-8")

            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)

            self.assertTrue(artifact.is_dir())
            self.assertEqual(anchor.read_text(encoding="utf-8"), "forged anchor\n")

    def test_one_live_link_cannot_authorize_foreign_artifact_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill/opencode-artifact"
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            anchor = Path(payload["artifact_anchor"])
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            for entry in payload["links"][1:]:
                Path(entry["destination"]).unlink()

            result = uninstall_opencode(repo, config, state)

            self.assertEqual(len(result.removed_links), 1)
            self.assertFalse(Path(payload["links"][0]["destination"]).exists())
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(artifact.exists())
            self.assertFalse(anchor.exists())

    def test_nearly_complete_live_inventory_cannot_authorize_artifact_deletion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill/opencode-artifact"
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            anchor = Path(payload["artifact_anchor"])
            missing = Path(payload["links"][-1]["destination"])
            missing.unlink()

            result = uninstall_opencode(repo, config, state)

            self.assertFalse(missing.exists())
            self.assertEqual(len(result.removed_links), len(payload["links"]) - 1)
            self.assertFalse(profile_symlinks(config))
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(artifact.exists())
            self.assertFalse(anchor.exists())

    def test_forged_pending_publish_cannot_delete_foreign_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            parent = state / "expskill"
            parent.mkdir(parents=True)
            artifact = parent / "opencode-artifact"
            candidate = parent / ".opencode-artifact.next-forged"
            candidate.mkdir()
            marker = candidate / "foreign.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            metadata = candidate.stat()
            lineage = "f" * 32
            receipt_path(state).write_text(
                json.dumps(
                    {
                        "artifact_root": str(artifact),
                        "lineage": lineage,
                        "links": [],
                        "marketplace_added": False,
                        "pending_publish": {
                            "artifact": str(artifact),
                            "candidate": str(candidate),
                            "candidate_dev": metadata.st_dev,
                            "candidate_ino": metadata.st_ino,
                            "lineage": lineage,
                            "phase": "prepared",
                        },
                        "plugin_installed": True,
                        "repository_root": str(repo.resolve()),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")

    def test_forged_digest_and_identity_cannot_delete_foreign_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            parent = state / "expskill"
            artifact = build_opencode_package(
                repo, parent / "opencode-artifact"
            )
            marker = artifact / "foreign-marker.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            metadata = artifact.stat()
            lineage = "f" * 32
            digest = install_module._artifact_evidence(artifact)
            self.assertIsNotNone(digest)
            receipt_path(state).write_text(
                json.dumps(
                    {
                        "artifact_root": str(artifact),
                        "lineage": lineage,
                        "links": [],
                        "marketplace_added": False,
                        "pending_publish": {
                            "artifact": str(artifact),
                            "candidate": str(
                                parent / ".opencode-artifact.next-forged"
                            ),
                            "candidate_dev": metadata.st_dev,
                            "candidate_digest": digest,
                            "candidate_ino": metadata.st_ino,
                            "lineage": lineage,
                            "phase": "published",
                        },
                        "plugin_installed": True,
                        "repository_root": str(repo.resolve()),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")
            self.assertTrue(receipt_path(state).is_file())

    def test_forged_digest_and_identity_cannot_delete_foreign_swap_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            parent = state / "expskill"
            artifact = parent / "opencode-artifact"
            candidate = build_opencode_package(
                repo, parent / ".opencode-artifact.next-forged"
            )
            marker = candidate / "foreign-marker.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            live_metadata = artifact.stat()
            candidate_metadata = candidate.stat()
            pending = {
                "artifact": str(artifact),
                "backup": str(parent / ".opencode-artifact.old-forged"),
                "backup_dev": live_metadata.st_dev,
                "backup_digest": payload["artifact_digest"],
                "backup_ino": live_metadata.st_ino,
                "candidate": str(candidate),
                "candidate_dev": candidate_metadata.st_dev,
                "candidate_digest": install_module._artifact_evidence(candidate),
                "candidate_ino": candidate_metadata.st_ino,
                "lineage": payload["lineage"],
                "live_dev": live_metadata.st_dev,
                "live_ino": live_metadata.st_ino,
                "phase": "prepared",
            }
            payload["pending_swap"] = pending
            payload["pending_swap_checksum"] = install_module._pending_swap_checksum(
                payload["lineage"], pending
            )
            receipt_path(state).write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")
            self.assertTrue(receipt_path(state).is_file())

    def test_uninstall_after_complete_initial_link_publication_crash_converges(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertEqual(
                len(profile_symlinks(config)),
                33,
            )
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            self.assertTrue(payload["pending_publish"]["planned_links"])
            self.assertEqual(len(payload["links"]), 33)
            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertEqual(
                len(profile_symlinks(config)),
                0,
            )

    def test_uninstall_after_partial_initial_link_publication_crash_converges(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create = install_module._create_destination_link
            created = 0

            def crash_after_third_link(
                destination: Path,
                source: Path,
                record_staged: object = None,
            ) -> None:
                nonlocal created
                real_create(destination, source, record_staged)
                created += 1
                if created == 3:
                    raise SystemExit("injected crash after partial link publication")

            with mock.patch.object(
                install_module,
                "_create_destination_link",
                side_effect=crash_after_third_link,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertEqual(
                len(profile_symlinks(config)),
                3,
            )
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            self.assertTrue(payload["pending_publish"]["planned_links"])
            self.assertEqual(len(payload["links"]), 33)
            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertEqual(
                len(profile_symlinks(config)),
                0,
            )

    def test_complete_live_inventory_recovers_when_publication_anchor_is_gone(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            Path(payload["pending_publish"]["candidate_anchor"]).unlink()
            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertEqual(
                len(profile_symlinks(config)),
                0,
            )

    def test_anchorless_recovery_retries_after_fourth_unlink_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            Path(payload["pending_publish"]["candidate_anchor"]).unlink()
            real_remove = install_module._remove_opencode_artifact_exact
            removed = False

            def crash_after_artifact_remove(path: Path, dev: int, ino: int) -> None:
                nonlocal removed
                real_remove(path, dev, ino)
                if not removed:
                    removed = True
                    raise SystemExit("injected artifact removal crash")

            with mock.patch.object(
                install_module,
                "_remove_opencode_artifact_exact",
                side_effect=crash_after_artifact_remove,
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)

            self.assertEqual(
                len(profile_symlinks(config)),
                0,
            )
            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertEqual(
                len(profile_symlinks(config)),
                0,
            )

    def test_committed_uninstall_retries_after_fourth_unlink_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            receipt = receipt_path(state)
            original_payload = json.loads(receipt.read_text(encoding="utf-8"))
            original_links = original_payload["links"]
            artifact = state / "expskill/opencode-artifact"
            artifact_identity = (artifact.stat().st_dev, artifact.stat().st_ino)
            real_unlink = install_module._retire_owned_object
            unlinks = 0

            def fail_fourth_unlink(
                destination: Path,
                identity: tuple[int, int],
                role: str,
                *,
                directory: bool,
            ) -> bool:
                nonlocal unlinks
                if role == "final-link":
                    unlinks += 1
                if role == "final-link" and unlinks == 4:
                    raise OSError(errno.EIO, "injected fourth unlink failure")
                return real_unlink(
                    destination, identity, role, directory=directory
                )

            with mock.patch.object(
                install_module,
                "_retire_owned_object",
                side_effect=fail_fourth_unlink,
            ):
                with self.assertRaisesRegex(
                    InstallError, "owned opencode link cleanup failed"
                ):
                    uninstall_opencode(repo, config, state)

            failed_payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(failed_payload["links"], original_links)
            self.assertEqual(failed_payload["teardown_phase"], "removing-links")
            anchor_metadata = Path(failed_payload["artifact_anchor"]).stat()
            anchor_source_metadata = (
                artifact / install_module.OPENCODE_ARTIFACT_ANCHOR_FILE
            ).stat()
            self.assertEqual(
                (anchor_metadata.st_dev, anchor_metadata.st_ino),
                (anchor_source_metadata.st_dev, anchor_source_metadata.st_ino),
            )
            self.assertEqual(
                (artifact.stat().st_dev, artifact.stat().st_ino), artifact_identity
            )
            remaining = [
                Path(entry["destination"])
                for entry in original_links
                if Path(entry["destination"]).is_symlink()
            ]
            self.assertEqual(len(remaining), len(original_links) - 3)
            remaining_exact = remaining[0]
            self.assertEqual(
                remaining_exact.resolve(strict=True),
                Path(
                    next(
                        entry["source"]
                        for entry in original_links
                        if Path(entry["destination"]) == remaining_exact
                    )
                ),
            )

            unrelated_destination = Path(original_links[0]["destination"])
            unrelated_destination.write_text("user-owned\n", encoding="utf-8")
            remaining_exact.unlink()
            unrelated_target = root / "user-owned-target.md"
            unrelated_target.write_text("retargeted\n", encoding="utf-8")
            remaining_exact.symlink_to(unrelated_target)

            uninstall_opencode(repo, config, state)

            self.assertEqual(
                unrelated_destination.read_text(encoding="utf-8"), "user-owned\n"
            )
            self.assertTrue(remaining_exact.is_symlink())
            self.assertEqual(os.readlink(remaining_exact), str(unrelated_target))
            self.assertFalse(receipt.exists())
            self.assertFalse(artifact.exists())

    def test_committed_uninstall_retries_after_fourth_unlink_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            receipt = receipt_path(state)
            original_payload = json.loads(receipt.read_text(encoding="utf-8"))
            real_unlink = install_module._retire_owned_object
            unlinks = 0

            def crash_before_fourth_unlink(
                destination: Path,
                identity: tuple[int, int],
                role: str,
                *,
                directory: bool,
            ) -> bool:
                nonlocal unlinks
                if role == "final-link":
                    unlinks += 1
                if role == "final-link" and unlinks == 4:
                    raise SystemExit("injected fourth unlink crash")
                return real_unlink(
                    destination, identity, role, directory=directory
                )

            with mock.patch.object(
                install_module,
                "_retire_owned_object",
                side_effect=crash_before_fourth_unlink,
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)

            interrupted_payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(interrupted_payload["links"], original_payload["links"])
            self.assertEqual(
                interrupted_payload["teardown_phase"], "removing-links"
            )
            self.assertTrue(Path(interrupted_payload["artifact_anchor"]).is_file())
            self.assertEqual(
                len(profile_symlinks(config)),
                len(original_payload["links"]) - 3,
            )

            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt.exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertFalse(profile_symlinks(config))

    def test_anchorless_recovery_preserves_state_when_reanchoring_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            anchor = Path(payload["pending_publish"]["candidate_anchor"])
            anchor.unlink()
            with mock.patch.object(
                install_module,
                "_create_artifact_anchor",
                side_effect=InstallError("injected re-anchor failure"),
            ):
                with self.assertRaisesRegex(InstallError, "re-anchor failure"):
                    uninstall_opencode(repo, config, state)

            self.assertFalse(anchor.exists())
            self.assertTrue((state / "expskill/opencode-artifact").is_dir())
            self.assertTrue(receipt_path(state).is_file())
            self.assertEqual(
                len(profile_symlinks(config)),
                len(payload["links"]),
            )

    def test_anchorless_recovery_removes_new_anchor_when_persistence_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            anchor = Path(payload["pending_publish"]["candidate_anchor"])
            anchor.unlink()
            with mock.patch.object(
                install_module,
                "_write_receipt",
                side_effect=InstallError("injected re-anchor persistence failure"),
            ):
                with self.assertRaisesRegex(InstallError, "persistence failure"):
                    uninstall_opencode(repo, config, state)

            self.assertFalse(anchor.exists())
            self.assertTrue((state / "expskill/opencode-artifact").is_dir())
            self.assertTrue(receipt_path(state).is_file())
            self.assertEqual(
                len(profile_symlinks(config)),
                len(payload["links"]),
            )

    def test_resumed_initial_publish_persist_failure_keeps_links_recoverable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            with mock.patch.object(
                install_module,
                "_write_receipt",
                side_effect=InstallError("injected resumed publication persistence failure"),
            ):
                with self.assertRaisesRegex(InstallError, "persistence failure"):
                    install_opencode(repo, config, state)

            artifact = state / "expskill/opencode-artifact"
            self.assertTrue(artifact.is_dir())
            self.assertTrue(receipt_path(state).is_file())
            self.assertEqual(
                len(profile_symlinks(config)),
                0,
            )
            self.assertTrue(
                all(
                    entry.get("destination_dev") is not None
                    and entry.get("destination_ino") is not None
                    and entry.get("staged_destination") is not None
                    for entry in payload["links"]
                )
            )

            install_opencode(repo, config, state)
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(artifact.exists())

    def test_hard_link_anchor_failure_aborts_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"

            with mock.patch.object(
                install_module.os,
                "link",
                side_effect=OSError(errno.EXDEV, "cross-device link"),
            ):
                with self.assertRaisesRegex(
                    InstallError, "anchor before publication"
                ):
                    install_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertFalse(
                any((state / "expskill").glob(".opencode-artifact.txn-*"))
            )
            self.assertFalse(profile_symlinks(config))

    def test_crash_cleanup_preserves_retargeted_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_create_links = install_module._create_links

            def crash_after_links(
                links: object, created: list[install_module.ProfileLink]
            ) -> None:
                real_create_links(links, created)
                raise SystemExit("injected crash after complete link publication")

            with mock.patch.object(
                install_module, "_create_links", side_effect=crash_after_links
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            retargeted = config / "agents/expskill-review.md"
            retargeted.unlink()
            unrelated = root / "user-owned.md"
            unrelated.write_text("user-owned\n", encoding="utf-8")
            retargeted.symlink_to(unrelated)
            uninstall_opencode(repo, config, state)

            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(os.readlink(retargeted), str(unrelated))
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertEqual(
                len(profile_symlinks(config)),
                1,
            )

    def test_failed_first_publish_preserves_replacement_at_fixed_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            artifact = state / "expskill" / "opencode-artifact"
            detached = state / "expskill" / "detached-artifact"
            real_preflight = install_module.preflight_opencode_links

            def replace_then_fail(*args: object, **kwargs: object) -> object:
                real_preflight(*args, **kwargs)
                artifact.rename(detached)
                artifact.mkdir()
                (artifact / "foreign.txt").write_text("must survive\n", encoding="utf-8")
                raise InstallError("injected post-publication failure")

            with mock.patch.object(
                install_module, "preflight_opencode_links", side_effect=replace_then_fail
            ):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertEqual(
                (artifact / "foreign.txt").read_text(encoding="utf-8"),
                "must survive\n",
            )
            self.assertTrue(detached.is_dir())

    def test_uninstall_preserves_replacement_at_fixed_artifact_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill" / "opencode-artifact"
            artifact.rename(state / "expskill" / "detached-artifact")
            artifact.mkdir()
            marker = artifact / "foreign.txt"
            marker.write_text("must survive\n", encoding="utf-8")

            uninstall_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")
            self.assertFalse(receipt_path(state).exists())

    def test_install_config_root_substitution_cannot_redirect_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            config.mkdir()
            moved = root / "moved-config"
            outside = root / "outside"
            outside.mkdir()
            state = root / "state"
            real_preflight = install_module.preflight_opencode_links

            def substitute_after_preflight(*args: object, **kwargs: object) -> object:
                links = real_preflight(*args, **kwargs)
                config.rename(moved)
                config.symlink_to(outside, target_is_directory=True)
                return links

            with mock.patch.object(
                install_module,
                "preflight_opencode_links",
                side_effect=substitute_after_preflight,
            ):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertEqual(list(outside.iterdir()), [])

    def test_uninstall_link_check_cannot_delete_through_replaced_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            moved = root / "moved-config"
            outside = root / "outside"
            outside.mkdir()
            state = root / "state"
            install_opencode(repo, config, state)
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            target = Path(payload["links"][0]["destination"])
            real_exact = install_module._recorded_opencode_link_is_live
            substituted = False

            def substitute_after_check(link: install_module.ProfileLink) -> bool:
                nonlocal substituted
                matches = real_exact(link)
                if matches and not substituted:
                    substituted = True
                    config.rename(moved)
                    config.symlink_to(outside, target_is_directory=True)
                    foreign = outside / link.destination.relative_to(config)
                    foreign.parent.mkdir(parents=True, exist_ok=True)
                    foreign.write_text("must survive\n", encoding="utf-8")
                return matches

            with mock.patch.object(
                install_module,
                "_recorded_opencode_link_is_live",
                side_effect=substitute_after_check,
            ):
                with self.assertRaises(InstallError):
                    uninstall_opencode(repo, config, state)

            foreign = outside / target.relative_to(config)
            self.assertEqual(foreign.read_text(encoding="utf-8"), "must survive\n")

    def test_receipt_write_preserves_leaf_replaced_after_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            receipt = receipt_path(state)
            detached = receipt.with_name("detached-receipt.json")
            real_exchange = install_module._renameat_exchange
            injected = False

            def replace_leaf(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                if target_name == receipt.name and not injected:
                    injected = True
                    receipt.rename(detached)
                    receipt.write_text("foreign receipt\n", encoding="utf-8")
                real_exchange(source_fd, source_name, target_fd, target_name)

            with mock.patch.object(
                install_module, "_renameat_exchange", side_effect=replace_leaf
            ):
                with self.assertRaises(InstallError):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertEqual(receipt.read_text(encoding="utf-8"), "foreign receipt\n")

    def test_receipt_unlink_preserves_leaf_replaced_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            receipt = receipt_path(state)
            detached = receipt.with_name("detached-receipt.json")
            real_unlink = install_module._unlink_state_path

            def replace_before_unlink(path: Path) -> None:
                receipt.rename(detached)
                receipt.write_text("foreign receipt\n", encoding="utf-8")
                real_unlink(path)

            with mock.patch.object(
                install_module, "_unlink_state_path", side_effect=replace_before_unlink
            ):
                with self.assertRaises(InstallError):
                    uninstall_opencode(repo, config, state)

            self.assertEqual(receipt.read_text(encoding="utf-8"), "foreign receipt\n")

    def test_uninstall_recovers_interrupted_live_to_backup_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "uninstall interrupted swap marker"
                ),
                encoding="utf-8",
            )
            def stop_after_live_backup(
                workspace: Path,
                workspace_identity: tuple[int, int],
                candidate: Path,
                candidate_identity: tuple[int, int],
                artifact: Path,
            ) -> None:
                raise SystemExit("simulated process stop")

            with mock.patch.object(
                install_module,
                "_publish_workspace_candidate",
                side_effect=stop_after_live_backup,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            uninstall_opencode(repo, config, state)

            state_directory = state / "expskill"
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state_directory / "opencode-artifact").exists())
            self.assertFalse(any(state_directory.glob(".opencode-artifact.next-*")))
            self.assertFalse(any(state_directory.glob(".opencode-artifact.old-*")))

    def test_validate_rejects_mutated_provenance_and_unexpected_artifact_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            real_build = validate_module.build_opencode_package

            def mutate_provenance(source: Path, artifact: Path) -> Path:
                result = real_build(source, artifact)
                (result / "provenance.json").write_text("{}\n", encoding="utf-8")
                return result

            with mock.patch.object(validate_module, "build_opencode_package", side_effect=mutate_provenance):
                errors = validate_module.validate_repository(
                    repo, include_main=False, include_opencode=True
                )
            self.assertTrue(any("provenance" in error.lower() for error in errors), errors)

            def inject_unexpected(source: Path, artifact: Path) -> Path:
                result = real_build(source, artifact)
                (result / "unexpected.txt").write_text("foreign\n", encoding="utf-8")
                return result

            with mock.patch.object(validate_module, "build_opencode_package", side_effect=inject_unexpected):
                errors = validate_module.validate_repository(
                    repo, include_main=False, include_opencode=True
                )
            self.assertTrue(any("unexpected" in error.lower() for error in errors), errors)

    def test_reinstall_repairs_deleted_modified_and_unexpected_artifact_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill" / "opencode-artifact"
            (artifact / "agents" / "expskill-review.md").unlink()
            (artifact / "plugins" / "unslop.js").write_text("tampered\n", encoding="utf-8")
            (artifact / "unexpected.txt").write_text("foreign\n", encoding="utf-8")
            install_opencode(repo, config, state)
            self.assertTrue((artifact / "agents" / "expskill-review.md").is_file())
            self.assertNotEqual((artifact / "plugins" / "unslop.js").read_text(), "tampered\n")
            self.assertFalse((artifact / "unexpected.txt").exists())

    def test_original_feature_legacy_receipt_installs_with_missing_old_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            links = original_feature_legacy_links(repo, config)
            self.assertTrue(
                all(not Path(entry["source"]).exists() for entry in links),
                "the migration proof requires the removed original source tree",
            )
            for entry in links:
                destination = Path(entry["destination"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(entry["source"])
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(json.dumps({
                "links": links,
                "marketplace_added": False,
                "plugin_installed": True,
                "repository_root": str(repo.resolve()),
            }), encoding="utf-8")
            install_opencode(repo, config, state)
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            self.assertIn("artifact_root", payload)
            self.assertTrue(all(Path(entry["source"]).is_relative_to(state / "expskill") for entry in payload["links"]))
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(profile_symlinks(config))

    def test_original_feature_legacy_receipt_uninstalls_with_missing_old_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            links = original_feature_legacy_links(repo, config)
            for entry in links:
                destination = Path(entry["destination"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(entry["source"])
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(
                json.dumps(
                    {
                        "links": links,
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(repo.resolve()),
                    }
                ),
                encoding="utf-8",
            )

            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(profile_symlinks(config))

    def test_legacy_receipt_rejects_forged_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(json.dumps({
                "links": [{"source": str(root / "outside"), "destination": str(config / "skills" / "plan")}],
                "marketplace_added": False,
                "plugin_installed": True,
                "repository_root": str(repo.resolve()),
            }), encoding="utf-8")
            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)
            self.assertTrue(receipt_path(state).exists())

    def test_legacy_receipt_rejects_forged_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            state = root / "state"
            forged_destination = root / "outside-config" / "plan"
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(json.dumps({
                "links": [{"source": str(repo / "plugins/expskill/skills/plan"), "destination": str(forged_destination)}],
                "marketplace_added": False,
                "plugin_installed": True,
                "repository_root": str(repo.resolve()),
            }), encoding="utf-8")
            with self.assertRaises(InstallError):
                uninstall_opencode(repo, root / "config", state)

    def test_builder_rejects_live_source_changes_during_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            real_render = build_module.render_all

            def mutate_then_render(snapshot: Path) -> dict[str, str]:
                skill.write_text(skill.read_text(encoding="utf-8").replace("Cut AI tells", "RACE"), encoding="utf-8")
                return real_render(snapshot)

            with mock.patch.object(build_module, "render_all", side_effect=mutate_then_render):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, root / "artifact")
            self.assertFalse((root / "artifact").exists())

    def test_backup_cleanup_fault_is_post_commit_success_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "backup cleanup retry marker"
                ),
                encoding="utf-8",
            )
            real_remove = install_module._remove_opencode_artifact_exact

            def fail_backup(path: Path, dev: int, ino: int) -> None:
                if ".old-" in path.name:
                    raise InstallError("injected backup cleanup failure")
                real_remove(path, dev, ino)

            with mock.patch.object(
                install_module, "_remove_opencode_artifact_exact", side_effect=fail_backup
            ):
                install_opencode(repo, config, state)
            self.assertTrue((state / "expskill/opencode-artifact").is_dir())
            self.assertTrue(receipt_path(state).is_file())
            self.assertTrue(any(path.name.startswith(".opencode-artifact.old-") for path in (state / "expskill").iterdir()))
            install_opencode(repo, config, state)
            self.assertFalse(any(path.name.startswith(".opencode-artifact.old-") for path in (state / "expskill").iterdir()))

    def test_builder_race_cannot_clobber_output_created_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            output = root / "artifact"
            real_publish = build_module._replace_output

            def inject_target(staging: Path, target: Path) -> None:
                target.mkdir(parents=True)
                (target / "foreign").write_text("must survive\n", encoding="utf-8")
                real_publish(staging, target)

            with mock.patch.object(build_module, "_replace_output", side_effect=inject_target):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, output)
            self.assertEqual((output / "foreign").read_text(encoding="utf-8"), "must survive\n")

    def test_dry_run_before_and_after_install_is_stable_and_read_only(self) -> None:
        from scripts.install import _print_opencode_dry_run
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            first = StringIO()
            with redirect_stdout(first):
                install_module._print_opencode_dry_run(repo, config, state)
            install_opencode(repo, config, state)
            before = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file())
            second = StringIO()
            with redirect_stdout(second):
                install_module._print_opencode_dry_run(repo, config, state)
            after = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file())
            self.assertEqual(before, after)
            self.assertNotIn("expskill-opencode-preflight-", second.getvalue())
            self.assertIn(str(state / "expskill/opencode-artifact"), second.getvalue())

    def test_invalid_policy_fails_before_creating_state_or_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            policy = repo / "plugins" / "expskill" / "assets" / "execution-policy.json"
            policy.write_text("{ invalid\n", encoding="utf-8")
            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)
            self.assertFalse(config.exists())
            self.assertFalse(state.exists())

    def test_platform_source_roster_rejects_generated_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            (repo / "plugins" / "expskill" / "opencode" / "commands").mkdir()
            with self.assertRaises(BuildError):
                build_opencode_package(repo, root / "artifact")

    def test_output_ancestor_symlink_and_script_ancestry_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            outside = root / "outside"
            outside.mkdir()
            linked_parent = root / "link"
            linked_parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(BuildError):
                build_opencode_package(repo, linked_parent / "output")
            script = repo / "scripts" / "render_opencode.py"
            saved = script.read_bytes()
            script.unlink()
            script.symlink_to(outside / "renderer.py")
            try:
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, root / "artifact")
            finally:
                script.unlink()
                script.write_bytes(saved)

    def test_dry_run_process_leaves_no_bytecode_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            state = root / "state"
            config = root / "config"
            env = dict(os.environ)
            env.update({"XDG_STATE_HOME": str(state), "OPENCODE_CONFIG_DIR": str(config), "PYTHONDONTWRITEBYTECODE": ""})
            result = subprocess.run([sys.executable, str(repo / "scripts" / "install.py"), "--target", "opencode", "--dry-run"], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state.exists())
            self.assertFalse(config.exists())
            self.assertFalse(any(path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts for path in repo.rglob("*")))

    def test_direct_build_and_validate_processes_leave_no_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            output = root / "artifact"
            for command in (
                [sys.executable, str(repo / "scripts/build_opencode_package.py"), "--output-dir", str(output)],
                [
                    sys.executable,
                    str(repo / "scripts/validate.py"),
                    "--no-include-main",
                    "--include-opencode",
                ],
            ):
                result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts for path in repo.rglob("*")))


if __name__ == "__main__":
    unittest.main()
