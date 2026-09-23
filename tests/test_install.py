from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from scripts.install import (
    InstallError,
    _codex_managed_root,
    _codex_recovery_root,
    install,
    main,
    uninstall,
)


ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAMES = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
RETIRED_PROFILE_NAMES = (
    "expskill-critical-reviewer",
    "expskill-implementer-high",
    "expskill-reviewer",
    "expskill-verifier",
    "expskill-verifier-low",
)
SKILL_NAMES = (
    "use-expskill",
    "brainstorm",
    "design",
    "grill-me",
    "plan",
    "implement",
    "correct",
    "review",
    "test",
    "setup-ui-testing",
    "skill-builder",
    "unslop",
    "autonomous-run",
    "review-loop",
)
PLUGIN_SELECTOR = "expskill@expskill"
MANIFEST_VERSION = json.loads(
    (ROOT / "plugins" / "expskill" / ".codex-plugin" / "plugin.json").read_text(
        encoding="utf-8"
    )
)["version"]


class FakeResult:
    def __init__(
        self,
        returncode: int = 0,
        payload: object = None,
        stderr: str = "",
        stdout: str | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = json.dumps(payload) if stdout is None else stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str]) -> FakeResult:
        self.calls.append(tuple(command))
        if not self.results:
            raise AssertionError(f"unexpected command: {command!r}")
        return self.results.pop(0)


def seed_repository(path: Path) -> Path:
    source_plugin = ROOT / "plugins" / "expskill"
    destination_plugin = path / "plugins" / "expskill"
    source_scripts = source_plugin / "content" / "scripts"
    source_helper = source_scripts / "worktrees.py"
    source_plan_helper = source_scripts / "plan_graph.py"
    if (
        not source_scripts.is_dir()
        or source_scripts.is_symlink()
        or not source_helper.is_file()
        or source_helper.is_symlink()
        or source_helper.stat().st_size == 0
        or not source_plan_helper.is_file()
        or source_plan_helper.is_symlink()
        or source_plan_helper.stat().st_size == 0
    ):
        raise AssertionError(f"invalid route-neutral plugin helper fixture: {source_helper}")
    # Sandbox mounts can expose an empty .agents directory in the source tree.
    if (ROOT / ".agents").is_dir() and any((ROOT / ".agents").iterdir()):
        shutil.copytree(ROOT / ".agents", path / ".agents")
    shutil.copytree(source_plugin / ".codex-plugin", destination_plugin / ".codex-plugin")
    shutil.copytree(source_plugin / "content", destination_plugin / "content")
    shutil.copytree(source_plugin / "codex", destination_plugin / "codex")
    shutil.copytree(source_plugin / "opencode", destination_plugin / "opencode")
    # Seed the same route-neutral plugin inputs that a real marketplace
    # registration receives, including the centralized worktree helper.
    # The helper scripts are part of the canonical content tree; retain the
    # legacy fixture assertion under that exact source boundary.
    shutil.copytree(ROOT / "scripts", path / "scripts")
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def destination_paths(codex_home: Path) -> dict[str, Path]:
    canonical_home = codex_home.resolve()
    return {
        name: canonical_home / "agents" / f"{name}.toml"
        for name in PROFILE_NAMES
    }


def managed_repository(repository: Path) -> Path:
    return _codex_managed_root(repository.resolve(), repository.parent / "state")


def recovery_repository(repository: Path) -> Path:
    return _codex_recovery_root(repository.resolve(), repository.parent / "state")


def marketplace_list_response(
    repository: Path | None = None,
    source: Path | None = None,
) -> FakeResult:
    marketplaces: list[dict[str, object]] = []
    if repository is not None:
        selected_source = managed_repository(repository) if source is None else source
        marketplaces.append(
            {
                "name": "expskill",
                "root": str(selected_source),
                "marketplaceSource": {
                    "sourceType": "local",
                    "source": str(selected_source),
                },
            }
        )
    return FakeResult(0, {"marketplaces": marketplaces})


def marketplace_add_response(repository: Path, already_added: bool = False) -> FakeResult:
    return FakeResult(
        0,
        {
            "marketplaceName": "expskill",
            "installedRoot": str(managed_repository(repository)),
            "alreadyAdded": already_added,
        },
    )


def legacy_marketplace_add_response(
    repository: Path, recovery_root: Path | None = None
) -> FakeResult:
    """Return the add payload emitted when the legacy registration is restored."""

    installed_root = repository.resolve() if recovery_root is None else recovery_root.resolve()

    return FakeResult(
        0,
        {
            "marketplaceName": "expskill",
            "installedRoot": str(installed_root),
            "alreadyAdded": False,
        },
    )


def plugin_entry(repository: Path, source: Path | None = None) -> dict[str, object]:
    plugin_source = managed_repository(repository) if source is None else source
    return {
        "pluginId": PLUGIN_SELECTOR,
        "name": "expskill",
        "marketplaceName": "expskill",
        "version": "0.1.0",
        "installed": True,
        "enabled": True,
        "source": {
            "source": "local",
            "path": str(plugin_source / "plugins" / "expskill"),
        },
        "marketplaceSource": {
            "sourceType": "local",
            "source": str(plugin_source),
        },
        "installPolicy": "AVAILABLE",
        "authPolicy": "ON_INSTALL",
    }


def plugin_list_response(
    repository: Path | None = None,
    source: Path | None = None,
) -> FakeResult:
    installed: list[dict[str, object]] = []
    if repository is not None:
        installed.append(plugin_entry(repository, source))
    return FakeResult(0, {"installed": installed, "available": []})


def plugin_add_response(repository: Path, version: str = MANIFEST_VERSION) -> FakeResult:
    return FakeResult(
        0,
        {
            "pluginId": PLUGIN_SELECTOR,
            "name": "expskill",
            "marketplaceName": "expskill",
            "version": version,
            "installedPath": str(
                repository
                / "codex"
                / "plugins"
                / "cache"
                / "expskill"
                / "expskill"
                / version
            ),
            "authPolicy": "ON_INSTALL",
        },
    )


def removal_response() -> FakeResult:
    return FakeResult(0, {"removed": True})


def install_results(
    repository: Path,
    marketplace_present: bool = False,
    plugin_present: bool = False,
    marketplace_source: Path | None = None,
    plugin_source: Path | None = None,
) -> list[FakeResult]:
    return [
        marketplace_list_response(
            repository if marketplace_present else None,
            marketplace_source,
        ),
        marketplace_add_response(repository, marketplace_present),
        plugin_list_response(
            repository if plugin_present else None,
            plugin_source,
        ),
        plugin_add_response(repository),
    ]


def receipt_path(state_home: Path) -> Path:
    return state_home.resolve() / "expskill" / "install.json"


def load_receipt(state_home: Path) -> dict[str, object]:
    return json.loads(receipt_path(state_home).read_text(encoding="utf-8"))


def assert_package_only_receipt(repo: Path, state: Path, *, owned: bool = True) -> None:
    from scripts import install as module
    receipt = load_receipt(state)
    assert receipt["links"] == []
    assert not receipt["marketplace_added"]
    assert not receipt["plugin_installed"]
    if owned:
        assert receipt["codex_package"] == module._codex_package_identity(managed_repository(repo))
    else:
        assert "codex_package" not in receipt


def wait_for_crashed_child(pid: int, expected_status: int = 73) -> None:
    deadline = time.monotonic() + 30
    while True:
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            break
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError(f"child {pid} exceeded the 30-second interruption bound")
        time.sleep(0.02)
    if waited != pid or not os.WIFEXITED(status):
        raise AssertionError(f"child {pid} did not exit normally: {status}")
    if os.WEXITSTATUS(status) != expected_status:
        raise AssertionError(
            f"child {pid} exited {os.WEXITSTATUS(status)}, expected {expected_status}"
        )


