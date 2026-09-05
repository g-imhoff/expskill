from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from scripts.install import InstallError, install, main, uninstall


ROOT = Path(__file__).resolve().parents[1]
PROFILE_NAMES = (
    "devflow-explorer",
    "devflow-implementer",
    "devflow-test-engineer",
    "devflow-review",
    "devflow-spec",
)
RETIRED_PROFILE_NAMES = (
    "devflow-critical-reviewer",
    "devflow-implementer-high",
    "devflow-reviewer",
    "devflow-verifier",
    "devflow-verifier-low",
)
SKILL_NAMES = (
    "use-expand",
    "brainstorm",
    "design",
    "grill-me",
    "plan",
    "implement",
    "skill-builder",
    "unslop",
)
PLUGIN_SELECTOR = "codex-dev-flow@codex-dev-flow"
MANIFEST_VERSION = json.loads(
    (ROOT / "plugins" / "codex-dev-flow" / ".codex-plugin" / "plugin.json").read_text(
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
    source_plugin = ROOT / "plugins" / "codex-dev-flow"
    destination_plugin = path / "plugins" / "codex-dev-flow"
    source_scripts = source_plugin / "scripts"
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
    shutil.copytree(ROOT / ".agents", path / ".agents")
    shutil.copytree(source_plugin / ".codex-plugin", destination_plugin / ".codex-plugin")
    shutil.copytree(source_plugin / "assets", destination_plugin / "assets")
    shutil.copytree(source_plugin / "hooks", destination_plugin / "hooks")
    shutil.copytree(source_plugin / "skills", destination_plugin / "skills")
    shutil.copytree(source_plugin / "third-party", destination_plugin / "third-party")
    # Seed the same route-neutral plugin inputs that a real marketplace
    # registration receives, including the centralized worktree helper.
    shutil.copytree(source_scripts, destination_plugin / "scripts")
    shutil.copytree(ROOT / "scripts", path / "scripts")
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def destination_paths(codex_home: Path) -> dict[str, Path]:
    canonical_home = codex_home.resolve()
    return {
        name: canonical_home / "agents" / f"{name}.toml"
        for name in PROFILE_NAMES
    }


def marketplace_list_response(
    repository: Path | None = None,
    source: Path | None = None,
) -> FakeResult:
    marketplaces: list[dict[str, object]] = []
    if repository is not None:
        selected_source = repository if source is None else source
        marketplaces.append(
            {
                "name": "codex-dev-flow",
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
            "marketplaceName": "codex-dev-flow",
            "installedRoot": str(repository),
            "alreadyAdded": already_added,
        },
    )


def plugin_entry(repository: Path, source: Path | None = None) -> dict[str, object]:
    plugin_source = repository if source is None else source
    return {
        "pluginId": PLUGIN_SELECTOR,
        "name": "codex-dev-flow",
        "marketplaceName": "codex-dev-flow",
        "version": "0.1.0",
        "installed": True,
        "enabled": True,
        "source": {
            "source": "local",
            "path": str(plugin_source / "plugins" / "codex-dev-flow"),
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
            "name": "codex-dev-flow",
            "marketplaceName": "codex-dev-flow",
            "version": version,
            "installedPath": str(
                repository
                / "codex"
                / "plugins"
                / "cache"
                / "codex-dev-flow"
                / "codex-dev-flow"
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
    return state_home.resolve() / "codex-dev-flow" / "install.json"


def load_receipt(state_home: Path) -> dict[str, object]:
    return json.loads(receipt_path(state_home).read_text(encoding="utf-8"))


class InstallerTests(unittest.TestCase):
    def test_seeded_plugin_package_preserves_every_real_phase_entrypoint(self) -> None:
        """Regression: installation fixtures silently omit independently callable phases."""

        with tempfile.TemporaryDirectory() as temporary:
            repository = seed_repository(Path(temporary) / "repository")
            skills_root = repository / "plugins" / "codex-dev-flow" / "skills"
            self.assertEqual({entry.name for entry in skills_root.iterdir()}, set(SKILL_NAMES))
            for name in SKILL_NAMES:
                self.assertTrue((skills_root / name / "SKILL.md").is_file(), name)
                self.assertTrue((skills_root / name / "agents" / "openai.yaml").is_file(), name)

    def test_seed_repository_copies_route_neutral_plugin_scripts(self) -> None:
        """Fixture regression: installed-package inputs retain the centralized helper."""

        with tempfile.TemporaryDirectory() as temporary:
            repository = seed_repository(Path(temporary) / "repository")
            source = ROOT / "plugins" / "codex-dev-flow" / "scripts"
            destination = repository / "plugins" / "codex-dev-flow" / "scripts"
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
            source = ROOT / "plugins" / "codex-dev-flow"
            destination = repository / "plugins" / "codex-dev-flow"
            for relative in ("hooks", "third-party"):
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
                shutil.copytree(ROOT / ".agents", source_root / ".agents")
                shutil.copytree(ROOT / "plugins" / "codex-dev-flow", source_root / "plugins" / "codex-dev-flow")
                shutil.copytree(ROOT / "scripts", source_root / "scripts")
                return source_root, source_root / "plugins" / "codex-dev-flow" / "scripts" / helper_name

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
                        "codex-dev-flow",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(tuple((codex_home / "agents").iterdir()), ())
            self.assertFalse(receipt_path(state_home).exists())

    def test_dry_run_prints_canonical_repository_and_only_planned_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex"
            before = tuple(codex_home.parent.iterdir())
            output = StringIO()
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
                with redirect_stdout(output):
                    result = main(["--dry-run"])

            self.assertEqual(result, 0)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), len(PROFILE_NAMES) + 2)
            self.assertEqual(
                lines[-2],
                f"codex plugin marketplace add {ROOT.resolve()} --json",
            )
            self.assertEqual(lines[-1], "codex plugin add codex-dev-flow@codex-dev-flow --json")
            self.assertEqual(before, tuple(codex_home.parent.iterdir()))

    def test_regular_file_conflict_refuses_without_partial_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            conflict = codex_home / "agents" / "devflow-review.toml"
            conflict.parent.mkdir(parents=True)
            conflict.write_text("user-owned\n", encoding="utf-8")
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "devflow-review.toml"):
                install(repo, codex_home, state_home, runner)

            self.assertEqual(
                sorted(path.name for path in conflict.parent.iterdir()),
                ["devflow-review.toml"],
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
                        str(repo.resolve()),
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
                expected = repo.resolve() / "plugins" / "codex-dev-flow" / "assets" / "agents" / f"{name}.toml"
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

    def test_install_migrates_owned_retired_profile_links(self) -> None:
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
                    / "codex-dev-flow"
                    / "assets"
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

            self.assertEqual(
                {link.destination.name for link in result.removed_links},
                {f"{name}.toml" for name in RETIRED_PROFILE_NAMES},
            )
            self.assertTrue(
                all(not os.path.lexists(path) for path in retired_destinations)
            )
            self.assertEqual(
                {
                    Path(entry["destination"]).stem
                    for entry in load_receipt(state_home)["links"]
                },
                set(PROFILE_NAMES),
            )

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
            broken = agents / "devflow-spec.toml"
            broken.symlink_to(root / "does-not-exist.toml")
            runner = FakeRunner([])

            with self.assertRaisesRegex(InstallError, "devflow-explorer.toml|devflow-spec.toml"):
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
                    "codex-dev-flow",
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
                        str(repo.resolve()),
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
                        "codex-dev-flow",
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

            with mock.patch("scripts.install.os.replace", side_effect=OSError("receipt disk full")):
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
                        "codex-dev-flow",
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
            target = codex_home / "agents" / "devflow-review.toml"
            original_unlink = Path.unlink

            def fail_target(path: Path, *args: object, **kwargs: object) -> None:
                if path == target:
                    raise OSError("owned link busy")
                original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", fail_target):
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
                repo
                / "plugins"
                / "codex-dev-flow"
                / "assets"
                / "agents"
                / "devflow-review.toml"
            )
            retargeted = codex_home / "agents" / "devflow-review.toml"
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
                    ("codex", "plugin", "marketplace", "remove", "codex-dev-flow", "--json"),
                ],
            )
            self.assertEqual(
                tuple(path.name for path in (codex_home / "agents").iterdir()),
                ("devflow-review.toml",),
            )
            self.assertFalse(receipt_path(state_home).exists())

    def test_intermediate_assets_symlink_escape_refuses_before_runner_or_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            escaped = root / "escaped-assets"
            shutil.copytree(repo / "plugins" / "codex-dev-flow" / "assets", escaped)
            assets = repo / "plugins" / "codex-dev-flow" / "assets"
            shutil.rmtree(assets)
            assets.symlink_to(escaped, target_is_directory=True)
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
            reviewer = repo / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-review.toml"
            reviewer.write_text(
                reviewer.read_text(encoding="utf-8").replace(
                    'model = "gpt-5.6-terra"', 'model = "gpt-5.6-luna"'
                ).replace(
                    'model_reasoning_effort = "medium"', 'model_reasoning_effort = "max"'
                ).replace(
                    'sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"'
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
                repo / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-explorer.toml"
            )
            state_home = root / "state"
            receipt_directory = state_home / "codex-dev-flow"
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
                                    / "codex-dev-flow"
                                    / "assets"
                                    / "agents"
                                    / "devflow-explorer.toml"
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
            destinations["devflow-explorer"].unlink()
            retargeted = destinations["devflow-implementer"]
            retargeted.unlink()
            target = root / "unrelated.toml"
            target.write_text("preserved\n", encoding="utf-8")
            retargeted.symlink_to(target)
            replaced = destinations["devflow-review"]
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
                        "codex-dev-flow",
                        "--json",
                    ),
                ],
            )
            self.assertEqual(
                {path.destination.name for path in result.removed_links},
                {
                    f"{name}.toml"
                    for name in PROFILE_NAMES
                    if name not in {"devflow-explorer", "devflow-implementer", "devflow-review"}
                },
            )
            self.assertTrue(retargeted.is_symlink())
            self.assertTrue(replaced.is_file())
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_preserves_retargeted_links_and_preexisting_marketplace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo, marketplace_present=True)))
            retargeted = destination_paths(codex_home)["devflow-review"]
            retargeted.unlink()
            unrelated = root / "unrelated.toml"
            unrelated.write_text("preserved\n", encoding="utf-8")
            retargeted.symlink_to(unrelated)
            runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    removal_response(),
                ]
            )

            uninstall(repo, codex_home, state_home, runner)

            self.assertEqual(
                runner.calls,
                [
                    ("codex", "plugin", "list", "--json"),
                    ("codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"),
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
            target = destination_paths(codex_home)["devflow-review"]
            original_unlink = Path.unlink
            failed = {"value": True}

            def fail_once(path: Path, *args: object, **kwargs: object) -> None:
                if path == target and failed["value"]:
                    failed["value"] = False
                    raise OSError("link busy")
                original_unlink(path, *args, **kwargs)

            runner = FakeRunner(
                [
                    plugin_list_response(),
                    marketplace_list_response(),
                    removal_response(),
                ]
            )

            with mock.patch.object(Path, "unlink", fail_once):
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
            retry_runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertFalse(receipt_path(state_home).exists())
            self.assertFalse(target.exists())
            self.assertEqual(retry_runner.calls, [])

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
                    ("codex", "plugin", "marketplace", "remove", "codex-dev-flow", "--json"),
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
                repo
                / "plugins"
                / "codex-dev-flow"
                / "assets"
                / "agents"
                / "devflow-review.toml"
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
                repo
                / "plugins"
                / "codex-dev-flow"
                / "assets"
                / "agents"
                / "devflow-review.toml"
            )
            damaged_source.unlink()
            damaged_source.symlink_to(outside_file)
            untouched_destination = destination_paths(codex_home)["devflow-implementer"]
            untouched_source = (
                repo
                / "plugins"
                / "codex-dev-flow"
                / "assets"
                / "agents"
                / "devflow-implementer.toml"
            )
            untouched_source.unlink()
            untouched_source.symlink_to(outside_file)
            retargeted = destination_paths(codex_home)["devflow-review"]
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
            self.assertTrue(damaged_source.is_symlink())
            self.assertEqual(damaged_source.read_text(encoding="utf-8"), "user-owned\n")
            self.assertTrue(untouched_source.is_symlink())
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
            receipt_directory = state_home / "codex-dev-flow"
            temporary_paths: list[Path] = []
            original_unlink = Path.unlink

            def fail_temporary(path: Path, *args: object, **kwargs: object) -> None:
                if path.parent == receipt_directory and path.name.startswith(".install.json."):
                    temporary_paths.append(path)
                    raise OSError("temporary receipt busy")
                original_unlink(path, *args, **kwargs)

            with mock.patch("scripts.install.os.replace", side_effect=OSError("receipt disk full")):
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
                    ("codex", "plugin", "marketplace", "remove", "codex-dev-flow", "--json"),
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
            retry_runner = FakeRunner([marketplace_list_response(repo), removal_response()])
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertEqual(
                retry_runner.calls,
                [
                    ("codex", "plugin", "marketplace", "list", "--json"),
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

    def test_uninstall_persists_link_progress_after_external_success_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            codex_home = root / "codex"
            state_home = root / "state"
            install(repo, codex_home, state_home, FakeRunner(install_results(repo)))
            target = destination_paths(codex_home)["devflow-review"]
            original_unlink = Path.unlink
            failed = {"value": True}

            def fail_once(path: Path, *args: object, **kwargs: object) -> None:
                if path == target and failed["value"]:
                    failed["value"] = False
                    raise OSError("link busy")
                original_unlink(path, *args, **kwargs)

            first_runner = FakeRunner(
                [
                    plugin_list_response(repo),
                    marketplace_list_response(repo),
                    removal_response(),
                    removal_response(),
                ]
            )
            with mock.patch.object(Path, "unlink", fail_once):
                with self.assertRaisesRegex(InstallError, "link busy"):
                    uninstall(repo, codex_home, state_home, first_runner)

            receipt = load_receipt(state_home)
            self.assertFalse(receipt["plugin_installed"])
            self.assertFalse(receipt["marketplace_added"])
            retry_runner = FakeRunner([])
            uninstall(repo, codex_home, state_home, retry_runner)
            self.assertEqual(retry_runner.calls, [])
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


if __name__ == "__main__":
    unittest.main()
