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
