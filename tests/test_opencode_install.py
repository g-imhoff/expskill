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
    install_opencode,
    preflight_opencode_links,
    uninstall_opencode,
)


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "brainstorm",
    "design",
    "grill-me",
    "implement",
    "plan",
    "setup-ui-testing",
    "skill-builder",
    "test",
    "unslop",
    "use-expskill",
)
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
    shutil.copytree(ROOT / ".agents", path / ".agents")
    shutil.copytree(ROOT / "packages", path / "packages")
    shutil.copytree(ROOT / "scripts", path / "scripts")
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
                    (repo / "packages" / "codex" / "skills" / name / "SKILL.md").read_bytes(),
                )
                self.assertTrue((config_dir / "commands" / f"{name}.md").is_symlink())
            for name in AGENTS:
                self.assertTrue((config_dir / "agents" / f"{name}.md").is_symlink())
            for name in PLUGINS:
                self.assertTrue((config_dir / "plugins" / name).is_symlink())
            receipt = load_receipt(state_home)
            self.assertEqual(len(receipt["links"]), EXPECTED_LINK_COUNT)
            self.assertEqual(receipt["repository_root"], str(repo.resolve()))

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

    def test_config_dir_env_override_is_respected(self) -> None:
        from scripts.install import _default_opencode_config_dir

        with tempfile.TemporaryDirectory() as temporary:
            custom = Path(temporary) / "custom"
            with mock.patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": str(custom)}, clear=False):
                self.assertEqual(_default_opencode_config_dir(), custom)

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
