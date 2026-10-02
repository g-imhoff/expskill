from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.build_hermes_package import BuildError, build_hermes_package
from scripts.render_hermes import (
    MODEL_POLICY as HERMES_MODEL_POLICY,
    render_agents,
    skill_inventory,
)
from scripts.validate import _parse_frontmatter, _parse_overlay_frontmatter, validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
FACETS = {
    "expskill-designer": ("designer", "workspace-write"),
    "expskill-explorer": ("explorer", "read-only"),
    "expskill-implementer": ("implementer", "workspace-write"),
    "expskill-planner": ("planner", "workspace-write"),
    "expskill-review": ("review", "read-only"),
    "expskill-spec": ("spec", "read-only"),
    "expskill-test-engineer": ("test-engineer", "read-only"),
}


class HermesContractTests(unittest.TestCase):
    def copy_repository(self, *, include_git: bool = True) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        ignored = ["__pycache__", "*.pyc", "*.pyo"]
        if not include_git:
            ignored.append(".git")
        shutil.copytree(
            ROOT,
            temporary / "repo",
            ignore=shutil.ignore_patterns(*ignored),
        )
        return temporary / "repo"

    def build_artifact(self, root: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        artifact = Path(temporary.name) / "artifact"
        build_hermes_package(root, artifact)
        self.addCleanup(temporary.cleanup)
        return temporary, artifact

    def test_no_hermes_contract_error_on_valid_repository(self) -> None:
        errors = validate_repository(ROOT)
        self.assertFalse([error for error in errors if "hermes" in error], errors)

    def test_default_validation_rejects_missing_whole_source_surface(self) -> None:
        for surface, expected_error in (
            ("plugins/expskill/hermes", "hermes package directory is missing"),
        ):
            with self.subTest(surface=surface):
                root = self.copy_repository()
                shutil.rmtree(root / surface)
                errors = validate_repository(root)
                self.assertTrue(
                    any(expected_error in error for error in errors),
                    errors,
                )

    def test_artifact_contains_regular_dynamic_shared_skill_inventory(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        names = skill_inventory(ROOT)
        self.assertEqual(sorted(path.name for path in (artifact / "skills").iterdir()), list(names))
        for name in names:
            source = PLUGIN_ROOT / "content" / "skills" / name / "SKILL.md"
            exposed = artifact / "skills" / name / "SKILL.md"
            self.assertFalse(exposed.is_symlink())
            self.assertEqual(exposed.read_bytes(), source.read_bytes())

    def test_every_shared_skill_preserves_canonical_frontmatter(self) -> None:
        for name in skill_inventory(ROOT):
            with self.subTest(skill=name):
                contents = (PLUGIN_ROOT / "content" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                errors: list[str] = []
                frontmatter = _parse_frontmatter(contents, name, errors)
                self.assertEqual(errors, [])
                assert frontmatter is not None
                self.assertEqual(frontmatter.get("name"), name)

    def test_every_agent_matches_pure_renderer_and_canonical_profile(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        rendered = render_agents(ROOT)
        self.assertEqual(set(rendered), set(AGENTS))
        content = json.loads((PLUGIN_ROOT / "content" / "agents.json").read_text(encoding="utf-8"))
        for name in AGENTS:
            with self.subTest(agent=name):
                contents = (artifact / "agents" / f"{name}.md").read_text(encoding="utf-8")
                self.assertEqual(contents, rendered[name])
                errors: list[str] = []
                parsed = _parse_overlay_frontmatter(contents, f"agent {name}", errors)
                self.assertEqual(errors, [])
                assert parsed is not None
                scalars, _mappings, _block = parsed
                role, sandbox = FACETS[name]
                self.assertEqual(scalars.get("name"), name)
                self.assertEqual(scalars.get("role"), role)
                self.assertEqual(scalars.get("sandbox"), sandbox)
                self.assertEqual(scalars.get("model_policy"), HERMES_MODEL_POLICY)
                self.assertIn(content["agents"][name]["description"], contents)
                self.assertIn(content["agents"][name]["closing"], contents)
                self.assertIn("You run as a Hermes agent", contents)

    def test_manifest_matches_agent_plugins_v1_and_base_version(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / "hermes" / "plugin.json").read_text(encoding="utf-8"))
        codex_manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["$schema"],
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        )
        self.assertEqual(manifest["name"], "expskill")
        self.assertEqual(manifest["version"], str(codex_manifest["version"]).split("+")[0])
        self.assertEqual(manifest["license"], "MIT")

    def test_execution_policy_is_copied_to_artifact(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        canonical = (PLUGIN_ROOT / "content" / "policies" / "execution-policy.json").read_bytes()
        self.assertEqual((artifact / "assets" / "execution-policy.json").read_bytes(), canonical)

    def test_validation_does_not_require_checked_in_generated_mirrors(self) -> None:
        self.assertEqual(validate_repository(ROOT), ())
        self.assertFalse((PLUGIN_ROOT / "hermes" / "agents").exists())
        self.assertFalse((PLUGIN_ROOT / "hermes" / "skills").exists())

    def test_validation_reports_malformed_canonical_closing(self) -> None:
        root = self.copy_repository()
        overlay = root / "plugins" / "expskill" / "content" / "agents.json"
        spec = json.loads(overlay.read_text(encoding="utf-8"))
        del spec["agents"]["expskill-review"]["closing"]
        overlay.write_text(json.dumps(spec), encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("closing" in error for error in errors), errors)

    def test_validation_rejects_wrong_agent_facet(self) -> None:
        root = self.copy_repository()
        overlay = root / "plugins" / "expskill" / "hermes" / "agents.json"
        spec = json.loads(overlay.read_text(encoding="utf-8"))
        spec["agents"]["expskill-review"]["sandbox"] = "workspace-write"
        overlay.write_text(json.dumps(spec), encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("hermes agent" in error for error in errors), errors)

    def test_source_mutation_changes_fresh_artifact_only(self) -> None:
        root = self.copy_repository()
        skill = root / "plugins" / "expskill" / "content" / "skills" / "unslop" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace("Cut AI tells", "Changed skill marker"),
            encoding="utf-8",
        )
        _temporary, artifact = self.build_artifact(root)
        self.assertIn("Changed skill marker", (artifact / "skills" / "unslop" / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