class InstallerTests(unittest.TestCase):
    def test_legacy_retirement_exit_retries_before_destination_preflight(self) -> None:
        from scripts import install as module
        for retry in (install, uninstall):
            for replacement in (None, "public", "private"):
                with self.subTest(retry=retry.__name__, replacement=replacement), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    repo = seed_repository(root / "repo")
                    codex_home, state_home = root / "codex", root / "state"
                    destination = destination_paths(codex_home)["expskill-review"]
                    destination.parent.mkdir(parents=True)
                    destination.symlink_to(repo / "plugins/expskill/assets/agents" / destination.name)
                    pid = os.fork()
                    if pid == 0:
                        exchange = module._renameat_exchange
                        def crash_exchange(*args: object) -> None:
                            exchange(*args)
                            if args[1] == destination.name:
                                os._exit(73)
                        with mock.patch("scripts.install._renameat_exchange", crash_exchange):
                            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                        os._exit(74)
                    wait_for_crashed_child(pid)
                    self.assertTrue(destination.is_file())
                    retired, = destination.parent.glob("*.retire")
                    saved = root / "saved-retirement"
                    preserved = None
                    if replacement is not None:
                        preserved = destination if replacement == "public" else retired
                        preserved.rename(saved)
                        preserved.write_text("user replacement")
                    journal = state_home / "expskill/codex-install.json"
                    unlink = os.unlink
                    def interrupt_retirement(path: object, *args: object, **kwargs: object) -> None:
                        if path == retired.name:
                            raise SystemExit(73)
                        unlink(path, *args, **kwargs)
                    if replacement is None:
                        for _ in range(2):
                            with mock.patch("scripts.install.os.unlink", interrupt_retirement), self.assertRaises(SystemExit):
                                retry(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                            self.assertTrue(journal.is_file())
                    if replacement == "private" or (replacement == "public" and retry is install):
                        with self.assertRaises(InstallError):
                            retry(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                        self.assertEqual(preserved.read_text(), "user replacement")
                        self.assertTrue(journal.is_file())
                        preserved.unlink()
                        if replacement == "private":
                            saved.rename(retired)
                    retry(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                    if retry is install:
                        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                    self.assertFalse(journal.exists())
                    self.assertEqual(list(destination.parent.iterdir()), [destination] if replacement == "public" and retry is uninstall else [])

    def test_read_only_rejections_allow_agents_only_retry(self) -> None:
        for rejection in ("marketplace", "plugin", "plugin-json", "sources", "receipt"):
            with self.subTest(rejection=rejection), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                results = {
                    "marketplace": [marketplace_list_response(repo, root / "other")],
                    "plugin": [marketplace_list_response(repo, repo), plugin_list_response(repo, root / "other")],
                    "plugin-json": [marketplace_list_response(repo, repo), FakeResult(stdout="bad JSON")],
                    "sources": [marketplace_list_response(repo, repo), plugin_list_response(repo)],
                    "receipt": [],
                }[rejection]
                if rejection == "receipt":
                    receipt_path(state_home).parent.mkdir(parents=True)
                    receipt_path(state_home).write_text("invalid JSON")
                runner = FakeRunner(results)
                with self.assertRaises(InstallError):
                    install(repo, codex_home, state_home, runner)
                self.assertEqual(runner.results, [])
                self.assertTrue(all("list" in call for call in runner.calls))
                self.assertFalse((state_home / "expskill/codex-install.json").exists())
                if rejection == "receipt":
                    receipt_path(state_home).unlink()
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

    def _install_base_receipt(
        self, repo: Path, codex_home: Path, state_home: Path, *, agents_only: bool = True
    ) -> None:
        source = subprocess.check_output(
            ["git", "show", "67f554398e72b58e451223bb17cb1af30e0de833:scripts/install.py"], cwd=ROOT
        )
        base = types.ModuleType("expskill_base_installer")
        base.__file__ = str(ROOT / "scripts/install.py")
        with mock.patch.dict(sys.modules, {base.__name__: base}):
            exec(compile(source, base.__file__, "exec"), base.__dict__)
            base.install(
                repo, codex_home, state_home,
                FakeRunner([] if agents_only else install_results(repo)),
                agents_only=agents_only,
            )
        self.assertTrue(all(set(entry) == {"source", "destination"} for entry in load_receipt(state_home)["links"]))
        self.assertNotIn("codex_package", load_receipt(state_home))

    def _assert_package_only_receipt(self, repo: Path, state: Path) -> None:
        assert_package_only_receipt(repo, state)

    def test_real_base_receipt_refresh_preserves_unproven_package(self) -> None:
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                home, state = root / "codex", root / "state"
                self._install_base_receipt(repo, home, state)
                package = managed_repository(repo)
                if replacement:
                    marker = (package / ".expskill-managed.json").read_bytes()
                    package.rename(root / "original")
                    package.mkdir()
                    (package / ".expskill-managed.json").write_bytes(marker)
                (package / "user-data").write_text("preserve unproven package")
                identity = package.stat().st_ino
                for _ in range(2):
                    try:
                        install(repo, home, state, FakeRunner([]), agents_only=True)
                    except InstallError:
                        pass
                    self.assertTrue((package / "user-data").is_file())
                    self.assertEqual((package / "user-data").read_text(), "preserve unproven package")
                    self.assertEqual(package.stat().st_ino, identity)
                    self.assertNotIn("codex_package", load_receipt(state))

    def test_real_base_receipt_upgrade_and_direct_uninstall(self) -> None:
        for upgrade, agents_only in ((False, True), (True, True), (False, False), (True, False)):
            with self.subTest(upgrade=upgrade, agents_only=agents_only), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                self._install_base_receipt(repo, codex_home, state_home, agents_only=agents_only)
                identities = {path: path.lstat().st_ino for path in destination_paths(codex_home).values()}
                if upgrade:
                    with self.assertRaisesRegex(InstallError, "lost its receipt identity"):
                        install(repo, codex_home, state_home, FakeRunner([]), agents_only=agents_only)
                    managed_repository(repo).rename(root / "preserved-old-package")
                    install(repo, codex_home, state_home, FakeRunner(
                        [] if agents_only else install_results(repo, True, True)
                    ), agents_only=agents_only)
                    self.assertEqual({path: path.lstat().st_ino for path in identities}, identities)
                runner = FakeRunner(
                    [] if agents_only else [plugin_list_response(repo), marketplace_list_response(repo), removal_response(), removal_response()]
                )
                if not upgrade and not agents_only:
                    # Old receipts never recorded package identity. Retain the
                    # package and evidence rather than adopting its marker.
                    managed = managed_repository(repo)
                    before = managed.stat().st_ino
                    with self.assertRaisesRegex(InstallError, "lacks receipt identity"):
                        uninstall(repo, codex_home, state_home, runner)
                    self.assertEqual(managed.stat().st_ino, before)
                    self.assertTrue(receipt_path(state_home).exists())
                    managed.rename(root / "preserved-old-package")
                    runner = FakeRunner([])
                uninstall(repo, codex_home, state_home, runner, agents_only=agents_only)
                self.assertEqual(list((codex_home / "agents").iterdir()), [])
                if agents_only:
                    assert_package_only_receipt(repo, state_home, owned=upgrade)
                else:
                    self.assertFalse(receipt_path(state_home).exists())

    def test_base_receipt_migration_recovers_process_exit_and_private_replacement(self) -> None:
        from scripts import install as module
        for boundary in ("frozen", "anchor", "committed"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                self._install_base_receipt(repo, codex_home, state_home)
                pid = os.fork()
                if pid == 0:
                    write, link = module._write_codex_receipt, os.link
                    def checkpoint(path: Path, value: object, **kwargs: object) -> None:
                        write(path, value, **kwargs)
                        if boundary == ("frozen" if kwargs.get("pending_link_migration") else "committed"):
                            os._exit(73)
                    def anchor(*args: object, **kwargs: object) -> None:
                        link(*args, **kwargs)
                        if boundary == "anchor":
                            os._exit(73)
                    with mock.patch("scripts.install._write_codex_receipt", checkpoint), mock.patch("scripts.install.os.link", anchor):
                        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                    os._exit(74)
                wait_for_crashed_child(pid)
                receipt = load_receipt(state_home)
                self.assertTrue(all("link_anchor" in entry for entry in receipt["links"]))
                if boundary == "anchor":
                    anchor = next(path for path in (codex_home / "agents").iterdir() if path.name.endswith(".anchor"))
                    saved = root / "original-anchor"
                    anchor.rename(saved)
                    anchor.write_text("private replacement")
                    before = receipt_path(state_home).read_bytes()
                    for _ in range(2):
                        with self.assertRaisesRegex(InstallError, "anchor is occupied"):
                            uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                        self.assertEqual(receipt_path(state_home).read_bytes(), before)
                        self.assertEqual(anchor.read_text(), "private replacement")
                    anchor.unlink()
                    saved.rename(anchor)
                if boundary != "committed":
                    unproven = [
                        Path(entry["destination"])
                        for entry in load_receipt(state_home)["links"]
                        if entry.get("link_anchor")
                        and not os.path.lexists(entry["link_anchor"])
                    ]
                    self.assertTrue(unproven)
                    with self.assertRaisesRegex(InstallError, "unproven.*depends"):
                        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                    self.assertTrue(receipt_path(state_home).exists())
                    self.assertTrue(all(path.is_symlink() for path in unproven))
                    for path in unproven:
                        path.unlink()
                uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(list((codex_home / "agents").iterdir()), [])

    def test_matching_links_without_base_receipt_do_not_gain_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            self._install_base_receipt(repo, codex_home, state_home)
            receipt_path(state_home).unlink()
            paths = list(destination_paths(codex_home).values())
            identities = {path: path.lstat().st_ino for path in paths}
            uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            with self.assertRaisesRegex(InstallError, "lost its receipt identity"):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            managed_repository(repo).rename(root / "preserved-unreceipted-package")
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            entries = load_receipt(state_home)["links"]
            self.assertEqual({entry["destination"] for entry in entries}, {str(path) for path in paths})
            self.assertTrue(all(set(entry) == {"source", "destination"} for entry in entries))
            with self.assertRaisesRegex(InstallError, "unproven.*depends"):
                uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertEqual({path: path.lstat().st_ino for path in paths}, identities)
            self.assertTrue(all(path.is_file() for path in paths))
            self.assertEqual(load_receipt(state_home)["links"], entries)

    def test_base_receipt_migration_preserves_replacements_across_exit(self) -> None:
        for boundary in ("before-anchor", "after-anchor"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                self._install_base_receipt(repo, codex_home, state_home)
                destinations = list(destination_paths(codex_home).values())
                destinations[0].unlink()
                destinations[0].write_text("regular replacement")
                destinations[1].unlink()
                destinations[1].symlink_to(root / "unrelated")
                original_link = os.link
                selected = None
                def interrupt_anchor(source: object, target: object, **kwargs: object) -> None:
                    nonlocal selected
                    if ".expskill-codex-link-anchor-" in str(target):
                        selected = Path(source)
                        if boundary == "after-anchor":
                            original_link(source, target, **kwargs)
                        raise SystemExit(73)
                    original_link(source, target, **kwargs)
                with mock.patch("scripts.install.os.link", interrupt_anchor), self.assertRaises(SystemExit):
                    uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertIsNotNone(selected)
                old_target = os.readlink(selected)
                selected.rename(root / "original-inode")
                selected.symlink_to(old_target)
                replacement_inode = selected.lstat().st_ino
                # A missing anchor cannot prove ownership after a crash. Keep
                # both replacements and original-looking links as dependencies.
                with self.assertRaisesRegex(InstallError, "unproven.*depends"):
                    uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(selected.lstat().st_ino, replacement_inode)
                self.assertEqual(set((codex_home / "agents").iterdir()), set(destinations))
                self.assertTrue(receipt_path(state_home).exists())
                selected.unlink()
                for destination in destinations[2:]:
                    if destination.is_symlink():
                        destination.unlink()
                uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(destinations[0].read_text(), "regular replacement")
                self.assertEqual(os.readlink(destinations[1]), str(root / "unrelated"))
                assert_package_only_receipt(repo, state_home, owned=False)

    def test_pending_uninstall_preserves_replacement_without_publishing_links(self) -> None:
        for boundary in ("receipt", "stage", "anchor", "published"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                from scripts import install as module
                original_write = module._write_codex_install_journal
                def write(path: Path, payload: dict[str, object]) -> None:
                    original_write(path, payload)
                    phases = {"stage": "anchoring", "anchor": "staged", "published": "published"}
                    if boundary in phases and any(record["phase"] == phases[boundary] for record in payload["links"]):
                        raise SystemExit(73)
                with mock.patch("scripts.install._write_codex_install_journal", write), mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit):
                    with self.assertRaises(SystemExit):
                        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                destination = next(iter(destination_paths(codex_home).values()))
                if os.path.lexists(destination):
                    destination.unlink()
                destination.write_text("user replacement")
                with mock.patch("scripts.install._create_codex_link", side_effect=AssertionError("uninstall must not publish")), mock.patch("scripts.install._recover_codex_transaction_link", side_effect=AssertionError("uninstall must not resume publication")):
                    uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(destination.read_text(), "user replacement")
                self.assertEqual(list(destination.parent.iterdir()), [destination])
                self.assertFalse((state_home / "expskill/codex-install.json").exists())
                self._assert_package_only_receipt(repo, state_home)

    def test_pending_uninstall_retains_authority_after_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit), self.assertRaises(SystemExit):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            paths = list(destination_paths(codex_home).values())
            paths[0].unlink()
            paths[0].write_text("user replacement")
            from scripts import install as module
            remove = module._remove_codex_recorded_link
            def fail_one(link: object) -> bool:
                if link.destination == paths[1]:
                    raise InstallError("owned cleanup blocked")
                return remove(link)
            for _ in range(2):
                with mock.patch("scripts.install._remove_codex_recorded_link", fail_one), self.assertRaisesRegex(InstallError, "owned cleanup blocked"):
                    uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertTrue((state_home / "expskill/codex-install.json").exists() or receipt_path(state_home).exists())
                self.assertTrue(paths[1].is_symlink())
                self.assertTrue(all(not os.path.lexists(path) for path in paths[2:]))
            uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertEqual(list(paths[0].parent.iterdir()), [paths[0]])

    def test_pending_uninstall_recovers_staged_retirement_exit(self) -> None:
        from scripts import install as module
        for initial_phase in ("staging", "anchoring", "staged"):
            with self.subTest(initial_phase=initial_phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                symlink = Path.symlink_to
                write = module._write_codex_install_journal
                def create(path: Path, target: object, *args: object, **kwargs: object) -> None:
                    symlink(path, target, *args, **kwargs)
                    if initial_phase == "staging" and path.name.endswith(".link"):
                        raise SystemExit(73)
                def checkpoint(path: Path, payload: dict[str, object]) -> None:
                    write(path, payload)
                    if initial_phase != "staging" and any(item["phase"] == initial_phase for item in payload["links"]):
                        raise SystemExit(73)
                with mock.patch.object(Path, "symlink_to", create), mock.patch("scripts.install._write_codex_install_journal", checkpoint), self.assertRaises(SystemExit):
                    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                pid = os.fork()
                if pid == 0:
                    exchange = module._renameat_exchange
                    def crash(*args: object) -> None:
                        exchange(*args)
                        os._exit(73)
                    with mock.patch("scripts.install._renameat_exchange", crash):
                        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                    os._exit(74)
                wait_for_crashed_child(pid)
                retired, = (codex_home / "agents").glob("*.retire")
                unlink = os.unlink
                def interrupt(path: object, *args: object, **kwargs: object) -> None:
                    if path == retired.name:
                        raise SystemExit(73)
                    unlink(path, *args, **kwargs)
                for _ in range(2):
                    with mock.patch("scripts.install.os.unlink", interrupt), self.assertRaises(SystemExit):
                        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(list((codex_home / "agents").iterdir()), [])

    def test_pending_uninstall_retires_unpublished_legacy_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            destination = destination_paths(codex_home)["expskill-review"]
            source = repo / "plugins/expskill/assets/agents" / destination.name
            destination.parent.mkdir(parents=True)
            destination.symlink_to(source)
            symlink = Path.symlink_to
            def crash(path: Path, target: object, *args: object, **kwargs: object) -> None:
                symlink(path, target, *args, **kwargs)
                if str(target) == str(source):
                    raise SystemExit(73)
            with mock.patch.object(Path, "symlink_to", crash), mock.patch("scripts.install._write_codex_receipt", side_effect=InstallError("receipt failed")), self.assertRaises(SystemExit):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertTrue(any(path.name.endswith(".link") for path in destination.parent.iterdir()))
            with mock.patch.object(Path, "symlink_to", side_effect=AssertionError("uninstall must not publish")):
                uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertEqual(list(destination.parent.iterdir()), [])
            self.assertFalse((state_home / "expskill/codex-install.json").exists())

    def test_pending_full_uninstall_transfers_cli_authority_before_journal_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit), self.assertRaises(SystemExit):
                install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            destination = destination_paths(codex_home)["expskill-review"]
            destination.unlink()
            destination.write_text("user replacement")
            with mock.patch("scripts.install._clear_codex_install_journal", side_effect=SystemExit), self.assertRaises(SystemExit):
                uninstall(repo, codex_home, state_home, FakeRunner([]))
            self.assertEqual(list(destination.parent.iterdir()), [destination])
            self.assertTrue((state_home / "expskill/codex-install.json").is_file())
            receipt = load_receipt(state_home)
            self.assertTrue(receipt["plugin_installed"])
            self.assertTrue(receipt["marketplace_added"])
            runner = FakeRunner([
                plugin_list_response(repo), marketplace_list_response(repo),
                FakeResult(1, stderr="plugin cleanup blocked"),
            ])
            with self.assertRaisesRegex(InstallError, "plugin cleanup blocked"):
                uninstall(repo, codex_home, state_home, runner)
            self.assertEqual(load_receipt(state_home), receipt)
            retry = FakeRunner([
                plugin_list_response(repo), marketplace_list_response(repo),
                removal_response(), removal_response(),
            ])
            uninstall(repo, codex_home, state_home, retry)
            self.assertEqual(runner.results + retry.results, [])
            self.assertFalse(any("add" in call for call in runner.calls + retry.calls))
            self.assertEqual(destination.read_text(), "user replacement")
            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(managed_repository(repo).exists())

    def test_codex_canonical_alias_lock_contends_across_processes(self) -> None:
        from scripts import install as module
        for alias_first in (False, True):
            with self.subTest(alias_first=alias_first), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                canonical = root / "canonical" / "state"
                canonical.mkdir(parents=True)
                alias = root / "alias" / "state"
                alias.parent.mkdir()
                alias.symlink_to(canonical, target_is_directory=True)
                first, second = (alias, canonical) if alias_first else (canonical, alias)
                self.assertEqual(module._codex_install_journal_path(first), module._codex_install_journal_path(second))
                with module._codex_transaction(first, create=True):
                    pid = os.fork()
                    if pid == 0:
                        module._CODEX_TRANSACTION_LEASES.clear()
                        module._STATE_BINDINGS.clear()
                        try:
                            with module._codex_transaction(second, create=True):
                                os._exit(74)
                        except InstallError as error:
                            os._exit(73 if "already active" in str(error) else 75)
                    wait_for_crashed_child(pid)
                with module._codex_transaction(second, create=True) as acquired:
                    self.assertTrue(acquired)

    def test_legacy_restoration_survives_repeated_failed_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            destination = destination_paths(codex_home)["expskill-review"]
            source = repo / "plugins/expskill/assets/agents" / destination.name
            destination.parent.mkdir(parents=True)
            destination.symlink_to(source)
            # Keep the original inode allocated so recreation cannot hide the bug.
            os.link(destination, root / "original-legacy", follow_symlinks=False)
            journal = state_home / "expskill" / "codex-install.json"
            for attempt in range(3):
                runner = FakeRunner([
                    marketplace_list_response(repo if attempt else None),
                    marketplace_add_response(repo, bool(attempt)),
                    plugin_list_response(),
                    FakeResult(1, stderr="plugin add failed"),
                    FakeResult(1, stderr="marketplace compensation failed"),
                ])
                with self.assertRaisesRegex(InstallError, "plugin add failed.*marketplace compensation failed"):
                    install(repo, codex_home, state_home, runner)
                self.assertEqual(runner.results, [])
                self.assertTrue(journal.is_file())
                self.assertEqual(Path(os.readlink(destination)), source)
            install(repo, codex_home, state_home, FakeRunner(install_results(repo, marketplace_present=True)))
            self.assertFalse(journal.exists())
            uninstall(repo, codex_home, state_home, FakeRunner([
                plugin_list_response(repo), marketplace_list_response(repo),
                removal_response(), removal_response(),
            ]))
            self.assertEqual(list(destination.parent.iterdir()), [])

    def test_legacy_restoration_recovers_process_exit(self) -> None:
        from scripts import install as module
        for boundary in ("intent", "symlink", "identified", "published", "checkpointed"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                destination = destination_paths(codex_home)["expskill-review"]
                source = repo / "plugins/expskill/assets/agents" / destination.name
                destination.parent.mkdir(parents=True)
                destination.symlink_to(source)
                os.link(destination, root / "original-legacy", follow_symlinks=False)
                pid = os.fork()
                if pid == 0:
                    original_symlink, original_sync = Path.symlink_to, module._fsync_directory
                    original_rename = module._renameat_noreplace
                    original_write = module._write_codex_install_journal
                    restoring = False
                    def symlink(path: Path, target: Path, **kwargs: object) -> None:
                        nonlocal restoring
                        if Path(target) == source and boundary == "intent":
                            os._exit(73)
                        original_symlink(path, target, **kwargs)
                        if Path(target) == source:
                            restoring = True
                            if boundary == "symlink":
                                os._exit(73)
                    def sync(path: Path) -> None:
                        original_sync(path)
                        if boundary == "published" and restoring and destination.is_symlink() and Path(os.readlink(destination)) == source:
                            os._exit(73)
                    def rename(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                        if restoring and boundary == "identified" and target_name == destination.name:
                            os._exit(73)
                        original_rename(source_fd, source_name, target_fd, target_name)
                    def write(path: Path, payload: dict[str, object]) -> None:
                        original_write(path, payload)
                        if restoring and boundary == "checkpointed":
                            record = next(item for item in payload["links"] if item["destination"] == str(destination))
                            if "legacy_restore" not in record:
                                os._exit(73)
                    with mock.patch.object(Path, "symlink_to", symlink), mock.patch("scripts.install._fsync_directory", sync), mock.patch("scripts.install._renameat_noreplace", rename), mock.patch("scripts.install._write_codex_install_journal", write):
                        install(repo, codex_home, state_home, FakeRunner([
                            marketplace_list_response(), marketplace_add_response(repo),
                            plugin_list_response(), FakeResult(1, stderr="plugin add failed"),
                            FakeResult(1, stderr="marketplace compensation failed"),
                        ]))
                    os._exit(74)
                wait_for_crashed_child(pid)
                self.assertTrue((state_home / "expskill" / "codex-install.json").is_file())
                install(repo, codex_home, state_home, FakeRunner(install_results(repo, marketplace_present=True)))
                uninstall(repo, codex_home, state_home, FakeRunner([
                    plugin_list_response(repo), marketplace_list_response(repo),
                    removal_response(), removal_response(),
                ]))
                self.assertEqual(list(destination.parent.iterdir()), [])

    def test_legacy_restoration_never_adopts_raced_same_target_replacement(self) -> None:
        from scripts import install as module
        for after_publish in (False, True):
            with self.subTest(after_publish=after_publish), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                destination = destination_paths(codex_home)["expskill-review"]
                source = repo / "plugins/expskill/assets/agents" / destination.name
                destination.parent.mkdir(parents=True)
                destination.symlink_to(source)
                original_rename = module._renameat_noreplace
                replacement_identity = None
                saved = root / "installer-restoration"
                def rename(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                    nonlocal replacement_identity
                    if target_name == destination.name and os.readlink(source_name, dir_fd=source_fd) == str(source):
                        if after_publish:
                            original_rename(source_fd, source_name, target_fd, target_name)
                            destination.rename(saved)
                        destination.symlink_to(source)
                        replacement_identity = destination.lstat().st_ino
                        if after_publish:
                            return
                    original_rename(source_fd, source_name, target_fd, target_name)
                with mock.patch("scripts.install._renameat_noreplace", rename):
                    with self.assertRaisesRegex(InstallError, "plugin add failed.*legacy link rollback"):
                        install(repo, codex_home, state_home, FakeRunner([
                            marketplace_list_response(), marketplace_add_response(repo),
                            plugin_list_response(), FakeResult(1, stderr="plugin add failed"),
                            FakeResult(1, stderr="marketplace compensation failed"),
                        ]))
                self.assertIsNotNone(replacement_identity)
                journal = state_home / "expskill" / "codex-install.json"
                before = journal.read_bytes()
                for _ in range(2):
                    with self.assertRaisesRegex(InstallError, "legacy link recovery failed"):
                        install(repo, codex_home, state_home, FakeRunner([]))
                    self.assertEqual(destination.lstat().st_ino, replacement_identity)
                    self.assertEqual(journal.read_bytes(), before)
                destination.unlink()
                if after_publish:
                    saved.rename(destination)
                install(repo, codex_home, state_home, FakeRunner(install_results(repo, marketplace_present=True)))
                self.assertFalse(journal.exists())

    def test_uninstall_resumes_anchor_exchange_process_exit(self) -> None:
        from scripts import install as module
        for replacement in ("absent", "public", "anchor"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                recorded = load_receipt(state_home)["links"][0]
                anchor, destination = Path(recorded["link_anchor"]), Path(recorded["destination"])
                pid = os.fork()
                if pid == 0:
                    original_exchange = module._renameat_exchange
                    def exchange(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                        original_exchange(source_fd, source_name, target_fd, target_name)
                        if source_name == anchor.name:
                            os._exit(73)
                    with mock.patch("scripts.install._renameat_exchange", exchange):
                        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                    os._exit(74)
                wait_for_crashed_child(pid)
                self.assertTrue(anchor.is_file())
                retired = list(anchor.parent.glob("*.retire"))
                self.assertEqual(len(retired), 1)
                self.assertTrue(retired[0].is_symlink())
                self.assertTrue(receipt_path(state_home).is_file())
                preserved = None
                if replacement != "absent":
                    preserved = destination if replacement == "public" else anchor
                    if os.path.lexists(preserved):
                        preserved.unlink()
                    preserved.write_text("user replacement")
                original_unlink = os.unlink
                def unlink(path: str, *args: object, **kwargs: object) -> None:
                    if path == retired[0].name:
                        raise PermissionError("anchor retirement blocked")
                    original_unlink(path, *args, **kwargs)
                with mock.patch("scripts.install.os.unlink", unlink):
                    for _ in range(2):
                        with self.assertRaisesRegex(InstallError, "anchor retirement blocked"):
                            uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                        self.assertTrue(retired[0].is_symlink())
                        self.assertIn(recorded, load_receipt(state_home)["links"])
                for _ in range(2):
                    uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(list(anchor.parent.iterdir()), [] if preserved is None else [preserved])
                if preserved is not None:
                    self.assertEqual(preserved.read_text(), "user replacement")
                self._assert_package_only_receipt(repo, state_home)

    def test_early_cli_inspection_failure_allows_agents_only_retry(self) -> None:
        for failure in ("missing", "command", "json", "shape"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                if failure == "missing":
                    runner = mock.Mock(side_effect=FileNotFoundError("codex"))
                else:
                    result = {
                        "command": FakeResult(1, stderr="marketplace inspection failed"),
                        "json": FakeResult(stdout="not JSON"),
                        "shape": FakeResult(payload={"marketplaces": "invalid"}),
                    }[failure]
                    runner = FakeRunner([result])
                with self.assertRaises(InstallError):
                    install(repo, codex_home, state_home, runner)
                self.assertFalse((state_home / "expskill" / "codex-install.json").exists())
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(len(load_receipt(state_home)["links"]), len(PROFILE_NAMES))

    def test_early_cli_failure_preserves_interrupted_full_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            journal = state_home / "expskill" / "codex-install.json"
            before = journal.read_bytes()
            with self.assertRaisesRegex(InstallError, "inspection failed"):
                install(repo, codex_home, state_home, FakeRunner([FakeResult(1, stderr="inspection failed")]))
            previous, current = json.loads(before), json.loads(journal.read_bytes())
            self.assertEqual(current.pop("package")["ino"], managed_repository(repo).stat().st_ino)
            previous.pop("package")
            self.assertEqual(current, previous)
            before = journal.read_bytes()
            with self.assertRaisesRegex(InstallError, "does not match this install"):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertEqual(journal.read_bytes(), before)

    def test_early_cli_failure_preserves_prior_migration_recovery_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            with self.assertRaisesRegex(InstallError, "recovery unavailable"):
                install(repo, codex_home, state_home, FakeRunner([
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(), removal_response(), marketplace_add_response(repo),
                    FakeResult(1, stderr="plugin add failed"), removal_response(),
                    FakeResult(1, stderr="recovery unavailable"),
                ]))
            journal = state_home / "expskill" / "codex-install.json"
            # Older interrupted migrations may have only their migration journal.
            journal.unlink()
            with self.assertRaisesRegex(InstallError, "lost its receipt identity"):
                install(repo, codex_home, state_home, FakeRunner([]))
            managed_repository(repo).rename(root / "preserved-unreceipted-package")
            runner = FakeRunner([
                marketplace_list_response(),
                legacy_marketplace_add_response(repo, recovery_repository(repo)),
                plugin_list_response(), plugin_add_response(repo),
                FakeResult(1, stderr="inspection failed after recovery"),
            ])
            with self.assertRaisesRegex(InstallError, "inspection failed after recovery"):
                install(repo, codex_home, state_home, runner)
            self.assertEqual(runner.results, [])
            self.assertTrue(journal.is_file())
            self.assertEqual(
                json.loads((state_home / "expskill/codex-migration.json").read_text())["recovery_ino"],
                recovery_repository(repo).stat().st_ino,
            )
            before = journal.read_bytes()
            with self.assertRaisesRegex(InstallError, "does not match this install"):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertEqual(journal.read_bytes(), before)

    def test_pending_install_can_be_uninstalled_without_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            runner = FakeRunner(
                [plugin_list_response(repo), marketplace_list_response(repo),
                   removal_response(), removal_response()]
            )
            uninstall(repo, codex_home, state_home, runner)
            self.assertEqual(runner.results, [])
            self.assertFalse(any("add" in call for call in runner.calls))
            self.assertFalse((state_home / "expskill" / "codex-install.json").exists())
            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(managed_repository(repo).exists())
            self.assertFalse(any((codex_home / "agents").iterdir()))

    def test_codex_lock_survives_state_leaf_replacement(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            state_home = Path(temporary) / "state"
            with module._codex_transaction(state_home, create=True):
                (state_home / "expskill").rename(state_home / "displaced")
                (state_home / "expskill").mkdir()
                # A distinct process has no process-local lease table.
                pid = os.fork()
                if pid == 0:
                    module._CODEX_TRANSACTION_LEASES.clear()
                    module._STATE_BINDINGS.clear()
                    try:
                        with module._codex_transaction(state_home, create=True):
                            os._exit(74)
                    except InstallError:
                        os._exit(73)
                wait_for_crashed_child(pid)

    def test_codex_rejects_substituted_state_before_journal_write(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            state_home = Path(temporary) / "state"
            with module._codex_transaction(state_home, create=True):
                (state_home / "expskill").rename(state_home / "displaced")
                (state_home / "expskill").mkdir()
                with self.assertRaisesRegex(InstallError, "binding.*replaced"):
                    module._write_codex_install_journal(
                        state_home / "expskill" / "codex-install.json", {}
                    )
                self.assertEqual(list((state_home / "expskill").iterdir()), [])

    def test_shared_state_binding_supports_codex_lock_opt_out(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("scripts.install.fcntl.flock", wraps=fcntl.flock) as flock:
                binding = module._open_state_binding(
                    Path(temporary) / "state", create=True, lock=False
                )
                module._close_state_binding(binding)
            flock.assert_not_called()

    def test_state_substitution_during_build_is_rejected_before_marker_write(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            state_home = root / "state"
            original_build = module._build_codex_marketplace
            candidates = []
            def substitute(source: Path, candidate: Path) -> object:
                result = original_build(source, candidate)
                (state_home / "expskill").rename(root / "displaced")
                candidate.mkdir(parents=True)
                (candidate / "user-data").write_text("preserve")
                candidates.append(candidate)
                return result
            with mock.patch("scripts.install._build_codex_marketplace", substitute):
                with self.assertRaisesRegex(InstallError, "binding.*replaced"):
                    install(repo, root / "codex", state_home, FakeRunner([]), agents_only=True)
            self.assertEqual(list(candidates[0].iterdir()), [candidates[0] / "user-data"])

    def test_created_state_ancestors_are_durable_before_external_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            state_home, codex_home = root / "nested" / "state", root / "codex"
            synced = set()
            original_fsync = os.fsync
            def fsync(fd: int) -> None:
                metadata = os.fstat(fd)
                synced.add((metadata.st_dev, metadata.st_ino))
                original_fsync(fd)
            def external(command: list[str]) -> FakeResult:
                for path in (root, root / "nested", state_home):
                    metadata = path.stat()
                    self.assertIn((metadata.st_dev, metadata.st_ino), synced, str(path))
                raise SystemExit
            with mock.patch("scripts.install.os.fsync", fsync):
                with self.assertRaises(SystemExit):
                    install(repo, codex_home, state_home, external)

    def test_package_publication_syncs_source_and_removed_temporary_parent(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            events = []
            original_rename, original_fsync, original_rmdir = module._renameat_noreplace, os.fsync, os.rmdir
            def rename(source_fd: int, source: str, target_fd: int, target: str) -> None:
                original_rename(source_fd, source, target_fd, target)
                source_parent = Path(os.readlink(f"/proc/self/fd/{source_fd}"))
                if source == "marketplace" and source_parent.name.startswith(".codex-package-"):
                    events.append(("rename", source_parent, Path(os.readlink(f"/proc/self/fd/{target_fd}"))))
            def fsync(fd: int) -> None:
                events.append(("sync", Path(os.readlink(f"/proc/self/fd/{fd}"))))
                original_fsync(fd)
            def rmdir(path: Path, *args: object, **kwargs: object) -> None:
                original_rmdir(path, *args, **kwargs)
                record = module._retirement_record_descriptor(str(path), directory=True)
                if record and record[0].startswith(".codex-package-"):
                    parent = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}"))
                    events.append(("remove", parent / record[0]))
            with mock.patch.object(module, "_renameat_noreplace", rename), mock.patch("scripts.install.os.fsync", fsync), mock.patch("scripts.install.os.rmdir", rmdir):
                install(repo, root / "codex", root / "state", FakeRunner([]), agents_only=True)
            index = next(i for i, event in enumerate(events) if event[0] == "rename")
            _, source_parent, target_parent = events[index]
            removal = events.index(("remove", source_parent), index)
            self.assertIn(("sync", source_parent), events[index:removal])
            self.assertIn(("sync", target_parent), events[index:removal])
            self.assertIn(("sync", target_parent), events[removal:])

    def test_recovered_public_link_is_synced_before_published_checkpoint(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            original_write = module._write_codex_install_journal
            def exit_before_checkpoint(path: Path, payload: dict) -> None:
                if any(link["phase"] == "published" for link in payload["links"]):
                    raise SystemExit
                original_write(path, payload)
            with mock.patch("scripts.install._write_codex_install_journal", exit_before_checkpoint):
                with self.assertRaises(SystemExit):
                    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            synced = []
            original_fsync = os.fsync
            def fsync(fd: int) -> None:
                synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")))
                original_fsync(fd)
            def checkpoint(path: Path, payload: dict) -> None:
                if any(link["phase"] == "published" for link in payload["links"]):
                    self.assertIn(codex_home / "agents", synced)
                original_write(path, payload)
            with mock.patch("scripts.install.os.fsync", fsync), mock.patch("scripts.install._write_codex_install_journal", checkpoint):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

    def test_unproven_deterministic_stages_are_preserved_in_both_intent_phases(self) -> None:
        from scripts import install as module
        for phase in ("planned", "staging"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                links = module._planned_codex_links(repo, codex_home, state_home)
                stage = module._codex_link_staging_path(links[0].source, links[0].destination)
                stage.parent.mkdir(parents=True)
                stage.symlink_to(links[0].source)
                before = stage.lstat()
                payload = module._new_codex_install_journal(repo, codex_home, managed_repository(repo), links, True)
                if phase == "staging":
                    payload["links"][0]["phase"] = phase
                    payload["links"][0]["staged_destination"] = str(stage)
                module._write_codex_install_journal(state_home / "expskill" / "codex-install.json", payload)
                with self.assertRaises(InstallError):
                    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                self.assertEqual(stage.lstat().st_ino, before.st_ino)

    def test_raced_regular_prestate_does_not_poison_retry(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            destination = destination_paths(codex_home)["expskill-review"]
            original_new = module._new_codex_install_journal
            def race(*args: object, **kwargs: object) -> dict:
                destination.parent.mkdir(parents=True)
                destination.write_text("user data")
                return original_new(*args, **kwargs)
            with mock.patch("scripts.install._new_codex_install_journal", race):
                with self.assertRaises(InstallError):
                    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            destination.unlink()
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertTrue(destination.is_symlink())

    def test_unproven_legacy_receipt_keeps_its_package_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            payload = load_receipt(state_home)
            payload["links"] = [{key: entry[key] for key in ("source", "destination")} for entry in payload["links"]]
            receipt_path(state_home).write_text(json.dumps(payload))
            try:
                uninstall(repo, codex_home, state_home, FakeRunner([
                    plugin_list_response(repo), marketplace_list_response(repo),
                    removal_response(), removal_response(),
                ]))
            except InstallError:
                pass
            self.assertTrue(receipt_path(state_home).exists())
            self.assertTrue(all(path.exists() for path in destination_paths(codex_home).values()))

    def test_install_preflight_rejects_invalid_utf8_readme_without_side_effects(self) -> None:
        """Regression: repository decoding failures stay controlled before installation."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner([])
            (repo / "README.md").write_bytes(b"\xff\xfe")

            try:
                install(repo, codex_home, state_home, runner)
            except InstallError as error:
                self.assertIn("README", str(error))
                self.assertIn("UTF-8 text", str(error))
            except UnicodeError as error:
                self.fail(f"install preflight leaked a decode exception: {error}")
            else:
                self.fail("install preflight accepted an invalid UTF-8 README")

            self.assertEqual(runner.calls, [])
            self.assertFalse(codex_home.exists())
            self.assertFalse(receipt_path(state_home).exists())

    def test_seeded_plugin_package_preserves_every_real_phase_entrypoint(self) -> None:
        """Regression: installation fixtures silently omit independently callable phases."""

        with tempfile.TemporaryDirectory() as temporary:
            repository = seed_repository(Path(temporary) / "repository")
            skills_root = repository / "plugins" / "expskill" / "content" / "skills"
            self.assertEqual({entry.name for entry in skills_root.iterdir()}, set(SKILL_NAMES))
            for name in SKILL_NAMES:
                self.assertTrue((skills_root / name / "SKILL.md").is_file(), name)
                self.assertTrue(
                    (
                        repository
                        / "plugins"
                        / "expskill"
                        / "codex"
                        / "skill-adapters"
                        / name
                        / "agents"
                        / "openai.yaml"
                    ).is_file(),
                    name,
                )

    def test_seed_repository_copies_route_neutral_plugin_scripts(self) -> None:
        """Fixture regression: installed-package inputs retain the centralized helper."""

        with tempfile.TemporaryDirectory() as temporary:
            repository = seed_repository(Path(temporary) / "repository")
            source = ROOT / "plugins" / "expskill" / "content" / "scripts"
            destination = repository / "plugins" / "expskill" / "content" / "scripts"
            self.assertTrue(destination.is_dir(), destination)
            source_files = {
                path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            destination_files = {
                path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            self.assertEqual(destination_files, source_files)
            self.assertTrue((destination / "worktrees.py").is_file())
            self.assertFalse((destination / "worktrees.py").is_symlink())
            self.assertGreater((destination / "worktrees.py").stat().st_size, 0)
            self.assertTrue((destination / "plan_graph.py").is_file())
            self.assertFalse((destination / "plan_graph.py").is_symlink())
            self.assertGreater((destination / "plan_graph.py").stat().st_size, 0)

    def test_seed_repository_copies_hooks_and_pinned_third_party_sources(self) -> None:
        """Regression: install fixtures must match the complete plugin package."""

        with tempfile.TemporaryDirectory() as temporary:
            repository = seed_repository(Path(temporary) / "repository")
            source = ROOT / "plugins" / "expskill"
            destination = repository / "plugins" / "expskill"
            for relative in ("codex/hooks", "content/third-party"):
                source_files = {
                    path.relative_to(source / relative).as_posix(): path.read_bytes()
                    for path in (source / relative).rglob("*")
                    if path.is_file() and not path.is_symlink()
                }
                destination_files = {
                    path.relative_to(destination / relative).as_posix(): path.read_bytes()
                    for path in (destination / relative).rglob("*")
                    if path.is_file() and not path.is_symlink()
                }
                self.assertEqual(destination_files, source_files, relative)

    def test_seed_repository_rejects_invalid_route_neutral_helper(self) -> None:
        """Fixture regression: missing, symlinked, or empty helpers fail closed."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def source_fixture(name: str, helper_name: str) -> tuple[Path, Path]:
                source_root = root / name / "source"
                if (ROOT / ".agents").is_dir():
                    shutil.copytree(ROOT / ".agents", source_root / ".agents")
                shutil.copytree(ROOT / "plugins" / "expskill", source_root / "plugins" / "expskill")
                shutil.copytree(ROOT / "scripts", source_root / "scripts")
                return source_root, source_root / "plugins" / "expskill" / "content" / "scripts" / helper_name

            for helper_name in ("worktrees.py", "plan_graph.py"):
                for mutation in ("missing", "symlink", "empty"):
                    case = f"{helper_name}-{mutation}"
                    with self.subTest(helper=helper_name, mutation=mutation):
                        source_root, helper = source_fixture(case, helper_name)
                        if mutation == "missing":
                            helper.unlink()
                        elif mutation == "symlink":
                            target = root / case / "target.py"
                            target.write_text("target\n", encoding="utf-8")
                            helper.unlink()
                            helper.symlink_to(target)
                        else:
                            helper.write_bytes(b"")
                        with mock.patch(__name__ + ".ROOT", source_root):
                            with self.assertRaisesRegex(AssertionError, "invalid route-neutral plugin helper fixture"):
                                seed_repository(root / case / "destination")

    def test_install_accepts_checked_in_manifest_cachebuster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(install_results(repo))

            result = install(repo, codex_home, state_home, runner)

            self.assertTrue(result.plugin_installed)
            self.assertTrue(receipt_path(state_home).is_file())

    def test_install_rejects_different_valid_cachebuster_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            results = install_results(repo)
            results[-1] = plugin_add_response(repo, "0.1.0+codex.different")
            runner = FakeRunner(results + [removal_response(), removal_response()])

            with self.assertRaisesRegex(InstallError, "wrong version"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-2:],
                [
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())
            self._assert_package_only_receipt(repo, state_home)

    def test_dry_run_prints_canonical_repository_and_only_planned_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            state_home = root / "state"
            before = tuple(codex_home.parent.iterdir())
            output = StringIO()
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_home), "XDG_STATE_HOME": str(state_home)},
                clear=False,
            ):
                with redirect_stdout(output):
                    result = main(["--dry-run"])

            self.assertEqual(result, 0)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), len(PROFILE_NAMES) + 2)
            self.assertEqual(
                lines[-2],
                f"codex plugin marketplace add {_codex_managed_root(ROOT.resolve(), state_home)} --json",
            )
            self.assertEqual(lines[-1], "codex plugin add expskill@expskill --json")
            self.assertEqual(before, tuple(codex_home.parent.iterdir()))

    def test_regular_file_conflict_refuses_without_partial_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            conflict = codex_home / "agents" / "expskill-review.toml"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("user-owned\n", encoding="utf-8")
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "expskill-review.toml"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                sorted(path.name for path in conflict.parent.iterdir()),
                ["expskill-review.toml"],
            )
            self.assertEqual(runner.calls, [])
            self.assertFalse(receipt_path(state_home).exists())

    def test_install_links_every_profile_and_registers_plugin_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(install_results(repo))

            result = install(repo, codex_home, state_home, runner)
            destinations = destination_paths(codex_home)

            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(managed_repository(repo)),
                        "--json",
                    ),
                    ("codex", "plugin", "list", "--json"),
                    (
                        "codex",
                        "plugin",
                        "add",
                        PLUGIN_SELECTOR,
                        "--json",
                    ),
                ],
            )
            self.assertEqual(
                {path.destination.name for path in result.created_links},
                {path.name for path in destinations.values()},
            )
            for name, destination in destinations.items():
                self.assertTrue(destination.is_symlink(), name)
                expected = managed_repository(repo) / "plugins" / "expskill" / "agents" / f"{name}.toml"
                self.assertEqual(destination.resolve(), expected)

            receipt = load_receipt(state_home)
            self.assertEqual(receipt["repository_root"], str(repo.resolve()))
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertEqual(
                {entry["destination"] for entry in receipt["links"]},
                {str(path) for path in destinations.values()},
            )

    def test_preexisting_marketplace_and_fresh_plugin_records_only_plugin_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(install_results(repo, marketplace_present=True))

            install(repo, codex_home, state_home, runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])

    def test_preexisting_exact_plugin_is_not_claimed_as_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(install_results(repo, True, True))

            install(repo, codex_home, state_home, runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["marketplace_added"])
            self.assertFalse(receipt["plugin_installed"])

    def test_second_install_is_a_no_op_for_owned_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            first_runner = FakeRunner(install_results(repo))
            install(repo, codex_home, state_home, first_runner)
            original_receipt = load_receipt(state_home)

            second_runner = FakeRunner(install_results(repo, True, True))
            result = install(repo, codex_home, state_home, second_runner)

            self.assertEqual(result.created_links, ())
            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertEqual(receipt.pop("codex_package")["ino"], managed_repository(repo).stat().st_ino)
            original_receipt.pop("codex_package")
            self.assertEqual(receipt, original_receipt)
            self.assertEqual(len(second_runner.calls), 4)

    def test_install_migrates_checkout_based_marketplace_and_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    plugin_add_response(repo),
                ]
            )

            result = install(repo, codex_home, state_home, runner)

            self.assertTrue(result.marketplace_added)
            self.assertTrue(result.plugin_installed)
            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(managed_repository(repo)),
                        "--json",
                    ),
                    ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"),
                ],
            )
            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])

    def test_legacy_migration_failure_restores_state_owned_cli_state(self) -> None:
        """A failed replacement install must restore a working registration."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            recovery_root = recovery_repository(repo)
            runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    FakeResult(1, {"error": "replacement unavailable"}, "replacement unavailable"),
                    removal_response(),
                    legacy_marketplace_add_response(repo, recovery_root),
                    plugin_add_response(repo),
                ]
            )

            with self.assertRaisesRegex(InstallError, "replacement unavailable"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-3:],
                [
                    ("codex", "plugin", "marketplace", "remove", "expskill", "--json"),
                    ("codex", "plugin", "marketplace", "add", str(recovery_root.resolve()), "--json"),
                    ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"),
                ],
            )
            self.assertTrue(
                (recovery_root / ".agents" / "plugins" / "marketplace.json").is_file()
            )
            self.assertFalse((repo / ".agents").exists())
            self._assert_package_only_receipt(repo, state_home)

    def test_resumed_failure_restores_all_journaled_legacy_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            recovery_root = recovery_repository(repo)
            agents = codex_home / "agents"
            agents.mkdir(parents=True)
            legacy_sources: dict[str, Path] = {}
            for name, destination in destination_paths(codex_home).items():
                source = (
                    repo.resolve()
                    / "plugins"
                    / "expskill"
                    / "codex"
                    / "runtime"
                    / "agents"
                    / f"{name}.toml"
                )
                destination.symlink_to(source)
                legacy_sources[name] = source

            install_module = __import__(
                "scripts.install", fromlist=["_create_codex_links"]
            )
            original_create_links = install_module._create_codex_links

            def exit_after_seven_links(*args: object, **kwargs: object) -> None:
                original_create_links(*args, **kwargs)
                raise SystemExit("simulated exit after seven links")

            with mock.patch(
                "scripts.install._create_codex_links", exit_after_seven_links
            ):
                with self.assertRaisesRegex(SystemExit, "seven links"):
                    install(
                        repo,
                        codex_home,
                        state_home,
                        FakeRunner(
                            [
                                marketplace_list_response(repo, repo.resolve()),
                                plugin_list_response(repo, repo.resolve()),
                            ]
                        ),
                    )

            with self.assertRaisesRegex(InstallError, "replacement unavailable"):
                install(
                    repo,
                    codex_home,
                    state_home,
                    FakeRunner(
                        [
                            marketplace_list_response(repo, repo.resolve()),
                            plugin_list_response(repo, repo.resolve()),
                            removal_response(),
                            removal_response(),
                            FakeResult(
                                1,
                                {"error": "replacement unavailable"},
                                "replacement unavailable",
                            ),
                            legacy_marketplace_add_response(repo, recovery_root),
                            plugin_add_response(repo),
                        ]
                    ),
                )

            for name, destination in destination_paths(codex_home).items():
                self.assertTrue(destination.is_symlink(), name)
                self.assertEqual(Path(os.readlink(destination)), legacy_sources[name])
            self.assertFalse(
                (state_home / "expskill" / "codex-install.json").exists()
            )

    def test_legacy_plugin_removal_with_malformed_json_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    FakeResult(0, stdout="not-json"),
                    plugin_add_response(repo),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-1],
                ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"),
            )
            self.assertEqual(
                json.loads((state_home / "expskill/codex-migration.json").read_text())["recovery_ino"],
                recovery_repository(repo).stat().st_ino,
            )
            self._assert_package_only_receipt(repo, state_home)

    def test_legacy_marketplace_removal_with_malformed_json_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            recovery_root = recovery_repository(repo)
            runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(),
                    FakeResult(0, stdout="not-json"),
                    legacy_marketplace_add_response(repo, recovery_root),
                    plugin_add_response(repo),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-2:],
                [
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(recovery_root.resolve()),
                        "--json",
                    ),
                    ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"),
                ],
            )
            self.assertEqual(
                json.loads((state_home / "expskill/codex-migration.json").read_text())["recovery_ino"],
                recovery_root.stat().st_ino,
            )
            self._assert_package_only_receipt(repo, state_home)

    def test_legacy_migration_restore_failure_keeps_recovery_journal(self) -> None:
        """If compensation fails, durable migration evidence remains for retry."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            recovery_root = recovery_repository(repo)
            runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    FakeResult(1, {"error": "replacement unavailable"}, "replacement unavailable"),
                    removal_response(),
                    FakeResult(1, {"error": "recovery unavailable"}, "recovery unavailable"),
                ]
            )

            with self.assertRaisesRegex(InstallError, "recovery unavailable"):
                install(repo, codex_home, state_home, runner)

            journal = state_home / "expskill" / "codex-migration.json"
            self.assertTrue(journal.is_file())
            payload = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(payload["repository_root"], str(repo.resolve()))
            self.assertEqual(payload["marketplace_state"], "legacy")
            self.assertEqual(payload["plugin_state"], "legacy")
            self.assertTrue(payload["marketplace_removed"])
            self.assertTrue(payload["plugin_removed"])
            self.assertEqual(payload["recovery_root"], str(recovery_root.resolve()))
            self.assertTrue(
                (recovery_root / ".agents" / "plugins" / "marketplace.json").is_file()
            )

    def test_install_retries_an_unfinished_legacy_migration_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            recovery_root = recovery_repository(repo)
            failing_runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    FakeResult(1, {"error": "replacement unavailable"}, "replacement unavailable"),
                    removal_response(),
                    FakeResult(1, {"error": "recovery unavailable"}, "recovery unavailable"),
                ]
            )
            with self.assertRaisesRegex(InstallError, "recovery unavailable"):
                install(repo, codex_home, state_home, failing_runner)

            journal = state_home / "expskill" / "codex-migration.json"
            stale_payload = json.loads(journal.read_text(encoding="utf-8"))
            stale_payload["marketplace_removed"] = False
            stale_payload["plugin_removed"] = False
            journal.write_text(
                json.dumps(stale_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            retry_runner = FakeRunner(
                [
                    marketplace_list_response(),
                    legacy_marketplace_add_response(repo, recovery_root),
                    plugin_list_response(),
                    plugin_add_response(repo),
                    marketplace_list_response(repo, recovery_root),
                    plugin_list_response(repo, recovery_root),
                    removal_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    plugin_add_response(repo),
                ]
            )
            install(repo, codex_home, state_home, retry_runner)

            self.assertFalse(
                (state_home / "expskill" / "codex-migration.json").exists()
            )
            self.assertFalse(recovery_root.exists())
            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertFalse((repo / ".agents").exists())

    def test_install_resumes_a_committed_migration_after_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            recovery_root = recovery_repository(repo)
            interrupted_runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(repo, repo.resolve()),
                    removal_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    plugin_add_response(repo),
                ]
            )

            with mock.patch(
                "scripts.install._write_codex_receipt",
                side_effect=SystemExit("simulated process exit"),
            ):
                with self.assertRaisesRegex(SystemExit, "simulated process exit"):
                    install(repo, codex_home, state_home, interrupted_runner)

            journal = state_home / "expskill" / "codex-migration.json"
            self.assertTrue(json.loads(journal.read_text(encoding="utf-8"))["committed"])
            self.assertFalse(receipt_path(state_home).exists())

            retry_runner = FakeRunner(
                [
                    marketplace_list_response(repo),
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    marketplace_add_response(repo, already_added=True),
                    plugin_list_response(repo),
                    plugin_add_response(repo),
                ]
            )
            install(repo, codex_home, state_home, retry_runner)

            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertEqual(len(receipt["links"]), len(PROFILE_NAMES))
            self.assertFalse(journal.exists())
            self.assertFalse(recovery_root.exists())

    def test_initial_install_resumes_links_created_before_process_exit(self) -> None:
        """A retry claims only the links named by the durable install transaction."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"

            def interrupt_after_links(command: list[str]) -> FakeResult:
                if tuple(command[:4]) == (
                    "codex",
                    "plugin",
                    "marketplace",
                    "add",
                ):
                    raise SystemExit("simulated exit after links")
                return marketplace_list_response()

            with self.assertRaisesRegex(SystemExit, "after links"):
                install(repo, codex_home, state_home, interrupt_after_links)

            journal = state_home / "expskill" / "codex-install.json"
            self.assertTrue(journal.is_file())
            interrupted = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(
                {entry["phase"] for entry in interrupted["links"]},
                {"published"},
            )
            self.assertFalse(receipt_path(state_home).exists())
            self.assertTrue(
                all(path.is_symlink() for path in destination_paths(codex_home).values())
            )

            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(install_results(repo)),
            )

            receipt = load_receipt(state_home)
            self.assertEqual(len(receipt["links"]), len(PROFILE_NAMES))
            self.assertFalse(journal.exists())

            uninstall(
                repo,
                codex_home,
                state_home,
                FakeRunner(
                    [
                        plugin_list_response(repo),
                        marketplace_list_response(repo),
                        removal_response(),
                        removal_response(),
                    ]
                ),
            )
            self.assertTrue(
                all(
                    not os.path.lexists(path)
                    for path in destination_paths(codex_home).values()
                )
            )

    def test_install_recovers_crash_immediately_after_stage_symlink_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            original_symlink_to = Path.symlink_to
            pid = os.fork()
            if pid == 0:
                def crash_after_stage(
                    path: Path, target: Path, *args: object, **kwargs: object
                ) -> None:
                    original_symlink_to(path, target, *args, **kwargs)
                    if path.name.startswith(".expskill-codex-link-stage-"):
                        os._exit(73)

                try:
                    with mock.patch.object(Path, "symlink_to", crash_after_stage):
                        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                finally:
                    os._exit(74)
            wait_for_crashed_child(pid)

            journal = state_home / "expskill" / "codex-install.json"
            interrupted = json.loads(journal.read_text(encoding="utf-8"))
            self.assertIn("staging", {entry["phase"] for entry in interrupted["links"]})
            self.assertTrue(
                any(
                    path.name.startswith(".expskill-codex-link-stage-")
                    for path in (codex_home / "agents").iterdir()
                )
            )

            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            self.assertFalse(journal.exists())
            self.assertTrue(
                all(path.is_symlink() for path in destination_paths(codex_home).values())
            )

    def test_install_recovers_crash_immediately_after_stage_anchor_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            original_link = os.link
            pid = os.fork()
            if pid == 0:
                def crash_after_anchor(
                    source: Path, destination: Path, *args: object, **kwargs: object
                ) -> None:
                    original_link(source, destination, *args, **kwargs)
                    if Path(source).name.startswith(".expskill-codex-link-stage-"):
                        os._exit(73)

                try:
                    with mock.patch("scripts.install.os.link", crash_after_anchor):
                        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                finally:
                    os._exit(74)
            wait_for_crashed_child(pid)

            journal = state_home / "expskill" / "codex-install.json"
            interrupted = json.loads(journal.read_text(encoding="utf-8"))
            self.assertIn("anchoring", {entry["phase"] for entry in interrupted["links"]})
            agents = codex_home / "agents"
            self.assertTrue(
                any(
                    path.name.startswith(".expskill-codex-link-anchor-")
                    for path in agents.iterdir()
                )
            )

            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            self.assertFalse(journal.exists())
            self.assertTrue(
                all(path.is_symlink() for path in destination_paths(codex_home).values())
            )

    def test_stage_and_anchor_directory_barrier_precedes_staged_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            agents = codex_home.resolve() / "agents"
            events: list[str] = []
            install_module = __import__(
                "scripts.install", fromlist=["_fsync_directory"]
            )
            original_fsync = install_module._fsync_directory
            original_write = install_module._write_codex_install_journal
            original_link = os.link

            def record_fsync(path: Path) -> None:
                original_fsync(path)
                if path == agents:
                    events.append("agents-fsync")

            def record_checkpoint(path: Path, payload: object) -> None:
                if any(
                    entry.get("phase") == "staged"
                    for entry in payload.get("links", ())
                ):
                    events.append("staged-checkpoint")
                original_write(path, payload)

            def record_anchor(
                source: Path, destination: Path, *args: object, **kwargs: object
            ) -> None:
                original_link(source, destination, *args, **kwargs)
                if Path(source).name.startswith(".expskill-codex-link-stage-"):
                    events.append("anchor-created")

            with mock.patch(
                "scripts.install._fsync_directory", record_fsync
            ), mock.patch(
                "scripts.install._write_codex_install_journal", record_checkpoint
            ), mock.patch(
                "scripts.install.os.link", record_anchor
            ):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            self.assertIn("staged-checkpoint", events)
            first_anchor = events.index("anchor-created")
            first_checkpoint = events.index("staged-checkpoint")
            self.assertLess(
                events.index("agents-fsync", first_anchor), first_checkpoint
            )

    def test_initial_install_resumes_cli_ownership_after_process_exit(self) -> None:
        """Successful adds remain owned when the process exits before the receipt."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"

            with mock.patch(
                "scripts.install._write_codex_receipt",
                side_effect=SystemExit("simulated exit after CLI adds"),
            ):
                with self.assertRaisesRegex(SystemExit, "after CLI adds"):
                    install(
                        repo,
                        codex_home,
                        state_home,
                        FakeRunner(install_results(repo)),
                    )

            journal = state_home / "expskill" / "codex-install.json"
            self.assertTrue(journal.is_file())
            interrupted = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(interrupted["marketplace_phase"], "added")
            self.assertEqual(interrupted["plugin_phase"], "added")
            retry = FakeRunner(
                install_results(repo, marketplace_present=True, plugin_present=True)
            )
            install(repo, codex_home, state_home, retry)

            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertFalse(journal.exists())

            uninstall_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            uninstall(repo, codex_home, state_home, uninstall_runner)
            self.assertIn(
                ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                uninstall_runner.calls,
            )

    def test_resumed_install_failure_retains_cli_ownership_until_cleanup(self) -> None:
        self._assert_resumed_install_failure_retains_cli_ownership(False)

    def test_uncheckpointed_add_failure_retains_cli_ownership_until_cleanup(self) -> None:
        self._assert_resumed_install_failure_retains_cli_ownership(True)

    def test_initial_add_failure_retires_restored_legacy_link_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            destination = destination_paths(codex_home)["expskill-review"]
            legacy_source = (
                repo / "plugins" / "expskill" / "codex" / "runtime" / "agents"
                / destination.name
            )
            destination.parent.mkdir(parents=True)
            destination.symlink_to(legacy_source)
            with self.assertRaisesRegex(InstallError, "initial add failed"):
                install(
                    repo, codex_home, state_home,
                    FakeRunner([
                        marketplace_list_response(),
                        FakeResult(1, stderr="initial add failed"),
                    ]),
                )
            self.assertEqual(Path(os.readlink(destination)), legacy_source)
            self.assertFalse((state_home / "expskill" / "codex-install.json").exists())
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            self.assertTrue(destination.is_file())

    def _assert_resumed_install_failure_retains_cli_ownership(
        self, interrupt_before_checkpoint: bool
    ) -> None:
        for failed_add in ("marketplace", "plugin"):
            for finish in ("retry", "uninstall"):
                for preexisting in (False, True):
                    with self.subTest(
                        failed_add=failed_add, finish=finish, preexisting=preexisting
                    ), tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        repo = seed_repository(root / "repo")
                        codex_home, state_home = root / "codex", root / "state"
                        registered = {
                            "marketplace": preexisting,
                            "plugin": preexisting,
                        }
                        failing = False
                        interrupted = False
                        removals = []

                        def run(command: list[str]) -> FakeResult:
                            nonlocal interrupted
                            kind = "marketplace" if command[2] == "marketplace" else "plugin"
                            action = command[3] if kind == "marketplace" else command[2]
                            if action == "list":
                                response = (
                                    marketplace_list_response
                                    if kind == "marketplace"
                                    else plugin_list_response
                                )
                                return response(repo if registered[kind] else None)
                            if action == "add":
                                if failing and kind == failed_add:
                                    return FakeResult(1, stderr=f"injected {kind} add failure")
                                present = registered[kind]
                                registered[kind] = True
                                if (
                                    interrupt_before_checkpoint
                                    and not interrupted
                                    and kind == failed_add
                                ):
                                    interrupted = True
                                    raise SystemExit(f"after {kind} add before checkpoint")
                                return (
                                    marketplace_add_response(repo, present)
                                    if kind == "marketplace"
                                    else plugin_add_response(repo)
                                )
                            self.assertEqual(action, "remove")
                            removals.append(kind)
                            registered[kind] = False
                            return removal_response()

                        with mock.patch(
                            "scripts.install._write_codex_receipt",
                            side_effect=SystemExit("after CLI adds"),
                        ):
                            with self.assertRaises(SystemExit):
                                install(repo, codex_home, state_home, run)

                        journal = state_home / "expskill" / "codex-install.json"
                        failing = True
                        for _ in range(2):
                            with self.assertRaisesRegex(InstallError, "injected .* add failure"):
                                install(repo, codex_home, state_home, run)
                            if not preexisting:
                                self.assertTrue(journal.is_file())
                                pending = json.loads(journal.read_text(encoding="utf-8"))
                                self.assertEqual(pending["marketplace_pre_state"], "absent")
                                self.assertEqual(
                                    pending["plugin_pre_state"],
                                    None
                                    if interrupt_before_checkpoint and failed_add == "marketplace"
                                    else "absent",
                                )

                        failing = False
                        if finish == "retry":
                            install(repo, codex_home, state_home, run)
                            receipt = load_receipt(state_home)
                            self.assertIs(receipt["marketplace_added"], not preexisting)
                            self.assertIs(receipt["plugin_installed"], not preexisting)
                        uninstall(repo, codex_home, state_home, run)
                        self.assertEqual(
                            registered,
                            {"marketplace": preexisting, "plugin": preexisting},
                        )
                        if preexisting:
                            self.assertEqual(removals, [])
                            self._assert_package_only_receipt(repo, state_home)
                        else:
                            self.assertFalse(managed_repository(repo).exists())
                            self.assertFalse(receipt_path(state_home).exists())
                        self.assertFalse(journal.exists())
                        self.assertFalse(any((codex_home / "agents").iterdir()))

    def test_managed_marketplace_swap_recovers_exact_backup_after_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            managed_root = managed_repository(repo)
            managed_parent = managed_root.parent
            (repo / "README.md").write_text(
                (repo / "README.md").read_text(encoding="utf-8") + "\nupdated\n",
                encoding="utf-8",
            )
            original_fsync = __import__("scripts.install", fromlist=["_fsync_directory"])._fsync_directory
            interrupted = {"value": False}

            def exit_after_published_swap(path: Path) -> None:
                original_fsync(path)
                backups = tuple(
                    candidate
                    for candidate in managed_parent.iterdir()
                    if ".old-" in candidate.name or "swap-backup" in candidate.name
                )
                if (
                    path == managed_parent
                    and managed_root.exists()
                    and backups
                    and not interrupted["value"]
                ):
                    interrupted["value"] = True
                    raise SystemExit("simulated exit during managed swap")

            with mock.patch("scripts.install._fsync_directory", exit_after_published_swap):
                with self.assertRaisesRegex(SystemExit, "managed swap"):
                    install(repo, codex_home, state_home, FakeRunner([]))

            self.assertTrue(
                any(
                    ".old-" in candidate.name or "swap-backup" in candidate.name
                    for candidate in managed_parent.iterdir()
                )
            )

            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(
                    install_results(repo, marketplace_present=True, plugin_present=True)
                ),
            )

            self.assertTrue(managed_root.is_dir())
            self.assertFalse(
                any(
                    ".old-" in candidate.name or "swap-backup" in candidate.name
                    for candidate in managed_parent.iterdir()
                )
            )
            self.assertFalse(
                (state_home / "expskill" / "codex-install.json").exists()
            )

    def test_recovery_marketplace_swap_restores_backup_only_crash_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install_module = __import__(
                "scripts.install", fromlist=["_materialize_codex_recovery_package"]
            )
            recovery_root = seed_journaled_recovery_package(repo, codex_home, state_home)
            recovery_parent = recovery_root.parent
            (repo / "README.md").write_text(
                (repo / "README.md").read_text(encoding="utf-8") + "\nupdated\n",
                encoding="utf-8",
            )
            original_fsync = install_module._fsync_directory

            def exit_with_backup_only(path: Path) -> None:
                original_fsync(path)
                backups = tuple(
                    candidate
                    for candidate in recovery_parent.iterdir()
                    if ".swap-backup-" in candidate.name
                )
                if path == recovery_parent and not recovery_root.exists() and backups:
                    raise SystemExit("simulated recovery backup-only crash")

            interrupted_runner = FakeRunner(
                [
                    marketplace_list_response(repo, repo.resolve()),
                    plugin_list_response(),
                ]
            )
            with mock.patch(
                "scripts.install._fsync_directory", exit_with_backup_only
            ):
                with self.assertRaisesRegex(SystemExit, "backup-only"):
                    install(repo, codex_home, state_home, interrupted_runner)

            journal = json.loads(
                (state_home / "expskill" / "codex-install.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNotNone(journal["recovery_swap"])
            self.assertFalse(recovery_root.exists())
            self.assertTrue(
                any(
                    ".swap-backup-" in candidate.name
                    for candidate in recovery_parent.iterdir()
                )
            )

            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(
                    [
                        marketplace_list_response(repo, repo.resolve()),
                        plugin_list_response(),
                        removal_response(),
                        marketplace_add_response(repo),
                        plugin_add_response(repo),
                    ]
                ),
            )

            self.assertFalse(recovery_root.exists())
            self.assertFalse(
                any(
                    ".swap-backup-" in candidate.name
                    for candidate in recovery_parent.iterdir()
                )
            )

    def test_recovery_swap_cleanup_failure_retains_backup_authority_for_retry(self) -> None:
        from scripts import install as module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            recovery_root = seed_journaled_recovery_package(repo, codex_home, state_home)
            original_remove = module._remove_exact_codex_swap_backup

            def fail_recovery_backup(
                backup: Path, expected: tuple[int, int], target: Path, repository: Path,
                **kwargs: object,
            ) -> None:
                if target == recovery_root:
                    raise InstallError("injected recovery backup cleanup failure")
                original_remove(backup, expected, target, repository, **kwargs)

            with mock.patch(
                "scripts.install._remove_exact_codex_swap_backup", fail_recovery_backup
            ):
                with self.assertRaisesRegex(InstallError, "recovery backup cleanup failure"):
                    install(
                        repo,
                        codex_home,
                        state_home,
                        FakeRunner([
                            marketplace_list_response(repo, repo),
                            plugin_list_response(),
                        ]),
                    )

            backups = tuple(recovery_root.parent.glob("*.swap-backup-*"))
            self.assertEqual(len(backups), 1)
            journal = state_home / "expskill" / "codex-install.json"
            self.assertTrue(journal.is_file())
            pending = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(pending["recovery_swap"]["backup"], str(backups[0]))

            install(
                repo,
                codex_home,
                state_home,
                FakeRunner([
                    marketplace_list_response(repo, repo),
                    plugin_list_response(),
                    removal_response(),
                    marketplace_add_response(repo),
                    plugin_add_response(repo),
                ]),
            )
            self.assertFalse(backups[0].exists())
            self.assertFalse(recovery_root.exists())
            self.assertFalse(journal.exists())
            uninstall(
                repo,
                codex_home,
                state_home,
                FakeRunner([
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]),
            )
            self.assertFalse(tuple((state_home / "expskill").rglob("*.swap-backup-*")))
            self.assertFalse(managed_repository(repo).exists())
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_removes_checkout_based_cli_state_from_owned_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            runner = FakeRunner(
                [
                    plugin_list_response(repo, repo.resolve()),
                    marketplace_list_response(repo, repo.resolve()),
                    removal_response(),
                    removal_response(),
                ]
            )

            uninstall(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_preserves_unowned_registration_and_managed_package(self) -> None:
        """A pre-existing exact registration keeps its package and registration."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(install_results(repo, marketplace_present=True, plugin_present=True)),
            )
            managed_root = managed_repository(repo)
            self.assertTrue(managed_root.is_dir())
            self.assertFalse(load_receipt(state_home)["marketplace_added"])
            self.assertFalse(load_receipt(state_home)["plugin_installed"])

            runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                ]
            )

            uninstall(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                ],
            )
            self.assertTrue(managed_root.is_dir())
            self._assert_package_only_receipt(repo, state_home)

    def test_install_preserves_unproven_retired_profile_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            receipt = load_receipt(state_home)
            retired_entries = []
            retired_destinations: list[Path] = []
            for name in RETIRED_PROFILE_NAMES:
                source = (
                    repo.resolve()
                    / "plugins"
                    / "expskill"
                    / "assets"
                    / "agents"
                    / f"{name}.toml"
                )
                destination = codex_home.resolve() / "agents" / f"{name}.toml"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f'name = "{name}"\n', encoding="utf-8")
                destination.symlink_to(source)
                retired_destinations.append(destination)
                entry = {"destination": str(destination), "source": str(source)}
                retired_entries.append(entry)
                receipt["links"].append(entry)
            receipt_path(state_home).write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            runner = FakeRunner(install_results(repo, True, True))

            result = install(repo, codex_home, state_home, runner)

            self.assertEqual(result.removed_links, ())
            self.assertTrue(all(path.is_symlink() for path in retired_destinations))
            self.assertTrue(all(path.is_file() for path in retired_destinations))
            self.assertTrue(all(entry in load_receipt(state_home)["links"] for entry in retired_entries))
            self.assertEqual(
                {
                    Path(entry["destination"]).stem
                    for entry in load_receipt(state_home)["links"]
                },
                set(PROFILE_NAMES + RETIRED_PROFILE_NAMES),
            )

    def test_retired_link_removal_is_fsynced_before_receipt_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            agents = codex_home.resolve() / "agents"
            source = (
                repo.resolve()
                / "plugins"
                / "expskill"
                / "codex"
                / "runtime"
                / "agents"
                / "expskill-reviewer.toml"
            )
            destination = agents / "expskill-reviewer.toml"
            destination.symlink_to(source)
            metadata = os.lstat(destination)
            anchor = agents / (
                f".expskill-codex-link-anchor-{destination.name}-"
                f"{metadata.st_dev:x}-{metadata.st_ino:x}.anchor"
            )
            os.link(destination, anchor, follow_symlinks=False)
            receipt = load_receipt(state_home)
            receipt["links"].append(
                {
                    "source": str(source),
                    "destination": str(destination),
                    "destination_dev": metadata.st_dev,
                    "destination_ino": metadata.st_ino,
                    "link_anchor": str(anchor),
                    "link_anchor_dev": metadata.st_dev,
                    "link_anchor_ino": metadata.st_ino,
                }
            )
            receipt_path(state_home).write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            events: list[str] = []
            install_module = __import__("scripts.install", fromlist=["_fsync_directory"])
            original_fsync = install_module._fsync_directory
            original_write = install_module._write_codex_receipt

            def record_fsync(path: Path) -> None:
                original_fsync(path)
                if path == agents:
                    events.append("agents-fsync")

            def record_receipt(path: Path, value: object) -> None:
                events.append("receipt")
                original_write(path, value)

            with mock.patch("scripts.install._fsync_directory", record_fsync), mock.patch(
                "scripts.install._write_codex_receipt", record_receipt
            ):
                result = install(
                    repo,
                    codex_home,
                    state_home,
                    FakeRunner(
                        install_results(
                            repo, marketplace_present=True, plugin_present=True
                        )
                    ),
                )

            self.assertIn(destination.name, {link.destination.name for link in result.removed_links})
            self.assertFalse(os.path.lexists(destination))
            self.assertFalse(os.path.lexists(anchor))
            self.assertLess(events.index("agents-fsync"), events.index("receipt"))

    def test_unrelated_and_broken_symlink_conflicts_refuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            agents = codex_home / "agents"
            agents.mkdir(parents=True)
            unrelated_target = root / "unrelated.toml"
            unrelated_target.write_text("unrelated\n", encoding="utf-8")
            unrelated = agents / "expskill-explorer.toml"
            unrelated.symlink_to(unrelated_target)
            broken = agents / "expskill-spec.toml"
            broken.symlink_to(root / "does-not-exist.toml")
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "expskill-explorer.toml|expskill-spec.toml"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(runner.calls, [])
            self.assertTrue(unrelated.is_symlink())
            self.assertTrue(os.path.lexists(broken))
            self.assertFalse(receipt_path(state_home).exists())

    def test_marketplace_name_conflict_is_rejected_before_external_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            other = seed_repository(root / "other")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner([marketplace_list_response(repo, other)])

            with self.assertRaisesRegex(InstallError, "marketplace"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(runner.calls, [("codex", "plugin", "marketplace", "list", "--json")])
            self.assertFalse((codex_home / "agents").exists())
            self._assert_package_only_receipt(repo, state_home)

    def test_marketplace_conflict_does_not_poison_successful_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            other = seed_repository(root / "other")
            codex_home = root / "codex"
            state_home = root / "state"

            with self.assertRaisesRegex(InstallError, "marketplace"):
                install(
                    repo,
                    codex_home,
                    state_home,
                    FakeRunner([marketplace_list_response(repo, other)]),
                )

            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(install_results(repo)),
            )

            self.assertTrue(receipt_path(state_home).is_file())
            self.assertTrue(
                all(path.is_symlink() for path in destination_paths(codex_home).values())
            )

    def test_legacy_marketplace_foreign_plugin_does_not_poison_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            other = seed_repository(root / "other")
            codex_home = root / "codex"
            state_home = root / "state"

            with self.assertRaisesRegex(InstallError, "plugin"):
                install(
                    repo,
                    codex_home,
                    state_home,
                    FakeRunner(
                        [
                            marketplace_list_response(repo, repo.resolve()),
                            plugin_list_response(repo, other.resolve()),
                        ]
                    ),
                )

            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(
                    [
                        marketplace_list_response(repo, repo.resolve()),
                        plugin_list_response(),
                        removal_response(),
                        marketplace_add_response(repo),
                        plugin_add_response(repo),
                    ]
                ),
            )

            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])

    def test_codex_transaction_lock_prevents_journal_overwrite_and_foreign_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            entered = threading.Event()
            release = threading.Event()
            failures: list[BaseException] = []
            install_module = __import__(
                "scripts.install", fromlist=["_create_codex_links"]
            )
            original_create_links = install_module._create_codex_links

            def pause_owner(*args: object, **kwargs: object) -> None:
                if threading.current_thread().name == "codex-owner":
                    entered.set()
                    if not release.wait(10):
                        raise AssertionError("timed out waiting to release owner")
                original_create_links(*args, **kwargs)

            def run_owner() -> None:
                try:
                    install(
                        repo,
                        codex_home,
                        state_home,
                        FakeRunner([]),
                        agents_only=True,
                    )
                except BaseException as error:
                    failures.append(error)

            with mock.patch(
                "scripts.install._create_codex_links", pause_owner
            ):
                owner = threading.Thread(target=run_owner, name="codex-owner")
                owner.start()
                self.assertTrue(entered.wait(10), "owner did not reach link publication")
                journal = state_home / "expskill" / "codex-install.json"
                before = journal.read_bytes()
                try:
                    with self.assertRaisesRegex(InstallError, "transaction.*active"):
                        install(
                            repo,
                            codex_home,
                            state_home,
                            FakeRunner([]),
                            agents_only=True,
                        )
                    with self.assertRaisesRegex(InstallError, "transaction.*active"):
                        uninstall(
                            repo,
                            codex_home,
                            state_home,
                            FakeRunner([]),
                            agents_only=True,
                        )
                    self.assertEqual(journal.read_bytes(), before)
                finally:
                    release.set()
                    owner.join(10)

            self.assertFalse(owner.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(receipt_path(state_home).is_file())
            self.assertFalse(
                (state_home / "expskill" / "codex-install.json").exists()
            )

    def test_plugin_failure_rolls_back_links_created_by_this_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(),
                    marketplace_add_response(repo),
                    plugin_list_response(),
                    FakeResult(1, {"error": "plugin unavailable"}, "plugin failed"),
                    removal_response(),
                ]
            )

            with self.assertRaisesRegex(InstallError, "plugin"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-1],
                (
                    "codex",
                    "plugin",
                    "marketplace",
                    "remove",
                    "expskill",
                    "--json",
                ),
            )
            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())
            self._assert_package_only_receipt(repo, state_home)

    def test_successful_marketplace_add_with_malformed_json_rolls_back_owned_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(),
                    FakeResult(0, stdout="not-json"),
                    removal_response(),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(managed_repository(repo)),
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )
            self._assert_package_only_receipt(repo, state_home)
            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())

    def test_successful_plugin_add_with_malformed_json_rolls_back_plugin_marketplace_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(),
                    marketplace_add_response(repo),
                    plugin_list_response(),
                    FakeResult(0, stdout="not-json"),
                    removal_response(),
                    removal_response(),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-2:],
                [
                    (
                        "codex",
                        "plugin",
                        "remove",
                        PLUGIN_SELECTOR,
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())
            self._assert_package_only_receipt(repo, state_home)

    def test_receipt_replace_failure_reports_and_rolls_back_external_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                install_results(repo) + [removal_response(), removal_response()]
            )
            original_replace = os.replace

            def fail_receipt_replace(source: Path, destination: Path) -> None:
                if Path(destination) == receipt_path(state_home):
                    raise OSError("receipt disk full")
                original_replace(source, destination)

            with mock.patch("scripts.install.os.replace", fail_receipt_replace):
                with self.assertRaisesRegex(InstallError, "receipt disk full"):
                    install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls[-2:],
                [
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_rollback_attempts_every_compensation_and_reports_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(),
                    marketplace_add_response(repo),
                    plugin_list_response(),
                    FakeResult(0, stdout="not-json"),
                    FakeResult(1, {"error": "plugin cleanup failed"}, "plugin cleanup failed"),
                    FakeResult(1, {"error": "market cleanup failed"}, "market cleanup failed"),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON") as context:
                install(repo, codex_home, state_home, runner)

            self.assertIn("plugin cleanup failed", str(context.exception))
            self.assertIn("market cleanup failed", str(context.exception))
            self.assertEqual(len(runner.calls), 6)

    def test_owned_link_unlink_failure_is_reported_without_swallowing_residual_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_list_response(),
                    marketplace_add_response(repo),
                    plugin_list_response(),
                    FakeResult(0, stdout="not-json"),
                    removal_response(),
                    removal_response(),
                ]
            )
            target = codex_home / "agents" / "expskill-review.toml"
            install_module = __import__(
                "scripts.install", fromlist=["_remove_exact_via_exchange"]
            )
            original_remove_exact = install_module._remove_exact_via_exchange

            def fail_target(
                parent_fd: int, name: str, *args: object, **kwargs: object
            ) -> bool:
                if name == target.name:
                    raise InstallError("owned link busy")
                return original_remove_exact(parent_fd, name, *args, **kwargs)

            with mock.patch("scripts.install._remove_exact_via_exchange", fail_target):
                with self.assertRaisesRegex(InstallError, "owned link busy"):
                    install(repo, codex_home, state_home, runner)

            self.assertTrue(os.path.lexists(target))
            self.assertEqual(len(runner.calls), 6)

    def test_install_rollback_preserves_retargeted_link_after_source_symlink_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            outside_file = root / "outside.toml"
            outside_file.write_text("user-owned\n", encoding="utf-8")
            outside_alias = root / "outside-alias.toml"
            outside_alias.symlink_to(outside_file)
            damaged_source = (
                managed_repository(repo)
                / "plugins"
                / "expskill"
                / "agents"
                / "expskill-review.toml"
            )
            retargeted = codex_home / "agents" / "expskill-review.toml"
            fake_runner = FakeRunner(
                [
                    marketplace_list_response(),
                    marketplace_add_response(repo),
                    plugin_list_response(),
                    FakeResult(0, stdout="not-json"),
                    removal_response(),
                    removal_response(),
                ]
            )

            def run(command: list[str]) -> FakeResult:
                result = fake_runner(command)
                if tuple(command) == ("codex", "plugin", "add", PLUGIN_SELECTOR, "--json"):
                    damaged_source.unlink()
                    damaged_source.symlink_to(outside_file)
                    retargeted.unlink()
                    retargeted.symlink_to(outside_alias)
                return result

            with self.assertRaisesRegex(InstallError, "preserved because ownership changed") as context:
                install(repo, codex_home, state_home, run)

            self.assertIn(str(retargeted), str(context.exception))
            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(os.readlink(retargeted), str(outside_alias))
            self.assertEqual(retargeted.read_text(encoding="utf-8"), "user-owned\n")
            self.assertTrue(damaged_source.is_symlink())
            self.assertEqual(
                fake_runner.calls[-2:],
                [
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    ("codex", "plugin", "marketplace", "remove", "expskill", "--json"),
                ],
            )
            self.assertEqual(
                tuple(path.name for path in (codex_home / "agents").iterdir()),
                ("expskill-review.toml",),
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_intermediate_content_symlink_escape_refuses_before_runner_or_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            escaped = root / "escaped-content"
            shutil.copytree(repo / "plugins" / "expskill" / "content", escaped)
            content = repo / "plugins" / "expskill" / "content"
            shutil.rmtree(content)
            content.symlink_to(escaped, target_is_directory=True)
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "symlink"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(runner.calls, [])
            self.assertFalse((codex_home / "agents").exists())

    def test_writable_wrong_model_reviewer_refuses_before_runner_or_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            reviewer = (
                repo
                / "plugins"
                / "expskill"
                / "content"
                / "agents"
                / "expskill-review.md"
            )
            reviewer.write_text(
                reviewer.read_text(encoding="utf-8").replace(
                    "Review exactly", "Changed review"
                ),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "repository validation"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(runner.calls, [])
            self.assertFalse((codex_home / "agents").exists())

    def test_crafted_receipt_cannot_authorize_outside_codex_home_link_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            outside_home = root / "outside"
            outside_home.mkdir()
            outside_target = outside_home / "owned.toml"
            outside_target.symlink_to(
                repo / "plugins" / "expskill" / "codex" / "runtime" / "agents" / "expskill-explorer.toml"
            )
            state_home = root / "state"
            receipt_directory = state_home / "expskill"
            receipt_directory.mkdir(parents=True)
            (receipt_directory / "install.json").write_text(
                json.dumps(
                    {
                        "repository_root": str(repo.resolve()),
                        "links": [
                            {
                                "source": str(
                                    repo.resolve()
                                    / "plugins"
                                    / "expskill"
                                    / "codex"
                                    / "runtime"
                                    / "agents"
                                    / "expskill-explorer.toml"
                                ),
                                "destination": str(outside_target),
                            }
                        ],
                        "marketplace_added": False,
                        "plugin_installed": False,
                    }
                ),
                encoding="utf-8",
            )
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "receipt"):
                uninstall(repo, codex_home, state_home, runner)

            self.assertTrue(outside_target.is_symlink())
            self.assertEqual(runner.calls, [])

    def test_uninstall_removes_only_links_still_owned_by_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install_runner = FakeRunner(install_results(repo))
            install(repo, codex_home, state_home, install_runner)
            destinations = destination_paths(codex_home)
            destinations["expskill-explorer"].unlink()
            retargeted = destinations["expskill-implementer"]
            retargeted.unlink()
            target = root / "unrelated.toml"
            target.write_text("preserved\n", encoding="utf-8")
            retargeted.symlink_to(target)
            replaced = destinations["expskill-review"]
            replaced.unlink()
            replaced.write_text("user replacement\n", encoding="utf-8")
            uninstall_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )

            result = uninstall(repo, codex_home, state_home, uninstall_runner)

            self.assertEqual(
                uninstall_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(
                {path.destination.name for path in result.removed_links},
                {
                    f"{name}.toml"
                    for name in PROFILE_NAMES
                    if name not in {"expskill-explorer", "expskill-implementer", "expskill-review"}
                },
            )
            self.assertTrue(retargeted.is_symlink())
            self.assertTrue(replaced.is_file())
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_preserves_same_target_symlink_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            replacement = destination_paths(codex_home)["expskill-review"]
            target = Path(os.readlink(replacement))
            original_content = replacement.read_text(encoding="utf-8")
            original_identity = replacement.lstat().st_ino
            replacement.unlink()
            replacement.symlink_to(target)
            replacement_identity = replacement.lstat().st_ino
            self.assertNotEqual(replacement_identity, original_identity)

            with self.assertRaisesRegex(InstallError, "unproven.*depends on its package"):
                uninstall(
                    repo,
                    codex_home,
                    state_home,
                    FakeRunner(
                        [
                            plugin_list_response(repo),
                            marketplace_list_response(repo),
                            removal_response(),
                            removal_response(),
                        ]
                    ),
                )

            self.assertTrue(replacement.is_symlink())
            self.assertEqual(Path(os.readlink(replacement)), target)
            self.assertEqual(replacement.read_text(encoding="utf-8"), original_content)
            self.assertEqual(replacement.lstat().st_ino, replacement_identity)
            remaining = load_receipt(state_home)
            self.assertEqual(len(remaining["links"]), 1)
            self.assertEqual(remaining["links"][0]["destination_ino"], original_identity)
            self.assertFalse(remaining["plugin_installed"])
            self.assertFalse(remaining["marketplace_added"])
            with self.assertRaisesRegex(InstallError, "unproven.*depends on its package"):
                uninstall(repo, codex_home, state_home, FakeRunner([]))
            self.assertEqual(replacement.read_text(encoding="utf-8"), original_content)

            replacement.unlink()
            uninstall(
                repo,
                codex_home,
                state_home,
                FakeRunner([plugin_list_response(), marketplace_list_response()]),
            )
            self.assertFalse(target.exists())
            self.assertFalse(managed_repository(repo).exists())
            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(any((codex_home / "agents").iterdir()))

    def test_uninstall_restores_user_replacement_racing_atomic_link_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            destination = destination_paths(codex_home)["expskill-review"]
            user_target = root / "user-owned.toml"
            user_target.write_text("user-owned\n", encoding="utf-8")
            install_module = __import__(
                "scripts.install", fromlist=["_renameat_exchange"]
            )
            original_exchange = install_module._renameat_exchange
            raced = {"value": False}

            def replace_during_exchange(
                source_fd: int,
                source_name: str,
                target_fd: int,
                target_name: str,
            ) -> None:
                if source_name == destination.name and not raced["value"]:
                    raced["value"] = True
                    destination.unlink()
                    destination.symlink_to(user_target)
                original_exchange(source_fd, source_name, target_fd, target_name)

            with mock.patch(
                "scripts.install._renameat_exchange", replace_during_exchange
            ):
                with self.assertRaisesRegex(InstallError, "identity changed"):
                    uninstall(
                        repo,
                        codex_home,
                        state_home,
                        FakeRunner(
                            [
                                plugin_list_response(repo),
                                marketplace_list_response(repo),
                                removal_response(),
                                removal_response(),
                            ]
                        ),
                    )

            self.assertTrue(raced["value"])
            self.assertTrue(destination.is_symlink())
            self.assertEqual(Path(os.readlink(destination)), user_target)
            self.assertEqual(user_target.read_text(encoding="utf-8"), "user-owned\n")

            uninstall(repo, codex_home, state_home, FakeRunner([
                plugin_list_response(), marketplace_list_response(),
            ]))
            self.assertEqual(Path(os.readlink(destination)), user_target)
            self.assertEqual(list(destination.parent.glob("*.retire")), [])
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_restores_replacement_racing_placeholder_move_and_retries(self) -> None:
        from scripts import install as module
        for regular in (False, True):
            with self.subTest(regular=regular), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo = seed_repository(root / "repo")
                codex_home, state_home = root / "codex", root / "state"
                install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
                destination = destination_paths(codex_home)["expskill-review"]
                original_rename = module._renameat_noreplace
                raced = False
                def race(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                    nonlocal raced
                    if source_name == destination.name and target_name.endswith(".sentinel") and not raced:
                        raced = True
                        destination.unlink()
                        if regular:
                            destination.write_text("user replacement")
                        else:
                            destination.symlink_to(root / "user-target")
                    original_rename(source_fd, source_name, target_fd, target_name)
                with mock.patch("scripts.install._renameat_noreplace", race):
                    with self.assertRaises(InstallError):
                        uninstall(repo, codex_home, state_home, FakeRunner([
                            plugin_list_response(repo), marketplace_list_response(repo),
                            removal_response(), removal_response(),
                        ]))
                self.assertTrue(raced)
                self.assertTrue(os.path.lexists(destination))
                uninstall(repo, codex_home, state_home, FakeRunner([
                    plugin_list_response(), marketplace_list_response(),
                ]))
                if regular:
                    self.assertEqual(destination.read_text(), "user replacement")
                else:
                    self.assertEqual(Path(os.readlink(destination)), root / "user-target")
                self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_uninstall_preserves_replacement_immediately_after_exchange(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            destination = destination_paths(codex_home)["expskill-review"]
            original_exchange = module._renameat_exchange
            raced = False
            def exchange(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                nonlocal raced
                original_exchange(source_fd, source_name, target_fd, target_name)
                if source_name == destination.name and not raced:
                    raced = True
                    destination.unlink()
                    destination.write_text("user replacement after exchange")
            with mock.patch("scripts.install._renameat_exchange", exchange):
                with self.assertRaises(InstallError):
                    uninstall(repo, codex_home, state_home, FakeRunner([
                        plugin_list_response(repo), marketplace_list_response(repo),
                        removal_response(), removal_response(),
                    ]))
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_text(), "user replacement after exchange")
            uninstall(repo, codex_home, state_home, FakeRunner([
                plugin_list_response(), marketplace_list_response(),
            ]))
            self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_legacy_link_migration_preserves_replacement_at_retirement(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            destination = destination_paths(codex_home)["expskill-review"]
            destination.parent.mkdir(parents=True)
            destination.symlink_to(repo / "plugins/expskill/assets/agents" / destination.name)
            original_unlink, original_exchange = Path.unlink, module._renameat_exchange
            raced = False
            def replace_user() -> None:
                nonlocal raced
                raced = True
                original_unlink(destination)
                destination.symlink_to(root / "user-target")
            def unlink(path: Path, *args: object, **kwargs: object) -> None:
                if path == destination and not raced:
                    replace_user()
                original_unlink(path, *args, **kwargs)
            def exchange(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                if source_name == destination.name and not raced:
                    replace_user()
                original_exchange(source_fd, source_name, target_fd, target_name)
            with mock.patch.object(Path, "unlink", unlink), mock.patch("scripts.install._renameat_exchange", exchange):
                try:
                    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
                except InstallError:
                    pass
            self.assertTrue(raced)
            self.assertEqual(Path(os.readlink(destination)), root / "user-target")

    def test_legacy_migration_uses_the_journaled_inode(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home, state_home = root / "codex", root / "state"
            destination = destination_paths(codex_home)["expskill-review"]
            destination.parent.mkdir(parents=True)
            source = repo / "plugins/expskill/assets/agents" / destination.name
            destination.symlink_to(source)
            original_migrate = module._migrate_legacy_codex_links
            replacement_identity = []
            def replace_before_migrate(*args: object, **kwargs: object) -> object:
                destination.rename(root / "original-legacy")
                destination.symlink_to(source)
                replacement_identity.append(destination.lstat().st_ino)
                return original_migrate(*args, **kwargs)
            with mock.patch("scripts.install._migrate_legacy_codex_links", replace_before_migrate):
                with self.assertRaises(InstallError):
                    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            self.assertEqual(destination.lstat().st_ino, replacement_identity[0])

    def test_link_publication_and_uninstall_are_fsynced_before_receipt_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            agents = codex_home.resolve() / "agents"
            install_events: list[tuple[str, int | None]] = []
            install_module = __import__("scripts.install", fromlist=["_fsync_directory"])
            original_fsync = install_module._fsync_directory
            original_write = install_module._write_codex_receipt

            def install_fsync(path: Path) -> None:
                original_fsync(path)
                if path == agents:
                    install_events.append(("agents-fsync", None))

            def install_write(path: Path, value: object) -> None:
                install_events.append(("receipt", len(value.links)))
                original_write(path, value)

            with mock.patch("scripts.install._fsync_directory", install_fsync), mock.patch(
                "scripts.install._write_codex_receipt", install_write
            ):
                install(repo, codex_home, state_home, FakeRunner(install_results(repo)))

            first_owned_receipt = next(
                index
                for index, event in enumerate(install_events)
                if event == ("receipt", len(PROFILE_NAMES))
            )
            self.assertIn(
                ("agents-fsync", None), install_events[:first_owned_receipt]
            )

            uninstall_events: list[tuple[str, int | None]] = []

            def uninstall_fsync(path: Path) -> None:
                original_fsync(path)
                if path == agents:
                    uninstall_events.append(("agents-fsync", None))

            def uninstall_write(path: Path, value: object) -> None:
                uninstall_events.append(("receipt", len(value.links)))
                original_write(path, value)

            with mock.patch("scripts.install._fsync_directory", uninstall_fsync), mock.patch(
                "scripts.install._write_codex_receipt", uninstall_write
            ):
                uninstall(
                    repo,
                    codex_home,
                    state_home,
                    FakeRunner(
                        [
                            plugin_list_response(repo),
                            marketplace_list_response(repo),
                            removal_response(),
                            removal_response(),
                        ]
                    ),
                )

            prior_receipt = -1
            prior_count = len(PROFILE_NAMES)
            for index, event in enumerate(uninstall_events):
                if event[0] != "receipt" or event[1] is None or event[1] >= prior_count:
                    if event[0] == "receipt" and event[1] is not None:
                        prior_count = event[1]
                        prior_receipt = index
                    continue
                self.assertIn(
                    ("agents-fsync", None),
                    uninstall_events[prior_receipt + 1 : index],
                )
                prior_count = event[1]
                prior_receipt = index

    def test_uninstall_preserves_retargeted_links_and_preexisting_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo, marketplace_present=True)))
            retargeted = destination_paths(codex_home)["expskill-review"]
            retargeted.unlink()
            unrelated = root / "unrelated.toml"
            unrelated.write_text("preserved\n", encoding="utf-8")
            retargeted.symlink_to(unrelated)
            runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    removal_response(),
                    marketplace_list_response(repo),
                ]
            )

            uninstall(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                ],
            )
            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(retargeted.resolve(), unrelated.resolve())
            self.assertTrue(unrelated.is_file())
            self._assert_package_only_receipt(repo, state_home)

    def test_uninstall_removes_hidden_owned_plugin_after_absent_state_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            target = destination_paths(codex_home)["expskill-review"]
            install_module = __import__(
                "scripts.install", fromlist=["_remove_exact_via_exchange"]
            )
            original_remove_exact = install_module._remove_exact_via_exchange
            failed = {"value": True}

            def fail_once(
                parent_fd: int, name: str, *args: object, **kwargs: object
            ) -> bool:
                if name == target.name and failed["value"]:
                    failed["value"] = False
                    raise InstallError("link busy")
                return original_remove_exact(parent_fd, name, *args, **kwargs)

            runner = FakeRunner(
                [
                    plugin_list_response(),
                    marketplace_list_response(),
                    removal_response(),
                ]
            )

            with mock.patch("scripts.install._remove_exact_via_exchange", fail_once):
                with self.assertRaisesRegex(InstallError, "link busy"):
                    uninstall(repo, codex_home, state_home, runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["plugin_installed"])
            self.assertFalse(receipt["marketplace_added"])
            self.assertTrue(target.is_symlink())
            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                ],
            )
            retry_runner = FakeRunner(
                [plugin_list_response(), marketplace_list_response()]
            )
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(target.exists())
            self.assertEqual(
                retry_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                ],
            )

    def test_uninstall_plugin_removal_failure_preserves_ownership_then_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    FakeResult(1, {"error": "plugin busy"}, "plugin busy"),
                ]
            )

            with self.assertRaisesRegex(InstallError, "plugin busy"):
                uninstall(repo, codex_home, state_home, first_runner)

            receipt = load_receipt(state_home)
            self.assertTrue(receipt["plugin_installed"])
            self.assertTrue(receipt["marketplace_added"])
            self.assertEqual(
                first_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                ],
            )
            retry_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertEqual(
                retry_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    ("codex", "plugin", "marketplace", "remove", "expskill", "--json"),
                ],
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_uses_receipt_after_source_profile_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            deleted_source = (
                managed_repository(repo)
                / "plugins"
                / "expskill"
                / "agents"
                / "expskill-review.toml"
            )
            deleted_source.unlink()
            runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )

            result = uninstall(repo, codex_home, state_home, runner)

            self.assertFalse(deleted_source.exists())
            self.assertEqual(len(result.removed_links), len(PROFILE_NAMES))
            self.assertFalse(receipt_path(state_home).exists())
            self.assertTrue(all(not os.path.lexists(path) for path in destination_paths(codex_home).values()))

    def test_uninstall_preserves_user_retarget_when_damaged_source_is_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            outside_file = root / "outside.toml"
            outside_file.write_text("user-owned\n", encoding="utf-8")
            outside_alias = root / "outside-alias.toml"
            outside_alias.symlink_to(outside_file)
            damaged_source = (
                managed_repository(repo)
                / "plugins"
                / "expskill"
                / "agents"
                / "expskill-review.toml"
            )
            damaged_source.unlink()
            damaged_source.symlink_to(outside_file)
            untouched_destination = destination_paths(codex_home)["expskill-implementer"]
            untouched_source = (
                managed_repository(repo)
                / "plugins"
                / "expskill"
                / "agents"
                / "expskill-implementer.toml"
            )
            untouched_source.unlink()
            untouched_source.symlink_to(outside_file)
            retargeted = destination_paths(codex_home)["expskill-review"]
            retargeted.unlink()
            retargeted.symlink_to(outside_alias)
            runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )

            result = uninstall(repo, codex_home, state_home, runner)

            self.assertEqual(len(result.removed_links), len(PROFILE_NAMES) - 1)
            self.assertFalse(managed_repository(repo).exists())
            self.assertEqual(outside_file.read_text(encoding="utf-8"), "user-owned\n")
            self.assertFalse(os.path.lexists(untouched_destination))
            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(os.readlink(retargeted), str(outside_alias))
            self.assertEqual(retargeted.read_text(encoding="utf-8"), "user-owned\n")
            self.assertFalse(receipt_path(state_home).exists())

    def test_receipt_temp_cleanup_failure_reports_exact_residual_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(install_results(repo) + [removal_response(), removal_response()])
            receipt_directory = state_home / "expskill"
            temporary_paths: list[Path] = []
            original_unlink = Path.unlink
            original_replace = os.replace

            def fail_receipt_replace(source: Path, destination: Path) -> None:
                if Path(destination) == receipt_path(state_home):
                    raise OSError("receipt disk full")
                original_replace(source, destination)

            def fail_temporary(path: Path, *args: object, **kwargs: object) -> None:
                if path.parent == receipt_directory and path.name.startswith(".install.json."):
                    temporary_paths.append(path)
                    raise OSError("temporary receipt busy")
                original_unlink(path, *args, **kwargs)

            with mock.patch("scripts.install.os.replace", fail_receipt_replace):
                with mock.patch.object(Path, "unlink", fail_temporary):
                    with self.assertRaisesRegex(InstallError, "receipt disk full") as context:
                        install(repo, codex_home, state_home, runner)

            # Both receipt publication and the package-identity handoff fail.
            self.assertEqual(len(temporary_paths), 2)
            for path in temporary_paths:
                self.assertIn(str(path), str(context.exception))
                self.assertTrue(os.path.lexists(path))
            self.assertTrue((state_home / "expskill/codex-install.json").exists())
            self.assertIn("temporary receipt cleanup", str(context.exception))
            self.assertEqual(
                runner.calls[-2:],
                [
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    ("codex", "plugin", "marketplace", "remove", "expskill", "--json"),
                ],
            )
            self.assertFalse(receipt_path(state_home).exists())
            self.assertEqual(
                tuple((codex_home / "agents").iterdir()),
                (),
            )

    def test_uninstall_persists_plugin_flag_before_marketplace_failure_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    FakeResult(1, {"error": "marketplace unavailable"}, "marketplace unavailable"),
                ]
            )

            with self.assertRaisesRegex(InstallError, "marketplace unavailable"):
                uninstall(repo, codex_home, state_home, first_runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["plugin_installed"])
            self.assertTrue(receipt["marketplace_added"])
            retry_runner = FakeRunner(
                [
                    plugin_list_response(),
                    marketplace_list_response(repo),
                    removal_response(),
                ]
            )
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertEqual(
                retry_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "expskill",
                        "--json",
                    ),
                ],
            )

    def test_uninstall_does_not_replay_plugin_removal_after_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    FakeResult(0, stdout="not-json"),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON"):
                uninstall(repo, codex_home, state_home, first_runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["plugin_installed"])
            self.assertTrue(receipt["marketplace_added"])
            retry_runner = FakeRunner(
                [
                    plugin_list_response(),
                    marketplace_list_response(repo),
                    removal_response(),
                ]
            )
            uninstall(repo, codex_home, state_home, retry_runner)

            self.assertNotIn(
                ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                retry_runner.calls,
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_does_not_replay_marketplace_removal_after_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    FakeResult(0, stdout="not-json"),
                ]
            )

            with self.assertRaisesRegex(InstallError, "invalid JSON"):
                uninstall(repo, codex_home, state_home, first_runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["plugin_installed"])
            self.assertFalse(receipt["marketplace_added"])
            retry_runner = FakeRunner(
                [plugin_list_response(), marketplace_list_response()]
            )
            uninstall(repo, codex_home, state_home, retry_runner)

            self.assertNotIn(
                (
                    "codex",
                    "plugin",
                    "marketplace",
                    "remove",
                    "expskill",
                    "--json",
                ),
                retry_runner.calls,
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_persists_link_progress_after_external_success_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            target = destination_paths(codex_home)["expskill-review"]
            install_module = __import__(
                "scripts.install", fromlist=["_remove_exact_via_exchange"]
            )
            original_remove_exact = install_module._remove_exact_via_exchange
            failed = {"value": True}

            def fail_once(
                parent_fd: int, name: str, *args: object, **kwargs: object
            ) -> bool:
                if name == target.name and failed["value"]:
                    failed["value"] = False
                    raise InstallError("link busy")
                return original_remove_exact(parent_fd, name, *args, **kwargs)

            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            with mock.patch("scripts.install._remove_exact_via_exchange", fail_once):
                with self.assertRaisesRegex(InstallError, "link busy"):
                    uninstall(repo, codex_home, state_home, first_runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["plugin_installed"])
            self.assertFalse(receipt["marketplace_added"])
            retry_runner = FakeRunner(
                [plugin_list_response(), marketplace_list_response()]
            )
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertEqual(
                retry_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                ],
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_retries_final_receipt_removal_without_replaying_external_commands(self) -> None:
        from scripts import install as module
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            receipt = receipt_path(state_home)
            original_unlink = module._clear_codex_record
            failed = {"value": True}

            def fail_receipt_once(path: Path, *args: object, **kwargs: object) -> None:
                if path == receipt and failed["value"]:
                    failed["value"] = False
                    raise OSError("receipt busy")
                original_unlink(path, *args, **kwargs)

            runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            with mock.patch.object(module, "_clear_codex_record", fail_receipt_once):
                with self.assertRaisesRegex(InstallError, "receipt busy"):
                    uninstall(repo, codex_home, state_home, runner)

            retry_runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertEqual(retry_runner.calls, [])
            self.assertFalse(receipt.exists())

    def test_uninstall_keeps_receipt_when_managed_package_removal_fails(self) -> None:
        """Package deletion failures leave enough ownership state for a retry."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            managed_root = managed_repository(repo)
            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )

            original_rmtree = shutil.rmtree
            failed = {"value": True}

            def fail_package_once(path: Path, *args: object, **kwargs: object) -> None:
                descriptor = kwargs.get("dir_fd")
                if path == "plugins" and descriptor is not None and os.fstat(descriptor).st_ino == managed_root.stat().st_ino and failed["value"]:
                    failed["value"] = False
                    raise OSError("package busy")
                original_rmtree(path, *args, **kwargs)

            with mock.patch("scripts.install.shutil.rmtree", fail_package_once):
                with self.assertRaisesRegex(InstallError, "package busy"):
                    uninstall(repo, codex_home, state_home, first_runner)

            self.assertTrue(receipt_path(state_home).is_file())
            self.assertTrue(managed_root.is_dir())
            self.assertTrue((managed_root / ".expskill-managed.json").is_file())

            retry_runner = FakeRunner(
                [
                    plugin_list_response(),
                    marketplace_list_response(),
                ]
            )
            uninstall(repo, codex_home, state_home, retry_runner)

            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(managed_root.exists())

    def test_uninstall_retries_after_marker_removal_precedes_root_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            managed_root = managed_repository(repo)
            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            original_rmdir = os.rmdir
            failed = {"value": True}

            def fail_root_once(path: Path, *args: object, **kwargs: object) -> None:
                from scripts import install as module
                record = module._retirement_record_descriptor(str(path), directory=True)
                if record and record[0] == managed_root.name and failed["value"]:
                    failed["value"] = False
                    raise OSError("root busy")
                original_rmdir(path, *args, **kwargs)

            with mock.patch("scripts.install.os.rmdir", fail_root_once):
                with self.assertRaisesRegex(InstallError, "root busy"):
                    uninstall(repo, codex_home, state_home, first_runner)

            self.assertTrue(receipt_path(state_home).is_file())
            self.assertFalse(managed_root.exists())
            retired, = managed_root.parent.glob("*.retire-dir")
            self.assertEqual(tuple(retired.iterdir()), ())

            retry_runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, retry_runner)

            self.assertEqual(retry_runner.calls, [])
            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(managed_root.exists())

    def test_agents_only_install_links_profiles_without_cli_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner([])

            result = install(repo, codex_home, state_home, runner, agents_only=True)

            self.assertEqual(runner.calls, [])
            self.assertEqual(len(result.created_links), len(PROFILE_NAMES))
            for name in PROFILE_NAMES:
                destination = destination_paths(codex_home)[name]
                self.assertTrue(destination.is_symlink())
            receipt = json.loads(receipt_path(state_home).read_text(encoding="utf-8"))
            self.assertEqual(len(receipt["links"]), len(PROFILE_NAMES))
            self.assertFalse(receipt["marketplace_added"])
            self.assertFalse(receipt["plugin_installed"])

    def test_agents_only_uninstall_removes_links_without_cli_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            runner = FakeRunner([])
            result = uninstall(repo, codex_home, state_home, runner, agents_only=True)

            self.assertEqual(runner.calls, [])
            self.assertEqual(len(result.removed_links), len(PROFILE_NAMES))
            self._assert_package_only_receipt(repo, state_home)
            self.assertTrue(
                all(not os.path.lexists(path) for path in destination_paths(codex_home).values())
            )

    def test_full_install_after_agents_only_claims_cli_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            runner = FakeRunner(install_results(repo))
            result = install(repo, codex_home, state_home, runner)

            self.assertEqual(len(runner.calls), 4)
            self.assertEqual(result.created_links, ())
            receipt = json.loads(receipt_path(state_home).read_text(encoding="utf-8"))
            self.assertEqual(len(receipt["links"]), len(PROFILE_NAMES))
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])

    def test_agents_only_uninstall_preserves_cli_managed_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            runner = FakeRunner([])
            result = uninstall(repo, codex_home, state_home, runner, agents_only=True)

            self.assertEqual(runner.calls, [])
            self.assertEqual(len(result.removed_links), len(PROFILE_NAMES))
            self._assert_package_only_receipt(repo, state_home)

    def test_full_install_agents_only_uninstall_retains_cli_ownership_for_full_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))

            partial_runner = FakeRunner([])
            partial_result = uninstall(
                repo,
                codex_home,
                state_home,
                partial_runner,
                agents_only=True,
            )

            self.assertEqual(partial_runner.calls, [])
            self.assertEqual(len(partial_result.removed_links), len(PROFILE_NAMES))
            receipt = load_receipt(state_home)
            self.assertEqual(receipt["links"], [])
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])

            full_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            uninstall(repo, codex_home, state_home, full_runner)

            self.assertEqual(
                full_runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "marketplace", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    ("codex", "plugin", "marketplace", "remove", "expskill", "--json"),
                ],
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_repeated_agents_only_uninstall_keeps_owned_cli_receipt_without_cli_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))

            first_runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, first_runner, agents_only=True)
            second_runner = FakeRunner([])
            second_result = uninstall(
                repo,
                codex_home,
                state_home,
                second_runner,
                agents_only=True,
            )

            self.assertEqual(first_runner.calls, [])
            self.assertEqual(second_runner.calls, [])
            self.assertEqual(second_result.removed_links, ())
            self.assertTrue(receipt_path(state_home).exists())
            receipt = load_receipt(state_home)
            self.assertEqual(receipt["links"], [])
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])

    def test_agents_only_uninstall_preserves_cli_ownership_across_link_failure_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            target = destination_paths(codex_home)["expskill-review"]
            install_module = __import__(
                "scripts.install", fromlist=["_remove_exact_via_exchange"]
            )
            original_remove_exact = install_module._remove_exact_via_exchange
            failed = {"value": True}

            def fail_once(
                parent_fd: int, name: str, *args: object, **kwargs: object
            ) -> bool:
                if name == target.name and failed["value"]:
                    failed["value"] = False
                    raise InstallError("link busy")
                return original_remove_exact(parent_fd, name, *args, **kwargs)

            with mock.patch("scripts.install._remove_exact_via_exchange", fail_once):
                with self.assertRaisesRegex(InstallError, "link busy"):
                    uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)

            receipt = load_receipt(state_home)
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertEqual(
                {Path(entry["destination"]).name for entry in receipt["links"]},
                {target.name},
            )
            self.assertTrue(target.is_symlink())

            retry_runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, retry_runner, agents_only=True)

            self.assertEqual(retry_runner.calls, [])
            self.assertTrue(receipt_path(state_home).exists())
            self.assertEqual(load_receipt(state_home)["links"], [])

    def test_agents_only_uninstall_retains_package_receipt_for_preexisting_cli_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(
                repo,
                codex_home,
                state_home,
                FakeRunner(install_results(repo, marketplace_present=True, plugin_present=True)),
            )
            receipt = load_receipt(state_home)
            self.assertFalse(receipt["marketplace_added"])
            self.assertFalse(receipt["plugin_installed"])
            managed_root = managed_repository(repo)
            self.assertTrue(managed_root.is_dir())

            runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, runner, agents_only=True)

            self.assertEqual(runner.calls, [])
            self._assert_package_only_receipt(repo, state_home)
            self.assertTrue(managed_root.is_dir())

    def test_agents_only_dry_run_lists_links_without_cli_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            output = StringIO()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
                with redirect_stdout(output):
                    from scripts.install import _print_dry_run

                    _print_dry_run(repo, codex_home, agents_only=True)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), len(PROFILE_NAMES) + 1)
            self.assertFalse(
                any("codex plugin marketplace add" in line for line in lines)
            )
            self.assertFalse(any("codex plugin add " in line for line in lines))


