from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import install
from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-dev-flow"
HELPER = PLUGIN / "scripts" / "design_state.py"


class _NeverCalledRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> object:
        self.calls.append(command)
        raise AssertionError(f"installer reached external command before preflight failed: {command}")


class DesignPackageAdversarialTests(unittest.TestCase):
    def _copy_repository(self) -> Path:
        temporary = Path(tempfile.mkdtemp(prefix="design-package-adversarial-"))
        self.addCleanup(shutil.rmtree, temporary, ignore_errors=True)
        for name in (".agents", "plugins", "scripts"):
            shutil.copytree(ROOT / name, temporary / name)
        return temporary

    def test_readme_and_manifest_expose_six_skills_and_design(self) -> None:
        """Regression: stale phase language hides Design and contradicts the public roster."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        description = str(manifest.get("description", ""))
        skill_wording = re.compile(r"\bsix independent skills and one optional lifecycle router\b", re.I)
        self.assertRegex(" ".join(readme.split()), skill_wording)
        self.assertIn("$design", readme)
        self.assertRegex(description, skill_wording)
        self.assertNotRegex(readme, re.compile(r"\b(?:six|seven) independent development phases\b", re.I))
        self.assertNotRegex(description, re.compile(r"\b(?:six|seven) standalone development phases\b", re.I))

    def test_validator_accepts_the_complete_design_package(self) -> None:
        """Regression: package validation must have an independent Design helper boundary."""
        errors = validate_repository(ROOT)
        self.assertEqual(errors, (), "validator must accept the complete Design package: " + "; ".join(errors))

    def test_validator_rejects_each_design_state_helper_mutation(self) -> None:
        """Regression: missing, duplicate, empty, nonregular, and symlink helpers must fail closed."""
        self.assertTrue(HELPER.is_file(), HELPER)
        mutations = ("missing", "duplicate", "empty", "directory", "symlink")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                root = self._copy_repository()
                helper = root / "plugins" / "codex-dev-flow" / "scripts" / "design_state.py"
                if mutation == "missing":
                    helper.unlink()
                elif mutation == "duplicate":
                    shadow = root / "plugins" / "codex-dev-flow" / "assets" / "design_state.py"
                    shadow.write_bytes(helper.read_bytes())
                elif mutation == "empty":
                    helper.write_bytes(b"")
                elif mutation == "directory":
                    helper.unlink()
                    helper.mkdir()
                else:
                    outside = root / "outside-design-state.py"
                    outside.write_text("outside\n", encoding="utf-8")
                    helper.unlink()
                    helper.symlink_to(outside)
                errors = validate_repository(root)
                self.assertTrue(
                    any("design state helper" in error.lower() for error in errors),
                    f"mutation {mutation} was accepted or failed for an unrelated reason: {errors}",
                )

    def test_installer_does_not_mutate_on_design_helper_preflight_failure(self) -> None:
        """Regression: invalid Design package state must fail before links, receipts, or external commands change."""
        root = self._copy_repository()
        helper = root / "plugins" / "codex-dev-flow" / "scripts" / "design_state.py"
        helper.unlink()
        codex_home = root / "codex-home"
        state_home = root / "state-home"
        runner = _NeverCalledRunner()
        with self.assertRaises(install.InstallError):
            install.install(root, codex_home, state_home, runner)
        self.assertEqual(runner.calls, [])
        self.assertFalse(codex_home.exists())
        self.assertFalse(state_home.exists())


if __name__ == "__main__":
    unittest.main()
