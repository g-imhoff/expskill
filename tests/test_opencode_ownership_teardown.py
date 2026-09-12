from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.install as install_module
from scripts.install import install_opencode, uninstall_opencode


ROOT = Path(__file__).resolve().parents[1]


def seed_repository(path: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(ROOT / ".agents", path / ".agents", ignore=ignore)
    shutil.copytree(ROOT / "packages", path / "packages", ignore=ignore)
    shutil.copytree(ROOT / "scripts", path / "scripts", ignore=ignore)
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def receipt_path(state: Path) -> Path:
    return state / "expskill" / "install-opencode.json"


def receipt(state: Path) -> dict[str, object]:
    return json.loads(receipt_path(state).read_text(encoding="utf-8"))


class OpenCodeOwnershipTeardownTests(unittest.TestCase):
    def test_source_changing_upgrade_waits_for_prior_retirement(self) -> None:
        for blocked_entrypoint in ("install", "uninstall"):
            with (
                self.subTest(blocked_entrypoint=blocked_entrypoint),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                original_text = skill.read_text(encoding="utf-8")
                skill.write_text(
                    original_text.replace(
                        "Cut AI tells", "first retirement generation"
                    ),
                    encoding="utf-8",
                )
                real_remove = install_module._remove_opencode_artifact_exact

                def fail_old_backup(path: Path, dev: int, ino: int) -> None:
                    if ".old-" in path.name:
                        raise install_module.InstallError("old backup remains busy")
                    real_remove(path, dev, ino)

                with mock.patch.object(
                    install_module,
                    "_remove_opencode_artifact_exact",
                    side_effect=fail_old_backup,
                ):
                    install_opencode(repo, config, state)

                first_receipt = receipt(state)
                first_pending = first_receipt["pending_swap"]
                first_receipt_bytes = receipt_path(state).read_bytes()
                artifact = Path(first_pending["artifact"])
                backup = Path(first_pending["backup"])
                current_anchor = Path(first_pending["candidate_anchor"])
                old_anchor = Path(first_pending["old_anchor"])
                frozen_identities = {
                    path: (path.lstat().st_dev, path.lstat().st_ino)
                    for path in (artifact, backup, current_anchor, old_anchor)
                }
                candidates_before = tuple(
                    artifact.parent.glob(f".{artifact.name}.next-*")
                )
                skill.write_text(
                    original_text.replace(
                        "Cut AI tells", "second retirement generation"
                    ),
                    encoding="utf-8",
                )

                with mock.patch.object(
                    install_module,
                    "_remove_opencode_artifact_exact",
                    side_effect=fail_old_backup,
                ):
                    with self.assertRaisesRegex(
                        install_module.InstallError,
                        "retirement|backup|publication",
                    ):
                        if blocked_entrypoint == "install":
                            install_opencode(repo, config, state)
                        else:
                            uninstall_opencode(repo, config, state)

                self.assertEqual(receipt_path(state).read_bytes(), first_receipt_bytes)
                self.assertEqual(receipt(state)["pending_swap"], first_pending)
                self.assertEqual(
                    tuple(artifact.parent.glob(f".{artifact.name}.next-*")),
                    candidates_before,
                )
                for path, identity in frozen_identities.items():
                    self.assertTrue(path.exists())
                    self.assertEqual((path.lstat().st_dev, path.lstat().st_ino), identity)

                install_opencode(repo, config, state)
                converged = receipt(state)
                self.assertNotIn("pending_swap", converged)
                self.assertTrue(
                    install_module._artifact_matches_sources(repo, artifact)
                )
                self.assertEqual(
                    tuple(artifact.parent.glob(f".{artifact.name}.old-*")), ()
                )
                self.assertEqual(
                    tuple(artifact.parent.glob(f".{artifact.name}.next-*")), ()
                )
                anchors = tuple(
                    artifact.parent.glob(
                        f"{install_module.OPENCODE_ARTIFACT_ANCHOR_PREFIX}*"
                    )
                )
                self.assertEqual(anchors, (Path(converged["artifact_anchor"]),))

    def test_foreign_receipt_quarantine_collision_blocks_terminal_deletion(
        self,
    ) -> None:
        for retry_entrypoint in ("install", "uninstall"):
            with (
                self.subTest(retry_entrypoint=retry_entrypoint),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                canonical = receipt_path(state)
                real_unlink = install_module._unlink_state_path
                collision: Path | None = None
                canonical_identity: tuple[int, int] | None = None
                canonical_bytes: bytes | None = None

                def collide(path: Path) -> None:
                    nonlocal collision, canonical_identity, canonical_bytes
                    metadata = path.lstat()
                    canonical_identity = (metadata.st_dev, metadata.st_ino)
                    canonical_bytes = path.read_bytes()
                    lineage, current_phase, pending_phase = (
                        install_module._receipt_deletion_descriptor(path)
                    )
                    collision = install_module._receipt_deletion_quarantine_path(
                        path,
                        *canonical_identity,
                        lineage,
                        current_phase,
                        pending_phase,
                    )
                    collision.write_text("foreign collision\n", encoding="utf-8")
                    real_unlink(path)

                with mock.patch.object(
                    install_module, "_unlink_state_path", side_effect=collide
                ):
                    with self.assertRaisesRegex(
                        install_module.InstallError, "quarantine|conditionally remove"
                    ):
                        uninstall_opencode(repo, config, state)

                assert collision is not None
                assert canonical_identity is not None
                assert canonical_bytes is not None
                self.assertEqual(canonical.read_bytes(), canonical_bytes)
                self.assertEqual(
                    (canonical.lstat().st_dev, canonical.lstat().st_ino),
                    canonical_identity,
                )
                self.assertEqual(collision.read_text(encoding="utf-8"), "foreign collision\n")
                collision.unlink()
                real_rename = install_module._renameat_noreplace
                rename_calls = 0
                real_fsync = os.fsync
                fsync_calls = 0

                def observe_rename(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal rename_calls
                    if source_name == canonical.name and target_name.endswith(".delete"):
                        rename_calls += 1
                    real_rename(source_fd, source_name, target_fd, target_name)

                def observe_fsync(descriptor: int) -> None:
                    nonlocal fsync_calls
                    fsync_calls += 1
                    real_fsync(descriptor)

                with (
                    mock.patch.object(
                        install_module,
                        "_renameat_noreplace",
                        side_effect=observe_rename,
                    ),
                    mock.patch.object(
                        install_module.os, "fsync", side_effect=observe_fsync
                    ),
                ):
                    if retry_entrypoint == "install":
                        install_opencode(repo, config, state)
                        self.assertTrue(canonical.is_file())
                    else:
                        uninstall_opencode(repo, config, state)
                        self.assertFalse(canonical.exists())
                self.assertEqual(rename_calls, 1)
                self.assertGreater(fsync_calls, 0)
                self.assertFalse(collision.exists())

    def test_foreign_receipt_quarantine_collision_blocks_nonterminal_deletion(
        self,
    ) -> None:
        for failure_type in (install_module.InstallError, SystemExit):
            for retry_entrypoint in ("install", "uninstall"):
                with (
                    self.subTest(
                        failure=failure_type.__name__,
                        retry_entrypoint=retry_entrypoint,
                    ),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    canonical = receipt_path(state)
                    real_unlink = install_module._unlink_state_path
                    collision: Path | None = None
                    canonical_identity: tuple[int, int] | None = None
                    canonical_bytes: bytes | None = None

                    def collide_and_interrupt(path: Path) -> None:
                        nonlocal collision, canonical_identity, canonical_bytes
                        metadata = path.lstat()
                        canonical_identity = (metadata.st_dev, metadata.st_ino)
                        canonical_bytes = path.read_bytes()
                        lineage, current_phase, pending_phase = (
                            install_module._receipt_deletion_descriptor(path)
                        )
                        collision = install_module._receipt_deletion_quarantine_path(
                            path,
                            *canonical_identity,
                            lineage,
                            current_phase,
                            pending_phase,
                        )
                        collision.write_text(
                            "foreign nonterminal collision\n", encoding="utf-8"
                        )
                        try:
                            real_unlink(path)
                        except install_module.InstallError as error:
                            raise failure_type("interrupted nonterminal deletion") from error
                        raise failure_type("interrupted nonterminal deletion")

                    with (
                        mock.patch.object(
                            install_module,
                            "_unlink_state_path",
                            side_effect=collide_and_interrupt,
                        ),
                        mock.patch.object(
                            install_module,
                            "preflight_opencode_links",
                            side_effect=install_module.InstallError(
                                "preflight failed"
                            ),
                        ),
                    ):
                        with self.assertRaises(failure_type):
                            install_opencode(repo, config, state)

                    assert collision is not None
                    assert canonical_identity is not None
                    assert canonical_bytes is not None
                    self.assertEqual(canonical.read_bytes(), canonical_bytes)
                    self.assertEqual(
                        (canonical.lstat().st_dev, canonical.lstat().st_ino),
                        canonical_identity,
                    )
                    self.assertIn("pending_publish", receipt(state))
                    self.assertEqual(
                        collision.read_text(encoding="utf-8"),
                        "foreign nonterminal collision\n",
                    )
                    collision.unlink()
                    real_rename = install_module._renameat_noreplace
                    receipt_renames = 0
                    real_fsync = os.fsync
                    fsync_calls = 0

                    def observe_rename(
                        source_fd: int,
                        source_name: str,
                        target_fd: int,
                        target_name: str,
                    ) -> None:
                        nonlocal receipt_renames
                        if source_name == canonical.name and target_name.endswith(
                            ".delete"
                        ):
                            receipt_renames += 1
                        real_rename(source_fd, source_name, target_fd, target_name)

                    def observe_fsync(descriptor: int) -> None:
                        nonlocal fsync_calls
                        fsync_calls += 1
                        real_fsync(descriptor)

                    with (
                        mock.patch.object(
                            install_module,
                            "_renameat_noreplace",
                            side_effect=observe_rename,
                        ),
                        mock.patch.object(
                            install_module.os, "fsync", side_effect=observe_fsync
                        ),
                    ):
                        if retry_entrypoint == "install":
                            install_opencode(repo, config, state)
                            self.assertTrue(canonical.is_file())
                            self.assertNotIn("pending_publish", receipt(state))
                        else:
                            uninstall_opencode(repo, config, state)
                            self.assertFalse(canonical.exists())
                    self.assertEqual(receipt_renames, 1)
                    self.assertGreater(fsync_calls, 0)
                    self.assertFalse(collision.exists())

    def test_partial_artifact_package_deletion_retries_through_both_entrypoints(
        self,
    ) -> None:
        for failure_type in (install_module.InstallError, SystemExit):
            for retry in ("install", "uninstall"):
                with (
                    self.subTest(failure=failure_type.__name__, retry=retry),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    payload = receipt(state)
                    artifact = Path(payload["artifact_root"])
                    anchor = Path(payload["artifact_anchor"])
                    real_remove = install_module._remove_opencode_artifact_exact
                    injected = False

                    def stop_after_package_delete(path: Path, dev: int, ino: int) -> None:
                        nonlocal injected
                        if path == artifact and not injected:
                            injected = True
                            (path / "package.json").unlink()
                            raise failure_type("after package deletion")
                        real_remove(path, dev, ino)

                    with mock.patch.object(
                        install_module,
                        "_remove_opencode_artifact_exact",
                        side_effect=stop_after_package_delete,
                    ):
                        with self.assertRaises(failure_type):
                            uninstall_opencode(repo, config, state)

                    self.assertTrue(injected)
                    if retry == "install":
                        install_opencode(repo, config, state)
                        self.assertEqual(receipt(state)["teardown_phase"], "committed")
                        uninstall_opencode(repo, config, state)
                    else:
                        uninstall_opencode(repo, config, state)
                    self.assertFalse(artifact.exists())
                    self.assertFalse(anchor.exists())
                    self.assertFalse(receipt_path(state).exists())

    def test_terminal_receipt_delete_quarantine_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            final_receipt = receipt_path(state)
            real_rename = install_module._renameat_noreplace
            injected = False

            def stop_after_receipt_rename(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                real_rename(source_fd, source_name, target_fd, target_name)
                if (
                    not injected
                    and source_name == final_receipt.name
                    and target_name.endswith(".delete")
                ):
                    injected = True
                    raise SystemExit("after terminal receipt rename")

            with mock.patch.object(
                install_module,
                "_renameat_noreplace",
                side_effect=stop_after_receipt_rename,
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertFalse(final_receipt.exists())
            self.assertEqual(len(tuple(final_receipt.parent.glob("*.delete"))), 1)
            uninstall_opencode(repo, config, state)
            self.assertFalse(final_receipt.exists())
            self.assertEqual(tuple(final_receipt.parent.glob("*.delete")), ())

    def test_legacy_migration_freezes_links_before_anchor_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            Path(payload["artifact_anchor"]).unlink()
            for key in (
                "artifact_anchor",
                "artifact_anchor_dev",
                "artifact_anchor_ino",
                "teardown_phase",
            ):
                payload.pop(key, None)
            for link in payload["links"]:
                link.pop("destination_dev", None)
                link.pop("destination_ino", None)
            receipt_path(state).write_text(json.dumps(payload), encoding="utf-8")
            victim = payload["links"][0]
            destination = Path(victim["destination"])
            source = Path(victim["source"])
            real_write = install_module._write_receipt
            injected = False
            replacement_identity: tuple[int, int] | None = None

            def replace_after_migration_prepare(path: Path, value: object) -> None:
                nonlocal injected, replacement_identity
                real_write(path, value)
                if getattr(value, "pending_migration", None) is None or injected:
                    return
                injected = True
                destination.rename(destination.with_name(f"{destination.name}.frozen"))
                destination.symlink_to(source)
                replacement_identity = (
                    destination.lstat().st_dev,
                    destination.lstat().st_ino,
                )

            with mock.patch.object(
                install_module,
                "_write_receipt",
                side_effect=replace_after_migration_prepare,
            ):
                with self.assertRaises(install_module.InstallError):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )

    def test_repaired_link_receipt_rewrite_preserves_pending_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            before = receipt(state)
            missing = Path(before["links"][0]["destination"])
            missing.unlink()
            skill = repo / "packages/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "pending swap repair marker"
                ),
                encoding="utf-8",
            )
            real_rename = install_module._renameat_noreplace
            injected = False

            def stop_after_repaired_link_publication(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                real_rename(source_fd, source_name, target_fd, target_name)
                if source_name.endswith(".link") and not injected:
                    injected = True
                    raise SystemExit("after repaired link publication")

            with mock.patch.object(
                install_module,
                "_renameat_noreplace",
                side_effect=stop_after_repaired_link_publication,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            interrupted = receipt(state)
            self.assertIn("pending_swap", interrupted)
            pending = interrupted["pending_swap"]
            self.assertTrue(Path(pending["artifact"]).is_dir())
            self.assertTrue(Path(pending["backup"]).is_dir())
            self.assertTrue(Path(pending["candidate_anchor"]).is_file())
            self.assertTrue(Path(pending["old_anchor"]).is_file())

            install_opencode(repo, config, state)
            converged = receipt(state)
            self.assertNotIn("pending_swap", converged)
            self.assertTrue(Path(converged["artifact_root"]).is_dir())
            self.assertTrue(Path(converged["artifact_anchor"]).is_file())

    def test_partial_backup_package_deletion_retries_through_both_entrypoints(
        self,
    ) -> None:
        for failure_type in (install_module.InstallError, SystemExit):
            for retry in ("install", "uninstall"):
                with (
                    self.subTest(failure=failure_type.__name__, retry=retry),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                    skill.write_text(
                        skill.read_text(encoding="utf-8").replace(
                            "Cut AI tells", "partial backup retirement marker"
                        ),
                        encoding="utf-8",
                    )
                    real_remove = install_module._remove_opencode_artifact_exact
                    injected = False

                    def stop_after_backup_package_delete(
                        path: Path, dev: int, ino: int
                    ) -> None:
                        nonlocal injected
                        if ".old-" in path.name and not injected:
                            injected = True
                            (path / "package.json").unlink()
                            raise failure_type("after backup package deletion")
                        real_remove(path, dev, ino)

                    with mock.patch.object(
                        install_module,
                        "_remove_opencode_artifact_exact",
                        side_effect=stop_after_backup_package_delete,
                    ):
                        if failure_type is SystemExit:
                            with self.assertRaises(SystemExit):
                                install_opencode(repo, config, state)
                        else:
                            install_opencode(repo, config, state)

                    self.assertTrue(injected)
                    interrupted = receipt(state)
                    backup = Path(interrupted["pending_swap"]["backup"])
                    old_anchor = Path(interrupted["pending_swap"]["old_anchor"])
                    self.assertTrue(backup.is_dir())
                    self.assertFalse((backup / "package.json").exists())
                    if retry == "install":
                        install_opencode(repo, config, state)
                        self.assertNotIn("pending_swap", receipt(state))
                        uninstall_opencode(repo, config, state)
                    else:
                        uninstall_opencode(repo, config, state)
                    self.assertFalse(backup.exists())
                    self.assertFalse(old_anchor.exists())
                    self.assertFalse(receipt_path(state).exists())

    def test_exact_original_and_quarantine_aliases_are_both_removed(self) -> None:
        for operation in ("link", "anchor"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                payload = receipt(state)
                if operation == "link":
                    recorded = payload["links"][0]
                    original = Path(recorded["destination"])
                    identity = (
                        recorded["destination_dev"],
                        recorded["destination_ino"],
                    )
                else:
                    original = Path(payload["artifact_anchor"])
                    identity = (
                        payload["artifact_anchor_dev"],
                        payload["artifact_anchor_ino"],
                    )
                quarantine = install_module._deletion_quarantine_path(
                    original, *identity
                )
                os.link(original, quarantine, follow_symlinks=False)
                self.assertEqual(
                    (quarantine.lstat().st_dev, quarantine.lstat().st_ino),
                    identity,
                )

                uninstall_opencode(repo, config, state)

                self.assertFalse(original.exists())
                self.assertFalse(original.is_symlink())
                self.assertFalse(quarantine.exists())
                self.assertFalse(quarantine.is_symlink())
                self.assertFalse(receipt_path(state).exists())

    def test_terminal_receipt_recovery_preserves_foreign_paths_and_syncs_absence(
        self,
    ) -> None:
        for replacement in ("canonical", "quarantine"):
            with (
                self.subTest(replacement=replacement),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                final_receipt = receipt_path(state)
                real_rename = install_module._renameat_noreplace
                injected = False

                def stop_after_receipt_rename(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal injected
                    real_rename(source_fd, source_name, target_fd, target_name)
                    if source_name == final_receipt.name and not injected:
                        injected = True
                        raise SystemExit("after receipt quarantine rename")

                with mock.patch.object(
                    install_module,
                    "_renameat_noreplace",
                    side_effect=stop_after_receipt_rename,
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)

                quarantine = next(final_receipt.parent.glob("*.anchor-removed.delete"))
                if replacement == "canonical":
                    foreign = final_receipt
                else:
                    quarantine.unlink()
                    foreign = quarantine
                foreign.write_text("foreign receipt path\n", encoding="utf-8")
                foreign_identity = (foreign.stat().st_dev, foreign.stat().st_ino)
                real_fsync = os.fsync
                fsync_calls = 0

                def count_fsync(descriptor: int) -> None:
                    nonlocal fsync_calls
                    fsync_calls += 1
                    real_fsync(descriptor)

                with mock.patch.object(
                    install_module.os, "fsync", side_effect=count_fsync
                ):
                    if replacement == "canonical":
                        with self.assertRaises(install_module.InstallError):
                            uninstall_opencode(repo, config, state)
                    else:
                        uninstall_opencode(repo, config, state)

                self.assertGreater(fsync_calls, 0)
                self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign receipt path\n")
                self.assertEqual((foreign.stat().st_dev, foreign.stat().st_ino), foreign_identity)

    def test_ordinary_legacy_unlink_failure_keeps_prepared_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            legacy_links = install_module._legacy_opencode_expected_links(repo, config)
            for link in legacy_links:
                link.destination.parent.mkdir(parents=True, exist_ok=True)
                link.destination.symlink_to(link.source)
            path = receipt_path(state)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "links": [
                            {
                                "source": str(link.source),
                                "destination": str(link.destination),
                            }
                            for link in legacy_links
                        ],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(repo),
                    }
                ),
                encoding="utf-8",
            )
            victim = legacy_links[0]
            real_unlink = install_module._unlink_destination
            injected = False

            def fail_after_migrated_unlink(destination: Path) -> bool:
                nonlocal injected
                removed = real_unlink(destination)
                if destination == victim.destination and not injected:
                    injected = True
                    raise install_module.InstallError("after migrated unlink durability")
                return removed

            with mock.patch.object(
                install_module,
                "_unlink_destination",
                side_effect=fail_after_migrated_unlink,
            ):
                with self.assertRaisesRegex(
                    install_module.InstallError, "after migrated unlink durability"
                ):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            interrupted = receipt(state)
            self.assertIn("pending_publish", interrupted)
            self.assertTrue(Path(interrupted["artifact_root"]).is_dir())
            self.assertFalse(victim.destination.is_symlink())

            install_opencode(repo, config, state)
            uninstall_opencode(repo, config, state)
            self.assertFalse(victim.destination.is_symlink())

    def test_link_delete_quarantine_is_recovered_through_both_entrypoints(self) -> None:
        for failure_type in (install_module.InstallError, SystemExit):
            for retry in ("install", "uninstall"):
                with (
                    self.subTest(failure=failure_type.__name__, retry=retry),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    payload = receipt(state)
                    recorded = payload["links"][0]
                    destination = Path(recorded["destination"])
                    expected = (
                        recorded["destination_dev"],
                        recorded["destination_ino"],
                    )
                    real_rename = install_module._renameat_noreplace
                    stopped = False

                    def stop_after_delete_rename(
                        source_fd: int,
                        source_name: str,
                        target_fd: int,
                        target_name: str,
                    ) -> None:
                        nonlocal stopped
                        real_rename(source_fd, source_name, target_fd, target_name)
                        if (
                            not stopped
                            and source_name == destination.name
                            and target_name.endswith(".delete")
                        ):
                            stopped = True
                            raise failure_type("after exact link delete rename")

                    with mock.patch.object(
                        install_module,
                        "_renameat_noreplace",
                        side_effect=stop_after_delete_rename,
                    ):
                        with self.assertRaises(failure_type):
                            uninstall_opencode(repo, config, state)

                    quarantines = tuple(destination.parent.glob(".*.delete"))
                    self.assertEqual(len(quarantines), 1)
                    quarantine = quarantines[0]
                    self.assertEqual(
                        (quarantine.lstat().st_dev, quarantine.lstat().st_ino),
                        expected,
                    )
                    foreign = root / "foreign"
                    foreign.write_text("foreign\n", encoding="utf-8")
                    destination.symlink_to(foreign)
                    foreign_identity = (
                        destination.lstat().st_dev,
                        destination.lstat().st_ino,
                    )

                    if retry == "install":
                        with self.assertRaisesRegex(
                            install_module.InstallError, "conflicting opencode destination"
                        ):
                            install_opencode(repo, config, state)
                        uninstall_opencode(repo, config, state)
                    else:
                        uninstall_opencode(repo, config, state)

                    self.assertFalse(quarantine.exists())
                    self.assertFalse(quarantine.is_symlink())
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(os.readlink(destination), str(foreign))
                    self.assertEqual(
                        (destination.lstat().st_dev, destination.lstat().st_ino),
                        foreign_identity,
                    )
                    self.assertFalse(receipt_path(state).exists())

    def test_staged_link_delete_quarantine_is_recovered(self) -> None:
        for failure_type in (install_module.InstallError, SystemExit):
            for retry in ("install", "uninstall"):
                with (
                    self.subTest(failure=failure_type.__name__, retry=retry),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    payload = receipt(state)
                    recorded = payload["links"][0]
                    destination = Path(recorded["destination"])
                    source = Path(recorded["source"])
                    staging = install_module._opencode_link_staging_path(
                        source, destination
                    )
                    destination.rename(staging)
                    recorded["staged_destination"] = str(staging)
                    receipt_path(state).write_text(
                        json.dumps(payload), encoding="utf-8"
                    )
                    expected = (
                        recorded["destination_dev"],
                        recorded["destination_ino"],
                    )
                    quarantine = install_module._deletion_quarantine_path(
                        staging, *expected
                    )
                    real_rename = install_module._renameat_noreplace
                    stopped = False

                    def stop_after_staged_delete_rename(
                        source_fd: int,
                        source_name: str,
                        target_fd: int,
                        target_name: str,
                    ) -> None:
                        nonlocal stopped
                        real_rename(source_fd, source_name, target_fd, target_name)
                        if (
                            not stopped
                            and source_name == staging.name
                            and target_name == quarantine.name
                        ):
                            stopped = True
                            raise failure_type("after staged link delete rename")

                    with mock.patch.object(
                        install_module,
                        "_renameat_noreplace",
                        side_effect=stop_after_staged_delete_rename,
                    ):
                        with self.assertRaises(failure_type):
                            uninstall_opencode(repo, config, state)

                    self.assertTrue(stopped)
                    self.assertTrue(quarantine.is_symlink())
                    if retry == "install":
                        install_opencode(repo, config, state)
                        uninstall_opencode(repo, config, state)
                    else:
                        uninstall_opencode(repo, config, state)
                    self.assertFalse(quarantine.exists())
                    self.assertFalse(quarantine.is_symlink())
                    self.assertFalse(receipt_path(state).exists())

    def test_anchor_delete_quarantine_is_recovered_through_both_entrypoints(self) -> None:
        for failure_type in (install_module.InstallError, SystemExit):
            for retry in ("install", "uninstall"):
                with (
                    self.subTest(failure=failure_type.__name__, retry=retry),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    payload = receipt(state)
                    anchor = Path(payload["artifact_anchor"])
                    expected = (
                        payload["artifact_anchor_dev"],
                        payload["artifact_anchor_ino"],
                    )
                    real_rename = install_module._renameat_noreplace
                    stopped = False

                    def stop_after_anchor_delete_rename(
                        source_fd: int,
                        source_name: str,
                        target_fd: int,
                        target_name: str,
                    ) -> None:
                        nonlocal stopped
                        real_rename(source_fd, source_name, target_fd, target_name)
                        if (
                            not stopped
                            and source_name == anchor.name
                            and target_name.endswith(".delete")
                        ):
                            stopped = True
                            raise failure_type("after exact anchor delete rename")

                    with mock.patch.object(
                        install_module,
                        "_renameat_noreplace",
                        side_effect=stop_after_anchor_delete_rename,
                    ):
                        with self.assertRaises(failure_type):
                            uninstall_opencode(repo, config, state)

                    quarantines = tuple(anchor.parent.glob(f".{anchor.name}.*.delete"))
                    self.assertEqual(len(quarantines), 1)
                    quarantine = quarantines[0]
                    self.assertEqual(
                        (quarantine.stat().st_dev, quarantine.stat().st_ino), expected
                    )
                    anchor.write_text("foreign anchor\n", encoding="utf-8")
                    foreign_identity = (anchor.stat().st_dev, anchor.stat().st_ino)

                    if retry == "install":
                        install_opencode(repo, config, state)
                        uninstall_opencode(repo, config, state)
                    else:
                        uninstall_opencode(repo, config, state)

                    self.assertFalse(quarantine.exists())
                    self.assertEqual(anchor.read_text(encoding="utf-8"), "foreign anchor\n")
                    self.assertEqual((anchor.stat().st_dev, anchor.stat().st_ino), foreign_identity)
                    self.assertFalse(receipt_path(state).exists())

    def test_link_and_anchor_unlink_fsync_failures_retain_receipt_authority(self) -> None:
        for operation in ("link", "anchor"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                payload = receipt(state)
                if operation == "link":
                    recorded = payload["links"][0]
                    owned_path = Path(recorded["destination"])
                    quarantine = install_module._deletion_quarantine_path(
                        owned_path,
                        recorded["destination_dev"],
                        recorded["destination_ino"],
                    )
                    retained_phase = "removing-links"
                else:
                    owned_path = Path(payload["artifact_anchor"])
                    quarantine = install_module._deletion_quarantine_path(
                        owned_path,
                        payload["artifact_anchor_dev"],
                        payload["artifact_anchor_ino"],
                    )
                    retained_phase = "artifact-removed"
                real_fsync = os.fsync
                failed = False

                def fail_post_unlink_fsync(descriptor: int) -> None:
                    nonlocal failed
                    current = receipt(state) if receipt_path(state).exists() else {}
                    if (
                        not failed
                        and current.get("teardown_phase") == retained_phase
                        and not owned_path.exists()
                        and not owned_path.is_symlink()
                        and not quarantine.exists()
                        and not quarantine.is_symlink()
                    ):
                        failed = True
                        raise OSError("injected parent fsync failure")
                    real_fsync(descriptor)

                with mock.patch.object(
                    install_module.os, "fsync", side_effect=fail_post_unlink_fsync
                ):
                    with self.assertRaisesRegex(
                        install_module.InstallError, "fsync failure"
                    ):
                        uninstall_opencode(repo, config, state)

                self.assertTrue(failed)
                self.assertEqual(receipt(state)["teardown_phase"], retained_phase)
                self.assertFalse(owned_path.exists())
                self.assertFalse(owned_path.is_symlink())
                self.assertFalse(quarantine.exists())
                self.assertFalse(quarantine.is_symlink())

                uninstall_opencode(repo, config, state)
                self.assertFalse(receipt_path(state).exists())

    def test_foreign_deletion_quarantine_replacements_survive(self) -> None:
        for operation in ("link", "anchor"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                payload = receipt(state)
                if operation == "link":
                    recorded = payload["links"][0]
                    owned_path = Path(recorded["destination"])
                    expected = (
                        recorded["destination_dev"],
                        recorded["destination_ino"],
                    )
                else:
                    owned_path = Path(payload["artifact_anchor"])
                    expected = (
                        payload["artifact_anchor_dev"],
                        payload["artifact_anchor_ino"],
                    )
                quarantine = install_module._deletion_quarantine_path(
                    owned_path, *expected
                )
                real_rename = install_module._renameat_noreplace
                stopped = False

                def stop_after_delete_rename(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal stopped
                    real_rename(source_fd, source_name, target_fd, target_name)
                    if (
                        not stopped
                        and source_name == owned_path.name
                        and target_name == quarantine.name
                    ):
                        stopped = True
                        raise SystemExit("after exact delete rename")

                with mock.patch.object(
                    install_module,
                    "_renameat_noreplace",
                    side_effect=stop_after_delete_rename,
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)

                self.assertTrue(stopped)
                if quarantine.is_dir() and not quarantine.is_symlink():
                    shutil.rmtree(quarantine)
                else:
                    quarantine.unlink()
                foreign = root / f"foreign-{operation}"
                foreign.write_text("foreign\n", encoding="utf-8")
                quarantine.symlink_to(foreign)
                foreign_identity = (
                    quarantine.lstat().st_dev,
                    quarantine.lstat().st_ino,
                )

                uninstall_opencode(repo, config, state)

                self.assertTrue(quarantine.is_symlink())
                self.assertEqual(os.readlink(quarantine), str(foreign))
                self.assertEqual(
                    (quarantine.lstat().st_dev, quarantine.lstat().st_ino),
                    foreign_identity,
                )
                self.assertFalse(receipt_path(state).exists())

    def test_ordinary_failure_after_retired_pruning_does_not_recreate_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            retired_skill = config / "skills/unslop"
            retired_command = config / "commands/unslop.md"
            shutil.rmtree(repo / "packages/expskill/skills/unslop")
            real_evidence = install_module._artifact_evidence
            stopped = False

            def fail_after_pruning(path: Path) -> str | None:
                nonlocal stopped
                evidence = real_evidence(path)
                if (
                    not stopped
                    and not retired_skill.is_symlink()
                    and not retired_command.is_symlink()
                ):
                    stopped = True
                    raise install_module.InstallError("after retired pruning")
                return evidence

            with (
                mock.patch.object(install_module, "_validate_repository"),
                mock.patch.object(
                    install_module, "_artifact_evidence", side_effect=fail_after_pruning
                ),
            ):
                with self.assertRaisesRegex(
                    install_module.InstallError, "after retired pruning"
                ):
                    install_opencode(repo, config, state)

            self.assertTrue(stopped)
            self.assertFalse(retired_skill.is_symlink())
            self.assertFalse(retired_command.is_symlink())
            with mock.patch.object(install_module, "_validate_repository"):
                install_opencode(repo, config, state)
                uninstall_opencode(repo, config, state)
            self.assertFalse(retired_skill.is_symlink())
            self.assertFalse(retired_command.is_symlink())

    def test_retired_pruning_propagates_install_error_without_losing_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            retired_skill = config / "skills/unslop"
            retired_command = config / "commands/unslop.md"
            shutil.rmtree(repo / "packages/expskill/skills/unslop")
            real_unlink = install_module._unlink_destination
            failed = False

            def fail_after_exact_unlink(path: Path) -> bool:
                nonlocal failed
                removed = real_unlink(path)
                if path == retired_skill and not failed:
                    failed = True
                    raise install_module.InstallError("after retired exact unlink")
                return removed

            with (
                mock.patch.object(install_module, "_validate_repository"),
                mock.patch.object(
                    install_module,
                    "_unlink_destination",
                    side_effect=fail_after_exact_unlink,
                ),
            ):
                with self.assertRaisesRegex(
                    install_module.InstallError, "after retired exact unlink"
                ):
                    install_opencode(repo, config, state)

            self.assertTrue(failed)
            self.assertFalse(retired_skill.is_symlink())
            with mock.patch.object(install_module, "_validate_repository"):
                install_opencode(repo, config, state)
                self.assertFalse(retired_skill.is_symlink())
                self.assertFalse(retired_command.is_symlink())
                uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())

    def test_legacy_migration_delete_quarantine_is_durably_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            legacy_links = install_module._legacy_opencode_expected_links(repo, config)
            for link in legacy_links:
                link.destination.parent.mkdir(parents=True, exist_ok=True)
                link.destination.symlink_to(link.source)
            path = receipt_path(state)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "links": [
                            {
                                "source": str(link.source),
                                "destination": str(link.destination),
                            }
                            for link in legacy_links
                        ],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(repo),
                    }
                ),
                encoding="utf-8",
            )
            victim = legacy_links[0]
            real_rename = install_module._renameat_noreplace
            stopped = False

            def stop_after_migration_delete_rename(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal stopped
                real_rename(source_fd, source_name, target_fd, target_name)
                if (
                    not stopped
                    and source_name == victim.destination.name
                    and target_name.endswith(".delete")
                ):
                    stopped = True
                    raise SystemExit("after migrated link delete rename")

            with mock.patch.object(
                install_module,
                "_renameat_noreplace",
                side_effect=stop_after_migration_delete_rename,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            interrupted = receipt(state)
            recorded = next(
                entry
                for entry in interrupted["links"]
                if entry["destination"] == str(victim.destination)
            )
            quarantine = install_module._deletion_quarantine_path(
                victim.destination,
                recorded["destination_dev"],
                recorded["destination_ino"],
            )
            self.assertTrue(quarantine.is_symlink())

            install_opencode(repo, config, state)
            self.assertFalse(quarantine.exists())
            self.assertFalse(quarantine.is_symlink())
            self.assertNotEqual(
                os.readlink(victim.destination), str(victim.source)
            )
            uninstall_opencode(repo, config, state)
            self.assertFalse(victim.destination.is_symlink())

    def test_link_identity_is_durable_before_final_publication_rename(self) -> None:
        for failure in (
            install_module.InstallError("after final link rename"),
            SystemExit("after final link rename"),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                real_rename = install_module._renameat_noreplace
                injected = False

                def fail_after_final_link_rename(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal injected
                    real_rename(source_fd, source_name, target_fd, target_name)
                    if source_name.endswith(".link") and not injected:
                        injected = True
                        raise failure

                with mock.patch.object(
                    install_module,
                    "_renameat_noreplace",
                    side_effect=fail_after_final_link_rename,
                ):
                    with self.assertRaises(type(failure)):
                        install_opencode(repo, config, state)

                self.assertTrue(injected)
                if isinstance(failure, install_module.InstallError):
                    self.assertFalse(receipt_path(state).exists())
                    self.assertFalse(
                        any(path.is_symlink() for path in config.rglob("*"))
                    )
                    destination = config / "skills/brainstorm"
                else:
                    payload = receipt(state)
                    published = next(
                        entry
                        for entry in payload["links"]
                        if Path(entry["destination"]).is_symlink()
                    )
                    destination = Path(published["destination"])
                    original_identity = (
                        destination.lstat().st_dev,
                        destination.lstat().st_ino,
                    )
                    self.assertEqual(
                        (
                            published["destination_dev"],
                            published["destination_ino"],
                        ),
                        original_identity,
                    )
                    self.assertIn("staged_destination", published)

                install_opencode(repo, config, state)
                uninstall_opencode(repo, config, state)

                self.assertFalse(receipt_path(state).exists())
                self.assertFalse(destination.is_symlink())

    def test_interrupted_link_publication_never_claims_same_target_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_rename = install_module._renameat_noreplace
            injected = False

            def crash_after_final_link_rename(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                real_rename(source_fd, source_name, target_fd, target_name)
                if source_name.endswith(".link") and not injected:
                    injected = True
                    raise SystemExit("after final link rename")

            with mock.patch.object(
                install_module,
                "_renameat_noreplace",
                side_effect=crash_after_final_link_rename,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = receipt(state)
            published = next(
                entry
                for entry in payload["links"]
                if Path(entry["destination"]).is_symlink()
            )
            destination = Path(published["destination"])
            source = Path(published["source"])
            displaced = destination.with_name(f"{destination.name}.displaced")
            destination.rename(displaced)
            destination.symlink_to(source)
            replacement_identity = (
                destination.lstat().st_dev,
                destination.lstat().st_ino,
            )

            install_opencode(repo, config, state)
            committed = receipt(state)
            recorded = next(
                entry
                for entry in committed["links"]
                if entry["destination"] == str(destination)
            )
            self.assertNotEqual(
                (recorded["destination_dev"], recorded["destination_ino"]),
                replacement_identity,
            )
            uninstall_opencode(repo, config, state)

            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )

    def test_interrupted_link_publication_never_deletes_foreign_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_rename = install_module._renameat_noreplace
            injected = False

            def crash_after_final_link_rename(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                real_rename(source_fd, source_name, target_fd, target_name)
                if source_name.endswith(".link") and not injected:
                    injected = True
                    raise SystemExit("after final link rename")

            with mock.patch.object(
                install_module,
                "_renameat_noreplace",
                side_effect=crash_after_final_link_rename,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            payload = receipt(state)
            published = next(
                entry
                for entry in payload["links"]
                if Path(entry["destination"]).is_symlink()
            )
            destination = Path(published["destination"])
            displaced = destination.with_name(f"{destination.name}.displaced")
            destination.rename(displaced)
            foreign = root / "foreign"
            foreign.write_text("foreign\n", encoding="utf-8")
            destination.symlink_to(foreign)
            replacement_identity = (
                destination.lstat().st_dev,
                destination.lstat().st_ino,
            )

            uninstall_opencode(repo, config, state)

            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(foreign))
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )
            self.assertFalse(receipt_path(state).exists())

    def test_crash_after_legacy_link_publication_does_not_restore_old_targets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            legacy_links = install_module._legacy_opencode_expected_links(repo, config)
            for link in legacy_links:
                link.destination.parent.mkdir(parents=True, exist_ok=True)
                link.destination.symlink_to(link.source)
            path = receipt_path(state)
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "links": [
                            {"source": str(link.source), "destination": str(link.destination)}
                            for link in legacy_links
                        ],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(repo),
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(
                install_module,
                "_prune_opencode_retired_links",
                side_effect=SystemExit("after migrated link publication"),
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertTrue(
                all(
                    Path(os.readlink(link.destination)).is_relative_to(
                        state / "expskill/opencode-artifact"
                    )
                    for link in legacy_links
                )
            )
            install_opencode(repo, config, state)
            uninstall_opencode(repo, config, state)
            self.assertFalse(any(path.is_symlink() for path in config.rglob("*")))

    def test_crash_after_retired_link_pruning_never_recreates_removed_entry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            retired_skill = config / "skills/unslop"
            retired_command = config / "commands/unslop.md"
            shutil.rmtree(repo / "packages/expskill/skills/unslop")
            real_evidence = install_module._artifact_evidence
            injected = False

            def crash_after_pruning(path: Path) -> str | None:
                nonlocal injected
                evidence = real_evidence(path)
                if (
                    not injected
                    and not retired_skill.is_symlink()
                    and not retired_command.is_symlink()
                ):
                    injected = True
                    raise SystemExit("after retired link pruning")
                return evidence

            with (
                mock.patch.object(install_module, "_validate_repository"),
                mock.patch.object(
                    install_module,
                    "_artifact_evidence",
                    side_effect=crash_after_pruning,
                ),
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertFalse(retired_skill.is_symlink())
            self.assertFalse(retired_command.is_symlink())
            with mock.patch.object(install_module, "_validate_repository"):
                install_opencode(repo, config, state)
                uninstall_opencode(repo, config, state)
            self.assertFalse(retired_skill.is_symlink())
            self.assertFalse(retired_command.is_symlink())

    def test_new_install_rejects_preexisting_same_target_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            planned = install_module.preflight_opencode_links(repo, config, state)
            existing = planned[0]
            existing.destination.parent.mkdir(parents=True)
            existing.destination.symlink_to(existing.source)
            identity = (
                existing.destination.lstat().st_dev,
                existing.destination.lstat().st_ino,
            )

            with self.assertRaisesRegex(
                install_module.InstallError, "unowned opencode destination"
            ):
                install_opencode(repo, config, state)

            self.assertTrue(existing.destination.is_symlink())
            self.assertEqual(os.readlink(existing.destination), str(existing.source))
            self.assertEqual(
                (
                    existing.destination.lstat().st_dev,
                    existing.destination.lstat().st_ino,
                ),
                identity,
            )

    def test_anchor_created_before_persistence_is_adopted_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_link = os.link
            stopped = False

            def stop_after_link(*args: object, **kwargs: object) -> None:
                nonlocal stopped
                real_link(*args, **kwargs)
                if not stopped:
                    stopped = True
                    raise SystemExit("after durable anchor creation")

            with mock.patch.object(install_module.os, "link", side_effect=stop_after_link):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            pending = receipt(state)
            pending_anchor = Path(pending["pending_publish"]["candidate_anchor"])
            pending_candidate = Path(pending["pending_publish"]["candidate"])
            self.assertTrue(pending_anchor.is_file())
            self.assertEqual(
                pending_anchor.stat().st_ino,
                (pending_candidate / "package.json").stat().st_ino,
            )

            install_opencode(repo, config, state)
            committed = receipt(state)
            self.assertEqual(Path(committed["artifact_anchor"]), pending_anchor)
            self.assertEqual(committed["teardown_phase"], "committed")

    def test_committed_anchor_is_permanent_and_reinstall_preserves_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            first = receipt(state)
            artifact = Path(first["artifact_root"])
            anchor = Path(first["artifact_anchor"])
            artifact_identity = (artifact.stat().st_dev, artifact.stat().st_ino)
            anchor_identity = (anchor.stat().st_dev, anchor.stat().st_ino)
            self.assertEqual(
                anchor_identity,
                ((artifact / "package.json").stat().st_dev, (artifact / "package.json").stat().st_ino),
            )
            self.assertEqual(first["teardown_phase"], "committed")
            self.assertTrue(
                all("destination_dev" in link and "destination_ino" in link for link in first["links"])
            )

            install_opencode(repo, config, state)
            second = receipt(state)
            self.assertEqual((artifact.stat().st_dev, artifact.stat().st_ino), artifact_identity)
            self.assertEqual(Path(second["artifact_anchor"]), anchor)
            self.assertEqual((anchor.stat().st_dev, anchor.stat().st_ino), anchor_identity)

    def test_uninstall_preserves_same_target_recreated_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            owned = payload["links"][0]
            destination = Path(owned["destination"])
            source = Path(owned["source"])
            old_identity = (destination.lstat().st_dev, destination.lstat().st_ino)
            destination.unlink()
            destination.symlink_to(source)
            self.assertNotEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino), old_identity
            )

            uninstall_opencode(repo, config, state)

            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertFalse(receipt_path(state).exists())

    def test_reinstall_does_not_adopt_same_target_recreated_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            first = receipt(state)
            owned = first["links"][0]
            destination = Path(owned["destination"])
            source = Path(owned["source"])
            recorded_identity = (owned["destination_dev"], owned["destination_ino"])
            displaced = destination.with_name(f"{destination.name}.displaced")
            destination.rename(displaced)
            destination.symlink_to(source)
            replacement_identity = (
                destination.lstat().st_dev,
                destination.lstat().st_ino,
            )
            self.assertNotEqual(replacement_identity, recorded_identity)

            install_opencode(repo, config, state)

            reinstalled = receipt(state)
            reinstalled_link = next(
                link
                for link in reinstalled["links"]
                if link["destination"] == str(destination)
            )
            self.assertEqual(
                (
                    reinstalled_link["destination_dev"],
                    reinstalled_link["destination_ino"],
                ),
                recorded_identity,
            )

            uninstall_opencode(repo, config, state)

            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )

    def test_retired_link_pruning_preserves_same_target_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            source = root / "source"
            destination = config / "skills" / "retired"
            destination.parent.mkdir(parents=True)
            source.mkdir()
            destination.symlink_to(source)
            owned_metadata = destination.lstat()
            displaced = destination.with_name("retired.displaced")
            destination.rename(displaced)
            destination.symlink_to(source)
            replacement_identity = (
                destination.lstat().st_dev,
                destination.lstat().st_ino,
            )
            self.assertNotEqual(
                replacement_identity,
                (owned_metadata.st_dev, owned_metadata.st_ino),
            )
            recorded = install_module.ProfileLink(
                source=source,
                destination=destination,
                destination_dev=owned_metadata.st_dev,
                destination_ino=owned_metadata.st_ino,
            )
            installed = install_module._Receipt(
                repository_root=root,
                links=(recorded,),
                marketplace_added=False,
                plugin_installed=True,
            )

            retained, removed = install_module._prune_opencode_retired_links(
                installed, (), config
            )

            self.assertEqual(retained, ())
            self.assertEqual(removed, ())
            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )

    def test_upgrade_accepts_frozen_inventory_and_prunes_only_exact_retired_inode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            original = receipt(state)
            retired = {
                Path(link["destination"]).parent.name: link
                for link in original["links"]
                if Path(link["destination"]).stem == "unslop"
                and Path(link["destination"]).parent.name in {"skills", "commands"}
            }
            owned_command = Path(retired["commands"]["destination"])
            replaced_skill = Path(retired["skills"]["destination"])
            replaced_source = Path(retired["skills"]["source"])
            displaced = replaced_skill.with_name("unslop.displaced")
            replaced_skill.rename(displaced)
            replaced_skill.symlink_to(replaced_source)
            replacement_identity = (
                replaced_skill.lstat().st_dev,
                replaced_skill.lstat().st_ino,
            )
            shutil.rmtree(repo / "packages/expskill/skills/unslop")

            with mock.patch.object(install_module, "_validate_repository"):
                install_opencode(repo, config, state)

            upgraded = receipt(state)
            self.assertNotIn(
                str(replaced_skill),
                {link["destination"] for link in upgraded["links"]},
            )
            self.assertFalse(owned_command.exists())
            self.assertFalse(owned_command.is_symlink())
            self.assertTrue(replaced_skill.is_symlink())
            self.assertEqual(os.readlink(replaced_skill), str(replaced_source))
            self.assertEqual(
                (replaced_skill.lstat().st_dev, replaced_skill.lstat().st_ino),
                replacement_identity,
            )

    def test_install_failure_rolls_back_only_exact_new_link_inodes(self) -> None:
        for failure in (
            install_module.InstallError("after link creation"),
            SystemExit("after link creation"),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                real_create_links = install_module._create_links
                created_snapshot: list[install_module.ProfileLink] = []
                replacement: tuple[Path, Path, tuple[int, int]] | None = None

                def replace_after_creation(
                    links: object, created: list[install_module.ProfileLink]
                ) -> None:
                    nonlocal replacement
                    real_create_links(links, created)
                    created_snapshot.extend(created)
                    victim = created[0]
                    displaced = victim.destination.with_name(
                        f"{victim.destination.name}.displaced"
                    )
                    victim.destination.rename(displaced)
                    victim.destination.symlink_to(victim.source)
                    replacement = (
                        victim.destination,
                        victim.source,
                        (
                            victim.destination.lstat().st_dev,
                            victim.destination.lstat().st_ino,
                        ),
                    )

                def fail_later(*_args: object) -> None:
                    raise failure

                with (
                    mock.patch.object(
                        install_module,
                        "_create_links",
                        side_effect=replace_after_creation,
                    ),
                    mock.patch.object(
                        install_module,
                        "_prune_opencode_retired_links",
                        side_effect=fail_later,
                    ),
                ):
                    with self.assertRaises(type(failure)):
                        install_opencode(repo, config, state)

                self.assertTrue(created_snapshot)
                self.assertTrue(
                    all(
                        link.destination_dev is not None
                        and link.destination_ino is not None
                        for link in created_snapshot
                    )
                )
                assert replacement is not None
                destination, source, identity = replacement
                self.assertTrue(destination.is_symlink())
                self.assertEqual(os.readlink(destination), str(source))
                self.assertEqual(
                    (destination.lstat().st_dev, destination.lstat().st_ino),
                    identity,
                )
                if isinstance(failure, SystemExit):
                    self.assertTrue(
                        all(
                            link.destination.is_symlink()
                            for link in created_snapshot[1:]
                        )
                    )
                    uninstall_opencode(repo, config, state)
                    self.assertFalse(
                        any(
                            link.destination.is_symlink()
                            for link in created_snapshot[1:]
                        )
                    )
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(
                        (destination.lstat().st_dev, destination.lstat().st_ino),
                        identity,
                    )
                else:
                    self.assertFalse(
                        any(
                            link.destination.is_symlink()
                            for link in created_snapshot[1:]
                        )
                    )

    def test_anchored_uninstall_accepts_missing_and_retargeted_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            missing = Path(payload["links"][0]["destination"])
            retargeted = Path(payload["links"][1]["destination"])
            missing.unlink()
            retargeted.unlink()
            foreign_target = root / "foreign-target"
            foreign_target.write_text("foreign\n", encoding="utf-8")
            retargeted.symlink_to(foreign_target)

            uninstall_opencode(repo, config, state)

            self.assertFalse(missing.exists())
            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(os.readlink(retargeted), str(foreign_target))
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(Path(payload["artifact_root"]).exists())
            self.assertFalse(Path(payload["artifact_anchor"]).exists())

    def test_reinstall_resumes_every_teardown_phase(self) -> None:
        for phase in ("removing-links", "artifact-removed", "anchor-removed"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                real_write = install_module._write_receipt
                stopped = False

                def stop_after_phase(path: Path, value: object) -> None:
                    nonlocal stopped
                    real_write(path, value)
                    if getattr(value, "teardown_phase", None) == phase and not stopped:
                        stopped = True
                        raise SystemExit(f"after {phase}")

                with mock.patch.object(
                    install_module, "_write_receipt", side_effect=stop_after_phase
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)

                self.assertEqual(receipt(state)["teardown_phase"], phase)
                install_opencode(repo, config, state)
                committed = receipt(state)
                self.assertEqual(committed["teardown_phase"], "committed")
                self.assertTrue(Path(committed["artifact_root"]).is_dir())
                self.assertTrue(Path(committed["artifact_anchor"]).is_file())

    def test_artifact_deletion_interruptions_resume_without_content(self) -> None:
        for failure in (OSError("partial delete"), SystemExit("root deleted")):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                payload = receipt(state)
                artifact = Path(payload["artifact_root"])
                real_remove = install_module._remove_opencode_artifact_exact
                stopped = False

                def interrupt(path: Path, dev: int, ino: int) -> None:
                    nonlocal stopped
                    if stopped:
                        real_remove(path, dev, ino)
                        return
                    stopped = True
                    if isinstance(failure, OSError):
                        (path / "catalog.json").unlink()
                        raise failure
                    real_remove(path, dev, ino)
                    raise failure

                with mock.patch.object(
                    install_module, "_remove_opencode_artifact_exact", side_effect=interrupt
                ):
                    with self.assertRaises(type(failure)):
                        uninstall_opencode(repo, config, state)

                self.assertEqual(receipt(state)["teardown_phase"], "removing-links")
                # Recovery uses frozen inode ownership, not a digest or intact tree.
                uninstall_opencode(repo, config, state)
                self.assertFalse(receipt_path(state).exists())
                self.assertFalse(artifact.exists())

    def test_anchor_and_receipt_unlink_interruptions_are_absence_as_done(self) -> None:
        for operation in ("anchor", "receipt"):
            for failure_type in (OSError, SystemExit):
                with (
                    self.subTest(operation=operation, failure=failure_type.__name__),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    if operation == "anchor":
                        original = install_module._unlink_artifact_anchor_identity

                        def stop_anchor(path: Path, dev: int, ino: int) -> None:
                            original(path, dev, ino)
                            raise failure_type("after anchor unlink")

                        patcher = mock.patch.object(
                            install_module,
                            "_unlink_artifact_anchor_identity",
                            side_effect=stop_anchor,
                        )
                    else:
                        original_receipt_unlink = install_module._unlink_state_path

                        def stop_receipt(path: Path) -> None:
                            original_receipt_unlink(path)
                            raise failure_type("after receipt unlink")

                        patcher = mock.patch.object(
                            install_module, "_unlink_state_path", side_effect=stop_receipt
                        )
                    with patcher:
                        with self.assertRaises(failure_type):
                            uninstall_opencode(repo, config, state)

                    # Reinstall must finish any retained phase before publishing.
                    install_opencode(repo, config, state)
                    self.assertEqual(receipt(state)["teardown_phase"], "committed")

    def test_phase_write_failures_resume_before_reinstall(self) -> None:
        for phase in ("removing-links", "artifact-removed", "anchor-removed"):
            for failure_type in (OSError, SystemExit):
                with (
                    self.subTest(phase=phase, failure=failure_type.__name__),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    real_write = install_module._write_receipt
                    failed = False

                    def fail_phase(path: Path, value: object) -> None:
                        nonlocal failed
                        if getattr(value, "teardown_phase", None) == phase and not failed:
                            failed = True
                            raise failure_type(f"before {phase} write")
                        real_write(path, value)

                    with mock.patch.object(
                        install_module, "_write_receipt", side_effect=fail_phase
                    ):
                        with self.assertRaises(failure_type):
                            uninstall_opencode(repo, config, state)
                    install_opencode(repo, config, state)
                    self.assertEqual(receipt(state)["teardown_phase"], "committed")

    def test_upgrade_retries_old_artifact_and_anchor_cleanup(self) -> None:
        for failure_point in ("backup-delete", "anchor-delete"):
            with self.subTest(failure_point=failure_point), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                old = receipt(state)
                skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                skill.write_text(
                    skill.read_text(encoding="utf-8").replace(
                        "Cut AI tells", "upgrade ownership marker"
                    ),
                    encoding="utf-8",
                )
                if failure_point == "backup-delete":
                    original_remove = install_module._remove_opencode_artifact_exact

                    def fail_old(path: Path, dev: int, ino: int) -> None:
                        if ".old-" in path.name:
                            raise install_module.InstallError("old backup delete")
                        original_remove(path, dev, ino)

                    patcher = mock.patch.object(
                        install_module, "_remove_opencode_artifact_exact", side_effect=fail_old
                    )
                else:
                    original_anchor_unlink = install_module._unlink_artifact_anchor_identity

                    def fail_old_anchor(path: Path, dev: int, ino: int) -> None:
                        if path == Path(old["artifact_anchor"]):
                            raise install_module.InstallError("old anchor delete")
                        original_anchor_unlink(path, dev, ino)

                    patcher = mock.patch.object(
                        install_module,
                        "_unlink_artifact_anchor_identity",
                        side_effect=fail_old_anchor,
                    )
                with patcher:
                    install_opencode(repo, config, state)
                interrupted = receipt(state)
                self.assertIn("pending_swap", interrupted)
                self.assertTrue(Path(interrupted["artifact_anchor"]).is_file())

                install_opencode(repo, config, state)
                committed = receipt(state)
                self.assertNotIn("pending_swap", committed)
                self.assertTrue(Path(committed["artifact_anchor"]).is_file())
                self.assertNotEqual(committed["artifact_anchor"], old["artifact_anchor"])

    def test_repeated_upgrade_cleanup_failure_keeps_one_retirement_journal(
        self,
    ) -> None:
        for final_entrypoint in ("install", "uninstall"):
            with (
                self.subTest(final_entrypoint=final_entrypoint),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                skill.write_text(
                    skill.read_text(encoding="utf-8").replace(
                        "Cut AI tells", "repeated retirement marker"
                    ),
                    encoding="utf-8",
                )
                real_remove = install_module._remove_opencode_artifact_exact

                def fail_exact_old_backup(path: Path, dev: int, ino: int) -> None:
                    if ".old-" in path.name:
                        raise install_module.InstallError("old backup remains busy")
                    real_remove(path, dev, ino)

                proof_keys = {
                    "artifact",
                    "candidate",
                    "candidate_dev",
                    "candidate_ino",
                    "candidate_digest",
                    "candidate_anchor",
                    "candidate_anchor_dev",
                    "candidate_anchor_ino",
                    "backup",
                    "backup_dev",
                    "backup_ino",
                    "backup_digest",
                    "old_anchor",
                    "old_anchor_dev",
                    "old_anchor_ino",
                    "lineage",
                }

                with mock.patch.object(
                    install_module,
                    "_remove_opencode_artifact_exact",
                    side_effect=fail_exact_old_backup,
                ):
                    install_opencode(repo, config, state)
                    first = receipt(state)["pending_swap"]
                    frozen_proof = {key: first[key] for key in proof_keys}
                    self.assertEqual(first["phase"], "published")
                    missing = Path(receipt(state)["links"][0]["destination"])
                    missing.unlink()

                    for _retry in range(2):
                        install_opencode(repo, config, state)
                        current = receipt(state)["pending_swap"]
                        self.assertEqual(
                            {key: current[key] for key in proof_keys}, frozen_proof
                        )
                        self.assertEqual(current["phase"], "published")

                pending = receipt(state)["pending_swap"]
                exact_backup = Path(pending["backup"])
                exact_old_anchor = Path(pending["old_anchor"])
                foreign_backup = exact_backup.with_name(
                    ".opencode-artifact.old-foreign"
                )
                foreign_backup.mkdir()
                foreign_backup.joinpath("keep").write_text("foreign\n", encoding="utf-8")
                foreign_anchor = exact_old_anchor.with_name(
                    ".opencode-artifact.anchor-foreign"
                )
                foreign_anchor.write_text("foreign\n", encoding="utf-8")

                if final_entrypoint == "install":
                    install_opencode(repo, config, state)
                    self.assertNotIn("pending_swap", receipt(state))
                else:
                    uninstall_opencode(repo, config, state)
                    self.assertFalse(receipt_path(state).exists())
                self.assertFalse(exact_backup.exists())
                self.assertFalse(exact_old_anchor.exists())
                self.assertEqual(
                    foreign_backup.joinpath("keep").read_text(encoding="utf-8"),
                    "foreign\n",
                )
                self.assertEqual(foreign_anchor.read_text(encoding="utf-8"), "foreign\n")

    def test_nonterminal_receipt_delete_quarantine_recovers_through_entrypoints(
        self,
    ) -> None:
        for cleanup_boundary in ("publication", "preflight"):
            for failure_type in (install_module.InstallError, SystemExit):
                for retry_entrypoint in ("install", "uninstall"):
                    with (
                        self.subTest(
                            cleanup_boundary=cleanup_boundary,
                            failure=failure_type.__name__,
                            retry_entrypoint=retry_entrypoint,
                        ),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        repo = seed_repository(root / "repo")
                        config = root / "config"
                        state = root / "state"
                        final_receipt = receipt_path(state)
                        real_delete_rename = install_module._renameat_noreplace
                        deletion_interrupted = False

                        def stop_after_receipt_quarantine(
                            source_fd: int,
                            source_name: str,
                            target_fd: int,
                            target_name: str,
                        ) -> None:
                            nonlocal deletion_interrupted
                            real_delete_rename(
                                source_fd, source_name, target_fd, target_name
                            )
                            if (
                                source_name == final_receipt.name
                                and target_name.endswith(".delete")
                                and not deletion_interrupted
                            ):
                                deletion_interrupted = True
                                raise failure_type("after nonterminal receipt rename")

                        patches = [
                            mock.patch.object(
                                install_module,
                                "_renameat_noreplace",
                                side_effect=stop_after_receipt_quarantine,
                            )
                        ]
                        if cleanup_boundary == "publication":
                            real_publish = install_module._rename_noreplace

                            def fail_initial_publication(
                                source: Path, target: Path
                            ) -> None:
                                if ".next-" in source.name and target.name == "opencode-artifact":
                                    raise install_module.InstallError(
                                        "initial publication failed"
                                    )
                                real_publish(source, target)

                            patches.append(
                                mock.patch.object(
                                    install_module,
                                    "_rename_noreplace",
                                    side_effect=fail_initial_publication,
                                )
                            )
                        else:
                            patches.append(
                                mock.patch.object(
                                    install_module,
                                    "preflight_opencode_links",
                                    side_effect=install_module.InstallError(
                                        "preflight failed"
                                    ),
                                )
                            )

                        with patches[0], patches[1]:
                            with self.assertRaises(
                                SystemExit
                                if failure_type is SystemExit
                                else install_module.InstallError
                            ):
                                install_opencode(repo, config, state)

                        self.assertTrue(deletion_interrupted)
                        self.assertFalse(final_receipt.exists())
                        quarantines = tuple(final_receipt.parent.glob("*.delete"))
                        self.assertEqual(len(quarantines), 1)
                        quarantined_receipt = quarantines[0]
                        quarantined_payload = json.loads(
                            quarantined_receipt.read_text(encoding="utf-8")
                        )
                        self.assertIn("lineage", quarantined_payload)
                        self.assertIn("pending_publish", quarantined_payload)

                        if retry_entrypoint == "install":
                            install_opencode(repo, config, state)
                            self.assertTrue(final_receipt.is_file())
                        else:
                            uninstall_opencode(repo, config, state)
                            self.assertFalse(final_receipt.exists())
                        self.assertFalse(quarantined_receipt.exists())
                        self.assertEqual(
                            tuple(final_receipt.parent.glob("*.delete")), ()
                        )

    def test_upgrade_cleanup_phase_write_failures_recover_from_absence(self) -> None:
        for phase in ("old-artifact-removed", "old-anchor-removed"):
            for failure_type in (OSError, SystemExit):
                with (
                    self.subTest(phase=phase, failure=failure_type.__name__),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                    skill.write_text(
                        skill.read_text(encoding="utf-8").replace(
                            "Cut AI tells", "upgrade phase write marker"
                        ),
                        encoding="utf-8",
                    )
                    real_write = install_module._write_receipt
                    failed = False

                    def fail_cleanup_phase(path: Path, value: object) -> None:
                        nonlocal failed
                        pending = getattr(value, "pending_swap", None)
                        if pending is not None and pending.phase == phase and not failed:
                            failed = True
                            raise failure_type(f"before {phase} persistence")
                        real_write(path, value)

                    with mock.patch.object(
                        install_module, "_write_receipt", side_effect=fail_cleanup_phase
                    ):
                        with self.assertRaises(failure_type):
                            install_opencode(repo, config, state)
                    self.assertIn("pending_swap", receipt(state))
                    install_opencode(repo, config, state)
                    self.assertNotIn("pending_swap", receipt(state))

    def test_upgrade_cleanup_recovery_does_not_require_live_link_quorum(self) -> None:
        for entrypoint, link_change in (("install", "remove"), ("uninstall", "retarget")):
            with (
                self.subTest(entrypoint=entrypoint, link_change=link_change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                skill.write_text(
                    skill.read_text(encoding="utf-8").replace(
                        "Cut AI tells", "frozen cleanup identity marker"
                    ),
                    encoding="utf-8",
                )
                real_write = install_module._write_receipt
                failed = False

                def fail_old_artifact_phase(path: Path, value: object) -> None:
                    nonlocal failed
                    pending = getattr(value, "pending_swap", None)
                    if (
                        pending is not None
                        and pending.phase == "old-artifact-removed"
                        and not failed
                    ):
                        failed = True
                        raise SystemExit("before old-artifact-removed persistence")
                    real_write(path, value)

                with mock.patch.object(
                    install_module,
                    "_write_receipt",
                    side_effect=fail_old_artifact_phase,
                ):
                    with self.assertRaises(SystemExit):
                        install_opencode(repo, config, state)

                interrupted = receipt(state)
                self.assertEqual(
                    interrupted["pending_swap"]["phase"], "published"
                )
                changed = interrupted["links"][0]
                destination = Path(changed["destination"])
                destination.unlink()
                foreign_target = root / "foreign-target"
                if link_change == "retarget":
                    foreign_target.write_text("foreign\n", encoding="utf-8")
                    destination.symlink_to(foreign_target)

                if entrypoint == "install":
                    install_opencode(repo, config, state)
                    converged = receipt(state)
                    self.assertNotIn("pending_swap", converged)
                    self.assertTrue(destination.is_symlink())
                else:
                    uninstall_opencode(repo, config, state)
                    self.assertFalse(receipt_path(state).exists())
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(os.readlink(destination), str(foreign_target))

    def test_initial_publication_receipt_boundaries_converge(self) -> None:
        boundaries = (
            "prepared",
            "anchor-recorded",
            "published",
            "planned-links",
            "final-committed",
        )
        for boundary in boundaries:
            for failure_type in (OSError, SystemExit):
                for entrypoint in ("install", "uninstall"):
                    with (
                        self.subTest(
                            boundary=boundary,
                            failure=failure_type.__name__,
                            entrypoint=entrypoint,
                        ),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        repo = seed_repository(root / "repo")
                        config = root / "config"
                        state = root / "state"
                        real_write = install_module._write_receipt
                        injected = False

                        def classify(value: object) -> str | None:
                            pending = getattr(value, "pending_publish", None)
                            if pending is not None:
                                return pending.phase
                            if getattr(value, "artifact_root", None) is not None:
                                return "final-committed"
                            return None

                        def interrupt_write(path: Path, value: object) -> None:
                            nonlocal injected
                            if classify(value) != boundary or injected:
                                real_write(path, value)
                                return
                            injected = True
                            if failure_type is SystemExit:
                                real_write(path, value)
                            raise failure_type(f"at {boundary}")

                        with mock.patch.object(
                            install_module,
                            "_write_receipt",
                            side_effect=interrupt_write,
                        ):
                            with self.assertRaises(
                                SystemExit
                                if failure_type is SystemExit
                                else (install_module.InstallError, OSError)
                            ):
                                install_opencode(repo, config, state)
                        self.assertTrue(injected)

                        replacement: tuple[Path, Path, tuple[int, int]] | None = None
                        replacement_has_no_authority = False
                        live_links = sorted(
                            path for path in config.rglob("*") if path.is_symlink()
                        )
                        if not live_links and receipt_path(state).is_file():
                            interrupted = receipt(state)
                            planned = interrupted.get("links", [])
                            if planned:
                                destination = Path(planned[0]["destination"])
                                target = Path(planned[0]["source"])
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                destination.symlink_to(target)
                                live_links = [destination]
                                replacement_has_no_authority = True
                        if live_links:
                            destination = live_links[0]
                            target = Path(os.readlink(destination))
                            displaced = destination.with_name(
                                f"{destination.name}.displaced"
                            )
                            destination.rename(displaced)
                            destination.symlink_to(target)
                            replacement = (
                                destination,
                                target,
                                (
                                    destination.lstat().st_dev,
                                    destination.lstat().st_ino,
                                ),
                            )

                        if entrypoint == "install":
                            install_opencode(repo, config, state)
                            converged = receipt(state)
                            self.assertNotIn("pending_publish", converged)
                            self.assertNotIn("pending_swap", converged)
                            self.assertEqual(converged["teardown_phase"], "committed")
                            self.assertTrue(Path(converged["artifact_anchor"]).is_file())
                            if replacement_has_no_authority:
                                preserved = next(
                                    link
                                    for link in converged["links"]
                                    if link["destination"] == str(replacement[0])
                                )
                                self.assertNotEqual(
                                    (
                                        preserved.get("destination_dev"),
                                        preserved.get("destination_ino"),
                                    ),
                                    replacement[2],
                                )
                        else:
                            uninstall_opencode(repo, config, state)
                            self.assertFalse(receipt_path(state).exists())
                            self.assertFalse(
                                (state / "expskill/opencode-artifact").exists()
                            )

                        if replacement is not None:
                            destination, target, identity = replacement
                            self.assertTrue(destination.is_symlink())
                            self.assertEqual(Path(os.readlink(destination)), target)
                            self.assertEqual(
                                (
                                    destination.lstat().st_dev,
                                    destination.lstat().st_ino,
                                ),
                                identity,
                            )

    def test_upgrade_receipt_boundaries_converge_without_claiming_replacements(
        self,
    ) -> None:
        boundaries = (
            "prepared",
            "anchor-recorded",
            "backup-created",
            "published",
            "final-committed",
            "old-artifact-removed",
            "old-anchor-removed",
        )
        for boundary in boundaries:
            for failure_type in (OSError, SystemExit):
                for entrypoint in ("install", "uninstall"):
                    with (
                        self.subTest(
                            boundary=boundary,
                            failure=failure_type.__name__,
                            entrypoint=entrypoint,
                        ),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        repo = seed_repository(root / "repo")
                        config = root / "config"
                        state = root / "state"
                        install_opencode(repo, config, state)
                        original = receipt(state)
                        skill = repo / "packages/expskill/skills/unslop/SKILL.md"
                        skill.write_text(
                            skill.read_text(encoding="utf-8").replace(
                                "Cut AI tells", f"{boundary} upgrade marker"
                            ),
                            encoding="utf-8",
                        )
                        real_write = install_module._write_receipt
                        injected = False

                        def classify(value: object) -> str | None:
                            pending = getattr(value, "pending_swap", None)
                            if pending is None:
                                return None
                            if (
                                pending.phase == "published"
                                and (
                                    getattr(value, "artifact_dev", None),
                                    getattr(value, "artifact_ino", None),
                                )
                                == (pending.candidate_dev, pending.candidate_ino)
                            ):
                                return "final-committed"
                            return pending.phase

                        def interrupt_write(path: Path, value: object) -> None:
                            nonlocal injected
                            if classify(value) != boundary or injected:
                                real_write(path, value)
                                return
                            injected = True
                            if failure_type is SystemExit:
                                real_write(path, value)
                            raise failure_type(f"at {boundary}")

                        with mock.patch.object(
                            install_module,
                            "_write_receipt",
                            side_effect=interrupt_write,
                        ):
                            with self.assertRaises(
                                SystemExit
                                if failure_type is SystemExit
                                else (install_module.InstallError, OSError)
                            ):
                                install_opencode(repo, config, state)
                        self.assertTrue(injected)

                        owned = original["links"][0]
                        destination = Path(owned["destination"])
                        target = Path(os.readlink(destination))
                        displaced = destination.with_name(
                            f"{destination.name}.displaced"
                        )
                        destination.rename(displaced)
                        destination.symlink_to(target)
                        replacement_identity = (
                            destination.lstat().st_dev,
                            destination.lstat().st_ino,
                        )

                        if entrypoint == "install":
                            install_opencode(repo, config, state)
                            converged = receipt(state)
                            self.assertNotIn("pending_swap", converged)
                            recorded = next(
                                link
                                for link in converged["links"]
                                if link["destination"] == str(destination)
                            )
                            self.assertEqual(
                                (
                                    recorded["destination_dev"],
                                    recorded["destination_ino"],
                                ),
                                (
                                    owned["destination_dev"],
                                    owned["destination_ino"],
                                ),
                            )
                        else:
                            uninstall_opencode(repo, config, state)
                            self.assertFalse(receipt_path(state).exists())
                            self.assertFalse(
                                (state / "expskill/opencode-artifact").exists()
                            )

                        self.assertTrue(destination.is_symlink())
                        self.assertEqual(Path(os.readlink(destination)), target)
                        self.assertEqual(
                            (destination.lstat().st_dev, destination.lstat().st_ino),
                            replacement_identity,
                        )

    def test_legacy_anchorless_receipt_migrates_only_with_full_live_inventory(self) -> None:
        for remove_link in (False, True):
            with self.subTest(remove_link=remove_link), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                payload = receipt(state)
                Path(payload["artifact_anchor"]).unlink()
                payload.pop("artifact_anchor")
                payload.pop("artifact_anchor_dev")
                payload.pop("artifact_anchor_ino")
                payload.pop("teardown_phase")
                for link in payload["links"]:
                    link.pop("destination_dev")
                    link.pop("destination_ino")
                if remove_link:
                    Path(payload["links"][0]["destination"]).unlink()
                receipt_path(state).write_text(json.dumps(payload), encoding="utf-8")

                if remove_link:
                    with self.assertRaises(install_module.InstallError):
                        uninstall_opencode(repo, config, state)
                    self.assertTrue(Path(payload["artifact_root"]).is_dir())
                    self.assertTrue(receipt_path(state).is_file())
                else:
                    uninstall_opencode(repo, config, state)
                    self.assertFalse(receipt_path(state).exists())

    def test_foreign_backup_and_receipt_replacements_survive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "packages/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "foreign backup marker"
                ),
                encoding="utf-8",
            )
            real_remove = install_module._remove_opencode_artifact_exact
            displaced: Path | None = None
            replacement: Path | None = None

            def replace_backup(path: Path, dev: int, ino: int) -> None:
                nonlocal displaced, replacement
                if ".old-" in path.name and displaced is None:
                    displaced = path.with_name("detached-old-artifact")
                    path.rename(displaced)
                    path.mkdir()
                    replacement = path
                real_remove(path, dev, ino)

            with mock.patch.object(
                install_module, "_remove_opencode_artifact_exact", side_effect=replace_backup
            ):
                install_opencode(repo, config, state)
            assert displaced is not None and replacement is not None
            install_opencode(repo, config, state)
            self.assertTrue(displaced.is_dir())
            self.assertTrue(replacement.is_dir())

            current_receipt = receipt_path(state)
            detached_receipt = current_receipt.with_name("detached-receipt")
            original_unlink = install_module._unlink_state_path

            def replace_receipt(path: Path) -> None:
                path.rename(detached_receipt)
                path.write_text("foreign receipt\n", encoding="utf-8")
                original_unlink(path)

            with mock.patch.object(
                install_module, "_unlink_state_path", side_effect=replace_receipt
            ):
                with self.assertRaises(install_module.InstallError):
                    uninstall_opencode(repo, config, state)
            self.assertEqual(current_receipt.read_text(), "foreign receipt\n")

    def test_foreign_artifact_anchor_receipt_backup_and_link_replacements_survive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            artifact = Path(payload["artifact_root"])
            detached = artifact.with_name("detached-owned-artifact")
            artifact.rename(detached)
            artifact.mkdir()
            artifact.joinpath("foreign").write_text("survive\n", encoding="utf-8")
            link = Path(payload["links"][0]["destination"])
            link.unlink()
            link.write_text("foreign link\n", encoding="utf-8")

            uninstall_opencode(repo, config, state)

            self.assertEqual((artifact / "foreign").read_text(), "survive\n")
            self.assertEqual(link.read_text(), "foreign link\n")
            self.assertTrue(detached.is_dir())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            anchor = Path(payload["artifact_anchor"])
            displaced_anchor = anchor.with_name("displaced-anchor")
            anchor.rename(displaced_anchor)
            anchor.write_text("foreign anchor\n", encoding="utf-8")
            with self.assertRaises(install_module.InstallError):
                uninstall_opencode(repo, config, state)
            self.assertEqual(anchor.read_text(), "foreign anchor\n")
            self.assertTrue(receipt_path(state).is_file())


if __name__ == "__main__":
    unittest.main()
