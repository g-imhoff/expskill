from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "codex-dev-flow"
EXPECTED_AGENTS = {
    "devflow-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "devflow-test-engineer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-reviewer": ("gpt-5.6-sol", "xhigh", "read-only"),
    "devflow-verifier": ("gpt-5.6-luna", "max", "workspace-write"),
}


class ContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "plugins", temporary / "plugins")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        return temporary

    def load_manifest(self, root: Path) -> dict[str, object]:
        return json.loads(
            (root / "plugins" / "codex-dev-flow" / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )

    def load_marketplace(self, root: Path) -> dict[str, object]:
        return json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))

    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual(validate_repository(ROOT), ())

    def test_plugin_and_marketplace_identities_are_exact(self) -> None:
        manifest = self.load_manifest(ROOT)
        marketplace = self.load_marketplace(ROOT)
        self.assertEqual(manifest["name"], "codex-dev-flow")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["repository"], "https://github.com/g-imhoff/codex-dev-flow")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["interface"]["category"], "Developer Tools")
        self.assertEqual(marketplace["name"], "codex-dev-flow")
        self.assertEqual(marketplace["plugins"][0]["name"], "codex-dev-flow")
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/codex-dev-flow")
        self.assertEqual(marketplace["plugins"][0]["category"], "Developer Tools")

    def test_agent_profiles_match_exact_roster_and_required_fields(self) -> None:
        agents_root = PLUGIN_ROOT / "assets" / "agents"
        observed: dict[str, tuple[str, str, str]] = {}
        for path in sorted(agents_root.glob("devflow-*.toml")):
            profile = tomllib.loads(path.read_text(encoding="utf-8"))
            for field in (
                "name",
                "description",
                "model",
                "model_reasoning_effort",
                "sandbox_mode",
                "developer_instructions",
            ):
                self.assertIn(field, profile, path.name)
                self.assertIsInstance(profile[field], str, path.name)
                self.assertTrue(profile[field].strip(), path.name)
            observed[profile["name"]] = (
                profile["model"],
                profile["model_reasoning_effort"],
                profile["sandbox_mode"],
            )
        self.assertEqual(observed, EXPECTED_AGENTS)

    def test_agent_instructions_state_the_required_boundaries(self) -> None:
        required_phrases = {
            "devflow-explorer": ("read-only", "no fixes", "no delegation"),
            "devflow-test-engineer": (
                "test strategy",
                "shared acceptance tests",
                "regression",
                "no product implementation",
            ),
            "devflow-implementer": (
                "exactly one brief",
                "red-green-refactor",
                "one owned branch",
                "no delegation",
                "no scope expansion",
            ),
            "devflow-reviewer": (
                "read-only",
                "severity",
                "evidence",
                "impact",
                "correction",
                "ready",
                "not ready",
            ),
            "devflow-verifier": (
                "exact commands",
                "exit evidence",
                "no tracked-source edits",
                "no reliance on another agent's claims",
            ),
        }
        for name, phrases in required_phrases.items():
            profile = tomllib.loads(
                (PLUGIN_ROOT / "assets" / "agents" / f"{name}.toml").read_text(encoding="utf-8")
            )
            instructions = profile["developer_instructions"].lower()
            for phrase in phrases:
                self.assertIn(phrase, instructions, name)

    def test_manifest_has_no_unsupported_runtime_dependencies(self) -> None:
        manifest = self.load_manifest(ROOT)
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("icons", manifest)
        self.assertNotIn("authentication", manifest)

    def test_missing_profile_is_rejected(self) -> None:
        root = self.copy_repository()
        (root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-reviewer.toml").unlink()
        errors = validate_repository(root)
        self.assertTrue(any("devflow-reviewer" in error and "missing" in error for error in errors))

    def test_unexpected_profile_is_rejected(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-surprise.toml"
        path.write_text(
            '\n'.join(
                (
                    'name = "devflow-surprise"',
                    'description = "Unexpected profile"',
                    'model = "gpt-5.6-luna"',
                    'model_reasoning_effort = "max"',
                    'sandbox_mode = "read-only"',
                    'developer_instructions = "Read-only evidence gathering."',
                    "",
                )
            ),
            encoding="utf-8",
        )
        errors = validate_repository(root)
        self.assertTrue(any("devflow-surprise" in error and "unexpected" in error for error in errors))

    def test_duplicate_skill_name_is_rejected(self) -> None:
        root = self.copy_repository()
        skills_root = root / "plugins" / "codex-dev-flow" / "skills"
        for directory in (skills_root / "first", skills_root / "second"):
            directory.mkdir()
            (directory / "SKILL.md").write_text(
                "---\nname: duplicate-skill\ndescription: A test skill\n---\n\nBody.\n",
                encoding="utf-8",
            )
        errors = validate_repository(root)
        self.assertTrue(any("duplicate-skill" in error and "duplicate" in error for error in errors))

    def test_terra_model_is_rejected(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-explorer.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace('model = "gpt-5.6-luna"', 'model = "terra"'),
            encoding="utf-8",
        )
        errors = validate_repository(root)
        self.assertTrue(any("terra" in error.lower() and "model" in error.lower() for error in errors))

    def test_reviewer_must_be_read_only(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-reviewer.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                'sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"'
            ),
            encoding="utf-8",
        )
        errors = validate_repository(root)
        self.assertTrue(any("reviewer" in error and "read-only" in error for error in errors))

    def test_invalid_manifest_path_is_rejected(self) -> None:
        root = self.copy_repository()
        manifest_path = root / "plugins" / "codex-dev-flow" / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["skills"] = "./missing-skills/"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("skills" in error and "path" in error for error in errors))

    def test_placeholder_text_is_rejected(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-verifier.toml"
        path.write_text(path.read_text(encoding="utf-8") + "\n# [TODO: remove this]\n", encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("placeholder" in error.lower() or "todo" in error.lower() for error in errors))

    def test_validation_aggregates_independent_errors(self) -> None:
        root = self.copy_repository()
        reviewer = root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-reviewer.toml"
        reviewer.write_text(
            reviewer.read_text(encoding="utf-8").replace(
                'sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"'
            ),
            encoding="utf-8",
        )
        (root / "plugins" / "codex-dev-flow" / "assets" / "agents" / "devflow-explorer.toml").unlink()
        errors = validate_repository(root)
        self.assertGreaterEqual(len(errors), 2)
        self.assertTrue(any("reviewer" in error for error in errors))
        self.assertTrue(any("explorer" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
