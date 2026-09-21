from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

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


def wait_for_crashed_child(pid: int, expected_status: int = 73) -> None:
    waited, status = os.waitpid(pid, 0)
    if waited != pid or not os.WIFEXITED(status):
        raise AssertionError(f"child {pid} did not exit normally: {status}")
    if os.WEXITSTATUS(status) != expected_status:
        raise AssertionError(
            f"child {pid} exited {os.WEXITSTATUS(status)}, expected {expected_status}"
        )


class InstallerTests(unittest.TestCase):
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
                self.assertFalse(receipt_path(state_home).exists())

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
            self.assertEqual(journal.read_bytes(), before)
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
            self.assertFalse((state_home / "expskill" / "codex-migration.json").exists())
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
                install_results(repo, marketplace_present=True, plugin_present=True)
                + [plugin_list_response(repo), marketplace_list_response(repo),
                   removal_response(), removal_response()]
            )
            uninstall(repo, codex_home, state_home, runner)
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
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            events = []
            original_rename, original_fsync, original_rmtree = Path.rename, os.fsync, shutil.rmtree
            def rename(path: Path, target: Path) -> Path:
                result = original_rename(path, target)
                if path.name == "marketplace" and path.parent.name.startswith(".codex-package-"):
                    events.append(("rename", path.parent, Path(target).parent))
                return result
            def fsync(fd: int) -> None:
                events.append(("sync", Path(os.readlink(f"/proc/self/fd/{fd}"))))
                original_fsync(fd)
            def rmtree(path: Path, *args: object, **kwargs: object) -> None:
                original_rmtree(path, *args, **kwargs)
                if Path(path).name.startswith(".codex-package-"):
                    events.append(("remove", Path(path)))
            with mock.patch.object(Path, "rename", rename), mock.patch("scripts.install.os.fsync", fsync), mock.patch("scripts.install.shutil.rmtree", rmtree):
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
            self.assertFalse(receipt_path(state_home).exists())

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
            self.assertFalse(receipt_path(state_home).exists())

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
            self.assertFalse(
                (state_home / "expskill" / "codex-migration.json").exists()
            )
            self.assertFalse(receipt_path(state_home).exists())

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
            self.assertFalse(
                (state_home / "expskill" / "codex-migration.json").exists()
            )
            self.assertFalse(receipt_path(state_home).exists())

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
                        else:
                            self.assertFalse(managed_repository(repo).exists())
                        self.assertFalse(journal.exists())
                        self.assertFalse(receipt_path(state_home).exists())
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
            recovery_root = install_module._materialize_codex_recovery_package(
                repo.resolve(), state_home
            )
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
            recovery_root = module._materialize_codex_recovery_package(repo, state_home)
            original_remove = module._remove_exact_codex_swap_backup

            def fail_recovery_backup(
                backup: Path, expected: tuple[int, int], target: Path, repository: Path
            ) -> None:
                if target == recovery_root:
                    raise InstallError("injected recovery backup cleanup failure")
                original_remove(backup, expected, target, repository)

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
            self.assertFalse(receipt_path(state_home).exists())

    def test_install_preserves_unproven_retired_profile_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            receipt = load_receipt(state_home)
            retired_destinations: list[Path] = []
            for name in RETIRED_PROFILE_NAMES:
                source = (
                    repo.resolve()
                    / "plugins"
                    / "expskill"
                    / "codex"
                    / "runtime"
                    / "agents"
                    / f"{name}.toml"
                )
                destination = codex_home.resolve() / "agents" / f"{name}.toml"
                destination.symlink_to(source)
                retired_destinations.append(destination)
                receipt["links"].append(
                    {"destination": str(destination), "source": str(source)}
                )
            receipt_path(state_home).write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            runner = FakeRunner(install_results(repo, True, True))

            result = install(repo, codex_home, state_home, runner)

            self.assertEqual(result.removed_links, ())
            self.assertTrue(all(path.is_symlink() for path in retired_destinations))
            self.assertEqual(
                {
                    Path(entry["destination"]).stem
                    for entry in load_receipt(state_home)["links"]
                },
                set(PROFILE_NAMES),
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

    def test_marketplace_name_conflict_is_rejected_before_any_mutation(self) -> None:
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
            self.assertFalse(receipt_path(state_home).exists())

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
            self.assertFalse(receipt_path(state_home).exists())

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
            self.assertFalse(receipt_path(state_home).exists())
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
            self.assertFalse(receipt_path(state_home).exists())

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
            self.assertFalse(receipt_path(state_home).exists())

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

            self.assertEqual(len(temporary_paths), 1)
            self.assertIn(str(temporary_paths[0]), str(context.exception))
            self.assertIn("temporary receipt cleanup", str(context.exception))
            self.assertEqual(
                runner.calls[-2:],
                [
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
                    ("codex", "plugin", "marketplace", "remove", "expskill", "--json"),
                ],
            )
            self.assertTrue(os.path.lexists(temporary_paths[0]))
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
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            receipt = receipt_path(state_home)
            original_unlink = Path.unlink
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
            with mock.patch.object(Path, "unlink", fail_receipt_once):
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
                if path == managed_root / "plugins" and failed["value"]:
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
            original_rmdir = Path.rmdir
            failed = {"value": True}

            def fail_root_once(path: Path) -> None:
                if path == managed_root and failed["value"]:
                    failed["value"] = False
                    raise OSError("root busy")
                original_rmdir(path)

            with mock.patch.object(Path, "rmdir", fail_root_once):
                with self.assertRaisesRegex(InstallError, "root busy"):
                    uninstall(repo, codex_home, state_home, first_runner)

            self.assertTrue(receipt_path(state_home).is_file())
            self.assertTrue(managed_root.is_dir())
            self.assertEqual(tuple(managed_root.iterdir()), ())

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
            self.assertFalse(receipt_path(state_home).exists())
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
            self.assertFalse(receipt_path(state_home).exists())

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

    def test_agents_only_uninstall_discards_receipt_for_preexisting_cli_state(self) -> None:
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
            self.assertFalse(receipt_path(state_home).exists())
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


if __name__ == "__main__":
    unittest.main()