def _seed_receipted_checkout_profile(repo: Path, codex_home: Path, state_home: Path) -> Path:
    destination = destination_paths(codex_home)["expskill-review"]
    source = repo / "plugins/expskill/assets/agents" / destination.name
    destination.parent.mkdir(parents=True)
    destination.symlink_to(source)
    receipt_path(state_home).parent.mkdir(parents=True)
    receipt_path(state_home).write_text(json.dumps({
        "repository_root": str(repo.resolve()),
        "links": [{"source": str(source), "destination": str(destination)}],
        "marketplace_added": False,
        "plugin_installed": False,
    }))
    return destination


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_legacy_receipt_alias_rejection_keeps_valid_recovery_identity(
    tmp_path: Path, retry, relative: bool,
) -> None:
    from scripts import install as module

    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    destination = _seed_receipted_checkout_profile(repo, home, state)
    source = Path(os.readlink(destination))
    source.parent.mkdir(parents=True)
    source.write_text('name = "legacy review"\n')
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    target = str(alias / source.relative_to(repo))
    if relative:
        target = os.path.relpath(target, destination.parent)
    destination.rename(tmp_path / "original-link")
    destination.symlink_to(target)
    replacement = destination.lstat()
    journal_path = module._codex_install_journal_path(state)
    writes = []
    write = module._write_codex_install_journal

    def checkpoint(path, payload):
        write(path, payload)
        writes.append(json.loads(path.read_text()))

    # Keep the journal after rejection to exercise recovery from the exact
    # durable evidence, including an interrupted rollback cleanup.
    with mock.patch.object(module, "_write_codex_install_journal", checkpoint), mock.patch.object(
        module, "_clear_codex_install_journal",
        side_effect=InstallError("journal cleanup interrupted"),
    ), pytest.raises(InstallError):
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert writes
    for payload in [*writes, json.loads(journal_path.read_text())]:
        record = next(item for item in payload["links"] if item["destination"] == str(destination))
        assert (record["preexisting_dev"], record["preexisting_ino"]) == (
            replacement.st_dev, replacement.st_ino,
        )
        assert record["preexisting_target"] == target
    assert (destination.lstat().st_dev, destination.lstat().st_ino) == (
        replacement.st_dev, replacement.st_ino,
    )
    assert os.readlink(destination) == target
    assert destination.read_bytes() == source.read_bytes()
    destination.unlink()
    for _ in range(2):
        retry(repo, home, state, FakeRunner(
            [] if retry is install else [plugin_list_response(), marketplace_list_response()]
        ), agents_only=retry is install)
        assert not journal_path.exists()
    if retry is install:
        assert all(path.is_file() for path in destination_paths(home).values())
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()
    assert not managed_repository(repo).exists()
    assert not list(destination.parent.iterdir())


