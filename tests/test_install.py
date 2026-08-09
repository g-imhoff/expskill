from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.install import InstallError, install, uninstall


PROFILE_NAMES = (
    "devflow-explorer",
    "devflow-implementer",
    "devflow-reviewer",
    "devflow-test-engineer",
    "devflow-verifier",
)


class FakeResult:
    def __init__(self, returncode: int, payload: object | None = None, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = "" if payload is None else json.dumps(payload)
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
    agents = path / "plugins" / "codex-dev-flow" / "assets" / "agents"
    agents.mkdir(parents=True)
    for name in PROFILE_NAMES:
        (agents / f"{name}.toml").write_text(
            "\n".join(
                (
                    f'name = "{name}"',
                    f'description = "{name}"',
                    'model = "gpt-5.6-luna"',
                    'model_reasoning_effort = "max"',
                    'sandbox_mode = "workspace-write"',
                    'developer_instructions = "Bounded profile."',
                    "",
                )
            ),
            encoding="utf-8",
        )
    return path


def marketplace_response(already_added: bool) -> FakeResult:
    return FakeResult(0, {"alreadyAdded": already_added})


def plugin_response(already_installed: bool) -> FakeResult:
    return FakeResult(0, {"alreadyInstalled": already_installed})


def removal_response() -> FakeResult:
    return FakeResult(0, {"removed": True})


def marketplace_list_response(repository: Path) -> FakeResult:
    return FakeResult(
        0,
        {
            "marketplaces": [
                {
                    "name": "codex-dev-flow",
                    "root": str(repository),
                    "marketplaceSource": {
                        "sourceType": "local",
                        "source": str(repository),
                    },
                }
            ]
        },
    )


def destination_paths(codex_home: Path) -> dict[str, Path]:
    return {
        name: codex_home / "agents" / f"{name}.toml"
        for name in PROFILE_NAMES
    }


class InstallerTests(unittest.TestCase):
    def test_regular_file_conflict_refuses_without_partial_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            conflict = codex_home / "agents" / "devflow-reviewer.toml"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("user-owned\n", encoding="utf-8")
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "devflow-reviewer.toml"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                sorted(path.name for path in conflict.parent.iterdir()),
                ["devflow-reviewer.toml"],
            )
            self.assertEqual(runner.calls, [])
            self.assertFalse((state_home / "codex-dev-flow" / "install.json").exists())

    def test_install_links_every_profile_and_registers_plugin_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [marketplace_response(False), plugin_response(False)]
            )

            result = install(repo, codex_home, state_home, runner)
            destinations = destination_paths(codex_home)

            self.assertEqual(
                runner.calls,
                [
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(repo.resolve()),
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "add",
                        "codex-dev-flow@codex-dev-flow",
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
                self.assertEqual(destination.resolve(), repo.resolve() / "plugins" / "codex-dev-flow" / "assets" / "agents" / f"{name}.toml")

            receipt_path = state_home / "codex-dev-flow" / "install.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["repository_root"], str(repo.resolve()))
            self.assertTrue(receipt["marketplace_added"])
            self.assertTrue(receipt["plugin_installed"])
            self.assertEqual(
                {
                    entry["destination"] for entry in receipt["links"]
                },
                {str(path) for path in destinations.values()},
            )
            self.assertEqual(runner.results, [])

    def test_second_install_is_a_no_op_for_owned_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            first_runner = FakeRunner(
                [marketplace_response(False), plugin_response(False)]
            )
            install(repo, codex_home, state_home, first_runner)
            receipt_path = state_home / "codex-dev-flow" / "install.json"
            before = receipt_path.read_bytes()

            second_runner = FakeRunner(
                [marketplace_response(True), plugin_response(True)]
            )
            result = install(repo, codex_home, state_home, second_runner)

            self.assertEqual(result.created_links, ())
            self.assertEqual(
                second_runner.calls,
                [
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(repo.resolve()),
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "add",
                        "codex-dev-flow@codex-dev-flow",
                        "--json",
                    ),
                ],
            )
            for destination in destination_paths(codex_home).values():
                self.assertTrue(destination.is_symlink())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["marketplace_added"], True)
            self.assertEqual(receipt["plugin_installed"], True)
            self.assertEqual(
                {entry["destination"] for entry in receipt["links"]},
                {str(path) for path in destination_paths(codex_home).values()},
            )
            self.assertEqual(before, receipt_path.read_bytes())

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
            unrelated = agents / "devflow-explorer.toml"
            unrelated.symlink_to(unrelated_target)
            broken = agents / "devflow-verifier.toml"
            broken.symlink_to(root / "does-not-exist.toml")
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "devflow-explorer.toml|devflow-verifier.toml"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(runner.calls, [])
            self.assertTrue(unrelated.is_symlink())
            self.assertTrue(os.path.lexists(broken))
            self.assertEqual(
                sorted(path.name for path in agents.iterdir()),
                ["devflow-explorer.toml", "devflow-verifier.toml"],
            )
            self.assertFalse((state_home / "codex-dev-flow" / "install.json").exists())

    def test_plugin_failure_rolls_back_links_created_by_this_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner(
                [
                    marketplace_response(False),
                    FakeResult(1, {"error": "plugin unavailable"}, "plugin failed"),
                    removal_response(),
                ]
            )

            with self.assertRaisesRegex(InstallError, "plugin"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls,
                [
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        str(repo.resolve()),
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "add",
                        "codex-dev-flow@codex-dev-flow",
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "codex-dev-flow",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())
            self.assertFalse((state_home / "codex-dev-flow" / "install.json").exists())

    def test_uninstall_removes_only_links_still_owned_by_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install_runner = FakeRunner(
                [marketplace_response(False), plugin_response(False)]
            )
            install(repo, codex_home, state_home, install_runner)
            destinations = destination_paths(codex_home)
            missing = destinations["devflow-explorer"]
            missing.unlink()
            retargeted = destinations["devflow-implementer"]
            retargeted.unlink()
            retargeted.symlink_to(root / "unrelated.toml")
            (root / "unrelated.toml").write_text("preserved\n", encoding="utf-8")
            replaced = destinations["devflow-reviewer"]
            replaced.unlink()
            replaced.write_text("user replacement\n", encoding="utf-8")
            uninstall_runner = FakeRunner(
                [
                    marketplace_list_response(repo.resolve()),
                    removal_response(),
                    removal_response(),
                ]
            )

            result = uninstall(repo, codex_home, state_home, uninstall_runner)

            self.assertEqual(
                uninstall_runner.calls,
                [
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "list",
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "remove",
                        "codex-dev-flow@codex-dev-flow",
                        "--json",
                    ),
                    (
                        "codex",
                        "plugin",
                        "marketplace",
                        "remove",
                        "codex-dev-flow",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(
                {path.destination.name for path in result.removed_links},
                {"devflow-test-engineer.toml", "devflow-verifier.toml"},
            )
            self.assertFalse(destinations["devflow-test-engineer"].exists())
            self.assertFalse(destinations["devflow-verifier"].exists())
            self.assertFalse(os.path.lexists(missing))
            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(retargeted.resolve(), (root / "unrelated.toml").resolve())
            self.assertTrue(replaced.is_file())
            self.assertFalse((state_home / "codex-dev-flow" / "install.json").exists())

    def test_uninstall_preserves_retargeted_links_and_preexisting_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install_runner = FakeRunner(
                [marketplace_response(True), plugin_response(False)]
            )
            install(repo, codex_home, state_home, install_runner)
            retargeted = codex_home / "agents" / "devflow-reviewer.toml"
            retargeted.unlink()
            target = root / "user-owned.toml"
            target.write_text("user-owned\n", encoding="utf-8")
            retargeted.symlink_to(target)
            uninstall_runner = FakeRunner([removal_response()])

            uninstall(repo, codex_home, state_home, uninstall_runner)

            self.assertEqual(
                uninstall_runner.calls,
                [
                    (
                        "codex",
                        "plugin",
                        "remove",
                        "codex-dev-flow@codex-dev-flow",
                        "--json",
                    )
                ],
            )
            self.assertTrue(retargeted.is_symlink())
            self.assertEqual(retargeted.resolve(), target.resolve())
            self.assertFalse(
                any(
                    call[-2:] == ("remove", "--json")
                    and "marketplace" in call
                    for call in uninstall_runner.calls
                )
            )
            self.assertFalse((state_home / "codex-dev-flow" / "install.json").exists())

    def test_malformed_command_json_refuses_without_partial_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            runner = FakeRunner([FakeResult(0)])

            with self.assertRaisesRegex(InstallError, "JSON"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())
            self.assertFalse((state_home / "codex-dev-flow" / "install.json").exists())

    def test_receipt_repository_mismatch_refuses_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            receipt_dir = state_home / "codex-dev-flow"
            receipt_dir.mkdir(parents=True)
            (receipt_dir / "install.json").write_text(
                json.dumps(
                    {
                        "repository_root": str(root / "another-repo"),
                        "links": [],
                        "marketplace_added": True,
                        "plugin_installed": True,
                    }
                ),
                encoding="utf-8",
            )
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "receipt"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(runner.calls, [])
            self.assertFalse((codex_home / "agents").exists())


if __name__ == "__main__":
    unittest.main()
