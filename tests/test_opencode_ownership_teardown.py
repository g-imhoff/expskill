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
