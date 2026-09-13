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

from scripts.install import (
    InstallError,
    _default_opencode_config_dir,
    install_opencode,
    preflight_opencode_links,
    uninstall_opencode,
)
from scripts.render_opencode import skill_inventory


ROOT = Path(__file__).resolve().parents[1]
SKILLS = skill_inventory(ROOT)
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
EXPECTED_LINK_COUNT = len(SKILLS) + len(SKILLS) + len(AGENTS) + len(PLUGINS)


def seed_repository(path: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(ROOT / ".agents", path / ".agents", ignore=ignore)
    shutil.copytree(ROOT / "plugins", path / "plugins", ignore=ignore)
    shutil.copytree(ROOT / "scripts", path / "scripts", ignore=ignore)
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def receipt_path(state_home: Path) -> Path:
    return state_home.resolve() / "expskill" / "install-opencode.json"


def load_receipt(state_home: Path) -> dict:
    return json.loads(receipt_path(state_home).read_text(encoding="utf-8"))


class OpencodeInstallerTests(unittest.TestCase):
    def test_install_creates_every_expected_link(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            result = install_opencode(repo, config_dir, state_home)
            self.assertEqual(len(result.links), EXPECTED_LINK_COUNT)
            self.assertEqual(len(result.created_links), EXPECTED_LINK_COUNT)
            for name in SKILLS:
                skill = config_dir / "skills" / name
                self.assertTrue(skill.is_symlink())
                self.assertEqual(
                    (skill / "SKILL.md").read_bytes(),
                    (repo / "plugins" / "expskill" / "skills" / name / "SKILL.md").read_bytes(),
                )
                self.assertTrue((config_dir / "commands" / f"{name}.md").is_symlink())
            for name in AGENTS:
                self.assertTrue((config_dir / "agents" / f"{name}.md").is_symlink())
            for name in PLUGINS:
                self.assertTrue((config_dir / "plugins" / name).is_symlink())
            receipt = load_receipt(state_home)
            self.assertEqual(len(receipt["links"]), EXPECTED_LINK_COUNT)
            self.assertEqual(receipt["repository_root"], str(repo.resolve()))
            self.assertTrue(
                all(
                    Path(entry["source"]).is_relative_to(state_home.resolve() / "expskill")
                    for entry in receipt["links"]
                )
            )
            self.assertFalse((repo / "plugins" / "expskill" / "opencode" / "agents").exists())
            self.assertFalse((repo / "plugins" / "expskill" / "opencode" / "commands").exists())

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            install_opencode(repo, config_dir, state_home)
            result = install_opencode(repo, config_dir, state_home)
            self.assertEqual(result.created_links, ())
            self.assertEqual(len(result.links), EXPECTED_LINK_COUNT)

    def test_reinstall_rebuilds_receipt_owned_artifact_after_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            install_opencode(repo, config_dir, state_home)
            skill = repo / "plugins" / "expskill" / "skills" / "unslop" / "SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace("Cut AI tells", "Reinstalled marker"),
                encoding="utf-8",
            )
            profile = repo / "plugins" / "expskill" / "assets" / "agents" / "expskill-review.toml"
            profile.write_text(
                profile.read_text(encoding="utf-8").replace(
                    "Independently review one immutable implementation candidate.",
                    "Reinstalled profile marker.",
                ),
                encoding="utf-8",
            )
            install_opencode(repo, config_dir, state_home)
            command = config_dir / "commands" / "unslop.md"
            self.assertIn("Reinstalled marker", command.read_text(encoding="utf-8"))
            agent = config_dir / "agents" / "expskill-review.md"
            self.assertIn("Reinstalled profile marker.", agent.read_text(encoding="utf-8"))
            receipt = load_receipt(state_home)
            self.assertEqual(Path(receipt["artifact_root"]), state_home / "expskill" / "opencode-artifact")

    def test_install_refuses_foreign_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            commands = config_dir / "commands"
            commands.mkdir(parents=True)
            (commands / "plan.md").write_text("user-owned\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "refusing conflicting opencode destination"):
                install_opencode(repo, config_dir, state_home)
            self.assertFalse(receipt_path(state_home).exists())

    def test_uninstall_removes_only_owned_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            install_opencode(repo, config_dir, state_home)
            foreign = config_dir / "commands" / "user-command.md"
            foreign.write_text("user-owned\n", encoding="utf-8")
            result = uninstall_opencode(repo, config_dir, state_home)
            self.assertEqual(len(result.removed_links), EXPECTED_LINK_COUNT)
            self.assertFalse(receipt_path(state_home).exists())
            self.assertTrue(foreign.is_file())
            remaining = [path for path in config_dir.rglob("*") if path.is_symlink()]
            self.assertEqual(remaining, [])

    def test_uninstall_without_receipt_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            result = uninstall_opencode(repo, config_dir, state_home)
            self.assertEqual(result.removed_links, ())

    def test_uninstall_preserves_user_retarget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            state_home = root / "state"
            install_opencode(repo, config_dir, state_home)
            retargeted = config_dir / "agents" / "expskill-review.md"
            retargeted.unlink()
            retargeted.write_text("user-owned\n", encoding="utf-8")
            result = uninstall_opencode(repo, config_dir, state_home)
            self.assertTrue(retargeted.is_file())
            self.assertEqual(retargeted.read_text(encoding="utf-8"), "user-owned\n")
            self.assertEqual(len(result.removed_links), EXPECTED_LINK_COUNT - 1)

    def test_preflight_reports_planned_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            links = preflight_opencode_links(repo, config_dir)
            self.assertEqual(len(links), EXPECTED_LINK_COUNT)

    def test_explicit_config_override_precedes_xdg_and_expands_user(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            xdg = root / "xdg"
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "OPENCODE_CONFIG_DIR": "~/custom",
                    "XDG_CONFIG_HOME": str(xdg),
                },
                clear=True,
            ), mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(_default_opencode_config_dir(), home / "custom")

    def test_absolute_xdg_config_home_is_used_when_explicit_override_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            xdg = root / "xdg"
            with mock.patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": str(xdg)},
                clear=True,
            ), mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(_default_opencode_config_dir(), xdg / "opencode")
            self.assertFalse(home.exists())

    def test_unset_empty_and_relative_xdg_config_home_use_home_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            for value in (None, "", "relative"):
                with self.subTest(xdg_config_home=value):
                    environment = {} if value is None else {"XDG_CONFIG_HOME": value}
                    with mock.patch.dict(
                        os.environ,
                        environment,
                        clear=True,
                    ), mock.patch.object(Path, "home", return_value=home):
                        self.assertEqual(
                            _default_opencode_config_dir(),
                            home / ".config" / "opencode",
                        )
                    self.assertFalse(home.exists())

    def test_empty_explicit_config_override_is_treated_as_unset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            xdg = root / "xdg"
            with mock.patch.dict(
                os.environ,
                {
                    "OPENCODE_CONFIG_DIR": "",
                    "XDG_CONFIG_HOME": str(xdg),
                },
                clear=True,
            ), mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(_default_opencode_config_dir(), xdg / "opencode")
            self.assertFalse(home.exists())

    def test_resolved_xdg_install_uninstall_preserves_owned_and_foreign_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            home = root / "home"
            xdg = root / "xdg"
            old_config = home / ".config" / "opencode"
            old_config.mkdir(parents=True)
            legacy_file = old_config / "legacy-user-file"
            legacy_file.write_text("legacy\n", encoding="utf-8")
            state_home = root / "state"
            with mock.patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": str(xdg)},
                clear=True,
            ), mock.patch.object(Path, "home", return_value=home):
                config_dir = _default_opencode_config_dir()
                self.assertEqual(config_dir, xdg / "opencode")
                result = install_opencode(repo, config_dir, state_home)
                self.assertEqual(len(result.created_links), EXPECTED_LINK_COUNT)

                foreign = config_dir / "commands" / "user-command.md"
                foreign.write_text("user-owned\n", encoding="utf-8")
                retargeted = config_dir / "agents" / "expskill-review.md"
                retargeted.unlink()
                retargeted.write_text("user-owned\n", encoding="utf-8")

                uninstall_result = uninstall_opencode(repo, config_dir, state_home)

            self.assertEqual(len(uninstall_result.removed_links), EXPECTED_LINK_COUNT - 1)
            self.assertFalse(receipt_path(state_home).exists())
            self.assertTrue(foreign.is_file())
            self.assertEqual(retargeted.read_text(encoding="utf-8"), "user-owned\n")
            self.assertEqual(legacy_file.read_text(encoding="utf-8"), "legacy\n")

    def test_dry_run_lists_links_and_receipt(self) -> None:
        from scripts.install import _print_opencode_dry_run

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config_dir = root / "config"
            output = StringIO()
            with redirect_stdout(output):
                _print_opencode_dry_run(repo, config_dir)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), EXPECTED_LINK_COUNT + 1)
            self.assertTrue(lines[-1].startswith("opencode opencode-expskill receipt"))


if __name__ == "__main__":
    unittest.main()