@pytest.mark.parametrize("retry", ["install", "uninstall"])
@pytest.mark.parametrize("pending_receipt", [False, True])
@pytest.mark.parametrize("replacement", [None, "regular", "same-target", "other-target"])
def test_legacy_migration_retires_identity_selected_after_journal_creation(
    tmp_path: Path, retry: str, pending_receipt: bool, replacement: str | None,
) -> None:
    from scripts import install as module

    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    destination = _seed_receipted_checkout_profile(repo, home, state)
    target = os.readlink(destination)
    original = destination.lstat()
    journal_path = module._codex_install_journal_path(state)
    pid = os.fork()
    if pid == 0:
        write_journal = module._write_codex_install_journal
        write_receipt = module._write_codex_receipt
        exchange = module._renameat_exchange
        replaced = False

        def checkpoint(path, payload):
            nonlocal replaced
            write_journal(path, payload)
            if not replaced:
                replaced = True
                entry = next(item for item in payload["links"] if item["destination"] == str(destination))
                assert entry["preexisting_ino"] == original.st_ino
                assert "codex_link_identity" not in load_receipt(state)
                # Keep the old inode alive so the replacement is distinct even
                # on filesystems that eagerly reuse unlinked symlink inodes.
                destination.rename(tmp_path / "original-link")
                destination.symlink_to(target)
                assert destination.lstat().st_ino != original.st_ino

        def receipt_checkpoint(path, receipt, **kwargs):
            if pending_receipt:
                kwargs["pending_link_migration"] = True
            write_receipt(path, receipt, **kwargs)

        def exchanged(source_fd, source, target_fd, private):
            if source == destination.name:
                durable = json.loads(journal_path.read_text())
                entry = next(item for item in durable["links"] if item["destination"] == str(destination))
                frozen, = load_receipt(state)["links"]
                assert (entry["preexisting_dev"], entry["preexisting_ino"]) == (
                    frozen["destination_dev"], frozen["destination_ino"],
                )
            exchange(source_fd, source, target_fd, private)
            if source == destination.name:
                os._exit(73)

        with mock.patch.object(module, "_write_codex_install_journal", checkpoint), mock.patch.object(module, "_write_codex_receipt", receipt_checkpoint), mock.patch.object(module, "_renameat_exchange", exchanged):
            install(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    assert destination.is_file() and not destination.is_symlink()
    retired, = destination.parent.glob("*.retire")
    frozen, = load_receipt(state)["links"]
    assert retired.lstat().st_ino == frozen["destination_ino"] != original.st_ino
    assert os.readlink(retired) == target
    assert load_receipt(state).get("pending_link_migration", False) is pending_receipt

    if replacement is not None:
        destination.rename(tmp_path / "public-placeholder")
        if replacement == "regular":
            destination.write_text("user replacement")
        else:
            destination.symlink_to(target if replacement == "same-target" else tmp_path / "unrelated")
        preserved = destination.lstat()
        for _ in range(2):
            if retry == "install" or replacement == "same-target":
                with pytest.raises(InstallError, match="identity|conflicting|ownership changed|depends"):
                    (install if retry == "install" else uninstall)(
                        repo, home, state, FakeRunner([]), agents_only=True,
                    )
                assert load_receipt(state)["links"] == [frozen]
            else:
                uninstall(repo, home, state, FakeRunner([]), agents_only=True)
                assert_package_only_receipt(repo, state)
                assert not journal_path.exists()
                assert list(destination.parent.iterdir()) == [destination]
            assert (destination.lstat().st_dev, destination.lstat().st_ino) == (
                preserved.st_dev, preserved.st_ino,
            )
            if replacement == "regular":
                assert destination.read_text() == "user replacement"
            else:
                assert os.readlink(destination) == (
                    target if replacement == "same-target" else str(tmp_path / "unrelated")
                )
        destination.unlink()

    receipts = []
    failures = []
    for _ in range(2):
        try:
            (install if retry == "install" else uninstall)(
                repo, home, state, FakeRunner([]), agents_only=True,
            )
        except InstallError as error:
            failures.append(str(error))
            continue
        assert not journal_path.exists()
        assert not os.path.lexists(retired)
        if retry == "install":
            receipt = load_receipt(state)
            assert receipt["marketplace_added"] is False
            assert receipt["plugin_installed"] is False
            assert receipt["codex_link_identity"] == 1
            assert not receipt.get("pending_link_migration", False)
            assert {entry["destination"] for entry in receipt["links"]} == {
                str(path) for path in destination_paths(home).values()
            }
            for entry in receipt["links"]:
                public, anchor = Path(entry["destination"]), Path(entry["link_anchor"])
                source = managed_repository(repo) / "plugins/expskill/agents" / public.name
                assert entry["source"] == os.readlink(public) == str(source)
                assert source.is_file()
                assert (public.lstat().st_dev, public.lstat().st_ino) == (
                    entry["destination_dev"], entry["destination_ino"],
                ) == (anchor.lstat().st_dev, anchor.lstat().st_ino)
            assert receipt.pop("codex_package")["ino"] == managed_repository(repo).stat().st_ino
            receipts.append(receipt)
        else:
            assert_package_only_receipt(repo, state)
            assert list(destination.parent.iterdir()) == []
    assert failures == []
    if retry == "install":
        assert receipts[0] == receipts[1]
        uninstall(repo, home, state, FakeRunner([]), agents_only=True)
        assert_package_only_receipt(repo, state)
        assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("agents_only", [True, False], ids=["agents", "full"])
@pytest.mark.parametrize("existing_receipt", [False, True], ids=["first", "existing"])
@pytest.mark.parametrize("resume", [False, True], ids=["direct-uninstall", "resume-install"])
@pytest.mark.parametrize("replacement", ["missing", "regular", "directory", "same-target", "other-target"])
def test_recovery_authority_public_replacement_matrix(
    tmp_path: Path, agents_only: bool, existing_receipt: bool, resume: bool, replacement: str,
) -> None:
    repo = seed_repository(tmp_path / "repo")
    codex_home, state_home = tmp_path / "codex", tmp_path / "state"
    destination = destination_paths(codex_home)["expskill-review"]
    prior_inode = None
    if existing_receipt:
        install(repo, codex_home, state_home, FakeRunner(
            [] if agents_only else install_results(repo)
        ), agents_only=agents_only)
        prior_inode = destination.lstat().st_ino
        destination.unlink()
    pid = os.fork()
    if pid == 0:
        with mock.patch("scripts.install._write_codex_receipt", side_effect=lambda *a, **k: os._exit(73)):
            install(repo, codex_home, state_home, FakeRunner(
                [] if agents_only else install_results(repo, existing_receipt, existing_receipt)
            ), agents_only=agents_only)
        os._exit(74)
    wait_for_crashed_child(pid)
    target = Path(os.readlink(destination))
    original_inode = destination.lstat().st_ino
    destination.unlink()
    if replacement == "regular":
        destination.write_text("user replacement")
    elif replacement == "directory":
        destination.mkdir()
        (destination / "user-data").write_text("user replacement")
    elif replacement in {"same-target", "other-target"}:
        destination.symlink_to(target if replacement == "same-target" else tmp_path / "unrelated")
    replacement_inode = destination.lstat().st_ino if replacement != "missing" else None
    if replacement_inode is not None:
        assert replacement_inode != original_inode
    if resume:
        runner = FakeRunner([] if agents_only else install_results(repo, True, True))
        if replacement in {"regular", "directory", "other-target"}:
            with pytest.raises(InstallError, match="conflict"):
                install(repo, codex_home, state_home, runner, agents_only=agents_only)
        else:
            install(repo, codex_home, state_home, runner, agents_only=agents_only)
            if replacement == "same-target":
                entries = [entry for entry in load_receipt(state_home)["links"] if entry["destination"] == str(destination)]
                assert len(entries) == 1, "resume discarded the package dependency"
                assert entries[0]["destination_ino"] in {prior_inode, original_inode}
                assert entries[0]["destination_ino"] != replacement_inode
    # A pending journal blocks before CLI cleanup; a committed receipt removes
    # owned registrations first. Either route must keep the replacement target.
    cli_present = not agents_only
    for _ in range(2 if replacement == "same-target" else 1):
        runner = FakeRunner([
            plugin_list_response(repo if cli_present else None),
            marketplace_list_response(repo if cli_present else None),
            removal_response(), removal_response(),
        ])
        if replacement == "same-target":
            with pytest.raises(InstallError, match="ownership changed|unproven.*depends"):
                uninstall(repo, codex_home, state_home, runner)
            assert destination.lstat().st_ino == replacement_inode
            assert target.is_file()
            assert receipt_path(state_home).exists() or (state_home / "expskill/codex-install.json").exists()
        else:
            uninstall(repo, codex_home, state_home, runner)
        if any("remove" in call for call in runner.calls):
            cli_present = False
    if replacement == "same-target":
        destination.unlink()
        uninstall(repo, codex_home, state_home, FakeRunner([
            plugin_list_response(repo if cli_present else None),
            marketplace_list_response(repo if cli_present else None),
            removal_response(), removal_response(),
        ]))
    survivors = {destination} if replacement in {"regular", "directory", "other-target"} else set()
    assert set(destination.parent.iterdir()) == survivors
    if survivors:
        assert destination.lstat().st_ino == replacement_inode
    if replacement == "directory":
        assert (destination / "user-data").read_text() == "user replacement"
    assert not receipt_path(state_home).exists()
    assert not (state_home / "expskill/codex-install.json").exists()
    assert not managed_repository(repo).exists()


@pytest.mark.parametrize("interrupted", [False, True], ids=["direct", "anchor-exchange"])
def test_uninstall_preserves_directory_replacement(tmp_path: Path, interrupted: bool) -> None:
    from scripts import install as module

    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    entry = load_receipt(state)["links"][0]
    destination, anchor = Path(entry["destination"]), Path(entry["link_anchor"])
    destination.unlink()
    destination.mkdir()
    (destination / "user-data").write_text("keep")
    replacement = destination.lstat()
    if interrupted:
        exchange = module._renameat_exchange

        def exchanged(source_fd, source, target_fd, target):
            exchange(source_fd, source, target_fd, target)
            if source == anchor.name:
                raise SystemExit(73)

        with mock.patch.object(module, "_renameat_exchange", exchanged), pytest.raises(SystemExit):
            uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        assert entry in load_receipt(state)["links"]
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        assert (destination.lstat().st_dev, destination.lstat().st_ino) == (
            replacement.st_dev, replacement.st_ino,
        )
        assert (destination / "user-data").read_text() == "keep"
        assert list(destination.parent.iterdir()) == [destination]
        assert not receipt_path(state).exists()
        assert not (state / "expskill/codex-install.json").exists()
        assert not managed_repository(repo).exists()


@pytest.mark.parametrize("directory", [False, True])
def test_exact_retirement_rejects_original_identity_with_wrong_type(tmp_path: Path, directory: bool) -> None:
    from scripts import install as module

    path = tmp_path / "original"
    if directory:
        path.write_text("keep")
    else:
        path.mkdir()
        (path / "user-data").write_text("keep")
    original = path.lstat()
    parent_fd = os.open(tmp_path, module._directory_open_flags())
    try:
        with pytest.raises(InstallError, match="pathname changed type"):
            module._remove_exact_via_exchange(
                parent_fd, path.name, (original.st_dev, original.st_ino),
                "owned object", directory=directory, preserve_replacements=True,
            )
    finally:
        os.close(parent_fd)
    assert path.lstat() == original
    assert list(tmp_path.iterdir()) == [path]
    assert (path if directory else path / "user-data").read_text() == "keep"


@pytest.mark.parametrize("legacy", [False, True], ids=["missing-repair", "checkout-migration"])
@pytest.mark.parametrize("boundary", ["none", "before-receipt", "after-receipt"])
@pytest.mark.parametrize("resume", [False, True], ids=["direct-uninstall", "resume-install"])
def test_recovery_authority_superseded_anchor(
    tmp_path: Path, legacy: bool, boundary: str, resume: bool,
) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    codex_home, state_home = tmp_path / "codex", tmp_path / "state"
    if legacy:
        destination = _seed_receipted_checkout_profile(repo, codex_home, state_home)
    else:
        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
        destination = destination_paths(codex_home)["expskill-review"]
        destination.unlink()
    if boundary == "none":
        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    else:
        pid = os.fork()
        if pid == 0:
            write = module._write_codex_receipt
            def checkpoint(path: Path, receipt: object, **kwargs: object) -> None:
                publishing = len(receipt.links) == len(PROFILE_NAMES) and all(
                    link.source.is_relative_to(managed_repository(repo)) for link in receipt.links
                )
                if publishing and boundary == "before-receipt":
                    os._exit(73)
                write(path, receipt, **kwargs)
                if publishing and boundary == "after-receipt":
                    os._exit(73)
            with mock.patch("scripts.install._write_codex_receipt", checkpoint):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            os._exit(74)
        wait_for_crashed_child(pid)
    if resume:
        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    uninstall(repo, codex_home, state_home, FakeRunner([
        plugin_list_response(), marketplace_list_response(),
    ]))
    assert list(destination.parent.iterdir()) == [], "superseded anchor lost cleanup authority"
    assert not receipt_path(state_home).exists()
    assert not (state_home / "expskill/codex-install.json").exists()
    assert not managed_repository(repo).exists()


@pytest.mark.parametrize("boundary", ["none", "restored", "journal-cleared"])
def test_recovery_authority_receipted_legacy_compensation(tmp_path: Path, boundary: str) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    codex_home, state_home = tmp_path / "codex", tmp_path / "state"
    destination = _seed_receipted_checkout_profile(repo, codex_home, state_home)
    original_target = os.readlink(destination)
    def fail_install() -> None:
        with pytest.raises(InstallError, match="plugin add failed"):
            install(repo, codex_home, state_home, FakeRunner([
                marketplace_list_response(), marketplace_add_response(repo),
                plugin_list_response(), FakeResult(1, stderr="plugin add failed"),
                removal_response(),
            ]))
    if boundary == "none":
        fail_install()
    else:
        pid = os.fork()
        if pid == 0:
            sync, clear = module._fsync_directory, module._clear_codex_install_journal
            def checkpoint(path: Path) -> None:
                sync(path)
                if boundary == "restored" and destination.is_symlink() and os.readlink(destination) == original_target:
                    journal = json.loads((state_home / "expskill/codex-install.json").read_text())
                    if any("legacy_restore" in record for record in journal["links"]):
                        os._exit(73)
            def cleared(path: Path) -> None:
                clear(path)
                if boundary == "journal-cleared":
                    os._exit(73)
            with mock.patch("scripts.install._fsync_directory", checkpoint), mock.patch("scripts.install._clear_codex_install_journal", cleared):
                fail_install()
            os._exit(74)
        wait_for_crashed_child(pid)
    if boundary != "restored":
        entry = next(entry for entry in load_receipt(state_home)["links"] if entry["destination"] == str(destination))
        assert entry["destination_ino"] == destination.lstat().st_ino, "compensation abandoned receipt identity"
        assert Path(entry["link_anchor"]).lstat().st_ino == destination.lstat().st_ino
    for _ in range(2):
        uninstall(repo, codex_home, state_home, FakeRunner([
            plugin_list_response(), marketplace_list_response(), removal_response(), removal_response(),
        ]))
    assert list(destination.parent.iterdir()) == []
    assert not receipt_path(state_home).exists()
    assert not managed_repository(repo).exists()


@pytest.mark.parametrize("legacy", [False, True], ids=["missing-repair", "checkout-migration"])
@pytest.mark.parametrize("fault", ["error", "exit-before", "exit-exchange", "exit-after", "private-regular", "private-same-target"])
def test_recovery_authority_anchor_retirement_retry(tmp_path: Path, legacy: bool, fault: str) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    codex_home, state_home = tmp_path / "codex", tmp_path / "state"
    if legacy:
        destination = _seed_receipted_checkout_profile(repo, codex_home, state_home)
    else:
        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
        destination = destination_paths(codex_home)["expskill-review"]
    metadata = destination.lstat()
    old_anchor = module._codex_link_anchor_path(destination, metadata.st_dev, metadata.st_ino)
    if not legacy:
        destination.unlink()
    unrelated = destination.parent / ".expskill-codex-link-anchor-unrelated.anchor"
    unrelated.write_text("unrelated private file")
    remove = module._remove_exact_via_exchange
    def retirement(parent_fd: int, name: str, *args: object, **kwargs: object) -> bool:
        if name == old_anchor.name:
            if fault == "error":
                raise InstallError("old anchor cleanup blocked")
            if fault == "exit-before":
                os._exit(73)
            if fault.startswith("private-") and not (tmp_path / "saved-anchor").is_symlink():
                old_anchor.rename(tmp_path / "saved-anchor")
                if fault == "private-regular":
                    old_anchor.write_text("private replacement")
                else:
                    old_anchor.symlink_to(os.readlink(tmp_path / "saved-anchor"))
        result = remove(parent_fd, name, *args, **kwargs)
        if name == old_anchor.name and fault == "exit-after":
            os._exit(73)
        return result
    if fault.startswith("exit-"):
        pid = os.fork()
        if pid == 0:
            exchange = module._renameat_exchange
            def exchanged(source_fd: int, source_name: str, target_fd: int, target_name: str) -> None:
                exchange(source_fd, source_name, target_fd, target_name)
                if fault == "exit-exchange" and source_name == old_anchor.name:
                    os._exit(73)
            with mock.patch("scripts.install._remove_exact_via_exchange", retirement), mock.patch("scripts.install._renameat_exchange", exchanged):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            os._exit(74)
        wait_for_crashed_child(pid)
    else:
        with mock.patch("scripts.install._remove_exact_via_exchange", retirement), pytest.raises(InstallError):
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    journal_path = state_home / "expskill/codex-install.json"
    assert journal_path.exists(), "failed retirement lost its durable authority"
    if fault.startswith("private-"):
        replacement_inode = old_anchor.lstat().st_ino
        for _ in range(2):
            with pytest.raises(InstallError):
                install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
            assert old_anchor.lstat().st_ino == replacement_inode
            assert journal_path.exists()
        old_anchor.unlink()
        (tmp_path / "saved-anchor").rename(old_anchor)
    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    uninstall(repo, codex_home, state_home, FakeRunner([
        plugin_list_response(), marketplace_list_response(),
    ]))
    assert list(destination.parent.iterdir()) == [unrelated]
    assert unrelated.read_text() == "unrelated private file"
    assert not receipt_path(state_home).exists()
    assert not journal_path.exists()
    assert not managed_repository(repo).exists()


@pytest.mark.parametrize("boundary", ["before-receipt", "after-receipt"])
def test_recovery_authority_dependency_receipt_handoff(tmp_path: Path, boundary: str) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    codex_home, state_home = tmp_path / "codex", tmp_path / "state"
    with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit), pytest.raises(SystemExit):
        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    destination = destination_paths(codex_home)["expskill-review"]
    original_inode, target = destination.lstat().st_ino, Path(os.readlink(destination))
    destination.unlink()
    destination.symlink_to(target)
    replacement_inode = destination.lstat().st_ino
    pid = os.fork()
    if pid == 0:
        write = module._write_codex_receipt
        def checkpoint(*args: object, **kwargs: object) -> None:
            if boundary == "before-receipt":
                os._exit(73)
            write(*args, **kwargs)
            os._exit(73)
        with mock.patch("scripts.install._write_codex_receipt", checkpoint):
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    entries = [entry for entry in load_receipt(state_home)["links"] if entry["destination"] == str(destination)]
    assert len(entries) == 1
    assert entries[0]["destination_ino"] == original_inode
    with pytest.raises(InstallError, match="unproven.*depends"):
        uninstall(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    assert destination.lstat().st_ino == replacement_inode
    assert target.is_file()
    destination.unlink()
    uninstall(repo, codex_home, state_home, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert list(destination.parent.iterdir()) == []
    assert not receipt_path(state_home).exists()
    assert not managed_repository(repo).exists()


@pytest.mark.parametrize("boundary", ["before-retirement", "after-retirement"])
def test_recovery_authority_failed_retry_after_receipt_publication(tmp_path: Path, boundary: str) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    codex_home, state_home = tmp_path / "codex", tmp_path / "state"
    destination = _seed_receipted_checkout_profile(repo, codex_home, state_home)
    pid = os.fork()
    if pid == 0:
        write, clear = module._write_codex_receipt, module._clear_codex_install_journal
        def checkpoint(path: Path, receipt: object, **kwargs: object) -> None:
            write(path, receipt, **kwargs)
            if boundary == "before-retirement" and len(receipt.links) == len(PROFILE_NAMES):
                os._exit(73)
        def clearing(path: Path) -> None:
            if boundary == "after-retirement":
                os._exit(73)
            clear(path)
        with mock.patch("scripts.install._write_codex_receipt", checkpoint), mock.patch("scripts.install._clear_codex_install_journal", clearing):
            install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    with mock.patch("scripts.install._write_codex_receipt", side_effect=InstallError("receipt publication failed")), pytest.raises(InstallError, match="receipt publication failed"):
        install(repo, codex_home, state_home, FakeRunner([]), agents_only=True)
    for _ in range(2):
        uninstall(repo, codex_home, state_home, FakeRunner([
            plugin_list_response(), marketplace_list_response(),
        ]))
    assert list(destination.parent.iterdir()) == []
    assert not receipt_path(state_home).exists()
    assert not (state_home / "expskill/codex-install.json").exists()
    assert not managed_repository(repo).exists()


def _checkout_cli_migration_results(repo: Path) -> list[FakeResult]:
    return [
        marketplace_list_response(repo, repo), plugin_list_response(repo, repo),
        removal_response(), removal_response(), marketplace_add_response(repo),
        plugin_add_response(repo),
    ]


class RecoveryCliRunner:
    """Persist fake CLI state across real child-process interruption boundaries."""

    def __init__(self, repo: Path, state: Path) -> None:
        self.repo, self.state = repo, state
        if not state.exists():
            state.write_text(json.dumps({"marketplace": str(repo), "plugin": str(repo)}))

    def __call__(self, command: list[str]) -> FakeResult:
        current = json.loads(self.state.read_text())
        kind = "marketplace" if command[2] == "marketplace" else "plugin"
        operation = command[3] if kind == "marketplace" else command[2]
        source = current[kind]
        if operation == "list":
            response = marketplace_list_response if kind == "marketplace" else plugin_list_response
            return response(self.repo if source else None, Path(source) if source else None)
        if operation == "remove":
            current[kind] = None
            result = removal_response()
        else:
            assert operation == "add"
            current[kind] = command[4] if kind == "marketplace" else current["marketplace"]
            result = legacy_marketplace_add_response(self.repo, Path(current[kind])) if kind == "marketplace" else plugin_add_response(self.repo)
        self.state.write_text(json.dumps(current))
        return result


def seed_journaled_recovery_package(repo, home, state):
    from scripts import install as module
    journal_path = module._codex_install_journal_path(state)
    journal = module._new_codex_install_journal(
        repo, home, managed_repository(repo), module._planned_codex_links(repo, home, state), False,
    )
    return module._materialize_codex_recovery_package(
        repo, state, install_journal_path=journal_path, install_journal=journal,
    )


def test_recovery_without_frozen_identity_is_preserved(tmp_path):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery = module._materialize_codex_recovery_package(repo, state)
    (recovery / "user-data").write_text("marker alone is not authority")
    identity = module._codex_package_identity(recovery)
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    for _ in range(2):
        with pytest.raises(InstallError, match="lost its receipt identity"):
            install(repo, home, state, runner)
        assert (recovery / "user-data").read_text() == "marker alone is not authority"
        assert module._codex_package_identity(recovery) == identity
        assert json.loads(runner.state.read_text()) == {"marketplace": str(repo), "plugin": str(repo)}


def test_failed_migration_checkpoint_retains_recovery_publication(tmp_path):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    with mock.patch.object(module, "_write_codex_migration_journal", side_effect=InstallError("migration write failed")):
        with pytest.raises(InstallError, match="migration write failed"):
            install(repo, home, state, runner)
    recovery = recovery_repository(repo)
    journal = json.loads(module._codex_install_journal_path(state).read_text())
    assert journal["recovery_package"] == module._codex_package_identity(recovery)
    install(repo, home, state, runner)
    uninstall(repo, home, state, runner)
    assert not recovery.exists()
    assert not managed_repository(repo).exists()
    assert not receipt_path(state).exists()
    assert not module._codex_install_journal_path(state).exists()


@pytest.mark.parametrize("operation", ["install", "uninstall"])
@pytest.mark.parametrize("fault", ["error", "content", "marker", "exchange", "removed"])
def test_recovery_package_cleanup_retains_exact_authority(tmp_path: Path, operation: str, fault: str) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery = recovery_repository(repo)
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    if operation == "uninstall":
        pid = os.fork()
        if pid == 0:
            with mock.patch.object(module, "_write_codex_receipt", side_effect=lambda *a, **k: os._exit(73)):
                install(repo, home, state, runner)
            os._exit(74)
        wait_for_crashed_child(pid)
    remove_tree, unlink, exchange, clear = shutil.rmtree, os.unlink, module._renameat_exchange, module._clear_codex_migration_journal
    def removing(path, *args, **kwargs):
        fd = kwargs.get("dir_fd")
        cleanup = fd is not None and recovery.exists() and os.fstat(fd).st_ino == recovery.lstat().st_ino
        if cleanup and fault == "error":
            raise OSError("recovery content cleanup blocked")
        remove_tree(path, *args, **kwargs)
        if cleanup and fault == "content":
            os._exit(73)
    def unlinking(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if fault == "marker" and path == module.CODEX_MANAGED_MARKER:
            os._exit(73)
    def exchanging(source_fd, source, target_fd, target):
        exchange(source_fd, source, target_fd, target)
        if fault == "exchange" and source == recovery.name:
            os._exit(73)
    def clearing(path):
        if fault == "removed":
            assert not recovery.exists()
            os._exit(73)
        clear(path)
    def attempt():
        with mock.patch.object(module.shutil, "rmtree", removing), mock.patch.object(module.os, "unlink", unlinking), mock.patch.object(module, "_renameat_exchange", exchanging), mock.patch.object(module, "_clear_codex_migration_journal", clearing):
            (install if operation == "install" else uninstall)(repo, home, state, runner)
    if fault == "error":
        with pytest.raises(InstallError, match="recovery content cleanup blocked"):
            attempt()
    else:
        pid = os.fork()
        if pid == 0:
            attempt()
            os._exit(74)
        wait_for_crashed_child(pid)
    journal_path = module._codex_migration_journal_path(state)
    journal = json.loads(journal_path.read_text())
    assert journal["cleanup_pending"] is True
    assert journal["recovery_ino"] > 0
    assert receipt_path(state).exists()
    if operation == "install":
        install(repo, home, state, runner)
    for _ in range(2):
        uninstall(repo, home, state, runner)
    assert not recovery.exists()
    assert list(recovery.parent.iterdir()) == []
    assert not journal_path.exists()
    assert not module._codex_install_journal_path(state).exists()
    assert not receipt_path(state).exists()


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_swap_backup_replacement_after_validation(tmp_path, replacement, interrupted):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = module._codex_managed_root(repo, state)
    metadata = managed.lstat()
    expected = (metadata.st_dev, metadata.st_ino)
    backup = module._codex_swap_backup_path(managed, expected)
    saved, user = tmp_path / "saved-backup", tmp_path / "user-directory"
    user.mkdir()
    (user / "user-data").write_text("keep root data")
    (user / "plugins").mkdir()
    (user / "plugins/user-data").write_text("keep nested data")
    shutil.copy2(managed / module.CODEX_MANAGED_MARKER, user / module.CODEX_MANAGED_MARKER)
    marker_bytes = (user / module.CODEX_MANAGED_MARKER).read_bytes()
    owned, remove_tree = module._codex_swap_marker_is_owned, shutil.rmtree

    def checked(path, *args, **kwargs):
        result = owned(path, *args, **kwargs)
        if path == backup and result and not saved.exists():
            backup.rename(saved)
            if replacement == "directory":
                user.rename(backup)
            else:
                backup.symlink_to(user, target_is_directory=True)
        return result

    def removing(path, *args, **kwargs):
        fd = kwargs.get("dir_fd")
        cleanup = (fd is not None and (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == expected) or (
            fd is None and Path(path).parent == backup
        )
        remove_tree(path, *args, **kwargs)
        if interrupted and cleanup:
            os._exit(73)

    def attempt():
        with mock.patch.object(module, "_codex_swap_marker_is_owned", checked), mock.patch.object(module.shutil, "rmtree", removing):
            install(repo, home, state, FakeRunner([]), agents_only=True)

    if interrupted:
        pid = os.fork()
        if pid == 0:
            attempt()
            os._exit(74)
        wait_for_crashed_child(pid)
    else:
        with pytest.raises(InstallError):
            attempt()
    replacement_inode = backup.lstat().st_ino
    journal_path = module._codex_install_journal_path(state)
    for _ in range(2):
        assert (backup / "user-data").read_text() == "keep root data"
        assert (backup / "plugins/user-data").read_text() == "keep nested data"
        assert (backup / module.CODEX_MANAGED_MARKER).read_bytes() == marker_bytes
        assert backup.lstat().st_ino == replacement_inode
        assert json.loads(journal_path.read_text())["swap"]["backup_ino"] == expected[1]
        with pytest.raises(InstallError, match="exact ownership"):
            install(repo, home, state, FakeRunner([]), agents_only=True)
    # Restore the exact original; retries must also finish partially emptied
    # and markerless originals without touching the saved user replacement.
    backup.rename(tmp_path / "preserved-replacement")
    saved.rename(backup)
    for _ in range(2):
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert not os.path.lexists(backup)
    assert list(managed.parent.iterdir()) == [managed]
    assert not journal_path.exists()
    assert (tmp_path / "preserved-replacement/plugins/user-data").read_text() == "keep nested data"


@pytest.mark.parametrize("boundary", [
    "content", "marker", "exchange", "sentinel", "before-checkpoint",
    "after-checkpoint", "retired-object", "removed",
])
def test_swap_backup_cleanup_process_exit(tmp_path, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = module._codex_managed_root(repo, state)
    metadata = managed.lstat()
    expected = (metadata.st_dev, metadata.st_ino)
    backup = module._codex_swap_backup_path(managed, expected)
    pid = os.fork()
    if pid == 0:
        remove_tree, unlink, rmdir = shutil.rmtree, os.unlink, os.rmdir
        exchange, write = module._renameat_exchange, module._write_codex_install_journal

        def removing(path, *args, **kwargs):
            fd = kwargs.get("dir_fd")
            cleanup = fd is not None and (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == expected
            remove_tree(path, *args, **kwargs)
            if boundary == "content" and cleanup:
                os._exit(73)

        def unlinking(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if boundary == "marker" and path == module.CODEX_MANAGED_MARKER:
                os._exit(73)
            if boundary == "sentinel" and str(path).startswith(f".{backup.name}.") and str(path).endswith(".sentinel-dir"):
                os._exit(73)

        def exchanging(source_fd, source, target_fd, target):
            exchange(source_fd, source, target_fd, target)
            if boundary == "exchange" and source == backup.name:
                os._exit(73)

        def writing(path, journal):
            swap = journal.get("swap")
            if boundary == "before-checkpoint" and swap and swap["phase"] == "retired":
                os._exit(73)
            if boundary == "removed" and journal.get("swap") is None and not backup.exists() and managed.lstat().st_ino != expected[1]:
                os._exit(73)
            write(path, journal)
            if boundary == "after-checkpoint" and swap and swap["phase"] == "retired":
                os._exit(73)

        def removing_directory(path, *args, **kwargs):
            rmdir(path, *args, **kwargs)
            record = module._retirement_record_descriptor(str(path), directory=True)
            if boundary == "retired-object" and record and record[:2] == (backup.name, expected):
                os._exit(73)

        with mock.patch.object(module.shutil, "rmtree", removing), mock.patch.object(module.os, "unlink", unlinking), mock.patch.object(module.os, "rmdir", removing_directory), mock.patch.object(module, "_renameat_exchange", exchanging), mock.patch.object(module, "_write_codex_install_journal", writing):
            install(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    journal_path = module._codex_install_journal_path(state)
    assert json.loads(journal_path.read_text())["swap"]["backup_ino"] == expected[1]
    for _ in range(2):
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert list(managed.parent.iterdir()) == [managed]
    assert not journal_path.exists()


@pytest.mark.parametrize("replacement", [None, "public-file", "public-link", "private"])
def test_managed_rollback_retirement_recovers_before_install_conflicts(tmp_path, replacement):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    pid = os.fork()
    if pid == 0:
        exchange = module._renameat_exchange

        def exchanged(*args):
            exchange(*args)
            if str(args[1]).endswith(".toml"):
                os._exit(73)

        with mock.patch.object(module, "_write_codex_receipt", side_effect=InstallError("receipt publication failed")), mock.patch.object(module, "_renameat_exchange", exchanged):
            install(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    retired, = (home / "agents").glob("*.retire")
    destination = home / "agents" / module._retirement_record_descriptor(retired.name)[0]
    assert destination.is_file() and not destination.is_symlink()
    assert retired.is_symlink()
    journal_path = module._codex_install_journal_path(state)
    saved, user = tmp_path / "saved-retirement", tmp_path / "user-data"
    preserved = None
    if replacement is not None:
        preserved = retired if replacement == "private" else destination
        preserved.rename(saved)
        if replacement == "public-link":
            user.write_text("keep")
            preserved.symlink_to(user)
        else:
            preserved.write_text("keep")
        identity = preserved.lstat().st_ino
        for _ in range(2):
            with pytest.raises(InstallError):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            assert preserved.lstat().st_ino == identity
            assert preserved.read_text() == "keep"
            assert journal_path.exists()
        preserved.unlink()
        if replacement == "private":
            saved.rename(retired)
    else:
        unlink = os.unlink

        def interrupted(path, *args, **kwargs):
            if path == retired.name:
                raise SystemExit(73)
            unlink(path, *args, **kwargs)

        for _ in range(2):
            with mock.patch.object(module.os, "unlink", interrupted), pytest.raises(SystemExit):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            assert journal_path.exists()
    for _ in range(2):
        install(repo, home, state, FakeRunner([]), agents_only=True)
        assert destination.is_symlink()
        assert not journal_path.exists()
    uninstall(repo, home, state, FakeRunner([]), agents_only=True)
    assert list(destination.parent.iterdir()) == []
    assert_package_only_receipt(repo, state)


@pytest.mark.parametrize("replacement", ["empty", "copied-marker", "symlink"])
def test_recovery_package_cleanup_preserves_replaced_directory(tmp_path, replacement):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery = recovery_repository(repo)
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    pid = os.fork()
    if pid == 0:
        with mock.patch.object(module, "_write_codex_receipt", side_effect=lambda *a, **k: os._exit(73)):
            install(repo, home, state, runner)
        os._exit(74)
    wait_for_crashed_child(pid)
    saved = tmp_path / "original-recovery"
    recovery.rename(saved)
    if replacement == "symlink":
        recovery.symlink_to(saved, target_is_directory=True)
    else:
        recovery.mkdir()
        if replacement == "copied-marker":
            shutil.copy2(saved / module.CODEX_MANAGED_MARKER, recovery / module.CODEX_MANAGED_MARKER)
            (recovery / "user-data").write_text("keep")
    inode = recovery.lstat().st_ino
    for retry in (uninstall, install, uninstall):
        with pytest.raises(InstallError, match="exact ownership"):
            retry(repo, home, state, runner)
        assert recovery.lstat().st_ino == inode
        assert module._codex_migration_journal_path(state).exists()
        assert module._codex_install_journal_path(state).exists()
    if recovery.is_symlink():
        recovery.unlink()
    else:
        shutil.rmtree(recovery)
    saved.rename(recovery)
    uninstall(repo, home, state, runner)
    assert not recovery.exists()


def test_recovery_package_replacement_after_marker_removal(tmp_path):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery, saved = recovery_repository(repo), tmp_path / "saved-recovery"
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    remove = module._remove_exact_via_exchange
    def replace_before_exchange(parent_fd, name, *args, **kwargs):
        if name == recovery.name and not saved.exists():
            assert list(recovery.iterdir()) == []
            recovery.rename(saved)
            recovery.mkdir()
            (recovery / "user-data").write_text("keep")
        return remove(parent_fd, name, *args, **kwargs)
    with mock.patch.object(module, "_remove_exact_via_exchange", replace_before_exchange), pytest.raises(InstallError, match="exact ownership"):
        install(repo, home, state, runner)
    inode = recovery.lstat().st_ino
    for _ in range(2):
        with pytest.raises(InstallError, match="exact ownership"):
            uninstall(repo, home, state, runner)
        assert recovery.lstat().st_ino == inode
        assert (recovery / "user-data").read_text() == "keep"
        assert module._codex_migration_journal_path(state).exists()
        assert receipt_path(state).exists()
    shutil.rmtree(recovery)
    saved.rename(recovery)
    uninstall(repo, home, state, runner)
    assert not recovery.exists()
    assert not receipt_path(state).exists()


def test_interrupted_cli_migration_uninstall_removes_recovery_package(tmp_path: Path) -> None:
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    pid = os.fork()
    if pid == 0:
        with mock.patch("scripts.install._write_codex_receipt", side_effect=lambda *a, **k: os._exit(73)):
            install(repo, home, state, FakeRunner(_checkout_cli_migration_results(repo)))
        os._exit(74)
    wait_for_crashed_child(pid)
    assert recovery_repository(repo).is_dir()
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([
            plugin_list_response(repo), marketplace_list_response(repo),
            removal_response(), removal_response(),
        ]))
    assert not recovery_repository(repo).exists(), "recovery cleanup authority was abandoned"
    assert not receipt_path(state).exists()
    assert not (state / "expskill/codex-migration.json").exists()
    assert not (state / "expskill/codex-install.json").exists()


def test_recovery_cleanup_waits_for_unowned_registration_dependency(tmp_path):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    with mock.patch.object(module, "_write_codex_receipt", side_effect=SystemExit), pytest.raises(SystemExit):
        install(repo, home, state, runner)
    write = module._write_codex_receipt
    def checkpoint(path, receipt, **kwargs):
        write(path, receipt, **kwargs)
        if receipt.marketplace_added and not receipt.plugin_installed:
            raise SystemExit
    with mock.patch.object(module, "_write_codex_receipt", checkpoint), pytest.raises(SystemExit):
        uninstall(repo, home, state, runner)
    recovery = recovery_repository(repo)
    dependency = {"marketplace": str(recovery), "plugin": str(recovery)}
    runner.state.write_text(json.dumps(dependency))
    for _ in range(2):
        uninstall(repo, home, state, runner)
        assert json.loads(runner.state.read_text()) == dependency
        assert (recovery / ".agents/plugins/marketplace.json").is_file()
        assert module._codex_migration_journal_path(state).exists()
        assert receipt_path(state).exists()
    runner.state.write_text(json.dumps({"marketplace": None, "plugin": None}))
    uninstall(repo, home, state, runner)
    assert not recovery.exists()
    assert not receipt_path(state).exists()
    assert not module._codex_migration_journal_path(state).exists()


@pytest.mark.parametrize("boundary", ["direct", "before-clear", "after-clear"])
@pytest.mark.parametrize("retry", ["install", "uninstall"])
def test_compensated_recovery_replacement_keeps_frozen_authority(tmp_path, boundary, retry):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    managed, recovery = managed_repository(repo), recovery_repository(repo)
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")

    def failing(command):
        if (command[2:4] == ["add", PLUGIN_SELECTOR]
                and json.loads(runner.state.read_text())["marketplace"] == str(managed)):
            return FakeResult(1, stderr="injected managed plugin failure")
        return runner(command)

    clear = module._clear_codex_install_journal

    def clearing(path):
        assert json.loads(runner.state.read_text()) == {
            "marketplace": str(recovery), "plugin": str(recovery),
        }
        if boundary == "before-clear":
            os._exit(73)
        clear(path)
        if boundary == "after-clear":
            os._exit(73)

    def attempt():
        with mock.patch.object(module, "_clear_codex_install_journal", clearing):
            with pytest.raises(InstallError, match="injected managed plugin failure"):
                install(repo, home, state, failing)

    if boundary == "direct":
        attempt()
    else:
        pid = os.fork()
        if pid == 0:
            attempt()
            os._exit(74)
        wait_for_crashed_child(pid)
    saved = tmp_path / "original-recovery"
    recovery.rename(saved)
    shutil.copytree(saved, recovery)
    sentinel = recovery / "user-data"
    sentinel.write_text("preserve compensated replacement")
    identity = recovery.stat().st_dev, recovery.stat().st_ino
    assert identity != (saved.stat().st_dev, saved.stat().st_ino)
    operation = install if retry == "install" else uninstall
    for operation in (operation, install, uninstall):
        try:
            operation(repo, home, state, runner)
        except InstallError:
            pass
        assert sentinel.is_file(), "retry deleted a replacement recovery package"
        assert sentinel.read_text() == "preserve compensated replacement"
        assert (recovery.stat().st_dev, recovery.stat().st_ino) == identity
        migration = json.loads(module._codex_migration_journal_path(state).read_text())
        assert migration["recovery_package"] == module._codex_package_identity(saved)
    foreign = tmp_path / "preserved-replacement"
    recovery.rename(foreign)
    saved.rename(recovery)
    install(repo, home, state, runner)
    for _ in range(2):
        uninstall(repo, home, state, runner)
    assert (foreign / "user-data").is_file()
    assert not recovery.exists()
    assert not managed.exists()
    assert not receipt_path(state).exists()
    assert not module._codex_migration_journal_path(state).exists()
    assert not module._codex_install_journal_path(state).exists()


@pytest.mark.parametrize("full_uninstall_first", [False, True])
def test_agents_only_package_receipt_lifecycle(tmp_path, full_uninstall_first):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    uninstall(repo, home, state, FakeRunner([]), agents_only=True)
    managed = managed_repository(repo)
    assert managed.is_dir()
    receipt = load_receipt(state)
    assert receipt["links"] == []
    assert receipt["codex_package"] == module._codex_package_identity(managed)
    assert not receipt["marketplace_added"] and not receipt["plugin_installed"]
    if full_uninstall_first:
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        assert not managed.exists()
        assert not receipt_path(state).exists()
    install(repo, home, state, FakeRunner([]), agents_only=True)
    assert len(list((home / "agents").glob("expskill-*.toml"))) == len(PROFILE_NAMES)
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not managed.exists()
    assert not receipt_path(state).exists()


def test_agents_only_cli_reinstall_after_partial_teardown(tmp_path):
    env = dict(os.environ, CODEX_HOME=str(tmp_path / "codex"),
               XDG_STATE_HOME=str(tmp_path / "state"), PYTHONDONTWRITEBYTECODE="1")
    results = [subprocess.run(
        [sys.executable, str(ROOT / "scripts/install.py"), "--agents-only", *arguments],
        env=env, capture_output=True, text=True, timeout=60,
    ) for arguments in ([], ["--uninstall"], [])]
    assert [result.returncode for result in results] == [0, 0, 0], [result.stderr for result in results]


@pytest.mark.parametrize("spelling", ["canonical", "state-alias", "package-alias"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_preexisting_profile_dependency_retains_package(tmp_path, spelling, interrupted):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    link = module._planned_codex_links(repo, home, state)[0]
    link.destination.parent.mkdir(parents=True)
    target = link.source
    if spelling != "canonical":
        alias = tmp_path / "source-alias"
        aliased = state if spelling == "state-alias" else managed_repository(repo)
        alias.symlink_to(aliased, target_is_directory=True)
        target = Path(os.path.relpath(alias / link.source.relative_to(aliased), link.destination.parent))
    link.destination.symlink_to(target)
    expected = link.destination.lstat().st_dev, link.destination.lstat().st_ino
    if interrupted:
        pid = os.fork()
        if pid == 0:
            with mock.patch.object(module, "_write_codex_receipt", side_effect=lambda *a, **k: os._exit(73)):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            os._exit(74)
        wait_for_crashed_child(pid)
    else:
        for _ in range(2):
            install(repo, home, state, FakeRunner([]), agents_only=True)
    before = link.source.read_bytes()
    for _ in range(2):
        try:
            uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        except InstallError:
            pass
        assert link.source.is_file(), "uninstall stranded a preexisting profile link"
        assert link.source.read_bytes() == before
        assert (link.destination.lstat().st_dev, link.destination.lstat().st_ino) == expected
        assert os.readlink(link.destination) == str(target)
        entry, = load_receipt(state)["links"]
        assert entry == {"source": str(link.source), "destination": str(link.destination)}
        assert not list(link.destination.parent.glob(".*.anchor"))
    link.destination.unlink()
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not managed_repository(repo).exists()
    assert not receipt_path(state).exists()


@pytest.mark.parametrize("boundary", ["frozen", "anchor", "committed"])
@pytest.mark.parametrize("agents_only", [True, False])
@pytest.mark.parametrize("existing_journal", [False, True])
def test_frozen_checkout_receipt_survives_operation_switch(
    tmp_path: Path, boundary: str, agents_only: bool, existing_journal: bool,
) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    destination = _seed_receipted_checkout_profile(repo, home, state)
    pid = os.fork()
    if pid == 0:
        write, link = module._write_codex_receipt, os.link
        def checkpoint(path, receipt, **kwargs):
            write(path, receipt, **kwargs)
            if boundary == ("frozen" if kwargs.get("pending_link_migration") else "committed"):
                os._exit(73)
        def anchor(*args, **kwargs):
            link(*args, **kwargs)
            if boundary == "anchor":
                os._exit(73)
        with mock.patch.object(module, "_write_codex_receipt", checkpoint), mock.patch.object(module.os, "link", anchor):
            uninstall(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    frozen = load_receipt(state)["links"]
    target = os.readlink(destination)
    destination.rename(tmp_path / "original-link")
    destination.symlink_to(target)
    replacement_ino = destination.lstat().st_ino
    assert replacement_ino != frozen[0]["destination_ino"]
    if existing_journal:
        journal = module._new_codex_install_journal(
            repo, home, managed_repository(repo), module._planned_codex_links(repo, home, state), agents_only,
        )
        module._write_codex_install_journal(module._codex_install_journal_path(state), journal)
    for _ in range(2):
        with pytest.raises(InstallError, match="identity|unproven"):
            install(repo, home, state, FakeRunner([] if agents_only else install_results(repo)), agents_only=agents_only)
        assert destination.lstat().st_ino == replacement_ino
        assert os.readlink(destination) == target
        assert load_receipt(state)["links"] == frozen
    with pytest.raises(InstallError, match="unproven.*depends|ownership changed"):
        uninstall(repo, home, state, FakeRunner([]), agents_only=agents_only)
    assert destination.lstat().st_ino == replacement_ino
    assert load_receipt(state)["links"] == frozen
    destination.unlink()
    uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()


@pytest.mark.parametrize("boundary", ["exchange", "private-removed"])
@pytest.mark.parametrize("origin", ["receipt", "pending", "failed"])
@pytest.mark.parametrize("retry", ["uninstall", "install"])
def test_retirement_recovery_retains_public_replacement_dependency(
    tmp_path: Path, boundary: str, origin: str, retry: str,
) -> None:
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    if origin == "receipt":
        install(repo, home, state, FakeRunner([]), agents_only=True)
    elif origin == "pending":
        with mock.patch.object(module, "_write_codex_receipt", side_effect=SystemExit(73)), pytest.raises(SystemExit):
            install(repo, home, state, FakeRunner([]), agents_only=True)
    pid = os.fork()
    if pid == 0:
        exchange, unlink = module._renameat_exchange, os.unlink
        def exchanged(source_fd, source, target_fd, target):
            exchange(source_fd, source, target_fd, target)
            if boundary == "exchange" and source in {f"{name}.toml" for name in PROFILE_NAMES}:
                os._exit(73)
        def unlinked(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if boundary == "private-removed" and str(path).endswith(".retire") and str(path).startswith(".expskill-"):
                os._exit(73)
        with mock.patch.object(module, "_renameat_exchange", exchanged), mock.patch.object(module.os, "unlink", unlinked):
            if origin == "failed":
                with mock.patch.object(module, "_write_codex_receipt", side_effect=InstallError("receipt failed")):
                    install(repo, home, state, FakeRunner([]), agents_only=True)
            else:
                uninstall(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    journal_path = module._codex_install_journal_path(state)
    evidence_path = receipt_path(state) if origin == "receipt" else journal_path
    entries = json.loads(evidence_path.read_text())["links"]
    entry = next(item for item in entries if not Path(item["destination"]).is_symlink())
    destination, source = Path(entry["destination"]), Path(entry["source"])
    if os.path.lexists(destination):
        destination.unlink()
    destination.symlink_to(source)
    replacement = destination.lstat()
    assert replacement.st_ino != entry["destination_ino"]
    if retry == "install":
        install(repo, home, state, FakeRunner([]), agents_only=True)
        evidence_path = receipt_path(state)
    for _ in range(2):
        with pytest.raises(InstallError, match="depends|ownership changed"):
            uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        assert destination.lstat().st_ino == replacement.st_ino
        assert os.readlink(destination) == str(source)
        assert source.is_file()
        retained = next(item for item in json.loads(evidence_path.read_text())["links"] if item["destination"] == str(destination))
        for key in ("source", "destination", "destination_dev", "destination_ino", "link_anchor"):
            assert retained[key] == entry[key]
    destination.unlink()
    uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()
    assert not journal_path.exists()
    assert not managed_repository(repo).exists()
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("retry", ["uninstall", "install"])
@pytest.mark.parametrize("reintroduced", [False, True])
def test_frozen_migration_missing_anchor_never_reconstructs_ownership(retry: str, reintroduced: bool) -> None:
    from scripts import install as module
    # Even the same public inode cannot prove a missing anchor. Reintroduce
    # that inode after removing its public name without relying on an allocator.
    with tempfile.TemporaryDirectory(prefix="expskill-migration-") as temporary:
        root = Path(temporary)
        repo = seed_repository(root / "repo")
        home, state = root / "codex", root / "state"
        InstallerTests()._install_base_receipt(repo, home, state)
        pid = os.fork()
        if pid == 0:
            write = module._write_codex_receipt
            def checkpoint(path, receipt, **kwargs):
                write(path, receipt, **kwargs)
                if kwargs.get("pending_link_migration"):
                    os._exit(73)
            with mock.patch.object(module, "_write_codex_receipt", checkpoint):
                uninstall(repo, home, state, FakeRunner([]), agents_only=True)
            os._exit(74)
        wait_for_crashed_child(pid)
        frozen = load_receipt(state)["links"]
        entry = frozen[0]
        destination, source = Path(entry["destination"]), Path(entry["source"])
        original = destination.lstat()
        assert original.st_nlink == 1
        assert not os.path.lexists(entry["link_anchor"])
        if reintroduced:
            saved = root / "unanchored-link"
            destination.rename(saved)
            destination.symlink_to(source)
            destination.unlink()
            saved.rename(destination)
            assert destination.lstat().st_ino == original.st_ino
        replacement = destination.lstat()
        if retry == "install":
            with pytest.raises(InstallError, match="lost its receipt identity"):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            assert load_receipt(state)["links"] == frozen
        for _ in range(2):
            with pytest.raises(InstallError, match="unproven.*depends"):
                uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
            assert destination.lstat().st_ino == replacement.st_ino
            assert os.readlink(destination) == str(source)
            assert source.is_file()
            assert load_receipt(state)["links"] == frozen
            assert all(not os.path.lexists(item["link_anchor"]) for item in frozen)
        for item in frozen:
            Path(item["destination"]).unlink()
        with pytest.raises(InstallError, match="lacks receipt identity"):
            uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        managed_repository(repo).rename(root / "preserved-old-package")
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        assert not receipt_path(state).exists()
        assert not managed_repository(repo).exists()


def seed_recorded_retired_profile(repo: Path, home: Path, state: Path, *, legacy: bool = False) -> dict:
    from scripts import install as module

    name = RETIRED_PROFILE_NAMES[0] + ".toml"
    directory = (
        repo / "plugins/expskill/assets/agents"
        if legacy else managed_repository(repo) / "plugins/expskill/agents"
    )
    directory.mkdir(parents=True, exist_ok=True)
    source, destination = directory / name, home / "agents" / name
    source.write_text(f'name = "{RETIRED_PROFILE_NAMES[0]}"\n', encoding="utf-8")
    destination.symlink_to(source)
    metadata = destination.lstat()
    anchor = module._codex_link_anchor_path(destination, metadata.st_dev, metadata.st_ino)
    os.link(destination, anchor, follow_symlinks=False)
    entry = dict(
        source=str(source), destination=str(destination),
        destination_dev=metadata.st_dev, destination_ino=metadata.st_ino,
        link_anchor=str(anchor), link_anchor_dev=metadata.st_dev, link_anchor_ino=metadata.st_ino,
    )
    receipt = load_receipt(state)
    receipt["links"].append(entry)
    receipt_path(state).write_text(json.dumps(receipt), encoding="utf-8")
    parsed = module._read_codex_receipt(
        receipt_path(state), repo, module._allowlisted_links(repo, home, state)
    )
    assert any(link.destination == destination and module._codex_link_path_is_live(link) for link in parsed.links)
    return entry


@pytest.mark.parametrize("retired", [False, True], ids=["current", "retired"])
@pytest.mark.parametrize("interrupted", [False, True], ids=["fresh", "pending-refresh"])
def test_reinstall_preserves_replacement_package_dependency(tmp_path: Path, retired: bool, interrupted: bool) -> None:
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    entry = seed_recorded_retired_profile(repo, home, state) if retired else load_receipt(state)["links"][0]
    source, destination = Path(entry["source"]), Path(entry["destination"])
    journal = state / "expskill/codex-install.json"
    if interrupted:
        with mock.patch("scripts.install._materialize_codex_package", side_effect=SystemExit), pytest.raises(SystemExit):
            install(repo, home, state, FakeRunner([]), agents_only=True)
        assert journal.is_file()
    destination.unlink()
    destination.symlink_to(source)
    replacement = destination.lstat().st_ino
    assert replacement != entry["destination_ino"]
    before_receipt = receipt_path(state).read_bytes()
    before_journal = journal.read_bytes() if journal.exists() else None
    before_package = managed_repository(repo).stat()
    before_source = source.read_bytes()
    if retired:
        # The renderer no longer produces this target, so refresh must refuse
        # before touching the package or its original dependency evidence.
        with pytest.raises(InstallError, match="unproven.*depends"):
            install(repo, home, state, FakeRunner([]), agents_only=True)
        assert receipt_path(state).read_bytes() == before_receipt
        assert (journal.read_bytes() if journal.exists() else None) == before_journal
        assert managed_repository(repo).stat() == before_package
        assert source.read_bytes() == before_source
    else:
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert destination.lstat().st_ino == replacement
    assert source.is_file()
    assert entry in load_receipt(state)["links"]
    for _ in range(2):
        with pytest.raises(InstallError, match="unproven.*depends"):
            uninstall(repo, home, state, FakeRunner([]))
        assert destination.lstat().st_ino == replacement
        assert source.is_file()
        assert entry in load_receipt(state)["links"]
    destination.unlink()
    uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()
    assert not journal.exists()
    assert not managed_repository(repo).exists()
    assert not list((home / "agents").iterdir())


@pytest.mark.parametrize("boundary", ["receipt", "journal-clear"])
def test_retired_replacement_receipt_survives_interrupted_merge(tmp_path: Path, boundary: str) -> None:
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    # Checkout targets survive managed package refresh, so this case can
    # succeed while retaining the retired entry through pruning and merging.
    entry = seed_recorded_retired_profile(repo, home, state, legacy=True)
    destination, source = Path(entry["destination"]), Path(entry["source"])
    destination.unlink()
    destination.symlink_to(source)
    replacement = destination.lstat().st_ino
    hook = "_write_codex_receipt" if boundary == "receipt" else "_clear_codex_install_journal"
    with mock.patch(f"scripts.install.{hook}", side_effect=SystemExit), pytest.raises(SystemExit):
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert entry in load_receipt(state)["links"]
    assert destination.lstat().st_ino == replacement
    assert destination.is_file()
    install(repo, home, state, FakeRunner([]), agents_only=True)
    assert entry in load_receipt(state)["links"]
    with pytest.raises(InstallError, match="unproven.*depends"):
        uninstall(repo, home, state, FakeRunner([]))
    assert source.is_file()
    assert destination.lstat().st_ino == replacement
    destination.unlink()
    uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()
    assert not managed_repository(repo).exists()


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("alias_first", [True, False], ids=["alias-to-real", "real-to-alias"])
@pytest.mark.parametrize("legacy_journal", [False, True], ids=["canonical-journal", "old-alias-journal"])
def test_codex_home_alias_journal_recovery(tmp_path: Path, retry, alias_first: bool, legacy_journal: bool) -> None:
    repo = seed_repository(tmp_path / "repo")
    home, state, alias = tmp_path / "codex", tmp_path / "state", tmp_path / "alias"
    home.mkdir()
    alias.symlink_to(home, target_is_directory=True)
    first, second = (alias, home) if alias_first else (home, alias)
    with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit), pytest.raises(SystemExit):
        install(repo, first, state, FakeRunner([]), agents_only=True)
    journal = state / "expskill/codex-install.json"
    payload = json.loads(journal.read_text())
    identities = {path: path.lstat().st_ino for path in destination_paths(home).values()}
    assert len(identities) == 7
    if legacy_journal:
        payload["codex_home"] = str(alias)
        journal.write_text(json.dumps(payload), encoding="utf-8")
    else:
        assert payload["codex_home"] == str(home)
    retry(repo, second, state, FakeRunner([]), agents_only=True)
    assert not journal.exists()
    if retry is install:
        assert {path: path.lstat().st_ino for path in identities} == identities
        assert len(load_receipt(state)["links"]) == 7
        uninstall(repo, second, state, FakeRunner([]), agents_only=True)
    assert_package_only_receipt(repo, state)
    assert not list((home / "agents").iterdir())


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("legacy_journal", [False, True], ids=["canonical-journal", "old-alias-journal"])
@pytest.mark.parametrize("changed", ["redirect", "foreign", "destination"])
def test_codex_home_alias_journal_rejects_foreign_paths(tmp_path: Path, retry, legacy_journal: bool, changed: str) -> None:
    repo = seed_repository(tmp_path / "repo")
    home, state, alias = tmp_path / "codex", tmp_path / "state", tmp_path / "alias"
    home.mkdir()
    alias.symlink_to(home, target_is_directory=True)
    with mock.patch("scripts.install._write_codex_receipt", side_effect=SystemExit), pytest.raises(SystemExit):
        install(repo, alias, state, FakeRunner([]), agents_only=True)
    journal = state / "expskill/codex-install.json"
    payload = json.loads(journal.read_text())
    payload["codex_home"] = str(alias if legacy_journal else home)
    other = tmp_path / "other"
    other.mkdir()
    selected = alias
    if changed == "redirect":
        alias.unlink()
        alias.symlink_to(other, target_is_directory=True)
    elif changed == "foreign":
        selected = other
    else:
        payload["links"][0]["destination"] = str(other / "agents" / (PROFILE_NAMES[0] + ".toml"))
    journal.write_text(json.dumps(payload), encoding="utf-8")
    before = journal.read_bytes()
    identities = {path: path.lstat().st_ino for path in destination_paths(home).values()}
    package = managed_repository(repo).stat()
    with pytest.raises(InstallError, match="install journal"):
        retry(repo, selected, state, FakeRunner([]), agents_only=True)
    assert journal.read_bytes() == before
    assert {path: path.lstat().st_ino for path in identities} == identities
    assert all(path.is_file() for path in identities)
    assert managed_repository(repo).stat() == package
    assert not receipt_path(state).exists()
    assert not list(other.rglob("*.toml"))


def _seed_legacy_repository_alias(tmp_path: Path, relative: bool):
    repo = seed_repository(tmp_path / "repo")
    home, state, alias = tmp_path / "codex", tmp_path / "state", tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    destination = destination_paths(home)["expskill-review"]
    source = repo / "plugins/expskill/assets/agents" / destination.name
    source.parent.mkdir(parents=True)
    source.write_text('name = "readable legacy review"\n')
    destination.parent.mkdir(parents=True)
    target = str(alias / source.relative_to(repo))
    if relative:
        target = os.path.relpath(target, destination.parent)
    destination.symlink_to(target)
    # Keep the original inode allocated across removal and restoration.
    os.link(destination, tmp_path / "original-legacy", follow_symlinks=False)
    return repo, home, state, alias, source, destination, target


def _legacy_alias_failure_results(repo: Path, compensation_fails: bool = False):
    return [
        marketplace_list_response(), marketplace_add_response(repo),
        plugin_list_response(), FakeResult(1, stderr="plugin add failed"),
        FakeResult(1, stderr="marketplace compensation failed")
        if compensation_fails else removal_response(),
    ]


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
@pytest.mark.parametrize("compensation_fails", [False, True])
def test_legacy_repository_alias_failure_restores_original_spelling(
    tmp_path: Path, relative: bool, compensation_fails: bool,
) -> None:
    repo, home, state, alias, source, destination, target = _seed_legacy_repository_alias(tmp_path, relative)
    runner = FakeRunner(_legacy_alias_failure_results(repo, compensation_fails))
    with pytest.raises(InstallError, match="plugin add failed") as failure:
        install(repo, home, state, runner)
    assert runner.results == []
    assert "legacy link rollback" not in str(failure.value)
    assert os.readlink(destination) == target
    assert destination.read_bytes() == source.read_bytes()
    journal = state / "expskill/codex-install.json"
    assert journal.exists() is compensation_fails
    install(repo, home, state, FakeRunner(install_results(repo, marketplace_present=compensation_fails)))
    assert destination.is_file()
    uninstall(repo, home, state, FakeRunner([
        plugin_list_response(repo), marketplace_list_response(repo),
        removal_response(), removal_response(),
    ]))
    assert not journal.exists()
    assert not receipt_path(state).exists()
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("old_journal", [False, True], ids=["canonical-evidence", "old-journal"])
@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
@pytest.mark.parametrize("boundary", ["intent", "symlink", "identified", "published"])
def test_legacy_repository_alias_interrupted_restoration(
    tmp_path: Path, retry, old_journal: bool, relative: bool, boundary: str,
) -> None:
    from scripts import install as module

    repo, home, state, alias, source, destination, target = _seed_legacy_repository_alias(tmp_path, relative)
    write, symlink, rename = module._write_codex_install_journal, Path.symlink_to, module._renameat_noreplace

    def checkpoint(path, payload):
        write(path, payload)
        record = next(item for item in payload["links"] if item["destination"] == str(destination))
        restoration = record.get("legacy_restore")
        if restoration is not None and (
            boundary == "intent" or boundary == "identified" and restoration["dev"] is not None
        ):
            raise SystemExit(73)

    def staged(path, value, *args, **kwargs):
        symlink(path, value, *args, **kwargs)
        if str(value) == target and boundary == "symlink":
            raise SystemExit(73)

    def published(source_fd, source_name, target_fd, target_name):
        restoring = target_name == destination.name and os.readlink(source_name, dir_fd=source_fd) == target
        rename(source_fd, source_name, target_fd, target_name)
        if restoring and boundary == "published":
            raise SystemExit(73)

    with mock.patch.object(module, "_write_codex_install_journal", checkpoint), mock.patch.object(Path, "symlink_to", staged), mock.patch.object(module, "_renameat_noreplace", published), pytest.raises(SystemExit):
        install(repo, home, state, FakeRunner(_legacy_alias_failure_results(repo)))
    journal = state / "expskill/codex-install.json"
    payload = json.loads(journal.read_text())
    record = next(item for item in payload["links"] if item["destination"] == str(destination))
    assert record["preexisting_target"] == target
    if old_journal:
        record.pop("legacy_source", None)
        journal.write_text(json.dumps(payload))
    else:
        assert record["legacy_source"] == str(source)
    results = install_results(repo) if retry is install else [
        plugin_list_response(), marketplace_list_response(),
        removal_response(),
    ]
    runner = FakeRunner(results)
    retry(repo, home, state, runner)
    assert runner.results == []
    assert not journal.exists()
    if retry is install:
        assert destination.is_file()
        uninstall(repo, home, state, FakeRunner([
            plugin_list_response(repo), marketplace_list_response(repo),
            removal_response(), removal_response(),
        ]))
    assert not receipt_path(state).exists()
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("old_journal", [False, True], ids=["canonical-evidence", "old-journal"])
@pytest.mark.parametrize("replacement", ["regular", "same-target", "stage"])
def test_legacy_repository_alias_redirect_rejects_without_adopting_replacement(
    tmp_path: Path, retry, old_journal: bool, replacement: str,
) -> None:
    from scripts import install as module

    repo, home, state, alias, source, destination, target = _seed_legacy_repository_alias(tmp_path, True)
    symlink = Path.symlink_to

    def interrupt(path, value, *args, **kwargs):
        symlink(path, value, *args, **kwargs)
        if str(value) == target:
            raise SystemExit(73)

    with mock.patch.object(Path, "symlink_to", interrupt), pytest.raises(SystemExit):
        install(repo, home, state, FakeRunner(_legacy_alias_failure_results(repo)))
    journal = state / "expskill/codex-install.json"
    payload = json.loads(journal.read_text())
    record = next(item for item in payload["links"] if item["destination"] == str(destination))
    if old_journal:
        record.pop("legacy_source", None)
        journal.write_text(json.dumps(payload))
    other = tmp_path / "other"
    foreign = other / source.relative_to(repo)
    foreign.parent.mkdir(parents=True)
    foreign.write_text("user source")
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    if replacement == "stage":
        candidate, = destination.parent.glob("*.link")
        candidate.rename(tmp_path / "original-stage")
        candidate.symlink_to(target)
    else:
        candidate = destination
        if replacement == "regular":
            candidate.write_text("user replacement")
        else:
            candidate.symlink_to(target)
    before = journal.read_bytes()
    identities = {path: path.lstat() for path in destination.parent.iterdir()}
    for _ in range(2):
        with pytest.raises(InstallError, match="legacy.*authority|legacy.*source"):
            retry(repo, home, state, FakeRunner([]))
        assert journal.read_bytes() == before
        assert {path: path.lstat() for path in destination.parent.iterdir()} == identities
        assert foreign.read_text() == "user source"
        assert not receipt_path(state).exists()
    if replacement == "regular":
        assert candidate.read_text() == "user replacement"
    else:
        assert os.readlink(candidate) == target


def test_legacy_repository_alias_redirect_during_failed_install(tmp_path: Path) -> None:
    repo, home, state, alias, source, destination, target = _seed_legacy_repository_alias(tmp_path, True)
    other = tmp_path / "other"
    foreign = other / source.relative_to(repo)
    foreign.parent.mkdir(parents=True)
    foreign.write_text("user source")
    runner = FakeRunner(_legacy_alias_failure_results(repo))

    def redirect(command):
        if command[1:3] == ["plugin", "add"]:
            alias.unlink()
            alias.symlink_to(other, target_is_directory=True)
        return runner(command)

    with pytest.raises(InstallError, match="legacy source"):
        install(repo, home, state, redirect)
    assert runner.results == []
    assert not os.path.lexists(destination)
    assert foreign.read_text() == "user source"
    assert (state / "expskill/codex-install.json").exists()


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("replacement", ["regular", "same-target", "stage"])
def test_legacy_repository_alias_keeps_recorded_restoration_identity(
    tmp_path: Path, retry, replacement: str,
) -> None:
    from scripts import install as module

    repo, home, state, alias, source, destination, target = _seed_legacy_repository_alias(tmp_path, True)
    write = module._write_codex_install_journal

    def checkpoint(path, payload):
        write(path, payload)
        record = next(item for item in payload["links"] if item["destination"] == str(destination))
        if record.get("legacy_restore", {}).get("dev") is not None:
            raise SystemExit(73)

    with mock.patch.object(module, "_write_codex_install_journal", checkpoint), pytest.raises(SystemExit):
        install(repo, home, state, FakeRunner(_legacy_alias_failure_results(repo)))
    if replacement == "stage":
        candidate, = destination.parent.glob("*.link")
        candidate.rename(tmp_path / "original-stage")
        candidate.symlink_to(target)
    else:
        candidate = destination
        if replacement == "regular":
            candidate.write_text("user replacement")
        else:
            candidate.symlink_to(target)
    identity = candidate.lstat()
    for _ in range(2):
        if retry is install or replacement == "stage":
            with pytest.raises(InstallError, match="conflicting|legacy link recovery|changed identity"):
                retry(repo, home, state, FakeRunner([]))
        else:
            runner = FakeRunner([
                plugin_list_response(), marketplace_list_response(),
                removal_response(),
            ] if (state / "expskill/codex-install.json").exists() else [])
            retry(repo, home, state, runner)
            assert runner.results == []
        metadata = candidate.lstat()
        assert (metadata.st_dev, metadata.st_ino) == (identity.st_dev, identity.st_ino)
        if replacement == "regular":
            assert candidate.read_text() == "user replacement"
        else:
            assert os.readlink(candidate) == target


@pytest.mark.parametrize("retry", [install, uninstall], ids=["install", "uninstall"])
@pytest.mark.parametrize("invalid_source", ["foreign", "alias", "other-profile", "null"])
def test_legacy_repository_alias_rejects_invalid_canonical_evidence(
    tmp_path: Path, retry, invalid_source: str,
) -> None:
    repo, home, state, alias, source, destination, target = _seed_legacy_repository_alias(tmp_path, False)
    with pytest.raises(InstallError, match="marketplace compensation failed"):
        install(repo, home, state, FakeRunner(_legacy_alias_failure_results(repo, True)))
    journal = state / "expskill/codex-install.json"
    payload = json.loads(journal.read_text())
    record = next(item for item in payload["links"] if item["destination"] == str(destination))
    record["legacy_source"] = {
        "foreign": str(tmp_path / "foreign" / destination.name),
        "alias": str(alias / source.relative_to(repo)),
        "other-profile": str(source.with_name("expskill-planner.toml")),
        "null": None,
    }[invalid_source]
    journal.write_text(json.dumps(payload))
    before, identity = journal.read_bytes(), destination.lstat()
    with pytest.raises(InstallError, match="legacy source"):
        retry(repo, home, state, FakeRunner([]))
    assert journal.read_bytes() == before
    assert destination.lstat() == identity
    assert os.readlink(destination) == target
    assert destination.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("interrupted", [False, True, "build"])
def test_package_replacement_during_build_preserves_original_authority(tmp_path, interrupted):
    from scripts import install as module

    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = module._codex_managed_root(repo, state)
    saved, foreign = tmp_path / "saved-package", tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "user-data").write_text("preserve replacement")
    shutil.copyfile(managed / module.CODEX_MANAGED_MARKER, foreign / module.CODEX_MANAGED_MARKER)
    original, replacement = managed.lstat(), foreign.lstat()
    build, write = module._build_codex_marketplace, module._write_codex_install_journal

    def building(repository, candidate):
        result = build(repository, candidate)
        managed.rename(saved)
        foreign.rename(managed)
        if interrupted == "build":
            os._exit(73)
        return result

    def writing(path, journal):
        write(path, journal)
        if interrupted is True and journal.get("swap") and journal["swap"]["phase"] == "published":
            os._exit(73)

    def attempt():
        try:
            with mock.patch.object(module, "_build_codex_marketplace", building), mock.patch.object(module, "_write_codex_install_journal", writing):
                install(repo, home, state, FakeRunner([]), agents_only=True)
        except InstallError:
            # A safe implementation rejects the swap before publication.
            if interrupted:
                os._exit(73)

    if interrupted:
        pid = os.fork()
        if pid == 0:
            attempt()
            os._exit(74)
        wait_for_crashed_child(pid)
    else:
        attempt()
    for _ in range(2):
        try:
            install(repo, home, state, FakeRunner([]), agents_only=True)
        except InstallError:
            pass
        survivors = list(tmp_path.rglob("user-data"))
        assert survivors, "rebuild/retry deleted a copied-marker replacement"
        assert survivors == [managed / "user-data"]
        assert survivors[0].read_text() == "preserve replacement"
        assert managed.lstat().st_ino == replacement.st_ino
        journal = json.loads(module._codex_install_journal_path(state).read_text())
        assert journal["swap"]["backup_ino"] == original.st_ino
    managed.rename(foreign)
    saved.rename(managed)
    install(repo, home, state, FakeRunner([]), agents_only=True)
    assert (foreign / "user-data").read_text() == "preserve replacement"
    assert not list(managed.parent.glob("*.swap-backup-*"))
    assert not module._codex_install_journal_path(state).exists()


@pytest.mark.parametrize("boundary", ["retired-object", "journal-clear"])
def test_swap_backup_terminal_checkpoint_preserves_replacement(boundary):
    from scripts import install as module

    # Reintroduce the same inode after its terminal checkpoint, then separately
    # exercise actual removal before journal clearing. Neither needs inode reuse
    # from a particular allocator, and all stat results are real.
    with tempfile.TemporaryDirectory(prefix="expskill-swap-retirement-") as temporary:
        root = Path(temporary)
        repo = seed_repository(root / "repo")
        home, state = root / "codex", root / "state"
        install(repo, home, state, FakeRunner([]), agents_only=True)
        managed = module._codex_managed_root(repo, state)
        original = managed.lstat()
        expected = (original.st_dev, original.st_ino)
        backup = module._codex_swap_backup_path(managed, expected)
        marker = (managed / module.CODEX_MANAGED_MARKER).read_bytes()
        pid = os.fork()
        if pid == 0:
            rmdir, write = os.rmdir, module._write_codex_install_journal

            def removing(path, *args, **kwargs):
                record = module._retirement_record_descriptor(str(path), directory=True)
                if boundary == "retired-object" and record and record[:2] == (backup.name, expected):
                    durable = json.loads(module._codex_install_journal_path(state).read_text())
                    assert durable["swap"]["phase"] == "retired"
                    assert durable["swap"]["retirement"] == str(path)
                    os.rename(path, backup.name, src_dir_fd=kwargs["dir_fd"], dst_dir_fd=kwargs["dir_fd"])
                    os._exit(73)
                rmdir(path, *args, **kwargs)

            def writing(path, journal):
                if boundary == "journal-clear" and journal.get("swap") is None and not backup.exists() and managed.lstat().st_ino != expected[1]:
                    os._exit(73)
                write(path, journal)

            with mock.patch.object(module.os, "rmdir", removing), mock.patch.object(module, "_write_codex_install_journal", writing):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            os._exit(74)
        wait_for_crashed_child(pid)
        journal_path = module._codex_install_journal_path(state)
        assert json.loads(journal_path.read_text())["swap"]["backup_ino"] == expected[1]
        assert json.loads(journal_path.read_text())["swap"]["phase"] == "retired"
        if boundary == "journal-clear":
            assert not backup.exists()
            backup.mkdir()
        else:
            assert backup.lstat().st_ino == expected[1]
        replacement_inode = backup.lstat().st_ino
        (backup / module.CODEX_MANAGED_MARKER).write_bytes(marker)
        (backup / "user-data").write_text("keep reused inode data")
        for _ in range(2):
            try:
                install(repo, home, state, FakeRunner([]), agents_only=True)
            except InstallError:
                pass
            assert (backup / "user-data").is_file(), "retry deleted data through a recycled backup inode"
            assert (backup / "user-data").read_text() == "keep reused inode data"
            assert (backup / module.CODEX_MANAGED_MARKER).read_bytes() == marker
            assert backup.lstat().st_ino == replacement_inode
        backup.rename(root / "preserved-replacement")
        for _ in range(2):
            install(repo, home, state, FakeRunner([]), agents_only=True)
        assert list(managed.parent.iterdir()) == [managed]
        assert not journal_path.exists()


@pytest.mark.parametrize("replacement", [None, "public-file", "public-link", "private"])
def test_receipt_retirement_recovers_before_install_conflicts(tmp_path, replacement):
    from scripts import install as module

    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    pid = os.fork()
    if pid == 0:
        exchange = module._renameat_exchange

        def exchanged(*args):
            exchange(*args)
            os._exit(73)

        with mock.patch.object(module, "_renameat_exchange", exchanged):
            uninstall(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    retired, = (home / "agents").glob("*.retire")
    destination = home / "agents" / module._retirement_record_descriptor(retired.name)[0]
    assert destination.is_file() and not destination.is_symlink()
    assert receipt_path(state).exists()
    assert not module._codex_install_journal_path(state).exists()
    if replacement is not None:
        preserved = retired if replacement == "private" else destination
        saved = tmp_path / "saved-retirement"
        preserved.rename(saved)
        if replacement == "public-link":
            user = tmp_path / "user-data"
            user.write_text("keep")
            preserved.symlink_to(user)
        else:
            preserved.write_text("keep")
        identity = preserved.lstat().st_ino
        for _ in range(2):
            with pytest.raises(InstallError):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            assert preserved.lstat().st_ino == identity
            assert preserved.read_text() == "keep"
            assert receipt_path(state).exists()
        preserved.unlink()
        if replacement == "private":
            saved.rename(retired)
    else:
        # Recovery itself must remain resumable without an install journal.
        unlink = os.unlink

        def interrupted(path, *args, **kwargs):
            if path == retired.name:
                raise SystemExit(73)
            unlink(path, *args, **kwargs)

        for _ in range(2):
            with mock.patch.object(module.os, "unlink", interrupted), pytest.raises(SystemExit):
                install(repo, home, state, FakeRunner([]), agents_only=True)
            assert receipt_path(state).exists()
    for _ in range(2):
        install(repo, home, state, FakeRunner([]), agents_only=True)
        assert destination.is_symlink()
        assert not module._codex_install_journal_path(state).exists()
    uninstall(repo, home, state, FakeRunner([]), agents_only=True)
    assert list(destination.parent.iterdir()) == []
    assert_package_only_receipt(repo, state)


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("boundary", ["between-invocations", "validated", "content"])
def test_correction_normal_package_replacement(tmp_path, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = managed_repository(repo)
    saved, foreign = tmp_path / "saved", tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "user-data").write_text("keep replacement")
    shutil.copyfile(managed / module.CODEX_MANAGED_MARKER, foreign / module.CODEX_MANAGED_MARKER)
    expected = foreign.stat().st_ino
    replaced, checks = False, 0
    owned, removing = module._codex_managed_root_is_owned, shutil.rmtree

    def substitute():
        nonlocal replaced
        managed.rename(saved)
        foreign.rename(managed)
        replaced = True

    def checked(path, repository):
        nonlocal checks
        result = owned(path, repository)
        if path == managed:
            checks += 1
            if boundary == "validated" and checks == 2 and result:
                substitute()
        return result

    def remove(path, *args, **kwargs):
        if boundary == "content" and not replaced and (
            path == managed / "plugins" or
            (path == "plugins" and kwargs.get("dir_fd") is not None)
        ):
            substitute()
        return removing(path, *args, **kwargs)

    if boundary == "between-invocations":
        substitute()
    for retry in (uninstall, install, uninstall):
        try:
            with mock.patch.object(module, "_codex_managed_root_is_owned", checked), mock.patch.object(module.shutil, "rmtree", remove):
                retry(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]), agents_only=retry is install)
        except InstallError:
            pass
        assert replaced
        assert (managed / "user-data").read_text() == "keep replacement"
        assert managed.stat().st_ino == expected
        assert receipt_path(state).exists()


@pytest.mark.parametrize("interrupted", [False, True])
def test_correction_publication_boundary_restores_replacement(tmp_path, interrupted):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed, saved = managed_repository(repo), tmp_path / "saved"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "user-data").write_text("keep public replacement")
    expected = foreign.stat().st_ino
    rename = module._renameat_noreplace
    injected = False

    def renamed(sfd, source, dfd, destination):
        nonlocal injected
        if source == managed.name and "swap-backup-" in destination and not injected:
            managed.rename(saved)
            foreign.rename(managed)
            injected = True
            rename(sfd, source, dfd, destination)
            if interrupted:
                raise SystemExit(73)
            return
        return rename(sfd, source, dfd, destination)

    with mock.patch.object(module, "_renameat_noreplace", renamed), pytest.raises((InstallError, SystemExit)):
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert injected
    for _ in range(2):
        try:
            install(repo, home, state, FakeRunner([]), agents_only=True)
        except InstallError:
            pass
        assert managed.exists(), "replacement was stranded under a private backup name"
        assert managed.stat().st_ino == expected
        assert (managed / "user-data").read_text() == "keep public replacement"
    managed.rename(foreign)
    saved.rename(managed)
    install(repo, home, state, FakeRunner([]), agents_only=True)
    assert (foreign / "user-data").is_file()


@pytest.mark.parametrize("boundary", ["before-free", "journal-clear"])
def test_correction_recovery_terminal_checkpoint(tmp_path, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery = recovery_repository(repo)
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    journal_path = module._codex_migration_journal_path(state)
    rmdir, clear = os.rmdir, module._clear_codex_migration_journal
    reached = False

    def removing(path, *args, **kwargs):
        nonlocal reached
        record = module._retirement_record_descriptor(str(path), directory=True)
        if record and record[0] == recovery.name:
            journal = json.loads(journal_path.read_text())
            assert journal.get("retirement") == str(path), "released inode still has recursive cleanup authority"
            if boundary == "before-free":
                # Return the exact retired inode to the public name. This
                # deterministically exercises released identity reuse without
                # relying on a filesystem allocator or falsifying stat data.
                os.rename(path, recovery.name, src_dir_fd=kwargs["dir_fd"], dst_dir_fd=kwargs["dir_fd"])
                module._write_codex_managed_marker(recovery, repo, recovery)
                (recovery / "user-data").write_text("keep released identity")
                reached = True
                raise SystemExit(73)
        return rmdir(path, *args, **kwargs)

    def clearing(path):
        nonlocal reached
        if boundary == "journal-clear":
            journal = json.loads(path.read_text())
            assert journal.get("retirement"), "journal retained live authority after removal"
            recovery.mkdir()
            module._write_codex_managed_marker(recovery, repo, recovery)
            (recovery / "user-data").write_text("keep released identity")
            reached = True
            raise SystemExit(73)
        clear(path)

    with mock.patch.object(module.os, "rmdir", removing), mock.patch.object(module, "_clear_codex_migration_journal", clearing), pytest.raises(SystemExit):
        install(repo, home, state, runner)
    assert reached
    expected = recovery.stat().st_ino
    for retry in (uninstall, install, uninstall):
        try:
            retry(repo, home, state, runner)
        except InstallError:
            pass
        assert recovery.stat().st_ino == expected
        assert (recovery / "user-data").read_text() == "keep released identity"
        assert journal_path.exists()
    shutil.rmtree(recovery)
    for _ in range(2):
        uninstall(repo, home, state, runner)
    assert not journal_path.exists()


@pytest.mark.parametrize("operation", ["uninstall", "retired-reinstall"])
@pytest.mark.parametrize("alias_root", ["state", "package"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_correction_alias_dependency(tmp_path, operation, alias_root, interrupted):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    entry = seed_recorded_retired_profile(repo, home, state) if operation == "retired-reinstall" else load_receipt(state)["links"][0]
    source, destination = Path(entry["source"]), Path(entry["destination"])
    managed = managed_repository(repo)
    alias = tmp_path / "alias"
    aliased = state if alias_root == "state" else managed
    alias.symlink_to(aliased, target_is_directory=True)
    if interrupted:
        with mock.patch.object(module, "_materialize_codex_package", side_effect=SystemExit), pytest.raises(SystemExit):
            install(repo, home, state, FakeRunner([]), agents_only=True)
    destination.unlink()
    destination.symlink_to(os.path.relpath(alias / source.relative_to(aliased), destination.parent))
    expected = destination.lstat().st_ino
    before = source.read_bytes()
    for _ in range(2):
        try:
            if operation == "uninstall":
                uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
            else:
                install(repo, home, state, FakeRunner([]), agents_only=True)
        except InstallError:
            pass
        assert source.is_file(), "preserved alias link lost its package dependency"
        assert source.read_bytes() == before
        assert destination.lstat().st_ino == expected
        assert entry in load_receipt(state)["links"]
    destination.unlink()
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()
    assert not managed.exists()


@pytest.mark.parametrize("retry", ["install", "uninstall"])
@pytest.mark.parametrize("boundary", ["content", "marker", "exchange", "before-checkpoint", "after-checkpoint", "retired-object", "receipt-clear"])
def test_correction_normal_package_cleanup_exit(tmp_path, boundary, retry):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = managed_repository(repo)
    expected = managed.stat().st_ino
    pid = os.fork()
    if pid == 0:
        rmtree, unlink, exchange, rmdir, write = shutil.rmtree, os.unlink, module._renameat_exchange, os.rmdir, module._write_codex_receipt

        def tree(path, *args, **kwargs):
            descriptor = kwargs.get("dir_fd")
            owned = descriptor is not None and os.fstat(descriptor).st_ino == expected
            rmtree(path, *args, **kwargs)
            if owned and boundary == "content":
                os._exit(73)

        def unlinked(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if boundary == "marker" and path == module.CODEX_MANAGED_MARKER:
                os._exit(73)

        def exchanged(sfd, source, dfd, destination):
            if boundary == "receipt-clear" and source == receipt_path(state).name:
                os._exit(73)
            exchange(sfd, source, dfd, destination)
            if source == managed.name and boundary == "exchange":
                os._exit(73)

        def written(path, receipt, **kwargs):
            retiring = receipt.codex_package and "retirement" in receipt.codex_package
            if retiring and boundary == "before-checkpoint":
                os._exit(73)
            write(path, receipt, **kwargs)
            if retiring and boundary == "after-checkpoint":
                os._exit(73)

        def removed(path, *args, **kwargs):
            record = module._retirement_record_descriptor(str(path), directory=True)
            owned = record and record[0] == managed.name
            if owned:
                assert load_receipt(state)["codex_package"]["retirement"] == str(path)
            rmdir(path, *args, **kwargs)
            if owned and boundary == "retired-object":
                os._exit(73)

        with mock.patch.object(module.shutil, "rmtree", tree), mock.patch.object(module.os, "unlink", unlinked), mock.patch.object(module, "_renameat_exchange", exchanged), mock.patch.object(module.os, "rmdir", removed), mock.patch.object(module, "_write_codex_receipt", written):
            uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
        os._exit(74)
    wait_for_crashed_child(pid)
    assert receipt_path(state).exists()
    if retry == "install":
        for _ in range(2):
            install(repo, home, state, FakeRunner([]), agents_only=True)
            assert list(managed.parent.iterdir()) == [managed]
            assert load_receipt(state)["codex_package"] == module._codex_package_identity(managed)
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not managed.exists()
    assert list(managed.parent.iterdir()) == []
    assert not receipt_path(state).exists()


@pytest.mark.parametrize("boundary", ["before-free", "receipt-clear"])
def test_correction_normal_terminal_replacement(tmp_path, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = managed_repository(repo)
    original = managed.stat().st_ino
    rmdir, clear = os.rmdir, module._clear_codex_record
    injected = False

    def replace_with_user_data():
        nonlocal injected
        module._write_codex_managed_marker(managed, repo, managed)
        (managed / "user-data").write_text("keep terminal replacement")
        injected = True
        raise SystemExit(73)

    def removing(path, *args, **kwargs):
        record = module._retirement_record_descriptor(str(path), directory=True)
        if boundary == "before-free" and record and record[0] == managed.name:
            assert load_receipt(state)["codex_package"]["retirement"] == str(path)
            os.rename(path, managed.name, src_dir_fd=kwargs["dir_fd"], dst_dir_fd=kwargs["dir_fd"])
            assert managed.stat().st_ino == original
            replace_with_user_data()
        return rmdir(path, *args, **kwargs)

    def clearing(path, *args, **kwargs):
        if boundary == "receipt-clear" and Path(path) == receipt_path(state):
            assert load_receipt(state)["codex_package"].get("retirement")
            managed.mkdir()
            replace_with_user_data()
        return clear(path, *args, **kwargs)

    with mock.patch.object(module.os, "rmdir", removing), mock.patch.object(module, "_clear_codex_record", clearing), pytest.raises(SystemExit):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert injected
    expected = managed.stat().st_ino
    for retry in (uninstall, install, uninstall):
        with pytest.raises(InstallError):
            retry(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]), agents_only=retry is install)
        assert managed.stat().st_ino == expected
        assert (managed / "user-data").read_text() == "keep terminal replacement"
        assert receipt_path(state).exists()
    managed.rename(tmp_path / "preserved")
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not receipt_path(state).exists()


@pytest.mark.parametrize("failure", [OSError, SystemExit, "restore-exit"])
def test_correction_unpublished_package_does_not_replace_receipt_authority(tmp_path, failure):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    install(repo, home, state, FakeRunner([]), agents_only=True)
    managed = managed_repository(repo)
    original = managed.stat().st_ino
    rename = module._renameat_noreplace

    def interrupted(sfd, source, dfd, destination):
        if source == "marketplace" and destination == managed.name:
            exception = OSError if failure == "restore-exit" else failure
            raise exception("candidate publication interrupted")
        result = rename(sfd, source, dfd, destination)
        if failure == "restore-exit" and "swap-backup-" in source and destination == managed.name:
            raise SystemExit("restoration checkpoint interrupted")
        return result

    with mock.patch.object(module, "_renameat_noreplace", interrupted), pytest.raises((InstallError, SystemExit)):
        install(repo, home, state, FakeRunner([]), agents_only=True)
    assert load_receipt(state)["codex_package"]["ino"] == original
    for _ in range(2):
        uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert not managed.exists()
    assert not receipt_path(state).exists()
    assert not module._codex_install_journal_path(state).exists()


@pytest.mark.parametrize("resume", ["direct", "install", "uninstall"])
@pytest.mark.parametrize("boundary", ["publication", "migration-checkpoint"])
def test_recovery_publication_preserves_replacement(tmp_path, resume, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery = recovery_repository(repo)
    saved = tmp_path / "original-recovery"
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    rename, write = module._renameat_noreplace, module._write_codex_migration_journal
    migration_path = module._codex_migration_journal_path(state)

    def published(sfd, source, dfd, destination):
        rename(sfd, source, dfd, destination)
        parent = os.fstat(dfd)
        if (source == "marketplace" and destination == recovery.name
                and (parent.st_dev, parent.st_ino)
                == (recovery.parent.stat().st_dev, recovery.parent.stat().st_ino)):
            marker = (recovery / module.CODEX_MANAGED_MARKER).read_bytes()
            recovery.rename(saved)
            recovery.mkdir()
            (recovery / module.CODEX_MANAGED_MARKER).write_bytes(marker)
            (recovery / "user-data").write_text("preserve publication replacement")
            assert recovery.stat().st_ino != saved.stat().st_ino
            if resume != "direct" and boundary == "publication":
                os._exit(73)

    def checkpoint(path, payload):
        write(path, payload)
        if resume != "direct" and boundary == "migration-checkpoint":
            os._exit(73)

    def attempt():
        with mock.patch.object(module, "_renameat_noreplace", published), mock.patch.object(
            module, "_write_codex_migration_journal", checkpoint
        ):
            try:
                install(repo, home, state, runner)
            except InstallError:
                pass

    if resume == "direct":
        attempt()
    else:
        pid = os.fork()
        if pid == 0:
            attempt()
            os._exit(74)
        wait_for_crashed_child(pid)
    assert saved.is_dir(), "publication injection was not reached"
    assert (recovery / "user-data").is_file()
    expected = recovery.stat().st_ino
    if resume != "direct":
        operation = install if resume == "install" else uninstall
        for operation in (operation, operation, install, uninstall):
            try:
                operation(repo, home, state, runner)
            except InstallError:
                pass
            assert (recovery / "user-data").read_text() == "preserve publication replacement"
            assert recovery.stat().st_ino == expected
    if migration_path.exists():
        migration = json.loads(migration_path.read_text())
        assert migration["recovery_ino"] == saved.stat().st_ino
        assert migration["recovery_package"] == module._codex_package_identity(saved)


@pytest.mark.parametrize("resume", ["install", "uninstall"])
def test_recovery_publication_exit_retains_cleanup_authority(tmp_path, resume):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    recovery = recovery_repository(repo)
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json")
    pid = os.fork()
    if pid == 0:
        rename = module._renameat_noreplace

        def published(sfd, source, dfd, destination):
            rename(sfd, source, dfd, destination)
            if source == "marketplace" and destination == recovery.name:
                journal = json.loads(module._codex_install_journal_path(state).read_text())
                assert journal["recovery_package"] == module._codex_package_identity(recovery)
                os._exit(73)

        with mock.patch.object(module, "_renameat_noreplace", published):
            install(repo, home, state, runner)
        os._exit(74)
    wait_for_crashed_child(pid)
    assert not module._codex_migration_journal_path(state).exists()
    operation = install if resume == "install" else uninstall
    for _ in range(2):
        operation(repo, home, state, runner)
    uninstall(repo, home, state, runner)
    assert not recovery.exists()
    # Hard exit can leave the empty, unpublished builder parent. Recovery must
    # retire the published package and every journal-authorized retirement.
    assert all(
        path.name.startswith(".codex-package-") and not any(path.iterdir())
        for path in recovery.parent.iterdir()
    )
    assert not receipt_path(state).exists()
    assert not module._codex_install_journal_path(state).exists()
    assert not module._codex_migration_journal_path(state).exists()


@pytest.mark.parametrize("trigger", ["publication-sync", "construction-conflict"])
def test_correction_base_receipt_survives_failed_upgrade(tmp_path, trigger):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    InstallerTests()._install_base_receipt(repo, home, state, agents_only=False)
    original = load_receipt(state)
    assert original["marketplace_added"] is original["plugin_installed"] is True
    assert len(original["links"]) == 7
    identities = {
        item["destination"]: Path(item["destination"]).lstat().st_ino
        for item in original["links"]
    }
    managed = managed_repository(repo)
    managed.rename(tmp_path / "old-package")
    selected = Path(original["links"][0]["destination"])
    saved_link = selected.with_name("saved-original-link")
    sync, build = module._fsync_directory, module._build_codex_marketplace
    injected = False

    def syncing(path):
        nonlocal injected
        if trigger == "publication-sync" and managed.exists() and not injected:
            injected = True
            raise OSError("injected publication sync failure")
        sync(path)

    def building(*args, **kwargs):
        nonlocal injected
        result = build(*args, **kwargs)
        if trigger == "construction-conflict":
            selected.rename(saved_link)
            selected.write_text("user conflict")
            injected = True
        return result

    with mock.patch.object(module, "_fsync_directory", syncing), mock.patch.object(
        module, "_build_codex_marketplace", building
    ), pytest.raises(InstallError):
        install(repo, home, state, FakeRunner([]))
    assert injected
    after = load_receipt(state)
    assert after["marketplace_added"] is after["plugin_installed"] is True
    assert len(after["links"]) == 7
    assert {item["destination"]: item["destination_ino"] for item in after["links"]} == identities
    if trigger == "construction-conflict":
        assert selected.read_text() == "user conflict"
        selected.unlink()
        saved_link.rename(selected)
    install(repo, home, state, FakeRunner(install_results(repo, True, True)))
    uninstall(repo, home, state, FakeRunner([
        plugin_list_response(repo), marketplace_list_response(repo),
        removal_response(), removal_response(),
    ]))
    assert all(not os.path.lexists(item["destination"]) for item in original["links"])
    assert not receipt_path(state).exists()
    assert not managed.exists()


@pytest.mark.parametrize("kind", ["receipt", "install", "migration"])
@pytest.mark.parametrize("boundary", ["before-exchange", "at-exchange", "after-exchange"])
def test_correction_final_record_preserves_replacement(tmp_path, kind, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    paths = {"receipt": receipt_path(state), "install": module._codex_install_journal_path(state),
             "migration": module._codex_migration_journal_path(state)}
    record = paths[kind]
    saved = tmp_path / "original-record.json"
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json") if kind == "migration" else FakeRunner([])
    if kind == "receipt":
        install(repo, home, state, FakeRunner([]), agents_only=True)
        runner = FakeRunner([plugin_list_response(), marketplace_list_response()])
    unlink, exchange = Path.unlink, module._renameat_exchange
    cleanup, clear_install, clear_migration = (module._remove_owned_codex_marketplace,
        module._clear_codex_install_journal, module._clear_codex_migration_journal)
    injected = False

    def substitute():
        nonlocal injected
        if not injected:
            record.rename(saved)
            record.write_text("unrelated replacement record\n")
            injected = True

    def unlinked(path, *args, **kwargs):
        # The rejected implementation uses this final public pathname unlink.
        if path == record:
            substitute()
        return unlink(path, *args, **kwargs)

    def exchanged(sfd, source, dfd, destination):
        if source == record.name and boundary != "after-exchange":
            substitute()
        result = exchange(sfd, source, dfd, destination)
        if source == record.name and boundary == "after-exchange":
            substitute()
        return result

    def cleaned(*args, **kwargs):
        result = cleanup(*args, **kwargs)
        if kind == "receipt" and boundary == "before-exchange":
            substitute()
        return result

    def cleared(path):
        if boundary == "before-exchange":
            substitute()
        return (clear_install if kind == "install" else clear_migration)(path)

    with mock.patch.object(Path, "unlink", unlinked), mock.patch.object(module, "_renameat_exchange", exchanged), mock.patch.object(
        module, "_remove_owned_codex_marketplace", cleaned
    ), mock.patch.object(module, "_clear_codex_install_journal", cleared if kind == "install" else clear_install), mock.patch.object(
        module, "_clear_codex_migration_journal", cleared if kind == "migration" else clear_migration
    ):
        try:
            (uninstall if kind == "receipt" else install)(repo, home, state, runner, agents_only=kind == "install")
        except InstallError:
            pass
    assert injected
    assert record.read_text() == "unrelated replacement record\n"
    expected = record.stat().st_ino
    for _ in range(2):
        with pytest.raises(InstallError):
            uninstall(repo, home, state, FakeRunner([]))
        assert record.stat().st_ino == expected
        assert record.read_text() == "unrelated replacement record\n"
    record.unlink()
    if boundary != "after-exchange":
        saved.rename(record)
    for _ in range(2):
        uninstall(repo, home, state, runner if kind == "migration" else FakeRunner([
            plugin_list_response(), marketplace_list_response(),
        ]))
    assert not record.exists()


@pytest.mark.parametrize("kind", ["managed", "refresh", "recovery"])
def test_correction_staging_cleanup_preserves_replacement(tmp_path, kind):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    target = (module._codex_recovery_root(repo, state) if kind == "recovery"
              else module._codex_managed_root(repo, state))
    if kind == "refresh":
        install(repo, home, state, FakeRunner([]), agents_only=True)
    rename = module._renameat_noreplace
    injected = []

    def published(sfd, source, dfd, destination):
        result = rename(sfd, source, dfd, destination)
        if source == "marketplace" and destination == target.name:
            identity = os.fstat(sfd)
            staging = next(path for path in target.parent.iterdir()
                           if path.stat().st_ino == identity.st_ino)
            staging.rename(tmp_path / "original-staging")
            staging.mkdir()
            (staging / "user-data").write_text("preserve unrelated directory\n")
            injected.append((staging, staging.stat().st_ino))
        return result

    runner = RecoveryCliRunner(repo, tmp_path / "cli.json") if kind == "recovery" else FakeRunner([])
    with mock.patch.object(module, "_renameat_noreplace", published):
        install(repo, home, state, runner, agents_only=kind != "recovery")
    assert len(injected) == 1
    for operation in (None, install, uninstall):
        if operation is not None:
            operation(repo, home, state, runner if kind == "recovery" else FakeRunner(
                [] if operation is install else [plugin_list_response(), marketplace_list_response()]
            ), agents_only=operation is install and kind != "recovery")
        staging, inode = injected[0]
        assert staging.stat().st_ino == inode
        assert (staging / "user-data").read_text() == "preserve unrelated directory\n"


@pytest.mark.parametrize("kind", ["receipt", "install", "migration"])
@pytest.mark.parametrize("boundary", ["prepared", "exchanged", "sentinel-moved", "before-unlink", "after-unlink"])
def test_correction_final_record_retirement_recovers_exit(tmp_path, kind, boundary):
    from scripts import install as module
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    paths = {"receipt": receipt_path(state), "install": module._codex_install_journal_path(state),
             "migration": module._codex_migration_journal_path(state)}
    record = paths[kind]
    runner = RecoveryCliRunner(repo, tmp_path / "cli.json") if kind == "migration" else FakeRunner([])
    if kind == "receipt":
        install(repo, home, state, FakeRunner([]), agents_only=True)
        runner = FakeRunner([plugin_list_response(), marketplace_list_response()])
    pid = os.fork()
    if pid == 0:
        exchange, rename, unlink = module._renameat_exchange, module._renameat_noreplace, os.unlink

        def exchanged(sfd, source, dfd, destination):
            if source == record.name and boundary == "prepared":
                os._exit(73)
            exchange(sfd, source, dfd, destination)
            if source == record.name and boundary == "exchanged":
                os._exit(73)

        def renamed(sfd, source, dfd, destination):
            rename(sfd, source, dfd, destination)
            if source == record.name and boundary == "sentinel-moved":
                os._exit(73)

        def unlinked(path, *args, **kwargs):
            retired = module._retirement_record_descriptor(str(path))
            owned = retired is not None and retired[0] == record.name
            if owned and boundary == "before-unlink":
                os._exit(73)
            unlink(path, *args, **kwargs)
            if owned and boundary == "after-unlink":
                os._exit(73)

        with mock.patch.object(module, "_renameat_exchange", exchanged), mock.patch.object(
            module, "_renameat_noreplace", renamed
        ), mock.patch.object(module.os, "unlink", unlinked):
            (uninstall if kind == "receipt" else install)(repo, home, state, runner, agents_only=kind == "install")
        os._exit(74)
    wait_for_crashed_child(pid)
    for _ in range(2):
        uninstall(repo, home, state, runner if kind == "migration" else FakeRunner([
            plugin_list_response(), marketplace_list_response(),
        ]))
    assert all(not path.exists() for path in paths.values())
    assert all(
        path.name in {"codex-marketplace", "codex-marketplace-recovery"}
        and path.is_dir() and not any(path.iterdir())
        for path in record.parent.iterdir()
    )
