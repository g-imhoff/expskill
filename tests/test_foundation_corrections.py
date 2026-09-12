from __future__ import annotations

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
    shutil.copytree(ROOT / "packages", path / "packages", ignore=ignore)
    shutil.copytree(ROOT / "scripts", path / "scripts", ignore=ignore)
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def receipt_path(state: Path) -> Path:
    return state / "expskill" / "install-opencode.json"


class FoundationCorrectionTests(unittest.TestCase):
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
            original_rename = install_module._rename_noreplace

            def crash_after_live_backup(source: Path, target: Path) -> None:
                if source.name.startswith(".opencode-artifact.next-") and target.name == "opencode-artifact":
                    raise SystemExit("simulated process crash")
                original_rename(source, target)

            with mock.patch.object(
                install_module, "_rename_noreplace", side_effect=crash_after_live_backup
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
            skill = repo / "packages/expskill/skills/unslop/SKILL.md"
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
            first = repo / "packages/expskill/skills/brainstorm/SKILL.md"
            second = repo / "packages/expskill/skills/design/SKILL.md"
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
                    errors = validate_module.validate_repository(repo)
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
            marker = artifact / "foreign.txt"
            marker.write_text("must survive\n", encoding="utf-8")
            shutil.rmtree(config)

            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")

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

            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)

            self.assertEqual(marker.read_text(encoding="utf-8"), "must survive\n")

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
            real_same = install_module._same_recorded_link
            substituted = False

            def substitute_after_check(destination: Path, source: Path) -> bool:
                nonlocal substituted
                matches = real_same(destination, source)
                if matches and not substituted:
                    substituted = True
                    config.rename(moved)
                    config.symlink_to(outside, target_is_directory=True)
                    foreign = outside / destination.relative_to(config)
                    foreign.parent.mkdir(parents=True, exist_ok=True)
                    foreign.write_text("must survive\n", encoding="utf-8")
                return matches

            with mock.patch.object(
                install_module, "_same_recorded_link", side_effect=substitute_after_check
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
            original_rename = install_module._rename_noreplace

            def stop_after_live_backup(source: Path, target: Path) -> None:
                if source.name.startswith(".opencode-artifact.next-"):
                    raise SystemExit("simulated process stop")
                original_rename(source, target)

            with mock.patch.object(
                install_module, "_rename_noreplace", side_effect=stop_after_live_backup
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
                errors = validate_module.validate_repository(repo)
            self.assertTrue(any("provenance" in error.lower() for error in errors), errors)

            def inject_unexpected(source: Path, artifact: Path) -> Path:
                result = real_build(source, artifact)
                (result / "unexpected.txt").write_text("foreign\n", encoding="utf-8")
                return result

            with mock.patch.object(validate_module, "build_opencode_package", side_effect=inject_unexpected):
                errors = validate_module.validate_repository(repo)
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

    def test_legacy_receipt_migrates_and_uninstall_accepts_missing_old_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            legacy_root = repo / "packages" / "expskill"
            links = []
            for name in SKILLS:
                links.append({"source": str(legacy_root / "skills" / name), "destination": str(config / "skills" / name)})
            for name in SKILLS:
                links.append({"source": str(legacy_root / "opencode" / "commands" / f"{name}.md"), "destination": str(config / "commands" / f"{name}.md")})
            for name in AGENTS:
                links.append({"source": str(legacy_root / "opencode" / "agents" / f"{name}.md"), "destination": str(config / "agents" / f"{name}.md")})
            for name in PLUGINS:
                links.append({"source": str(legacy_root / "opencode" / "plugins" / name), "destination": str(config / "plugins" / name)})
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
            self.assertFalse(any(path.is_symlink() for path in config.rglob("*")))

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
                "links": [{"source": str(repo / "packages/expskill/skills/plan"), "destination": str(forged_destination)}],
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
            skill = repo / "packages/expskill/skills/unslop/SKILL.md"
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
            policy = repo / "packages" / "expskill" / "assets" / "execution-policy.json"
            policy.write_text("{ invalid\n", encoding="utf-8")
            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)
            self.assertFalse(config.exists())
            self.assertFalse(state.exists())

    def test_platform_source_roster_rejects_generated_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            (repo / "packages" / "expskill" / "opencode" / "commands").mkdir()
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
                [sys.executable, str(repo / "scripts/validate.py")],
            ):
                result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts for path in repo.rglob("*")))


if __name__ == "__main__":
    unittest.main()
