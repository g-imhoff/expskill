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
from scripts.install import install_opencode, uninstall_opencode


ROOT = Path(__file__).resolve().parents[1]
TEST_RETIREMENT_SECRET = (
    "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
)


def seed_repository(path: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(ROOT / ".agents", path / ".agents", ignore=ignore)
    shutil.copytree(ROOT / "plugins", path / "plugins", ignore=ignore)
    shutil.copytree(ROOT / "scripts", path / "scripts", ignore=ignore)
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def receipt_path(state: Path) -> Path:
    return state / "expskill" / "install-opencode.json"


def receipt(state: Path) -> dict[str, object]:
    return json.loads(receipt_path(state).read_text(encoding="utf-8"))


def link_anchor_paths(config: Path) -> list[Path]:
    return [
        path
        for path in config.rglob("*")
        if path.name.startswith(install_module.OPENCODE_LINK_ANCHOR_PREFIX)
    ]


class OpenCodeOwnershipTeardownTests(unittest.TestCase):
    def setUp(self) -> None:
        # This foundation checkout intentionally omits the two release-only
        # skill directories.  Repository validation has its own contract
        # suite; ownership tests isolate the installer state machine.
        validation_patch = mock.patch.object(
            install_module, "_validate_repository", return_value=None
        )
        validation_patch.start()
        self.addCleanup(validation_patch.stop)
        # Capability names must be deterministic only inside tests that need
        # to preoccupy or replace the exact private pathname.
        secret_patch = mock.patch.object(
            install_module,
            "_retirement_secret",
            return_value=TEST_RETIREMENT_SECRET,
            create=True,
        )
        secret_patch.start()
        self.addCleanup(secret_patch.stop)

    def test_initial_rollback_preserves_same_target_planned_only_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            replacement: tuple[Path, Path, tuple[int, int]] | None = None

            def fail_before_link_publication(
                destination: Path,
                source: Path,
                _record_staged: object = None,
            ) -> tuple[int, int] | None:
                nonlocal replacement
                planned = receipt(state)
                self.assertTrue(planned["pending_publish"]["planned_links"])
                self.assertNotIn("destination_dev", planned["links"][0])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(source)
                replacement = (
                    destination,
                    source,
                    (destination.lstat().st_dev, destination.lstat().st_ino),
                )
                raise install_module.InstallError("injected link creation failure")

            with mock.patch.object(
                install_module,
                "_create_destination_link",
                side_effect=fail_before_link_publication,
            ):
                with self.assertRaisesRegex(
                    install_module.InstallError, "injected link creation failure"
                ):
                    install_opencode(repo, config, state)

            assert replacement is not None
            destination, source, identity = replacement
            self.assertTrue(destination.is_symlink())
            self.assertEqual(Path(os.readlink(destination)), source)
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino), identity
            )
            self.assertFalse(receipt_path(state).exists())

    def test_leaf_retirement_crash_after_exchange_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            victim = payload["links"][0]
            destination = Path(victim["destination"])
            real_exchange = install_module._renameat_exchange
            injected = False

            def crash_after_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                real_exchange(source_fd, source_name, target_fd, target_name)
                if (
                    source_name == destination.name
                    and target_name.endswith(".retire")
                    and not injected
                ):
                    injected = True
                    raise SystemExit("after successful leaf retirement exchange")

            with mock.patch.object(
                install_module, "_renameat_exchange", side_effect=crash_after_exchange
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertTrue(receipt_path(state).is_file())
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(
                any(path.name.endswith((".retire", ".sentinel")) for path in config.rglob(".*"))
            )

    def test_directory_retirement_crash_after_exchange_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = Path(receipt(state)["artifact_root"])
            real_exchange = install_module._renameat_exchange
            injected = False

            def crash_after_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                real_exchange(source_fd, source_name, target_fd, target_name)
                if (
                    source_name == artifact.name
                    and target_name.endswith(".retire-dir")
                    and not injected
                ):
                    injected = True
                    raise SystemExit("after successful directory retirement exchange")

            with mock.patch.object(
                install_module, "_renameat_exchange", side_effect=crash_after_exchange
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertTrue(receipt_path(state).is_file())
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(
                any(
                    path.name.endswith((".retire-dir", ".sentinel-dir"))
                    for path in artifact.parent.iterdir()
                )
            )

    def test_terminal_leaf_unlink_preserves_private_name_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            victim = payload["links"][0]
            destination = Path(victim["destination"])
            expected = (victim["destination_dev"], victim["destination_ino"])
            foreign_target = root / "foreign-target"
            token = install_module._retirement_token(
                payload["lineage"], "final-link", "leaf", destination, *expected,
                secret=TEST_RETIREMENT_SECRET,
            )
            private = install_module._retirement_private_path(
                destination, token, "leaf"
            )
            private.symlink_to(foreign_target)
            private_identity = (private.lstat().st_dev, private.lstat().st_ino)

            with self.assertRaises(install_module.InstallError):
                uninstall_opencode(repo, config, state)

            self.assertTrue(receipt_path(state).is_file())
            self.assertTrue(private.is_symlink())
            self.assertEqual(Path(os.readlink(private)), foreign_target)
            self.assertEqual((private.lstat().st_dev, private.lstat().st_ino), private_identity)

    def test_terminal_directory_remove_preserves_private_name_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            artifact = Path(payload["artifact_root"])
            expected = (payload["artifact_dev"], payload["artifact_ino"])
            token = install_module._retirement_token(
                payload["lineage"], "artifact", "directory", artifact, *expected,
                secret=TEST_RETIREMENT_SECRET,
            )
            private = install_module._retirement_private_path(
                artifact, token, "directory"
            )
            private.mkdir()
            (private / "foreign").write_text("foreign\n", encoding="utf-8")
            private_identity = (private.stat().st_dev, private.stat().st_ino)

            with self.assertRaises(install_module.InstallError):
                uninstall_opencode(repo, config, state)

            self.assertTrue(receipt_path(state).is_file())
            self.assertTrue(private.is_dir())
            self.assertEqual((private.stat().st_dev, private.stat().st_ino), private_identity)
            self.assertEqual((private / "foreign").read_text(), "foreign\n")

    def test_candidate_rollback_uses_exact_identity_when_anchor_is_lost(self) -> None:
        for generation in ("initial", "upgrade"):
            for anchor_change in ("missing", "foreign"):
                with (
                    self.subTest(generation=generation, anchor_change=anchor_change),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    original_artifact_identity: tuple[int, int] | None = None
                    if generation == "upgrade":
                        install_opencode(repo, config, state)
                        original = receipt(state)
                        original_artifact_identity = (
                            original["artifact_dev"],
                            original["artifact_ino"],
                        )
                        skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
                        skill.write_text(
                            skill.read_text(encoding="utf-8").replace(
                                "Cut AI tells", "rollback anchor relationship"
                            ),
                            encoding="utf-8",
                        )

                    changed_anchor: Path | None = None
                    detached_anchor: Path | None = None

                    def invalidate_anchor(*_args: object, **_kwargs: object) -> None:
                        nonlocal changed_anchor, detached_anchor
                        payload = receipt(state)
                        pending = payload[
                            "pending_publish" if generation == "initial" else "pending_swap"
                        ]
                        changed_anchor = Path(pending["candidate_anchor"])
                        if anchor_change == "foreign":
                            detached_anchor = changed_anchor.with_name(
                                f"{changed_anchor.name}.detached"
                            )
                            changed_anchor.rename(detached_anchor)
                            changed_anchor.write_text("foreign anchor\n", encoding="utf-8")
                        else:
                            changed_anchor.unlink()
                        raise install_module.InstallError("injected preflight failure")

                    with mock.patch.object(
                        install_module,
                        "preflight_opencode_links",
                        side_effect=invalidate_anchor,
                    ):
                        with self.assertRaises(install_module.InstallError):
                            install_opencode(repo, config, state)

                    interrupted = receipt(state) if receipt_path(state).exists() else None
                    artifact = state / "expskill/opencode-artifact"
                    if generation == "initial":
                        self.assertIsNone(interrupted)
                        self.assertFalse(artifact.exists())
                    else:
                        assert interrupted is not None
                        self.assertNotIn("pending_swap", interrupted)
                        self.assertEqual(
                            (artifact.stat().st_dev, artifact.stat().st_ino),
                            original_artifact_identity,
                        )
                    assert changed_anchor is not None
                    if anchor_change == "foreign":
                        self.assertEqual(
                            changed_anchor.read_text(encoding="utf-8"),
                            "foreign anchor\n",
                        )
                        assert detached_anchor is not None
                        self.assertTrue(detached_anchor.is_file())

    def test_anchorless_recovery_rejects_same_target_replacement_inode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            artifact = Path(payload["artifact_root"])
            Path(payload["artifact_anchor"]).unlink()
            for key in (
                "artifact_anchor",
                "artifact_anchor_dev",
                "artifact_anchor_ino",
            ):
                payload.pop(key)
            victim = payload["links"][0]
            destination = Path(victim["destination"])
            source = Path(victim["source"])
            displaced = destination.with_name(f"{destination.name}.owned")
            destination.rename(displaced)
            destination.symlink_to(source)
            replacement_identity = (
                destination.lstat().st_dev,
                destination.lstat().st_ino,
            )
            self.assertNotEqual(
                replacement_identity,
                (victim["destination_dev"], victim["destination_ino"]),
            )
            receipt_path(state).write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                install_module.InstallError, "ownership evidence|link identity"
            ):
                uninstall_opencode(repo, config, state)

            self.assertTrue(artifact.is_dir())
            self.assertTrue(receipt_path(state).is_file())
            self.assertTrue(destination.is_symlink())
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )

    def test_link_final_exchange_race_restores_foreign_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            victim = payload["links"][0]
            destination = Path(victim["destination"])
            expected = (victim["destination_dev"], victim["destination_ino"])
            detached = destination.with_name(f"{destination.name}.owned")
            foreign_target = root / "foreign-target"
            real_exchange = install_module._renameat_exchange
            injected = False

            def replace_at_final_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal injected
                if (
                    source_name == destination.name
                    and target_name.endswith(".retire")
                    and not injected
                ):
                    injected = True
                    os.rename(
                        source_name,
                        detached.name,
                        src_dir_fd=source_fd,
                        dst_dir_fd=source_fd,
                    )
                    os.symlink(foreign_target, source_name, dir_fd=source_fd)
                real_exchange(source_fd, source_name, target_fd, target_name)

            with mock.patch.object(
                install_module,
                "_renameat_exchange",
                side_effect=replace_at_final_exchange,
            ):
                with self.assertRaises(install_module.InstallError):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(injected, "cleanup never used a reversible final exchange")
            self.assertTrue(receipt_path(state).is_file())
            self.assertTrue(destination.is_symlink())
            self.assertEqual(Path(os.readlink(destination)), foreign_target)
            self.assertTrue(detached.is_symlink())
            self.assertEqual(
                (detached.lstat().st_dev, detached.lstat().st_ino), expected
            )

    def test_state_cleanup_final_exchange_races_preserve_foreign_replacements(
        self,
    ) -> None:
        # Receipt replacement has a dedicated authenticated-sidecar matrix
        # below; this older loop retains the object-retirement races.
        for kind in ("artifact", "anchor"):
            with (
                self.subTest(kind=kind),
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
                receipt_file = receipt_path(state)
                real_exchange = install_module._renameat_exchange
                injected = False
                raced_path: Path | None = None
                detached: Path | None = None

                def replace_at_final_exchange(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal injected, raced_path, detached
                    matches = (
                        (
                            kind == "artifact"
                            and source_name == artifact.name
                            and target_name.endswith(".retire-dir")
                        )
                        or (
                            kind == "anchor"
                            and source_name == anchor.name
                            and target_name.endswith(".retire")
                        )
                        or (
                            kind == "receipt"
                            and source_name.startswith(f".{receipt_file.name}.")
                            and source_name.endswith(".anchor-removed.delete")
                        )
                    )
                    if matches and not injected:
                        injected = True
                        raced_path = (
                            artifact
                            if kind == "artifact"
                            else anchor
                            if kind == "anchor"
                            else receipt_file.parent / source_name
                        )
                        detached = raced_path.with_name(f"{raced_path.name}.owned")
                        os.rename(
                            raced_path.name,
                            detached.name,
                            src_dir_fd=source_fd,
                            dst_dir_fd=source_fd,
                        )
                        if kind == "artifact":
                            os.mkdir(raced_path.name, dir_fd=source_fd)
                            (raced_path / "foreign").write_text(
                                "foreign artifact\n", encoding="utf-8"
                            )
                        else:
                            descriptor = os.open(
                                raced_path.name,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=source_fd,
                            )
                            os.write(descriptor, f"foreign {kind}\n".encode())
                            os.close(descriptor)
                    real_exchange(source_fd, source_name, target_fd, target_name)

                with mock.patch.object(
                    install_module,
                    "_renameat_exchange",
                    side_effect=replace_at_final_exchange,
                ):
                    with self.assertRaises(install_module.InstallError):
                        uninstall_opencode(repo, config, state)

                self.assertTrue(injected)
                assert raced_path is not None and detached is not None
                if kind == "artifact":
                    self.assertEqual(
                        (raced_path / "foreign").read_text(encoding="utf-8"),
                        "foreign artifact\n",
                    )
                else:
                    self.assertEqual(
                        raced_path.read_text(encoding="utf-8"),
                        f"foreign {kind}\n",
                    )
                self.assertTrue(detached.exists())
                self.assertTrue(
                    receipt_file.exists()
                    or any(receipt_file.parent.glob(f".{receipt_file.name}.*.pin"))
                )

    def test_initial_rollback_link_failure_retains_all_ownership_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            real_write = install_module._write_receipt
            real_retire = install_module._retire_owned_object

            def fail_final_commit(path: Path, value: object) -> None:
                if (
                    getattr(value, "artifact_root", None) is not None
                    and getattr(value, "pending_publish", None) is None
                    and getattr(value, "pending_swap", None) is None
                ):
                    raise install_module.InstallError("injected final commit failure")
                real_write(path, value)

            def fail_link_compensation(
                source: Path,
                identity: tuple[int, int],
                role: str,
                *,
                directory: bool,
                source_kind: str = "public",
            ) -> bool:
                if role in {"final-link", "staged-link"}:
                    raise install_module.InstallError(
                        "injected link compensation failure"
                    )
                return real_retire(
                    source,
                    identity,
                    role,
                    directory=directory,
                    source_kind=source_kind,
                )

            with (
                mock.patch.object(
                    install_module, "_write_receipt", side_effect=fail_final_commit
                ),
                mock.patch.object(
                    install_module,
                    "_retire_owned_object",
                    side_effect=fail_link_compensation,
                ),
            ):
                with self.assertRaisesRegex(
                    install_module.InstallError, "link compensation failure"
                ):
                    install_opencode(repo, config, state)

            self.assertTrue(receipt_path(state).is_file())
            retained = receipt(state)
            self.assertIn("pending_publish", retained)
            self.assertEqual(retained["pending_publish"]["phase"], "rollback-prepared")
            artifact = Path(retained["pending_publish"]["artifact"])
            anchor = Path(retained["pending_publish"]["candidate_anchor"])
            self.assertTrue(artifact.is_dir())
            self.assertTrue(anchor.is_file())
            self.assertTrue(retained["links"])
            self.assertTrue(
                all(
                    "destination_dev" in link
                    and "destination_ino" in link
                    and "staged_destination" in link
                    for link in retained["links"]
                )
            )

    def test_staged_link_authority_is_durable_before_directory_fsync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            script = """
import os
import sys
from pathlib import Path
import scripts.install as install_module

repo, config, state = map(Path, sys.argv[1:])
install_module._validate_repository = lambda _repo: None
real_fsync = os.fsync
exited = False
def exit_after_staged_fsync(fd):
    global exited
    real_fsync(fd)
    staged = tuple(config.rglob("*.link")) if config.exists() else ()
    try:
        synced_path = Path(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        synced_path = None
    if staged and synced_path == staged[0].parent and not exited:
        exited = True
        os._exit(79)
install_module.os.fsync = exit_after_staged_fsync
install_module.install_opencode(repo, config, state)
"""
            stopped = subprocess.run(
                [sys.executable, "-c", script, str(repo), str(config), str(state)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(stopped.returncode, 79, stopped.stderr)
            staged_paths = tuple(config.rglob("*.link"))
            self.assertEqual(len(staged_paths), 1)
            interrupted = receipt(state)
            staged_record = next(
                link
                for link in interrupted["links"]
                if link.get("staged_destination") == str(staged_paths[0])
            )
            self.assertEqual(
                (
                    staged_record["destination_dev"],
                    staged_record["destination_ino"],
                ),
                (
                    staged_paths[0].lstat().st_dev,
                    staged_paths[0].lstat().st_ino,
                ),
            )

            install_opencode(repo, config, state)
            self.assertFalse(any(config.rglob("*.link")))
            install_opencode(repo, config, state)

            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())

    def test_staged_crash_uninstall_does_not_adopt_same_target_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            script = """
import os
import sys
from pathlib import Path
import scripts.install as install_module

repo, config, state = map(Path, sys.argv[1:])
install_module._validate_repository = lambda _repo: None
real_fsync = os.fsync
def exit_after_staged_fsync(fd):
    real_fsync(fd)
    staged = tuple(config.rglob("*.link")) if config.exists() else ()
    try:
        synced_path = Path(os.readlink(f"/proc/self/fd/{fd}"))
    except OSError:
        synced_path = None
    if staged and synced_path == staged[0].parent:
        os._exit(79)
install_module.os.fsync = exit_after_staged_fsync
install_module.install_opencode(repo, config, state)
"""
            stopped = subprocess.run(
                [sys.executable, "-c", script, str(repo), str(config), str(state)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(stopped.returncode, 79, stopped.stderr)
            staged = next(config.rglob("*.link"))
            interrupted = receipt(state)
            recorded = next(
                link
                for link in interrupted["links"]
                if link.get("staged_destination") == str(staged)
            )
            source = Path(recorded["source"])
            staged.unlink()
            staged.symlink_to(source)
            replacement_identity = (staged.lstat().st_dev, staged.lstat().st_ino)
            self.assertNotEqual(
                replacement_identity,
                (recorded["destination_dev"], recorded["destination_ino"]),
            )

            install_opencode(repo, config, state)
            committed = receipt(state)
            final = Path(recorded["destination"])
            committed_link = next(
                link
                for link in committed["links"]
                if link["destination"] == str(final)
            )
            self.assertTrue(final.is_symlink())
            self.assertNotEqual(
                (
                    committed_link["destination_dev"],
                    committed_link["destination_ino"],
                ),
                replacement_identity,
            )
            self.assertTrue(staged.is_symlink())
            self.assertEqual(
                (staged.lstat().st_dev, staged.lstat().st_ino),
                replacement_identity,
            )

            uninstall_opencode(repo, config, state)

            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())
            self.assertTrue(staged.is_symlink())
            self.assertEqual(
                (staged.lstat().st_dev, staged.lstat().st_ino),
                replacement_identity,
            )

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
                skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                    collision = install_module._receipt_retirement_private_path(
                        path,
                        canonical_identity,
                        TEST_RETIREMENT_SECRET,
                    )
                    collision.write_text("foreign collision\n", encoding="utf-8")
                    real_unlink(path)

                with mock.patch.object(
                    install_module, "_unlink_state_path", side_effect=collide
                ):
                    with self.assertRaisesRegex(
                        install_module.InstallError, "private name|sidecar"
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
                    if source_name == canonical.name and target_name.endswith(".placeholder"):
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
                        collision = install_module._receipt_retirement_private_path(
                            path,
                            canonical_identity,
                            TEST_RETIREMENT_SECRET,
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
                            ".placeholder"
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
                    and target_name.endswith(".placeholder")
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
            self.assertEqual(len(tuple(final_receipt.parent.glob("*.retire"))), 1)
            uninstall_opencode(repo, config, state)
            self.assertFalse(final_receipt.exists())
            self.assertEqual(tuple(final_receipt.parent.glob("*.retire")), ())

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
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                    skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                    if (
                        source_name == final_receipt.name
                        and target_name.endswith(".placeholder")
                        and not injected
                    ):
                        injected = True
                        raise SystemExit("after receipt quarantine rename")

                with mock.patch.object(
                    install_module,
                    "_renameat_noreplace",
                    side_effect=stop_after_receipt_rename,
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)

                private = next(final_receipt.parent.glob("*.retire"))
                displaced: Path | None = None
                if replacement == "canonical":
                    foreign = final_receipt
                else:
                    displaced = private.with_name("displaced-owned-receipt")
                    private.rename(displaced)
                    foreign = private
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
                    with self.assertRaises(install_module.InstallError):
                        uninstall_opencode(repo, config, state)

                self.assertGreater(fsync_calls, 0)
                self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign receipt path\n")
                self.assertEqual((foreign.stat().st_dev, foreign.stat().st_ino), foreign_identity)
                if displaced is not None:
                    self.assertTrue(displaced.is_file())

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
            real_unlink = install_module._retire_owned_object
            injected = False

            def fail_after_migrated_unlink(
                destination: Path,
                identity: tuple[int, int],
                role: str,
                *,
                directory: bool,
            ) -> bool:
                nonlocal injected
                removed = real_unlink(
                    destination, identity, role, directory=directory
                )
                if destination == victim.destination and not injected:
                    injected = True
                    raise install_module.InstallError("after migrated unlink durability")
                return removed

            with mock.patch.object(
                install_module,
                "_retire_owned_object",
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
                    token = install_module._retirement_token(
                        payload["lineage"], "final-link", "leaf", destination, *expected,
                        secret=TEST_RETIREMENT_SECRET,
                    )
                    quarantine = install_module._retirement_private_path(
                        destination, token, "leaf"
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
                            and target_name.endswith(".placeholder")
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

                    entrypoint = install_opencode if retry == "install" else uninstall_opencode
                    with self.assertRaises(install_module.InstallError):
                        entrypoint(repo, config, state)

                    self.assertTrue(quarantine.is_symlink())
                    self.assertTrue(destination.is_symlink())
                    self.assertEqual(os.readlink(destination), str(foreign))
                    self.assertEqual(
                        (destination.lstat().st_dev, destination.lstat().st_ino),
                        foreign_identity,
                    )
                    self.assertTrue(receipt_path(state).exists())

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
                    token = install_module._retirement_token(
                        payload["lineage"], "staged-link", "leaf", staging, *expected,
                        secret=TEST_RETIREMENT_SECRET,
                    )
                    quarantine = install_module._retirement_private_path(
                        staging, token, "leaf"
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
                            and target_name.endswith(".placeholder")
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
                    token = install_module._retirement_token(
                        payload["lineage"], "anchor", "leaf", anchor, *expected,
                        secret=TEST_RETIREMENT_SECRET,
                    )
                    quarantine = install_module._retirement_private_path(
                        anchor, token, "leaf"
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
                            and target_name.endswith(".placeholder")
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

                    self.assertEqual(
                        (quarantine.stat().st_dev, quarantine.stat().st_ino), expected
                    )
                    anchor.write_text("foreign anchor\n", encoding="utf-8")
                    foreign_identity = (anchor.stat().st_dev, anchor.stat().st_ino)

                    entrypoint = install_opencode if retry == "install" else uninstall_opencode
                    with self.assertRaises(install_module.InstallError):
                        entrypoint(repo, config, state)

                    self.assertTrue(quarantine.is_file())
                    self.assertEqual(anchor.read_text(encoding="utf-8"), "foreign anchor\n")
                    self.assertEqual((anchor.stat().st_dev, anchor.stat().st_ino), foreign_identity)
                    self.assertTrue(receipt_path(state).exists())

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
                role = "final-link" if operation == "link" else "anchor"
                token = install_module._retirement_token(
                    payload["lineage"], role, "leaf", owned_path, *expected,
                    secret=TEST_RETIREMENT_SECRET,
                )
                quarantine = install_module._retirement_private_path(
                    owned_path, token, "leaf"
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
                        and target_name.endswith(".placeholder")
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

                with self.assertRaises(install_module.InstallError):
                    uninstall_opencode(repo, config, state)

                self.assertTrue(quarantine.is_symlink())
                self.assertEqual(os.readlink(quarantine), str(foreign))
                self.assertEqual(
                    (quarantine.lstat().st_dev, quarantine.lstat().st_ino),
                    foreign_identity,
                )
                self.assertTrue(receipt_path(state).is_file())

    def test_ordinary_failure_after_retired_pruning_does_not_recreate_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            retired_skill = config / "skills/unslop"
            retired_command = config / "commands/unslop.md"
            shutil.rmtree(repo / "plugins/expskill/skills/unslop")
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
            shutil.rmtree(repo / "plugins/expskill/skills/unslop")
            real_unlink = install_module._retire_owned_object
            failed = False

            def fail_after_exact_unlink(
                path: Path,
                identity: tuple[int, int],
                role: str,
                *,
                directory: bool,
            ) -> bool:
                nonlocal failed
                removed = real_unlink(path, identity, role, directory=directory)
                if path == retired_skill and not failed:
                    failed = True
                    raise install_module.InstallError("after retired exact unlink")
                return removed

            with (
                mock.patch.object(install_module, "_validate_repository"),
                mock.patch.object(
                    install_module,
                    "_retire_owned_object",
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
                    and target_name.endswith(".placeholder")
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
            quarantine = Path(interrupted["pending_retirement"]["private"])
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
            shutil.rmtree(repo / "plugins/expskill/skills/unslop")
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
            published_artifact = Path(pending["pending_publish"]["artifact"])
            self.assertTrue(pending_anchor.is_file())
            self.assertEqual(
                pending_anchor.stat().st_ino,
                (published_artifact / "package.json").stat().st_ino,
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
            replacement = destination.with_name(f"{destination.name}.replacement")
            replacement.symlink_to(source)
            if (replacement.lstat().st_dev, replacement.lstat().st_ino) == old_identity:
                # The filesystem may immediately reuse an unlinked inode. Keep
                # that inode occupied while creating the actual replacement so
                # this test proves a different symlink identity deterministically.
                alternate = destination.with_name(f"{destination.name}.replacement-2")
                alternate.symlink_to(source)
                replacement.unlink()
                replacement = alternate
            replacement.rename(destination)
            self.assertNotEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino), old_identity
            )

            uninstall_opencode(repo, config, state)

            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertFalse(receipt_path(state).exists())
            self.assertEqual(link_anchor_paths(config), [])

    def test_uninstall_preserves_immediate_same_inode_recreated_symlink(self) -> None:
        """An ABA pathname replacement must not inherit receipt ownership."""
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
            replacement_identity = (
                destination.lstat().st_dev,
                destination.lstat().st_ino,
            )

            # Simulate the filesystem's immediate same-inode ABA reuse
            # deterministically while preserving the foreign replacement's
            # actual inode on disk.  The installed receipt retains a private
            # hard-link anchor, so the public pathname must still prove that
            # it is the anchored inode before teardown may retire it.
            real_stat = install_module.os.stat
            retired: list[Path] = []

            def same_inode_stat(path: object, *args: object, **kwargs: object) -> object:
                observed = real_stat(path, *args, **kwargs)
                if (
                    kwargs.get("follow_symlinks") is False
                    and kwargs.get("dir_fd") is not None
                    and path == destination.name
                ):
                    values = list(observed)
                    values[1] = old_identity[1]
                    values[2] = old_identity[0]
                    return os.stat_result(values)
                return observed

            def record_retirement(
                path: Path,
                expected: tuple[int, int],
                role: str,
                **kwargs: object,
            ) -> bool:
                if path == destination:
                    retired.append(path)
                    return False
                return real_retire(path, expected, role, **kwargs)

            real_retire = install_module._retire_owned_object
            with mock.patch.object(install_module.os, "stat", side_effect=same_inode_stat), mock.patch.object(
                install_module,
                "_retire_owned_object",
                side_effect=record_retirement,
            ):
                uninstall_opencode(repo, config, state)

            self.assertEqual(retired, [])
            self.assertTrue(destination.is_symlink())
            self.assertEqual(os.readlink(destination), str(source))
            self.assertEqual(
                (destination.lstat().st_dev, destination.lstat().st_ino),
                replacement_identity,
            )
            self.assertFalse(receipt_path(state).exists())
            self.assertEqual(link_anchor_paths(config), [])

    def test_uninstall_preserves_foreign_replacement_at_private_link_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            payload = receipt(state)
            owned = payload["links"][0]
            destination = Path(owned["destination"])
            anchor = Path(owned["link_anchor"])
            anchor.unlink()
            anchor.write_text("foreign-anchor\n", encoding="utf-8")

            uninstall_opencode(repo, config, state)

            self.assertEqual(anchor.read_text(encoding="utf-8"), "foreign-anchor\n")
            self.assertTrue(destination.is_symlink())
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
            shutil.rmtree(repo / "plugins/expskill/skills/unslop")

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
                skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                                and target_name.endswith(".placeholder")
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
                            real_publish = install_module._publish_workspace_candidate

                            def fail_initial_publication(
                                workspace: Path,
                                workspace_identity: tuple[int, int],
                                candidate: Path,
                                candidate_identity: tuple[int, int],
                                artifact: Path,
                            ) -> None:
                                if artifact.name == "opencode-artifact":
                                    raise install_module.InstallError(
                                        "initial publication failed"
                                    )
                                real_publish(
                                    workspace,
                                    workspace_identity,
                                    candidate,
                                    candidate_identity,
                                    artifact,
                                )

                            patches.append(
                                mock.patch.object(
                                    install_module,
                                    "_publish_workspace_candidate",
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
                        quarantines = tuple(final_receipt.parent.glob("*.retire"))
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
                            tuple(final_receipt.parent.glob("*.retire")), ()
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
                    skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
                        skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
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

    def test_initial_rollback_retries_after_package_child_destruction(self) -> None:
        for retry_entrypoint in ("install", "uninstall"):
            with (
                self.subTest(retry_entrypoint=retry_entrypoint),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                real_remove = install_module._remove_opencode_artifact_exact
                interrupted = False

                def stop_after_child_removal(path: Path, dev: int, ino: int) -> None:
                    nonlocal interrupted
                    if path.name == "opencode-artifact" and not interrupted:
                        interrupted = True
                        (path / "package.json").unlink()
                        raise SystemExit("after rollback package.json removal")
                    real_remove(path, dev, ino)

                with (
                    mock.patch.object(
                        install_module,
                        "preflight_opencode_links",
                        side_effect=install_module.InstallError("preflight failed"),
                    ),
                    mock.patch.object(
                        install_module,
                        "_remove_opencode_artifact_exact",
                        side_effect=stop_after_child_removal,
                    ),
                ):
                    with self.assertRaises(SystemExit):
                        install_opencode(repo, config, state)

                self.assertTrue(interrupted)
                pending = receipt(state)["pending_publish"]
                self.assertEqual(pending["phase"], "rollback-prepared")
                if retry_entrypoint == "install":
                    install_opencode(repo, config, state)
                    uninstall_opencode(repo, config, state)
                else:
                    uninstall_opencode(repo, config, state)
                self.assertFalse(receipt_path(state).exists())
                self.assertFalse((state / "expskill/opencode-artifact").exists())
                self.assertEqual(
                    tuple((state / "expskill").glob(".opencode-artifact.anchor-*.delete-*")),
                    (),
                )

    def test_receipt_replacement_before_delete_rename_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            canonical = receipt_path(state)
            owned_identity = (canonical.stat().st_dev, canonical.stat().st_ino)
            displaced = canonical.with_name("displaced-owned-receipt")
            real_write = install_module._write_receipt_retirement_sidecar
            raced = False

            def replace_before_rename(sidecar: Path, payload: object) -> None:
                nonlocal raced
                real_write(sidecar, payload)
                if (
                    not raced
                    and isinstance(payload, dict)
                    and payload.get("phase") == "prepared"
                ):
                    raced = True
                    canonical.rename(displaced)
                    canonical.write_text("foreign receipt\n", encoding="utf-8")

            with mock.patch.object(
                install_module,
                "_write_receipt_retirement_sidecar",
                side_effect=replace_before_rename,
            ):
                with self.assertRaises(install_module.InstallError):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(raced)
            foreign_identity = (canonical.lstat().st_dev, canonical.lstat().st_ino)
            self.assertEqual(canonical.read_text(encoding="utf-8"), "foreign receipt\n")
            self.assertTrue(displaced.is_file())
            self.assertEqual(len(tuple(canonical.parent.glob("*.journal"))), 1)

            canonical.unlink()
            displaced.rename(canonical)
            uninstall_opencode(repo, config, state)
            self.assertFalse(canonical.exists())
            self.assertFalse(tuple(canonical.parent.glob("*.journal")))
            self.assertNotEqual(foreign_identity, owned_identity)

    def test_quarantined_receipt_replacement_before_recovery_rename_is_restored(
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
                real_rename = install_module._renameat_noreplace
                stopped = False

                def stop_after_initial_rename(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal stopped
                    real_rename(source_fd, source_name, target_fd, target_name)
                    if (
                        source_name == canonical.name
                        and target_name.endswith(".placeholder")
                        and not stopped
                    ):
                        stopped = True
                        raise SystemExit("after receipt quarantine rename")

                with mock.patch.object(
                    install_module,
                    "_renameat_noreplace",
                    side_effect=stop_after_initial_rename,
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)

                private = next(canonical.parent.glob("*.retire"))
                journal = next(canonical.parent.glob("*.journal"))
                displaced = private.with_name("displaced-owned-receipt")
                private.rename(displaced)
                private.write_text("foreign private\n", encoding="utf-8")
                foreign_identity = (private.stat().st_dev, private.stat().st_ino)

                if retry_entrypoint == "install":
                    with self.assertRaises(install_module.InstallError):
                        install_opencode(repo, config, state)
                else:
                    with self.assertRaises(install_module.InstallError):
                        uninstall_opencode(repo, config, state)

                self.assertFalse(canonical.exists())
                self.assertEqual(
                    private.read_text(encoding="utf-8"), "foreign private\n"
                )
                self.assertEqual(
                    (private.stat().st_dev, private.stat().st_ino), foreign_identity
                )
                self.assertTrue(journal.is_file())
                private.unlink()
                displaced.rename(private)
                if retry_entrypoint == "install":
                    install_opencode(repo, config, state)
                    uninstall_opencode(repo, config, state)
                else:
                    uninstall_opencode(repo, config, state)
                self.assertFalse(canonical.exists())
                self.assertFalse(private.exists())
                self.assertFalse(journal.exists())

    def test_upgrade_rollback_retries_after_candidate_child_destruction(self) -> None:
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
                skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
                skill.write_text(
                    skill.read_text(encoding="utf-8").replace(
                        "Cut AI tells", "upgrade rollback destruction marker"
                    ),
                    encoding="utf-8",
                )
                real_remove = install_module._remove_opencode_artifact_exact
                interrupted = False

                def stop_after_child_removal(path: Path, dev: int, ino: int) -> None:
                    nonlocal interrupted
                    if path.name == "opencode-artifact" and not interrupted:
                        interrupted = True
                        (path / "package.json").unlink()
                        raise SystemExit("after upgrade rollback package.json removal")
                    real_remove(path, dev, ino)

                with (
                    mock.patch.object(
                        install_module,
                        "preflight_opencode_links",
                        side_effect=install_module.InstallError("preflight failed"),
                    ),
                    mock.patch.object(
                        install_module,
                        "_remove_opencode_artifact_exact",
                        side_effect=stop_after_child_removal,
                    ),
                ):
                    with self.assertRaises(SystemExit):
                        install_opencode(repo, config, state)

                self.assertTrue(interrupted)
                pending = receipt(state)["pending_swap"]
                self.assertEqual(pending["phase"], "rollback-prepared")
                self.assertTrue(Path(pending["backup"]).is_dir())
                if retry_entrypoint == "install":
                    install_opencode(repo, config, state)
                    uninstall_opencode(repo, config, state)
                else:
                    uninstall_opencode(repo, config, state)
                self.assertFalse(receipt_path(state).exists())
                self.assertFalse((state / "expskill/opencode-artifact").exists())

    def test_anchor_retirement_exchange_is_receipt_journaled_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            real_rename = install_module._renameat_exchange
            interrupted = False

            def stop_after_private_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal interrupted
                real_rename(source_fd, source_name, target_fd, target_name)
                if (
                    source_name.startswith(".opencode-artifact.anchor-")
                    and target_name.endswith(".retire")
                    and not interrupted
                ):
                    interrupted = True
                    raise SystemExit("after journaled anchor exchange")

            with mock.patch.object(
                install_module, "_renameat_exchange", side_effect=stop_after_private_exchange
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(interrupted)
            pending = receipt(state)["pending_retirement"]
            self.assertEqual(pending["role"], "anchor")
            self.assertEqual(pending["phase"], "prepared")
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())

    def test_prepublication_candidate_exchange_enters_durable_swap_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "candidate exchange rollback marker"
                ),
                encoding="utf-8",
            )
            real_publish = install_module._publish_workspace_candidate
            interrupted = False

            def stop_after_candidate_exchange(
                workspace: Path,
                workspace_identity: tuple[int, int],
                candidate: Path,
                candidate_identity: tuple[int, int],
                artifact: Path,
            ) -> None:
                nonlocal interrupted
                real_publish(
                    workspace,
                    workspace_identity,
                    candidate,
                    candidate_identity,
                    artifact,
                )
                if artifact.name == "opencode-artifact" and not interrupted:
                    interrupted = True
                    raise SystemExit("after candidate directory exchange")

            with mock.patch.object(
                install_module,
                "_publish_workspace_candidate",
                side_effect=stop_after_candidate_exchange,
            ):
                with self.assertRaises(SystemExit):
                    install_opencode(repo, config, state)

            self.assertTrue(interrupted)
            observed_rollback = False
            real_remove = install_module._remove_opencode_artifact_exact

            def observe_candidate_retirement(
                path: Path, dev: int, ino: int
            ) -> None:
                nonlocal observed_rollback
                if path.name == "opencode-artifact":
                    observed_rollback = (
                        receipt(state)["pending_swap"]["phase"]
                        == "rollback-prepared"
                    )
                real_remove(path, dev, ino)

            with mock.patch.object(
                install_module,
                "_remove_opencode_artifact_exact",
                side_effect=observe_candidate_retirement,
            ):
                install_opencode(repo, config, state)
            self.assertTrue(observed_rollback)
            self.assertNotIn("pending_swap", receipt(state))

    def test_nested_stat_disappearance_retains_receipt_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            real_scandir = install_module.os.scandir
            injected = False

            class VanishedEntry:
                def __init__(self, entry: os.DirEntry[str]) -> None:
                    self.name = entry.name

                def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
                    nonlocal injected
                    injected = True
                    raise FileNotFoundError(self.name)

            def disappear_between_scan_and_stat(directory_fd: int) -> object:
                entries = list(real_scandir(directory_fd))
                if not injected and entries:
                    entries[0] = VanishedEntry(entries[0])  # type: ignore[assignment]
                return iter(entries)

            with mock.patch.object(
                install_module.os,
                "scandir",
                side_effect=disappear_between_scan_and_stat,
            ):
                with self.assertRaises(install_module.InstallError):
                    uninstall_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertTrue(receipt_path(state).is_file())
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse((state / "expskill/opencode-artifact").exists())

    def test_failed_install_leaves_new_empty_state_directories_unretired(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"

            with (
                mock.patch.object(
                    install_module,
                    "_ensure_opencode_artifact",
                    side_effect=install_module.InstallError("injected initial failure"),
                ),
                mock.patch.object(
                    install_module,
                    "_rmdir_exact_via_exchange",
                    side_effect=SystemExit("state directory retirement is forbidden"),
                ),
            ):
                with self.assertRaisesRegex(
                    install_module.InstallError, "injected initial failure"
                ):
                    install_opencode(repo, config, state)

            self.assertTrue((state / "expskill").is_dir())
            install_opencode(repo, config, state)
            self.assertEqual(receipt(state)["teardown_phase"], "committed")

    def test_receipt_recovery_rejects_arbitrary_self_consistent_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            canonical = receipt_path(state)
            identity = (canonical.stat().st_dev, canonical.stat().st_ino)
            forged_secret = "f" * (
                install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2
            )
            forged = install_module._receipt_retirement_payload(
                canonical, identity, forged_secret, "prepared"
            )
            arbitrary = Path(str(forged["sidecar"]))
            arbitrary.write_text(json.dumps(forged), encoding="utf-8")

            with self.assertRaisesRegex(
                install_module.InstallError, "receipt authority"
            ):
                install_opencode(repo, config, state)
            self.assertTrue(arbitrary.is_file())
            self.assertTrue(canonical.is_file())

    def test_retirement_entry_serialization_has_closed_roles_and_phases(self) -> None:
        lineage = "1" * 32
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for role, kind in (
                ("final-link", "leaf"),
                ("staged-link", "leaf"),
                ("anchor", "leaf"),
                ("artifact", "directory"),
                ("candidate", "directory"),
                ("backup", "directory"),
            ):
                for source_kind in ("public", "quarantine"):
                    for phase in ("prepared", "exchanged", "reclaimed", "done"):
                        with self.subTest(
                            role=role, source_kind=source_kind, phase=phase
                        ):
                            source = parent / f"{role}-{source_kind}-source"
                            token = install_module._retirement_token(
                                lineage, role, kind, source, 11, 12,
                                secret=TEST_RETIREMENT_SECRET,
                            )
                            private = install_module._retirement_private_path(
                                source, token, kind
                            )
                            entry = install_module._PendingRetirement(
                                role=role,
                                kind=kind,
                                source=source,
                                private=private,
                                expected_dev=11,
                                expected_ino=12,
                                placeholder_dev=13 if phase == "prepared" else None,
                                placeholder_ino=14 if phase == "prepared" else None,
                                lineage=lineage,
                                token=token,
                                phase=phase,
                                source_kind=source_kind,
                                secret=TEST_RETIREMENT_SECRET,
                            )
                            encoded = install_module._pending_retirement_payload(entry)
                            self.assertEqual(
                                install_module._pending_retirement_from_payload(
                                    encoded, lineage
                                ),
                                entry,
                            )
            for key, invalid in (
                ("role", "receipt"),
                ("kind", "other"),
                ("phase", "unknown"),
                ("source_kind", "other"),
                ("token", "f" * 32),
            ):
                with self.subTest(invalid=key):
                    malformed = {**encoded, key: invalid}
                    with self.assertRaises(install_module.InstallError):
                        install_module._pending_retirement_from_payload(
                            malformed, lineage
                        )
            with self.assertRaises(install_module.InstallError):
                install_module._pending_retirement_from_payload(
                    {**encoded, "unexpected": True}, lineage
                )
            with self.assertRaises(install_module.InstallError):
                install_module._pending_retirement_from_payload(
                    {**encoded, "placeholder_dev": 13}, lineage
                )

    def test_each_retirement_receipt_phase_boundary_recovers_before_teardown(self) -> None:
        for boundary in ("prepared", "exchanged", "reclaimed", "done"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                real_rewrite = install_module._rewrite_receipt_retirement
                interrupted = False

                def stop_after_boundary(
                    path: Path,
                    pending: install_module._PendingRetirement | None,
                ) -> None:
                    nonlocal interrupted
                    real_rewrite(path, pending)
                    if (
                        pending is not None
                        and pending.phase == boundary
                        and not interrupted
                    ):
                        interrupted = True
                        raise SystemExit(f"after retirement {boundary} write")

                with mock.patch.object(
                    install_module,
                    "_rewrite_receipt_retirement",
                    side_effect=stop_after_boundary,
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)

                self.assertTrue(interrupted)
                self.assertEqual(
                    receipt(state)["pending_retirement"]["phase"], boundary
                )
                uninstall_opencode(repo, config, state)
                self.assertFalse(receipt_path(state).exists())

    def test_retirement_authority_is_secret_and_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "owned"
            source.write_text("owned\n", encoding="utf-8")
            lineage = "a" * 32
            expected = (source.stat().st_dev, source.stat().st_ino)
            secret = "b" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)
            token = install_module._retirement_token(
                lineage,
                "final-link",
                "leaf",
                source,
                *expected,
                secret=secret,
            )
            pending = install_module._PendingRetirement(
                role="final-link",
                kind="leaf",
                source=source,
                private=install_module._retirement_private_path(
                    source, token, "leaf"
                ),
                expected_dev=expected[0],
                expected_ino=expected[1],
                lineage=lineage,
                token=token,
                phase="prepared",
                secret=secret,
                placeholder_dev=13,
                placeholder_ino=14,
            )
            encoded = install_module._pending_retirement_payload(pending)
            self.assertNotIn(lineage, str(pending.private))
            observable_token = install_module._retirement_token(
                lineage,
                "final-link",
                "leaf",
                source,
                *expected,
                secret="c" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2),
            )
            self.assertNotEqual(
                pending.private,
                install_module._retirement_private_path(
                    source, observable_token, "leaf"
                ),
            )
            self.assertEqual(
                install_module._pending_retirement_from_payload(
                    encoded, lineage, require_secret=True
                ),
                pending,
            )
            for field, replacement in (
                ("secret", "c" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)),
                ("token", "d" * (install_module.OPENCODE_RETIREMENT_TOKEN_BYTES * 2)),
                ("auth", "e" * 64),
                ("phase", "exchanged"),
                ("source_kind", "quarantine"),
                ("placeholder_dev", 15),
            ):
                with self.subTest(field=field):
                    forged = {**encoded, field: replacement}
                    with self.assertRaises(install_module.InstallError):
                        install_module._pending_retirement_from_payload(
                            forged, lineage, require_secret=True
                        )

    def test_ordinary_receipt_rewrite_recovers_every_private_boundary(self) -> None:
        secret = "9" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)
        for boundary in ("write", "exchange", "fsync", "reclaim"):
            for moment in ("before", "after"):
                with (
                    self.subTest(boundary=boundary, moment=moment),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    state_directory = Path(temporary) / "state" / "expskill"
                    state_directory.mkdir(parents=True)
                    receipt_file = state_directory / "install-opencode.json"
                    receipt_file.write_text(
                        json.dumps(
                            {
                                "generation": "original",
                                "receipt_secret": secret,
                            }
                        ),
                        encoding="utf-8",
                    )
                    payload = {
                        "generation": "next",
                        "receipt_secret": secret,
                    }
                    binding = install_module._open_state_binding(
                        state_directory, create=False
                    )
                    self.assertIsNotNone(binding)
                    assert binding is not None
                    key = str(binding.directory)
                    install_module._STATE_BINDINGS[key] = binding
                    real_write = install_module._write_named_state_generation
                    real_exchange = install_module._renameat_exchange
                    real_fsync = os.fsync
                    real_reclaim = install_module._unlink_private_state_inode
                    triggered = False
                    exchanged = False

                    def interrupt(action: str, call: object) -> object:
                        nonlocal triggered
                        if boundary != action or triggered:
                            return call()
                        triggered = True
                        if moment == "before":
                            raise SystemExit(f"before ordinary receipt {action}")
                        result = call()
                        raise SystemExit(f"after ordinary receipt {action}")

                    def write_generation(
                        bound: object, name_from_identity: object, encoded: bytes
                    ) -> tuple[str, tuple[int, int]]:
                        return interrupt(
                            "write",
                            lambda: real_write(bound, name_from_identity, encoded),
                        )  # type: ignore[return-value]

                    def exchange_generation(
                        source_fd: int,
                        source_name: str,
                        target_fd: int,
                        target_name: str,
                    ) -> None:
                        nonlocal exchanged
                        if source_name.startswith(
                            install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                        ):
                            def perform() -> None:
                                nonlocal exchanged
                                real_exchange(
                                    source_fd, source_name, target_fd, target_name
                                )
                                exchanged = True

                            interrupt("exchange", perform)
                            return
                        real_exchange(source_fd, source_name, target_fd, target_name)

                    def sync_generation(descriptor: int) -> None:
                        if exchanged and descriptor == binding.directory_fd:
                            interrupt("fsync", lambda: real_fsync(descriptor))
                            return
                        real_fsync(descriptor)

                    def reclaim_generation(
                        bound: object,
                        name: str,
                        expected: tuple[int, int],
                        label: str,
                        **kwargs: object,
                    ) -> None:
                        call = lambda: real_reclaim(
                            bound, name, expected, label, **kwargs
                        )
                        if label == "receipt generation":
                            interrupt("reclaim", call)
                            return
                        call()

                    try:
                        with (
                            mock.patch.object(
                                install_module,
                                "_write_named_state_generation",
                                side_effect=write_generation,
                            ),
                            mock.patch.object(
                                install_module,
                                "_renameat_exchange",
                                side_effect=exchange_generation,
                            ),
                            mock.patch.object(
                                install_module.os,
                                "fsync",
                                side_effect=sync_generation,
                            ),
                            mock.patch.object(
                                install_module,
                                "_unlink_private_state_inode",
                                side_effect=reclaim_generation,
                            ),
                        ):
                            with self.assertRaises(SystemExit):
                                install_module._write_state_payload(
                                    receipt_file, payload
                                )
                        self.assertTrue(triggered)
                        install_module._write_state_payload(receipt_file, payload)
                        self.assertEqual(
                            json.loads(receipt_file.read_text(encoding="utf-8")),
                            payload,
                        )
                        self.assertFalse(
                            tuple(
                                state_directory.glob(
                                    ".install-opencode.json.receipt-*.retire"
                                )
                            )
                        )
                        self.assertFalse(
                            any(path.name.endswith(".tmp") for path in state_directory.iterdir())
                        )
                    finally:
                        install_module._STATE_BINDINGS.pop(key, None)
                        install_module._close_state_binding(binding)

    def test_ordinary_receipt_replacement_before_reclaim_is_never_adopted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            canonical = receipt_path(state)
            old_identity = (canonical.stat().st_dev, canonical.stat().st_ino)
            detached_published = root / "detached-published-receipt.json"
            real_reclaim = install_module._unlink_private_state_inode
            replacement_identity: tuple[int, int] | None = None
            injected = False

            def replace_public_before_reclaim(
                binding: object,
                name: str,
                expected: tuple[int, int],
                label: str,
                **kwargs: object,
            ) -> None:
                nonlocal injected, replacement_identity
                if label == "receipt generation" and not injected:
                    injected = True
                    published_bytes = canonical.read_bytes()
                    canonical.rename(detached_published)
                    descriptor = os.open(
                        canonical,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    try:
                        os.write(descriptor, published_bytes)
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    replacement_identity = (
                        canonical.stat().st_dev,
                        canonical.stat().st_ino,
                    )
                real_reclaim(binding, name, expected, label, **kwargs)

            with mock.patch.object(
                install_module,
                "_unlink_private_state_inode",
                side_effect=replace_public_before_reclaim,
            ):
                with self.assertRaises(install_module.InstallError):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            self.assertIsNotNone(replacement_identity)
            assert replacement_identity is not None
            self.assertEqual(canonical.read_bytes(), detached_published.read_bytes())
            self.assertEqual(
                (canonical.stat().st_dev, canonical.stat().st_ino),
                replacement_identity,
            )
            generations = tuple(
                canonical.parent.glob(".install-opencode.json.receipt-*.retire")
            )
            self.assertEqual(len(generations), 1)
            self.assertEqual(
                (generations[0].stat().st_dev, generations[0].stat().st_ino),
                old_identity,
            )

            for entrypoint in (install_opencode, uninstall_opencode):
                with self.subTest(entrypoint=entrypoint.__name__):
                    with self.assertRaises(install_module.InstallError):
                        entrypoint(repo, config, state)
                    self.assertEqual(
                        (canonical.stat().st_dev, canonical.stat().st_ino),
                        replacement_identity,
                    )
                    self.assertEqual(
                        (
                            generations[0].stat().st_dev,
                            generations[0].stat().st_ino,
                        ),
                        old_identity,
                    )

    def test_ordinary_receipt_pin_restores_authority_after_private_unlink(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            canonical = receipt_path(state)
            old_identity = (canonical.stat().st_dev, canonical.stat().st_ino)
            detached_published = root / "detached-after-unlink.json"
            real_unlink = os.unlink
            injected = False
            foreign_identity: tuple[int, int] | None = None

            def replace_after_private_unlink(
                path: str | bytes | Path,
                *args: object,
                **kwargs: object,
            ) -> None:
                nonlocal injected, foreign_identity
                real_unlink(path, *args, **kwargs)
                name = os.fsdecode(path)
                if (
                    not injected
                    and name.startswith(
                        install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                    )
                    and name.endswith(
                        install_module.OPENCODE_RECEIPT_GENERATION_SUFFIX
                    )
                ):
                    injected = True
                    published_bytes = canonical.read_bytes()
                    canonical.rename(detached_published)
                    canonical.write_bytes(published_bytes)
                    foreign_identity = (
                        canonical.stat().st_dev,
                        canonical.stat().st_ino,
                    )

            with mock.patch.object(
                install_module.os, "unlink", side_effect=replace_after_private_unlink
            ):
                with self.assertRaises(install_module.InstallError):
                    install_opencode(repo, config, state)

            self.assertTrue(injected)
            assert foreign_identity is not None
            self.assertEqual(
                (canonical.stat().st_dev, canonical.stat().st_ino),
                foreign_identity,
            )
            generations = tuple(
                canonical.parent.glob(".install-opencode.json.receipt-*.retire")
            )
            self.assertEqual(len(generations), 1)
            self.assertEqual(
                (generations[0].stat().st_dev, generations[0].stat().st_ino),
                old_identity,
            )
            self.assertFalse(
                tuple(
                    canonical.parent.glob(
                        ".install-opencode.json.receipt-*.retire.pin"
                    )
                )
            )

    def test_final_receipt_sidecar_recovers_every_low_level_boundary(self) -> None:
        boundaries = (
            "journal-write",
            "journal-fsync",
            "phase-prepared",
            "receipt-exchange",
            "receipt-fsync",
            "sidecar-stage-write",
            "sidecar-stage-exchange",
            "sidecar-stage-fsync",
            "phase-exchanged",
            "receipt-reclaim",
            "phase-reclaimed",
            "phase-done",
            "sidecar-reclaim",
        )
        for boundary in boundaries:
            for moment in ("before", "after"):
                with (
                    self.subTest(boundary=boundary, moment=moment),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    state_directory = root / "state" / "expskill"
                    state_directory.mkdir(parents=True)
                    receipt_file = state_directory / "install-opencode.json"
                    receipt_file.write_text(
                        json.dumps(
                            {
                                "links": [],
                                "marketplace_added": False,
                                "plugin_installed": True,
                                "repository_root": str(root),
                                "lineage": "8" * 32,
                                "teardown_phase": "anchor-removed",
                                "receipt_secret": TEST_RETIREMENT_SECRET,
                            }
                        ),
                        encoding="utf-8",
                    )
                    binding = install_module._open_state_binding(
                        state_directory, create=False
                    )
                    self.assertIsNotNone(binding)
                    assert binding is not None
                    key = str(binding.directory)
                    install_module._STATE_BINDINGS[key] = binding
                    real_phase_write = install_module._write_receipt_retirement_sidecar
                    real_generation_write = install_module._write_state_generation
                    real_named_write = install_module._write_named_state_generation
                    real_exchange = install_module._renameat_exchange
                    real_fsync = os.fsync
                    real_reclaim = install_module._unlink_private_state_inode
                    triggered = False
                    journal_written = False
                    receipt_exchanged = False
                    stage_exchanged = False

                    def interrupt(action: str, call: object) -> object:
                        nonlocal triggered
                        if boundary != action or triggered:
                            return call()
                        triggered = True
                        if moment == "before":
                            raise SystemExit(f"before final receipt {action}")
                        result = call()
                        raise SystemExit(f"after final receipt {action}")

                    def write_phase(sidecar: Path, payload: object) -> None:
                        phase = payload.get("phase") if isinstance(payload, dict) else None
                        action = f"phase-{phase}"
                        interrupt(action, lambda: real_phase_write(sidecar, payload))

                    def write_generation(
                        bound: object, name: str, encoded: bytes
                    ) -> tuple[int, int]:
                        nonlocal journal_written
                        if name.endswith(".journal"):
                            def perform_journal() -> tuple[int, int]:
                                nonlocal journal_written
                                result = real_generation_write(bound, name, encoded)
                                journal_written = True
                                return result

                            return interrupt(
                                "journal-write", perform_journal
                            )  # type: ignore[return-value]
                        return real_generation_write(bound, name, encoded)

                    def write_named_generation(
                        bound: object, name_from_identity: object, encoded: bytes
                    ) -> tuple[str, tuple[int, int]]:
                        return interrupt(
                            "sidecar-stage-write",
                            lambda: real_named_write(
                                bound, name_from_identity, encoded
                            ),
                        )  # type: ignore[return-value]

                    def exchange_sidecar(
                        source_fd: int,
                        source_name: str,
                        target_fd: int,
                        target_name: str,
                    ) -> None:
                        nonlocal receipt_exchanged, stage_exchanged
                        if source_name == receipt_file.name and target_name.endswith(
                            ".retire"
                        ):
                            def perform_receipt() -> None:
                                nonlocal receipt_exchanged
                                real_exchange(
                                    source_fd, source_name, target_fd, target_name
                                )
                                receipt_exchanged = True

                            interrupt("receipt-exchange", perform_receipt)
                            return
                        if ".journal.stage-" in source_name:
                            def perform() -> None:
                                nonlocal stage_exchanged
                                real_exchange(
                                    source_fd, source_name, target_fd, target_name
                                )
                                stage_exchanged = True

                            interrupt("sidecar-stage-exchange", perform)
                            return
                        real_exchange(
                            source_fd, source_name, target_fd, target_name
                        )

                    def sync_sidecar(descriptor: int) -> None:
                        nonlocal journal_written, receipt_exchanged, stage_exchanged
                        if descriptor == binding.directory_fd:
                            if receipt_exchanged:
                                interrupt("receipt-fsync", lambda: real_fsync(descriptor))
                                receipt_exchanged = False
                                return
                            if stage_exchanged:
                                interrupt(
                                    "sidecar-stage-fsync",
                                    lambda: real_fsync(descriptor),
                                )
                                stage_exchanged = False
                                return
                            if journal_written:
                                interrupt("journal-fsync", lambda: real_fsync(descriptor))
                                journal_written = False
                                return
                        real_fsync(descriptor)

                    def reclaim_sidecar(
                        bound: object,
                        name: str,
                        expected: tuple[int, int],
                        label: str,
                    ) -> None:
                        call = lambda: real_reclaim(bound, name, expected, label)
                        if label == "receipt retirement":
                            interrupt("receipt-reclaim", call)
                            return
                        if label == "receipt retirement sidecar" and name.endswith(
                            ".journal"
                        ):
                            interrupt("sidecar-reclaim", call)
                            return
                        call()

                    try:
                        with (
                            mock.patch.object(
                                install_module,
                                "_write_receipt_retirement_sidecar",
                                side_effect=write_phase,
                            ),
                            mock.patch.object(
                                install_module,
                                "_write_state_generation",
                                side_effect=write_generation,
                            ),
                            mock.patch.object(
                                install_module,
                                "_write_named_state_generation",
                                side_effect=write_named_generation,
                            ),
                            mock.patch.object(
                                install_module,
                                "_renameat_exchange",
                                side_effect=exchange_sidecar,
                            ),
                            mock.patch.object(
                                install_module.os,
                                "fsync",
                                side_effect=sync_sidecar,
                            ),
                            mock.patch.object(
                                install_module,
                                "_unlink_private_state_inode",
                                side_effect=reclaim_sidecar,
                            ),
                        ):
                            with self.assertRaises(SystemExit):
                                install_module._unlink_state_path(receipt_file)
                        self.assertTrue(triggered)
                        install_module._recover_receipt_deletion(
                            root, root / "config", root / "state", receipt_file
                        )
                        if receipt_file.exists():
                            install_module._unlink_state_path(receipt_file)
                        self.assertFalse(receipt_file.exists())
                        self.assertFalse(tuple(state_directory.glob("*.retire")))
                        self.assertFalse(tuple(state_directory.glob("*.journal*")))
                    finally:
                        install_module._STATE_BINDINGS.pop(key, None)
                        install_module._close_state_binding(binding)

    def test_final_receipt_retirement_recovers_at_every_sidecar_phase(self) -> None:
        """Every durable sidecar transition must be retryable after death."""

        for boundary in ("prepared", "exchanged", "reclaimed", "done"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_directory = root / "state" / "expskill"
                state_directory.mkdir(parents=True)
                receipt = state_directory / "install-opencode.json"
                receipt.write_text(
                    json.dumps(
                        {
                            "links": [],
                            "marketplace_added": False,
                            "plugin_installed": True,
                            "repository_root": str(root),
                            "lineage": "1" * 32,
                            "teardown_phase": "anchor-removed",
                            "receipt_secret": TEST_RETIREMENT_SECRET,
                        }
                    ),
                    encoding="utf-8",
                )
                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                real_write = install_module._write_receipt_retirement_sidecar
                interrupted = False

                def stop_after_phase(
                    sidecar: Path, payload: object
                ) -> None:
                    nonlocal interrupted
                    real_write(sidecar, payload)
                    if (
                        isinstance(payload, dict)
                        and payload.get("phase") == boundary
                        and not interrupted
                    ):
                        interrupted = True
                        raise SystemExit(f"after receipt sidecar {boundary}")

                try:
                    with mock.patch.object(
                        install_module,
                        "_write_receipt_retirement_sidecar",
                        side_effect=stop_after_phase,
                    ):
                        with self.assertRaises(SystemExit):
                            install_module._unlink_state_path(receipt)
                    self.assertTrue(interrupted)
                    journals = tuple(state_directory.glob("*.journal"))
                    self.assertEqual(len(journals), 1)
                    journal_payload = json.loads(
                        journals[0].read_text(encoding="utf-8")
                    )
                    self.assertEqual(journal_payload["phase"], boundary)
                    if boundary in {"prepared", "exchanged"}:
                        self.assertTrue(receipt.is_file())
                    else:
                        self.assertFalse(receipt.exists())

                    install_module._recover_receipt_deletion(
                        root, root / "config", root / "state", receipt
                    )
                    self.assertFalse(receipt.exists())
                    self.assertFalse(tuple(state_directory.glob("*.journal")))
                    self.assertFalse(
                        tuple(
                            state_directory.glob(
                                ".install-opencode.json.*.retire"
                            )
                        )
                    )
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

    def test_final_receipt_sidecar_tampering_is_rejected_and_preserved(self) -> None:
        for field in ("auth", "secret", "token", "extra"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_directory = root / "state" / "expskill"
                state_directory.mkdir(parents=True)
                receipt = state_directory / "install-opencode.json"
                receipt.write_text(
                    json.dumps(
                        {
                            "links": [],
                            "marketplace_added": False,
                            "plugin_installed": True,
                            "repository_root": str(root),
                            "lineage": "2" * 32,
                            "teardown_phase": "anchor-removed",
                            "receipt_secret": TEST_RETIREMENT_SECRET,
                        }
                    ),
                    encoding="utf-8",
                )
                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                real_rename = install_module._renameat_noreplace
                interrupted = False

                def stop_after_exchange(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal interrupted
                    real_rename(source_fd, source_name, target_fd, target_name)
                    if (
                        source_name == receipt.name
                        and target_name.endswith(".placeholder")
                        and not interrupted
                    ):
                        interrupted = True
                        raise SystemExit("after receipt sidecar exchange")

                try:
                    with mock.patch.object(
                        install_module,
                        "_renameat_noreplace",
                        side_effect=stop_after_exchange,
                    ):
                        with self.assertRaises(SystemExit):
                            install_module._unlink_state_path(receipt)
                    self.assertTrue(interrupted)
                    journals = tuple(state_directory.glob("*.journal"))
                    self.assertEqual(len(journals), 1)
                    private = next(state_directory.glob("*.retire"))
                    value = json.loads(journals[0].read_text(encoding="utf-8"))
                    if field == "auth":
                        value[field] = "f" * 64
                    elif field == "secret":
                        value[field] = "c" * 64
                    elif field == "token":
                        value[field] = "d" * 64
                    else:
                        value[field] = True
                    journals[0].write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(install_module.InstallError):
                        install_module._recover_receipt_deletion(
                            root, root / "config", root / "state", receipt
                        )
                    self.assertTrue(journals[0].is_file())
                    self.assertTrue(private.is_file())
                    self.assertFalse(receipt.exists())
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

    def test_final_receipt_sidecar_exchange_crash_retries_with_old_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_directory = root / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            receipt.write_text(
                json.dumps(
                    {
                        "links": [],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(root),
                        "lineage": "7" * 32,
                        "teardown_phase": "anchor-removed",
                        "receipt_secret": TEST_RETIREMENT_SECRET,
                    }
                ),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            real_exchange = install_module._renameat_exchange
            interrupted = False

            def crash_after_sidecar_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal interrupted
                staged_phase = None
                if ".stage-" in source_name:
                    staged_phase = json.loads(
                        (state_directory / source_name).read_text(encoding="utf-8")
                    ).get("phase")
                real_exchange(source_fd, source_name, target_fd, target_name)
                if (
                    not interrupted
                    and staged_phase == "exchanged"
                    and target_name.endswith(".journal")
                ):
                    interrupted = True
                    raise SystemExit("after sidecar generation exchange")

            try:
                with mock.patch.object(
                    install_module,
                    "_renameat_exchange",
                    side_effect=crash_after_sidecar_exchange,
                ):
                    with self.assertRaises(SystemExit):
                        install_module._unlink_state_path(receipt)
                self.assertTrue(interrupted)
                journal = next(state_directory.glob("*.journal"))
                stage = next(state_directory.glob(".*.journal.stage*"))
                self.assertEqual(
                    json.loads(journal.read_text(encoding="utf-8"))["phase"],
                    "exchanged",
                )
                self.assertEqual(
                    json.loads(stage.read_text(encoding="utf-8"))["phase"],
                    "prepared",
                )
                install_module._recover_receipt_deletion(
                    root, root / "config", root / "state", receipt
                )
                self.assertFalse(receipt.exists())
                self.assertFalse(tuple(state_directory.glob("*.journal")))
                self.assertFalse(tuple(state_directory.glob("*.retire")))
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_final_receipt_deterministic_private_and_journal_collisions_survive(self) -> None:
        fixed_secret = "a" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)
        for occupied in ("private", "journal"):
            with self.subTest(occupied=occupied), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_directory = root / "state" / "expskill"
                state_directory.mkdir(parents=True)
                receipt = state_directory / "install-opencode.json"
                receipt.write_text(
                    json.dumps(
                        {
                            "links": [],
                            "marketplace_added": False,
                            "plugin_installed": True,
                            "repository_root": str(root),
                            "lineage": "3" * 32,
                            "teardown_phase": "anchor-removed",
                            "receipt_secret": fixed_secret,
                        }
                    ),
                    encoding="utf-8",
                )
                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                expected = (receipt.stat().st_dev, receipt.stat().st_ino)
                private = install_module._receipt_retirement_private_path(
                    receipt, expected, fixed_secret
                )
                journal = install_module._receipt_retirement_sidecar_path(
                    receipt, expected, fixed_secret
                )
                target = private if occupied == "private" else journal
                target.write_text("foreign\n", encoding="utf-8")
                identity = (target.lstat().st_dev, target.lstat().st_ino)
                try:
                    with mock.patch.object(
                        install_module, "_retirement_secret", return_value=fixed_secret
                    ):
                        with self.assertRaises(install_module.InstallError):
                            install_module._unlink_state_path(receipt)
                    self.assertTrue(receipt.is_file())
                    self.assertEqual(
                        (target.lstat().st_dev, target.lstat().st_ino), identity
                    )
                    self.assertEqual(target.read_text(encoding="utf-8"), "foreign\n")
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

    def test_final_receipt_replacement_before_retirement_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_directory = root / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            receipt.write_text(
                json.dumps(
                    {
                        "links": [],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(root),
                        "lineage": "4" * 32,
                        "teardown_phase": "anchor-removed",
                        "receipt_secret": TEST_RETIREMENT_SECRET,
                    }
                ),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            detached = receipt.with_name("receipt-owned")
            real_write = install_module._write_receipt_retirement_sidecar
            replaced = False

            def replace_after_prepared(sidecar: Path, payload: object) -> None:
                nonlocal replaced
                real_write(sidecar, payload)
                if not replaced and isinstance(payload, dict) and payload.get("phase") == "prepared":
                    replaced = True
                    receipt.rename(detached)
                    receipt.write_text("foreign receipt\n", encoding="utf-8")

            try:
                with mock.patch.object(
                    install_module,
                    "_write_receipt_retirement_sidecar",
                    side_effect=replace_after_prepared,
                ):
                    with self.assertRaises(install_module.InstallError):
                        install_module._unlink_state_path(receipt)
                self.assertTrue(replaced)
                self.assertEqual(receipt.read_text(encoding="utf-8"), "foreign receipt\n")
                self.assertTrue(detached.is_file())
                self.assertTrue(tuple(state_directory.glob("*.journal")))
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_final_receipt_private_replacement_after_exchange_is_preserved(self) -> None:
        fixed_secret = "b" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_directory = root / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            receipt.write_text(
                json.dumps(
                    {
                        "links": [],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(root),
                        "lineage": "5" * 32,
                        "teardown_phase": "anchor-removed",
                        "receipt_secret": fixed_secret,
                    }
                ),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            expected = (receipt.stat().st_dev, receipt.stat().st_ino)
            private = install_module._receipt_retirement_private_path(
                receipt, expected, fixed_secret
            )
            detached = private.with_name(private.name + ".owned")
            real_rename = install_module._renameat_exchange
            replaced = False

            def replace_private(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal replaced
                real_rename(source_fd, source_name, target_fd, target_name)
                if (
                    not replaced
                    and source_name == receipt.name
                    and target_name == private.name
                ):
                    replaced = True
                    os.rename(private.name, detached.name, src_dir_fd=target_fd, dst_dir_fd=target_fd)
                    private.write_text("foreign private\n", encoding="utf-8")

            try:
                with mock.patch.object(
                    install_module, "_retirement_secret", return_value=fixed_secret
                ), mock.patch.object(
                    install_module, "_renameat_exchange", side_effect=replace_private
                ):
                    with self.assertRaises(install_module.InstallError):
                        install_module._unlink_state_path(receipt)
                self.assertTrue(replaced)
                self.assertEqual(receipt.read_text(encoding="utf-8"), "foreign private\n")
                self.assertTrue(detached.is_file())
                self.assertTrue(private.is_file())
                self.assertEqual(private.read_bytes(), b"")
                self.assertTrue(tuple(state_directory.glob("*.journal")))
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_receipt_deletion_recovers_or_blocks_pending_ordinary_generation(self) -> None:
        secret = "c" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)
        for mutate_generation in (False, True):
            with self.subTest(mutate_generation=mutate_generation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_directory = root / "state" / "expskill"
                state_directory.mkdir(parents=True)
                receipt = state_directory / "install-opencode.json"
                receipt.write_text(
                    json.dumps(
                        {
                            "links": [],
                            "marketplace_added": False,
                            "plugin_installed": True,
                            "repository_root": str(root),
                            "lineage": "6" * 32,
                            "teardown_phase": "anchor-removed",
                            "receipt_secret": secret,
                            "generation": "original",
                        }
                    ),
                    encoding="utf-8",
                )
                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                real_exchange = install_module._renameat_exchange
                interrupted = False

                def crash_after_generation(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal interrupted
                    real_exchange(source_fd, source_name, target_fd, target_name)
                    if (
                        source_name.startswith(
                            install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                        )
                        and not interrupted
                    ):
                        interrupted = True
                        raise SystemExit("after ordinary receipt exchange")

                try:
                    with mock.patch.object(
                        install_module,
                        "_renameat_exchange",
                        side_effect=crash_after_generation,
                    ):
                        with self.assertRaises(SystemExit):
                            install_module._write_state_payload(
                                receipt,
                                {
                                    "links": [],
                                    "marketplace_added": False,
                                    "plugin_installed": True,
                                    "repository_root": str(root),
                                    "lineage": "6" * 32,
                                    "teardown_phase": "anchor-removed",
                                    "receipt_secret": secret,
                                    "generation": "next",
                                },
                            )
                    self.assertTrue(interrupted)
                    generation = next(
                        state_directory.glob(".install-opencode.json.receipt-*.retire")
                    )
                    if mutate_generation:
                        generation.write_text("foreign generation\n", encoding="utf-8")
                        with self.assertRaises(install_module.InstallError):
                            install_module._unlink_state_path(receipt)
                        self.assertTrue(receipt.is_file())
                        self.assertTrue(generation.is_file())
                    else:
                        install_module._unlink_state_path(receipt)
                        self.assertFalse(receipt.exists())
                        self.assertFalse(generation.exists())
                        self.assertFalse(tuple(state_directory.glob("*.journal")))
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

    def test_receipt_deletion_blocks_outstanding_object_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            receipt_file = receipt_path(state)
            real_rewrite = install_module._rewrite_receipt_retirement
            interrupted = False

            def stop_after_prepared(
                path: Path,
                pending: object,
            ) -> None:
                nonlocal interrupted
                real_rewrite(path, pending)  # type: ignore[arg-type]
                if (
                    pending is not None
                    and getattr(pending, "phase", None) == "prepared"
                    and not interrupted
                ):
                    interrupted = True
                    raise SystemExit("after object retirement preparation")

            with mock.patch.object(
                install_module,
                "_rewrite_receipt_retirement",
                side_effect=stop_after_prepared,
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)
            self.assertTrue(interrupted)
            self.assertIn("pending_retirement", receipt(state))

            binding = install_module._open_state_binding(
                receipt_file.parent, create=False
            )
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            try:
                with self.assertRaisesRegex(
                    install_module.InstallError, "outstanding retirement"
                ):
                    install_module._unlink_state_path(receipt_file)
                self.assertTrue(receipt_file.is_file())
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_file.exists())

    def test_install_and_uninstall_retry_converge_after_final_receipt_crash(self) -> None:
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
                real_write = install_module._write_receipt_retirement_sidecar
                interrupted = False

                def crash_after_exchange(sidecar: Path, payload: object) -> None:
                    nonlocal interrupted
                    real_write(sidecar, payload)
                    if (
                        isinstance(payload, dict)
                        and payload.get("phase") == "exchanged"
                        and not interrupted
                    ):
                        interrupted = True
                        raise SystemExit("after final receipt exchange")

                with mock.patch.object(
                    install_module,
                    "_write_receipt_retirement_sidecar",
                    side_effect=crash_after_exchange,
                ):
                    with self.assertRaises(SystemExit):
                        uninstall_opencode(repo, config, state)
                self.assertTrue(interrupted)
                self.assertTrue(receipt_path(state).is_file())

                if retry_entrypoint == "install":
                    install_opencode(repo, config, state)
                    self.assertTrue(receipt_path(state).is_file())
                else:
                    uninstall_opencode(repo, config, state)
                    self.assertFalse(receipt_path(state).exists())
                    install_opencode(repo, config, state)
                uninstall_opencode(repo, config, state)
                uninstall_opencode(repo, config, state)
                self.assertFalse(receipt_path(state).exists())

    def test_receipt_exchange_crash_retries_without_random_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_directory = Path(temporary) / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            receipt.write_text(
                json.dumps(
                    {
                        "generation": "original",
                        "receipt_secret": "a"
                        * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2),
                    }
                ),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            real_exchange = install_module._renameat_exchange
            stopped = False

            def crash_after_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal stopped
                real_exchange(source_fd, source_name, target_fd, target_name)
                if source_name.startswith(
                    install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                ) and not stopped:
                    stopped = True
                    raise SystemExit("after receipt generation exchange")

            try:
                with mock.patch.object(
                    install_module,
                    "_renameat_exchange",
                    side_effect=crash_after_exchange,
                ):
                    with self.assertRaises(SystemExit):
                        install_module._write_state_payload(
                            receipt,
                            {
                                "generation": "next",
                                "receipt_secret": "a"
                                * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2),
                            },
                        )
                self.assertTrue(stopped)
                self.assertFalse(
                    any(path.name.endswith(".tmp") for path in state_directory.iterdir())
                )
                install_module._write_state_payload(
                    receipt,
                    {
                        "generation": "retry",
                        "receipt_secret": "a"
                        * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2),
                    },
                )
                self.assertFalse(
                    any(
                        path.name.startswith(
                            install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                        )
                        and path.name.endswith(
                            install_module.OPENCODE_RECEIPT_GENERATION_SUFFIX
                        )
                        for path in state_directory.iterdir()
                    )
                )
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_receipt_generation_prepare_crash_retries_without_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_directory = Path(temporary) / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            secret = "d" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2)
            receipt.write_text(
                json.dumps({"generation": "original", "receipt_secret": secret}),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            real_write = install_module._write_named_state_generation
            interrupted = False

            def crash_after_private_write(
                bound: object, name_from_identity: object, encoded: bytes
            ) -> tuple[str, tuple[int, int]]:
                nonlocal interrupted
                result = real_write(bound, name_from_identity, encoded)
                interrupted = True
                raise SystemExit("after receipt generation preparation")

            try:
                payload = {"generation": "candidate", "receipt_secret": secret}
                with mock.patch.object(
                    install_module,
                    "_write_named_state_generation",
                    side_effect=crash_after_private_write,
                ):
                    with self.assertRaises(SystemExit):
                        install_module._write_state_payload(receipt, payload)
                self.assertTrue(interrupted)
                self.assertTrue(
                    tuple(
                        state_directory.glob(
                            ".install-opencode.json.receipt-*.retire"
                        )
                    )
                )
                install_module._write_state_payload(receipt, payload)
                self.assertEqual(
                    json.loads(receipt.read_text(encoding="utf-8"))["generation"],
                    "candidate",
                )
                self.assertFalse(
                    tuple(
                        state_directory.glob(
                            ".install-opencode.json.receipt-*.retire"
                        )
                    )
                )
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_final_receipt_retirement_sidecar_recovers_and_preserves_foreign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_directory = Path(temporary) / "state" / "expskill"
            state_directory.mkdir(parents=True)
            receipt = state_directory / "install-opencode.json"
            receipt.write_text(
                json.dumps(
                    {
                        "links": [],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(Path(temporary)),
                        "lineage": "f" * 32,
                        "teardown_phase": "anchor-removed",
                        "receipt_secret": TEST_RETIREMENT_SECRET,
                    }
                ),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            real_rename = install_module._renameat_noreplace
            stopped = False

            def crash_after_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal stopped
                real_rename(source_fd, source_name, target_fd, target_name)
                if (
                    source_name == receipt.name
                    and target_name.endswith(".placeholder")
                    and not stopped
                ):
                    stopped = True
                    raise SystemExit("after final receipt retirement exchange")

            try:
                with mock.patch.object(
                    install_module,
                    "_renameat_noreplace",
                    side_effect=crash_after_exchange,
                ):
                    with self.assertRaises(SystemExit):
                        install_module._unlink_state_path(receipt)
                self.assertTrue(stopped)
                journals = tuple(state_directory.glob("*.journal"))
                self.assertEqual(len(journals), 1)
                private = next(state_directory.glob("*.retire"))
                foreign = receipt.with_name("foreign-receipt")
                receipt.write_text("foreign\n", encoding="utf-8")
                with self.assertRaises(install_module.InstallError):
                    install_module._recover_receipt_deletion(
                        Path(temporary), Path(temporary) / "config", Path(temporary) / "state", receipt
                    )
                self.assertEqual(receipt.read_text(encoding="utf-8"), "foreign\n")
                self.assertTrue(private.exists())
                receipt.unlink()
                install_module._recover_receipt_deletion(
                    Path(temporary), Path(temporary) / "config", Path(temporary) / "state", receipt
                )
                self.assertFalse(private.exists())
                self.assertFalse(journals[0].exists())
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_object_retirement_pre_syscall_replacement_is_restored_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            record = receipt(state)["links"][0]
            public = Path(record["destination"])
            detached = public.with_name(public.name + ".detached-owned")
            real_rename = install_module._renameat_exchange
            interrupted = False

            def displace_then_stop(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal interrupted
                if source_name == public.name and target_name.endswith(".retire"):
                    os.rename(
                        source_name,
                        detached.name,
                        src_dir_fd=source_fd,
                        dst_dir_fd=source_fd,
                    )
                    descriptor = os.open(
                        source_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=source_fd,
                    )
                    os.write(descriptor, b"foreign public\n")
                    os.close(descriptor)
                    real_rename(source_fd, source_name, target_fd, target_name)
                    interrupted = True
                    raise SystemExit("after displaced object retirement")
                real_rename(source_fd, source_name, target_fd, target_name)

            with mock.patch.object(
                install_module,
                "_renameat_exchange",
                side_effect=displace_then_stop,
            ):
                with self.assertRaises(SystemExit):
                    uninstall_opencode(repo, config, state)
            self.assertTrue(interrupted)
            private = Path(receipt(state)["pending_retirement"]["private"])
            self.assertTrue(public.is_file())
            foreign_identity = (private.stat().st_dev, private.stat().st_ino)

            with self.assertRaises(install_module.InstallError):
                uninstall_opencode(repo, config, state)
            self.assertEqual(public.read_text(encoding="utf-8"), "foreign public\n")
            self.assertEqual(
                (public.stat().st_dev, public.stat().st_ino), foreign_identity
            )
            self.assertTrue(detached.is_symlink())
            self.assertTrue(private.exists())

    def test_final_receipt_pre_syscall_replacement_is_restored_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_directory = root / "state" / "expskill"
            state_directory.mkdir(parents=True)
            canonical = state_directory / "install-opencode.json"
            canonical.write_text(
                json.dumps(
                    {
                        "links": [],
                        "marketplace_added": False,
                        "plugin_installed": True,
                        "repository_root": str(root),
                        "lineage": "9" * 32,
                        "teardown_phase": "anchor-removed",
                        "receipt_secret": TEST_RETIREMENT_SECRET,
                    }
                ),
                encoding="utf-8",
            )
            binding = install_module._open_state_binding(state_directory, create=False)
            self.assertIsNotNone(binding)
            assert binding is not None
            key = str(binding.directory)
            install_module._STATE_BINDINGS[key] = binding
            detached = canonical.with_name("detached-owned-receipt")
            real_rename = install_module._renameat_exchange
            interrupted = False

            def displace_then_stop(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                nonlocal interrupted
                if source_name == canonical.name and target_name.endswith(".retire"):
                    os.rename(
                        source_name,
                        detached.name,
                        src_dir_fd=source_fd,
                        dst_dir_fd=source_fd,
                    )
                    descriptor = os.open(
                        source_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=source_fd,
                    )
                    os.write(descriptor, b"foreign receipt\n")
                    os.close(descriptor)
                    real_rename(source_fd, source_name, target_fd, target_name)
                    interrupted = True
                    raise SystemExit("after displaced final receipt")
                real_rename(source_fd, source_name, target_fd, target_name)

            try:
                with mock.patch.object(
                    install_module,
                    "_renameat_exchange",
                    side_effect=displace_then_stop,
                ):
                    with self.assertRaises(SystemExit):
                        install_module._unlink_state_path(canonical)
                self.assertTrue(interrupted)
                journal = next(state_directory.glob("*.journal"))
                private = Path(json.loads(journal.read_text(encoding="utf-8"))["private"])
                foreign_identity = (private.stat().st_dev, private.stat().st_ino)

                with self.assertRaises(install_module.InstallError):
                    install_module._resume_receipt_retirement(
                        journal, json.loads(journal.read_text(encoding="utf-8"))
                    )
                self.assertEqual(
                    canonical.read_text(encoding="utf-8"), "foreign receipt\n"
                )
                self.assertEqual(
                    (canonical.stat().st_dev, canonical.stat().st_ino),
                    foreign_identity,
                )
                self.assertTrue(detached.is_file())
                self.assertTrue(private.exists())
                self.assertTrue(journal.exists())
            finally:
                install_module._STATE_BINDINGS.pop(key, None)
                install_module._close_state_binding(binding)

    def test_ordinary_receipt_post_exchange_foreign_is_never_adopted(self) -> None:
        for retry_entrypoint in ("install", "uninstall"):
            with self.subTest(retry_entrypoint=retry_entrypoint), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                canonical = receipt_path(state)
                state_directory = canonical.parent
                payload = receipt(state)
                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                detached = canonical.with_name("detached-published-receipt")
                real_exchange = install_module._renameat_exchange
                replaced = False

                def replace_after_exchange(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal replaced
                    real_exchange(source_fd, source_name, target_fd, target_name)
                    if (
                        source_name.startswith(
                            install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                        )
                        and not replaced
                    ):
                        replaced = True
                        canonical.rename(detached)
                        canonical.write_bytes(detached.read_bytes())

                try:
                    with mock.patch.object(
                        install_module,
                        "_renameat_exchange",
                        side_effect=replace_after_exchange,
                    ):
                        with self.assertRaises(install_module.InstallError):
                            install_module._write_state_payload(canonical, payload)
                    self.assertTrue(replaced)
                    foreign_identity = (
                        canonical.stat().st_dev,
                        canonical.stat().st_ino,
                    )
                    generations = tuple(
                        state_directory.glob(
                            ".install-opencode.json.receipt-*.retire"
                        )
                    )
                    self.assertEqual(len(generations), 1)
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

                entrypoint = install_opencode if retry_entrypoint == "install" else uninstall_opencode
                with self.assertRaises(install_module.InstallError):
                    entrypoint(repo, config, state)
                self.assertEqual(
                    (canonical.stat().st_dev, canonical.stat().st_ino),
                    foreign_identity,
                )
                self.assertTrue(detached.is_file())
                self.assertTrue(generations[0].is_file())

    def test_sidecar_post_exchange_foreign_is_never_adopted(self) -> None:
        for retry_entrypoint in ("install", "uninstall"):
            with self.subTest(retry_entrypoint=retry_entrypoint), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                canonical = receipt_path(state)
                detached = canonical.parent / "detached-published-sidecar"
                real_exchange = install_module._renameat_exchange
                replaced = False

                def replace_after_exchange(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    nonlocal replaced
                    real_exchange(source_fd, source_name, target_fd, target_name)
                    if (
                        target_name.endswith(".journal")
                        and source_name.startswith(".")
                        and ".stage" in source_name
                        and not replaced
                    ):
                        replaced = True
                        journal = canonical.parent / target_name
                        journal.rename(detached)
                        journal.write_bytes(detached.read_bytes())

                with mock.patch.object(
                    install_module,
                    "_renameat_exchange",
                    side_effect=replace_after_exchange,
                ):
                    with self.assertRaises(install_module.InstallError):
                        uninstall_opencode(repo, config, state)
                self.assertTrue(replaced)
                journal = next(canonical.parent.glob("*.journal"))
                foreign_identity = (journal.stat().st_dev, journal.stat().st_ino)
                stages = tuple(canonical.parent.glob(".*.journal.stage*"))
                self.assertEqual(len(stages), 1)

                entrypoint = install_opencode if retry_entrypoint == "install" else uninstall_opencode
                with self.assertRaises(install_module.InstallError):
                    entrypoint(repo, config, state)
                self.assertEqual(
                    (journal.stat().st_dev, journal.stat().st_ino),
                    foreign_identity,
                )
                self.assertTrue(detached.is_file())
                self.assertTrue(stages[0].is_file())

    def test_sidecar_named_generations_never_expose_partial_bytes_after_exit(self) -> None:
        for rewrite in (False, True):
            with self.subTest(rewrite=rewrite), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                state_directory = root / "state" / "expskill"
                state_directory.mkdir(parents=True)
                canonical = state_directory / "install-opencode.json"
                canonical.write_text("{}\n", encoding="utf-8")
                script = r'''
import json
import os
import sys
from pathlib import Path
import scripts.install as install_module

state_directory = Path(sys.argv[1])
rewrite = sys.argv[2] == "rewrite"
canonical = state_directory / "install-opencode.json"
binding = install_module._open_state_binding(state_directory, create=False)
assert binding is not None
install_module._STATE_BINDINGS[str(binding.directory)] = binding
identity = (canonical.stat().st_dev, canonical.stat().st_ino)
payload = install_module._receipt_retirement_payload(
    canonical,
    identity,
    "e" * (install_module.OPENCODE_RETIREMENT_SECRET_BYTES * 2),
    "prepared",
)
sidecar = Path(payload["sidecar"])
if rewrite:
    install_module._write_receipt_retirement_sidecar(sidecar, payload)
    payload = dict(payload)
    payload["phase"] = "exchanged"
    payload["auth"] = install_module._receipt_retirement_auth(payload)
real_write = os.write
def exit_after_half_write(descriptor, value):
    real_write(descriptor, value[:max(1, len(value) // 2)])
    os._exit(79)
install_module.os.write = exit_after_half_write
install_module._write_receipt_retirement_sidecar(sidecar, payload)
'''
                stopped = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(state_directory),
                        "rewrite" if rewrite else "initial",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(stopped.returncode, 79, stopped.stderr)
                journals = tuple(state_directory.glob("*.journal"))
                stages = tuple(state_directory.glob(".*.journal.stage*"))
                if rewrite:
                    self.assertEqual(len(journals), 1)
                    self.assertEqual(
                        json.loads(journals[0].read_text(encoding="utf-8"))[
                            "phase"
                        ],
                        "prepared",
                    )
                    self.assertFalse(stages)
                else:
                    self.assertFalse(journals)
                    self.assertFalse(stages)

    def test_reappeared_public_receipt_blocks_terminal_retirement_recovery(self) -> None:
        for phase in ("reclaimed", "done"):
            for retry_entrypoint in ("install", "uninstall"):
                with (
                    self.subTest(phase=phase, retry_entrypoint=retry_entrypoint),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    install_opencode(repo, config, state)
                    canonical = receipt_path(state)
                    original_bytes = canonical.read_bytes()
                    original_identity = (
                        canonical.stat().st_dev,
                        canonical.stat().st_ino,
                    )
                    # Keep the retired inode allocated so the recreated public
                    # receipt is guaranteed to have a distinct identity.
                    original_pin = canonical.with_name("retired-receipt-pin")
                    os.link(canonical, original_pin)
                    real_write = install_module._write_receipt_retirement_sidecar
                    interrupted = False

                    def exit_after_phase(sidecar: Path, payload: object) -> None:
                        nonlocal interrupted
                        real_write(sidecar, payload)
                        if (
                            isinstance(payload, dict)
                            and payload.get("phase") == phase
                            and not interrupted
                        ):
                            interrupted = True
                            raise SystemExit(f"after durable {phase} phase")

                    with mock.patch.object(
                        install_module,
                        "_write_receipt_retirement_sidecar",
                        side_effect=exit_after_phase,
                    ):
                        with self.assertRaises(SystemExit):
                            uninstall_opencode(repo, config, state)
                    self.assertTrue(interrupted)
                    self.assertFalse(canonical.exists())
                    journals = tuple(canonical.parent.glob("*.journal"))
                    self.assertEqual(len(journals), 1)
                    self.assertEqual(
                        json.loads(journals[0].read_text(encoding="utf-8"))["phase"],
                        phase,
                    )

                    canonical.write_bytes(original_bytes)
                    foreign_identity = (
                        canonical.stat().st_dev,
                        canonical.stat().st_ino,
                    )
                    self.assertNotEqual(foreign_identity, original_identity)
                    journal_identity = (
                        journals[0].stat().st_dev,
                        journals[0].stat().st_ino,
                    )
                    entrypoint = (
                        install_opencode
                        if retry_entrypoint == "install"
                        else uninstall_opencode
                    )
                    for attempt in range(2):
                        with self.subTest(attempt=attempt):
                            with self.assertRaises(install_module.InstallError):
                                entrypoint(repo, config, state)
                            self.assertEqual(canonical.read_bytes(), original_bytes)
                            self.assertEqual(
                                (
                                    canonical.stat().st_dev,
                                    canonical.stat().st_ino,
                                ),
                                foreign_identity,
                            )
                            self.assertTrue(journals[0].is_file())
                            self.assertEqual(
                                (
                                    journals[0].stat().st_dev,
                                    journals[0].stat().st_ino,
                                ),
                                journal_identity,
                            )
                            self.assertEqual(
                                json.loads(
                                    journals[0].read_text(encoding="utf-8")
                                )["phase"],
                                phase,
                            )
                            self.assertFalse(
                                tuple(canonical.parent.glob("*.retire"))
                            )

    def test_initial_workspace_process_death_recovers_by_entrypoint(
        self,
    ) -> None:
        boundaries = (
            "before-receipt-link",
            "after-receipt-link",
            "before-workspace-mkdir",
            "after-workspace-mkdir",
            "after-workspace-identity",
            "after-candidate-build",
        )
        for boundary in boundaries:
            for retry_entrypoint in ("install", "uninstall"):
                with (
                    self.subTest(
                        boundary=boundary, retry_entrypoint=retry_entrypoint
                    ),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    config = root / "config"
                    state = root / "state"
                    script = r'''
import os
import sys
from pathlib import Path
import scripts.install as install_module

repo, config, state = map(Path, sys.argv[1:4])
boundary = sys.argv[4]
state_directory = state / "expskill"
canonical = state_directory / install_module.OPENCODE_RECEIPT_FILENAME
real_link = install_module._link_open_descriptor
real_mkdir = os.mkdir
real_build = install_module.build_opencode_package

if boundary in {"before-receipt-link", "after-receipt-link"}:
    def exit_at_receipt_link(source_fd, target_fd, target_name):
        if target_name == canonical.name and boundary == "before-receipt-link":
            os._exit(79)
        real_link(source_fd, target_fd, target_name)
        if target_name == canonical.name:
            os._exit(79)
    install_module._link_open_descriptor = exit_at_receipt_link

if boundary in {"before-workspace-mkdir", "after-workspace-mkdir"}:
    def exit_at_workspace_mkdir(name, mode=0o777, *, dir_fd=None):
        if str(name).startswith(".opencode-artifact.txn-"):
            if boundary == "before-workspace-mkdir":
                os._exit(79)
            real_mkdir(name, mode=mode, dir_fd=dir_fd)
            os._exit(79)
        return real_mkdir(name, mode=mode, dir_fd=dir_fd)
    install_module.os.mkdir = exit_at_workspace_mkdir

if boundary in {"after-workspace-identity", "after-candidate-build"}:
    def exit_at_build(repo_root, output, **kwargs):
        if boundary == "after-workspace-identity":
            os._exit(79)
        result = real_build(repo_root, output, **kwargs)
        os._exit(79)
    install_module.build_opencode_package = exit_at_build

install_module.install_opencode(repo, config, state)
'''
                    stopped = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            script,
                            str(repo),
                            str(config),
                            str(state),
                            boundary,
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(stopped.returncode, 79, stopped.stderr)
                    canonical = receipt_path(state)
                    state_directory = state / "expskill"
                    self.assertTrue(state_directory.is_dir())
                    workspaces = tuple(
                        state_directory.glob(".opencode-artifact.txn-*")
                    )
                    if boundary == "before-receipt-link":
                        self.assertFalse(canonical.exists())
                        self.assertFalse(workspaces)
                    else:
                        self.assertTrue(canonical.is_file())
                        interrupted = json.loads(
                            canonical.read_text(encoding="utf-8")
                        )
                        pending = interrupted["pending_publish"]
                        if boundary in {
                            "after-receipt-link",
                            "before-workspace-mkdir",
                            "after-workspace-mkdir",
                        }:
                            self.assertEqual(
                                pending["phase"], "planned-unmaterialized"
                            )
                        else:
                            self.assertEqual(pending["phase"], "workspace-recorded")
                            self.assertGreater(pending["workspace_dev"], 0)
                            self.assertGreater(pending["workspace_ino"], 0)
                        self.assertNotIn("candidate_dev", pending)
                        self.assertNotIn("candidate_ino", pending)
                        expected_workspaces = (
                            1
                            if boundary
                            in {
                                "after-workspace-mkdir",
                                "after-workspace-identity",
                                "after-candidate-build",
                            }
                            else 0
                        )
                        self.assertEqual(len(workspaces), expected_workspaces)

                    entrypoint = (
                        install_opencode
                        if retry_entrypoint == "install"
                        else uninstall_opencode
                    )
                    entrypoint(repo, config, state)
                    self.assertTrue(state_directory.is_dir())
                    self.assertFalse(
                        tuple(state_directory.glob(".opencode-artifact.txn-*"))
                    )
                    self.assertFalse(tuple(state_directory.glob(".opencode-artifact.next-*")))
                    if retry_entrypoint == "install":
                        self.assertTrue(canonical.is_file())
                        self.assertTrue(
                            (canonical.parent / "opencode-artifact").is_dir()
                        )
                    else:
                        self.assertFalse(canonical.exists())
                        self.assertFalse(
                            (canonical.parent / "opencode-artifact").exists()
                        )

    def test_planned_workspace_schema_and_identity_fail_closed(self) -> None:
        for scenario in ("malformed", "preoccupied", "recorded-replaced"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                canonical = receipt_path(state)
                real_write = install_module._write_receipt
                real_build = install_module.build_opencode_package
                workspace: Path | None = None
                detached: Path | None = None
                foreign_identity: tuple[int, int] | None = None

                if scenario == "malformed":
                    def stop_before_workspace(
                        receipt_file: Path, value: install_module._Receipt
                    ) -> None:
                        real_write(receipt_file, value)
                        pending = value.pending_publish
                        if (
                            pending is not None
                            and pending.phase == "planned-unmaterialized"
                        ):
                            raise SystemExit("planned receipt is durable")

                    with mock.patch.object(
                        install_module, "_write_receipt", side_effect=stop_before_workspace
                    ):
                        with self.assertRaises(SystemExit):
                            install_opencode(repo, config, state)
                    payload = json.loads(canonical.read_text(encoding="utf-8"))
                    payload["pending_publish"]["workspace_dev"] = 1
                    canonical.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(install_module.InstallError):
                        install_opencode(repo, config, state)
                    self.assertTrue(canonical.is_file())
                    continue

                if scenario == "preoccupied":
                    def occupy_after_planned_write(
                        receipt_file: Path, value: install_module._Receipt
                    ) -> None:
                        nonlocal workspace, foreign_identity
                        real_write(receipt_file, value)
                        pending = value.pending_publish
                        if (
                            pending is not None
                            and pending.phase == "planned-unmaterialized"
                            and workspace is None
                        ):
                            workspace = pending.workspace
                            workspace.mkdir()
                            (workspace / "foreign").write_text(
                                "must survive\n", encoding="utf-8"
                            )
                            foreign_identity = (
                                workspace.stat().st_dev,
                                workspace.stat().st_ino,
                            )

                    with mock.patch.object(
                        install_module,
                        "_write_receipt",
                        side_effect=occupy_after_planned_write,
                    ):
                        with self.assertRaises(install_module.InstallError):
                            install_opencode(repo, config, state)
                    assert workspace is not None and foreign_identity is not None
                    self.assertEqual(
                        (workspace.stat().st_dev, workspace.stat().st_ino),
                        foreign_identity,
                    )
                    self.assertEqual(
                        (workspace / "foreign").read_text(encoding="utf-8"),
                        "must survive\n",
                    )
                    continue

                def stop_after_workspace_identity(
                    repo_root: Path, output: Path, **_kwargs: object
                ) -> Path:
                    raise SystemExit("workspace identity is durable")

                with mock.patch.object(
                    install_module,
                    "build_opencode_package",
                    side_effect=stop_after_workspace_identity,
                ):
                    with self.assertRaises(SystemExit):
                        install_opencode(repo, config, state)
                payload = json.loads(canonical.read_text(encoding="utf-8"))
                workspace = Path(payload["pending_publish"]["workspace"])
                recorded_identity = (
                    payload["pending_publish"]["workspace_dev"],
                    payload["pending_publish"]["workspace_ino"],
                )
                detached = workspace.with_name("detached-recorded-workspace")
                workspace.rename(detached)
                workspace.mkdir()
                (workspace / "foreign").write_text("must survive\n", encoding="utf-8")
                foreign_identity = (workspace.stat().st_dev, workspace.stat().st_ino)
                self.assertNotEqual(recorded_identity, foreign_identity)
                for entrypoint in (install_opencode, uninstall_opencode):
                    with self.subTest(entrypoint=entrypoint.__name__):
                        with self.assertRaises(install_module.InstallError):
                            entrypoint(repo, config, state)
                        self.assertEqual(
                            (workspace.stat().st_dev, workspace.stat().st_ino),
                            foreign_identity,
                        )
                        self.assertTrue(detached.is_dir())

    def test_state_lock_contends_for_both_entrypoints(self) -> None:
        import fcntl

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            state_directory = state / "expskill"
            descriptor = os.open(state_directory, install_module._directory_open_flags())
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for entrypoint in (install_opencode, uninstall_opencode):
                    with self.subTest(entrypoint=entrypoint.__name__):
                        with self.assertRaises(install_module.InstallError):
                            entrypoint(repo, config, state)
                        self.assertTrue(receipt_path(state).is_file())
                        self.assertTrue(state_directory.is_dir())
            finally:
                os.close(descriptor)

    def test_first_receipt_link_preserves_foreign_canonical_occupant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            canonical = receipt_path(state)
            real_link = install_module._link_open_descriptor
            occupied = False
            foreign_identity: tuple[int, int] | None = None

            def occupy_before_receipt_link(
                source_fd: int, target_fd: int, target_name: str
            ) -> None:
                nonlocal occupied, foreign_identity
                if (
                    not occupied
                    and (
                        target_name == canonical.name
                        or ".receipt-initial-" in target_name
                    )
                ):
                    canonical.write_bytes(b"foreign canonical receipt\n")
                    foreign_identity = (
                        canonical.stat().st_dev,
                        canonical.stat().st_ino,
                    )
                    occupied = True
                real_link(source_fd, target_fd, target_name)

            with mock.patch.object(
                install_module,
                "_link_open_descriptor",
                side_effect=occupy_before_receipt_link,
            ):
                with self.assertRaises(install_module.InstallError):
                    install_opencode(repo, config, state)
            self.assertTrue(occupied)
            self.assertEqual(canonical.read_bytes(), b"foreign canonical receipt\n")
            self.assertEqual(
                (canonical.stat().st_dev, canonical.stat().st_ino),
                foreign_identity,
            )
            self.assertFalse(
                tuple(
                    canonical.parent.glob(
                        ".install-opencode.json.receipt-initial-*"
                    )
                )
            )

    def test_receipt_deletion_requires_bound_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "install-opencode.json"
            receipt.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(install_module.InstallError):
                install_module._unlink_state_path(receipt)

    def test_recovery_reclaim_preserves_replaced_canonical_for_both_relations(
        self,
    ) -> None:
        for relation in ("unpublished-candidate", "displaced-old"):
            with (
                self.subTest(relation=relation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                install_opencode(repo, config, state)
                canonical = receipt_path(state)
                state_directory = canonical.parent
                payload = receipt(state)
                payload["marketplace_added"] = not payload["marketplace_added"]
                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                real_exchange = install_module._renameat_exchange
                real_named_write = install_module._write_named_state_generation

                def stop_at_exchange(
                    source_fd: int,
                    source_name: str,
                    target_fd: int,
                    target_name: str,
                ) -> None:
                    if source_name.startswith(
                        install_module.OPENCODE_RECEIPT_GENERATION_PREFIX
                    ):
                        real_exchange(
                            source_fd, source_name, target_fd, target_name
                        )
                        raise SystemExit(f"created {relation} relation")
                    real_exchange(source_fd, source_name, target_fd, target_name)

                def stop_after_candidate_write(
                    bound: object,
                    name_from_identity: object,
                    encoded: bytes,
                ) -> tuple[str, tuple[int, int]]:
                    result = real_named_write(
                        bound, name_from_identity, encoded
                    )  # type: ignore[arg-type]
                    raise SystemExit("created unpublished-candidate relation")

                try:
                    target = (
                        "_write_named_state_generation"
                        if relation == "unpublished-candidate"
                        else "_renameat_exchange"
                    )
                    side_effect = (
                        stop_after_candidate_write
                        if relation == "unpublished-candidate"
                        else stop_at_exchange
                    )
                    with mock.patch.object(install_module, target, side_effect=side_effect):
                        with self.assertRaises(SystemExit):
                            install_module._write_state_payload(canonical, payload)
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

                generations = tuple(
                    state_directory.glob(
                        ".install-opencode.json.receipt-*.retire"
                    )
                )
                self.assertEqual(len(generations), 1)
                generation = generations[0]
                generation_identity = (
                    generation.stat().st_dev,
                    generation.stat().st_ino,
                )
                sampled_public_identity = (
                    canonical.stat().st_dev,
                    canonical.stat().st_ino,
                )
                expected_label = (
                    "receipt generation candidate"
                    if relation == "unpublished-candidate"
                    else "receipt generation"
                )
                detached = root / f"detached-{relation}.json"
                original_public_bytes = canonical.read_bytes()
                replacement_identity: tuple[int, int] | None = None
                replaced = False

                binding = install_module._open_state_binding(
                    state_directory, create=False
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                key = str(binding.directory)
                install_module._STATE_BINDINGS[key] = binding
                real_reclaim = install_module._unlink_private_state_inode
                real_link = install_module._link_open_descriptor

                def require_public_guard(
                    bound: object,
                    name: str,
                    expected: tuple[int, int],
                    label: str,
                    **kwargs: object,
                ) -> None:
                    if label == expected_label:
                        self.assertEqual(kwargs.get("public_name"), canonical.name)
                        self.assertEqual(
                            kwargs.get("public_expected"), sampled_public_identity
                        )
                    real_reclaim(bound, name, expected, label, **kwargs)

                def replace_public_after_pin(
                    source_fd: int, target_fd: int, target_name: str
                ) -> None:
                    nonlocal replaced, replacement_identity
                    real_link(source_fd, target_fd, target_name)
                    if target_name.endswith(".pin") and not replaced:
                        canonical.rename(detached)
                        canonical.write_bytes(original_public_bytes)
                        replacement_identity = (
                            canonical.stat().st_dev,
                            canonical.stat().st_ino,
                        )
                        replaced = True

                try:
                    with (
                        mock.patch.object(
                            install_module,
                            "_unlink_private_state_inode",
                            side_effect=require_public_guard,
                        ),
                        mock.patch.object(
                            install_module,
                            "_link_open_descriptor",
                            side_effect=replace_public_after_pin,
                        ),
                    ):
                        with self.assertRaises(install_module.InstallError):
                            install_module._recover_receipt_generations(
                                canonical, binding
                            )
                finally:
                    install_module._STATE_BINDINGS.pop(key, None)
                    install_module._close_state_binding(binding)

                self.assertTrue(replaced)
                assert replacement_identity is not None
                self.assertEqual(
                    (canonical.stat().st_dev, canonical.stat().st_ino),
                    replacement_identity,
                )
                self.assertEqual(canonical.read_bytes(), original_public_bytes)
                self.assertEqual(
                    (generation.stat().st_dev, generation.stat().st_ino),
                    generation_identity,
                )
                self.assertFalse(
                    tuple(
                        state_directory.glob(
                            ".install-opencode.json.receipt-*.retire.pin"
                        )
                    )
                )
                for entrypoint in (install_opencode, uninstall_opencode):
                    with self.subTest(entrypoint=entrypoint.__name__):
                        with self.assertRaises(install_module.InstallError):
                            entrypoint(repo, config, state)
                        self.assertEqual(
                            (canonical.stat().st_dev, canonical.stat().st_ino),
                            replacement_identity,
                        )
                        self.assertEqual(
                            (generation.stat().st_dev, generation.stat().st_ino),
                            generation_identity,
                        )

    def test_candidate_less_rollback_phases_reload_through_both_entrypoints(
        self,
    ) -> None:
        boundaries = {
            "publish": (
                "rollback-prepared",
                "rollback-links-restored",
                "rollback-artifact-removed",
                "rollback-anchor-removed",
                "rollback-complete",
            ),
            "swap": (
                "rollback-prepared",
                "rollback-candidate-removed",
                "rollback-anchor-removed",
                "rollback-restored",
                "rollback-complete",
            ),
        }
        for transaction, transaction_boundaries in boundaries.items():
            for boundary in transaction_boundaries:
                for retry_entrypoint in ("install", "uninstall"):
                    with (
                        self.subTest(
                            transaction=transaction,
                            boundary=boundary,
                            retry_entrypoint=retry_entrypoint,
                        ),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        repo = seed_repository(root / "repo")
                        config = root / "config"
                        state = root / "state"
                        canonical = receipt_path(state)
                        if transaction == "swap":
                            install_opencode(repo, config, state)
                            skill = repo / "plugins/expskill/skills/unslop/SKILL.md"
                            skill.write_text(
                                skill.read_text(encoding="utf-8")
                                + "\ncandidate-less rollback marker\n",
                                encoding="utf-8",
                            )

                        real_write = install_module._write_receipt
                        real_rollback_links = install_module._rollback_links
                        real_unlink = install_module._unlink_state_path
                        rollback_seen = False
                        crashed = False

                        def write_and_crash(
                            path: Path, value: install_module._Receipt
                        ) -> None:
                            nonlocal rollback_seen, crashed
                            real_write(path, value)
                            pending = value.pending_publish or value.pending_swap
                            phase = pending.phase if pending is not None else None
                            if phase is not None and phase.startswith("rollback-"):
                                rollback_seen = True
                            target = boundary
                            if phase == target and not crashed:
                                crashed = True
                                raise SystemExit(f"after durable {phase}")
                            if (
                                boundary == "rollback-complete"
                                and transaction == "swap"
                                and rollback_seen
                                and value.pending_swap is None
                                and not crashed
                            ):
                                crashed = True
                                raise SystemExit("after durable rollback completion")

                        def rollback_links_and_crash(
                            links: object,
                        ) -> list[str]:
                            nonlocal crashed
                            result = real_rollback_links(links)  # type: ignore[arg-type]
                            if (
                                transaction == "publish"
                                and boundary == "rollback-links-restored"
                                and not crashed
                            ):
                                crashed = True
                                raise SystemExit("after publication links were restored")
                            return result

                        def unlink_and_crash(
                            path: Path, *args: object, **kwargs: object
                        ) -> None:
                            nonlocal crashed
                            real_unlink(path, *args, **kwargs)
                            if (
                                transaction == "publish"
                                and boundary == "rollback-complete"
                                and path == canonical
                                and not crashed
                            ):
                                crashed = True
                                raise SystemExit("after publication rollback completion")

                        with (
                            mock.patch.object(
                                install_module,
                                "build_opencode_package",
                                side_effect=install_module.OpencodeBuildError(
                                    "candidate was never created"
                                ),
                            ),
                            mock.patch.object(
                                install_module,
                                "_write_receipt",
                                side_effect=write_and_crash,
                            ),
                            mock.patch.object(
                                install_module,
                                "_rollback_links",
                                side_effect=rollback_links_and_crash,
                            ),
                            mock.patch.object(
                                install_module,
                                "_unlink_state_path",
                                side_effect=unlink_and_crash,
                            ),
                        ):
                            with self.assertRaises(SystemExit):
                                install_opencode(repo, config, state)
                        self.assertTrue(crashed)

                        entrypoint = (
                            install_opencode
                            if retry_entrypoint == "install"
                            else uninstall_opencode
                        )
                        entrypoint(repo, config, state)
                        artifact = state / "expskill/opencode-artifact"
                        if retry_entrypoint == "install":
                            self.assertTrue(canonical.is_file())
                            self.assertTrue(artifact.is_dir())
                        else:
                            self.assertFalse(canonical.exists())
                            self.assertFalse(artifact.exists())

    def test_workspace_replacement_during_builder_never_writes_foreign_path(
        self,
    ) -> None:
        for moment in ("before-builder", "after-builder-start"):
            with (
                self.subTest(moment=moment),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                config = root / "config"
                state = root / "state"
                real_build = install_module.build_opencode_package
                real_snapshot = build_module._snapshot_sources
                workspace: Path | None = None
                detached: Path | None = None
                marker: Path | None = None
                replaced = False

                def replace_workspace() -> None:
                    nonlocal detached, marker, replaced
                    assert workspace is not None
                    detached = workspace.with_name(
                        f"detached-{moment}-{workspace.name}"
                    )
                    workspace.rename(detached)
                    workspace.mkdir()
                    marker = workspace / "foreign-marker"
                    marker.write_bytes(b"must survive\n")
                    replaced = True

                def snapshot_after_replacement(source: Path) -> tuple[Path, Path]:
                    replace_workspace()
                    return real_snapshot(source)

                def build_with_replacement(
                    source: Path,
                    output: Path,
                    *,
                    output_parent_fd: int,
                ) -> Path:
                    nonlocal workspace
                    workspace = output.parent
                    if moment == "before-builder":
                        replace_workspace()
                    return real_build(
                        source,
                        output,
                        output_parent_fd=output_parent_fd,
                    )

                snapshot_patch = (
                    mock.patch.object(
                        build_module,
                        "_snapshot_sources",
                        side_effect=snapshot_after_replacement,
                    )
                    if moment == "after-builder-start"
                    else mock.patch.object(
                        build_module,
                        "_snapshot_sources",
                        wraps=real_snapshot,
                    )
                )
                with (
                    snapshot_patch,
                    mock.patch.object(
                        install_module,
                        "build_opencode_package",
                        side_effect=build_with_replacement,
                    ),
                ):
                    with self.assertRaises(install_module.InstallError):
                        install_opencode(repo, config, state)

                self.assertTrue(replaced)
                assert workspace is not None
                assert detached is not None
                assert marker is not None
                self.assertEqual(tuple(workspace.iterdir()), (marker,))
                self.assertEqual(marker.read_bytes(), b"must survive\n")
                candidate_names = tuple(
                    path.name
                    for path in detached.iterdir()
                    if path.name.startswith(".opencode-artifact.next-")
                )
                self.assertEqual(len(candidate_names), 1)
                self.assertTrue(
                    (detached / candidate_names[0] / "package.json").is_file()
                )


if __name__ == "__main__":
    unittest.main()
