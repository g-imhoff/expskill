from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.install import InstallError, install, preflight_links, uninstall
from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILLS_ROOT = PLUGIN / "skills"
HERMES_AGENTS_ROOT = PLUGIN / "assets" / "agents-hermes"
CODEX_AGENTS_ROOT = PLUGIN / "assets" / "agents"
PROFILE_NAMES = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)


class FakeResult:
    def __init__(self, returncode: int = 0, payload: object = None) -> None:
        self.returncode = returncode
        self.stdout = json.dumps(payload)
        self.stderr = ""


class FakeRunner:
    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str]) -> FakeResult:
        self.calls.append(tuple(command))
        if not self.results:
            raise AssertionError(f"unexpected command: {command!r}")
        return self.results.pop(0)


def copy_repository(destination: Path) -> Path:
    shutil.copytree(ROOT / ".agents", destination / ".agents")
    shutil.copytree(PLUGIN, destination / "plugins" / "expskill")
    shutil.copytree(ROOT / "scripts", destination / "scripts")
    shutil.copy2(ROOT / "README.md", destination / "README.md")
    return destination


class HermesPackagingTests(unittest.TestCase):
    def test_every_skill_ships_matching_hermes_metadata(self) -> None:
        skills = sorted(path for path in SKILLS_ROOT.iterdir() if (path / "SKILL.md").is_file())
        self.assertGreater(len(skills), 0)
        for skill in skills:
            with self.subTest(skill=skill.name):
                openai = skill / "agents" / "openai.yaml"
                hermes = skill / "agents" / "hermes.yaml"
                self.assertTrue(openai.is_file(), openai)
                self.assertTrue(hermes.is_file(), hermes)
                self.assertEqual(
                    hermes.read_text(encoding="utf-8"),
                    openai.read_text(encoding="utf-8"),
                )

    def test_hermes_role_profiles_cover_every_codex_profile(self) -> None:
        observed = {path.name for path in HERMES_AGENTS_ROOT.glob("expskill-*.md")}
        self.assertEqual(observed, {f"{name}.md" for name in PROFILE_NAMES})
        for name in PROFILE_NAMES:
            with self.subTest(profile=name):
                contents = (HERMES_AGENTS_ROOT / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn(f"name: {name}", contents.split("---")[1])
                self.assertIn("model_policy: active-hermes-provider", contents)
                self.assertNotIn("gpt-5.6", contents)

    def test_hermes_role_instructions_match_codex_profiles(self) -> None:
        for name in PROFILE_NAMES:
            with self.subTest(profile=name):
                body = (HERMES_AGENTS_ROOT / f"{name}.md").read_text(encoding="utf-8")
                instructions = (CODEX_AGENTS_ROOT / f"{name}.toml").read_text(encoding="utf-8")
                marker = 'developer_instructions = """\n'
                abouts = instructions.split(marker, 1)[1].rsplit('"""', 1)[0]
                self.assertIn(abouts.strip(), body)

    def test_hermes_metadata_drift_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            metadata = root / "plugins" / "expskill" / "skills" / "plan" / "agents" / "hermes.yaml"
            metadata.write_text(
                metadata.read_text(encoding="utf-8").replace("Plan", "Plot", 1),
                encoding="utf-8",
            )
            errors = validate_repository(root)
            self.assertTrue(
                any("plan" in error and "Hermes" in error for error in errors),
                errors,
            )

    def test_missing_hermes_metadata_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            metadata = root / "plugins" / "expskill" / "skills" / "plan" / "agents" / "hermes.yaml"
            metadata.unlink()
            errors = validate_repository(root)
            self.assertTrue(
                any("plan" in error and "Hermes" in error for error in errors),
                errors,
            )

    def test_hermes_instruction_drift_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            profile = root / "plugins" / "expskill" / "assets" / "agents-hermes" / "expskill-planner.md"
            profile.write_text(
                profile.read_text(encoding="utf-8").replace("only Plan Graph writer", "occasional graph editor"),
                encoding="utf-8",
            )
            errors = validate_repository(root)
            self.assertTrue(
                any("expskill-planner" in error and "instructions" in error for error in errors),
                errors,
            )

    def test_router_and_readme_document_hermes_lifecycle(self) -> None:
        router = (SKILLS_ROOT / "use-expskill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("hermes chat -s plan", router)
        self.assertIn("hermes chat -s design", router)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("--provider hermes", readme)
        self.assertIn("HERMES_HOME", readme)


class HermesInstallerTests(unittest.TestCase):
    def test_hermes_install_links_skills_and_roles_without_codex_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            codex_home = Path(temporary) / "codex"
            hermes_home = Path(temporary) / "hermes"
            state_home = Path(temporary) / "state"
            runner = FakeRunner()
            result = install(root, codex_home, state_home, runner, "hermes", hermes_home)
            self.assertEqual(runner.calls, [])
            self.assertFalse(result.marketplace_added)
            self.assertFalse(result.plugin_installed)
            for name in PROFILE_NAMES:
                link = hermes_home / "agents" / f"{name}.md"
                self.assertTrue(link.is_symlink(), link)
            skill_names = sorted(
                path.name for path in (root / "plugins" / "expskill" / "skills").iterdir()
            )
            self.assertGreater(len(skill_names), 0)
            for name in skill_names:
                link = hermes_home / "skills" / "expskill" / name
                self.assertTrue(link.is_symlink(), link)
                self.assertTrue((link / "SKILL.md").is_file(), link)
            self.assertFalse(codex_home.exists())
            uninstall(root, codex_home, state_home, runner, "hermes", hermes_home)
            self.assertEqual(runner.calls, [])
            self.assertEqual(tuple((hermes_home / "agents").iterdir()), ())
            self.assertEqual(tuple((hermes_home / "skills" / "expskill").iterdir()), ())

    def test_both_provider_installs_codex_and_hermes_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            codex_home = Path(temporary) / "codex"
            hermes_home = Path(temporary) / "hermes"
            state_home = Path(temporary) / "state"
            listed = {"marketplaces": []}
            runner = FakeRunner(
                [
                    FakeResult(0, listed),
                    FakeResult(0, {"marketplaceName": "expskill", "installedRoot": str(root.resolve()), "alreadyAdded": False}),
                    FakeResult(0, {"installed": []}),
                    FakeResult(
                        0,
                        {
                            "pluginId": "expskill@expskill",
                            "name": "expskill",
                            "marketplaceName": "expskill",
                            "version": json.loads(
                                (root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json").read_text(
                                    encoding="utf-8"
                                )
                            )["version"],
                            "installedPath": str(root.resolve()),
                        },
                    ),
                ]
            )
            result = install(root, codex_home, state_home, runner, "both", hermes_home)
            self.assertTrue(result.marketplace_added)
            self.assertTrue(result.plugin_installed)
            self.assertTrue((codex_home / "agents" / "expskill-planner.toml").is_symlink())
            self.assertTrue((hermes_home / "agents" / "expskill-planner.md").is_symlink())
            self.assertTrue((hermes_home / "skills" / "expskill" / "plan").is_symlink())

    def test_preflight_rejects_unknown_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            with self.assertRaises(InstallError):
                preflight_links(root, Path(temporary) / "codex", " guess ", Path(temporary) / "hermes")

    def test_hermes_requires_a_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = copy_repository(Path(temporary) / "repository")
            with self.assertRaises(InstallError):
                preflight_links(root, Path(temporary) / "codex", "hermes", None)


if __name__ == "__main__":
    unittest.main()
