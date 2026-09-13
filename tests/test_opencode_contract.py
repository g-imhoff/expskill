from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.build_opencode_package import build_opencode_package
from scripts.render_opencode import (
    OPENCODE_DESCRIPTION_MAX_LENGTH,
    render_agents,
    skill_inventory,
)
from scripts.validate import _parse_frontmatter, _parse_overlay_frontmatter, validate_repository


ROOT = Path(__file__).resolve().parents[1]
CODEX_ROOT = ROOT / "packages" / "expskill"
AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)


class OpencodeContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        shutil.copytree(
            ROOT,
            temporary / "repo",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        return temporary / "repo"

    def build_artifact(self, root: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        artifact = Path(temporary.name) / "artifact"
        build_opencode_package(root, artifact)
        self.addCleanup(temporary.cleanup)
        return temporary, artifact

    def test_no_opencode_contract_error_on_valid_repository(self) -> None:
        errors = validate_repository(ROOT)
        self.assertFalse([error for error in errors if "opencode" in error], errors)

    def test_default_validation_rejects_missing_whole_source_surfaces(self) -> None:
        for surface, expected_error in (
            ("plugins", "plugin directory is missing"),
            ("packages", "opencode package directory is missing"),
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
            source = CODEX_ROOT / "skills" / name / "SKILL.md"
            exposed = artifact / "skills" / name / "SKILL.md"
            self.assertFalse(exposed.is_symlink())
            self.assertEqual(exposed.read_bytes(), source.read_bytes())

    def test_every_shared_skill_declares_exact_opencode_metadata(self) -> None:
        for name in skill_inventory(ROOT):
            with self.subTest(skill=name):
                contents = (CODEX_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                errors: list[str] = []
                frontmatter = _parse_frontmatter(contents, name, errors)
                self.assertEqual(errors, [])
                assert frontmatter is not None
                expected_autoinvoke = "true" if name == "use-expskill" else "false"
                self.assertEqual(
                    frontmatter.get("metadata"),
                    {"opencode/slash": "true", "opencode/autoinvoke": expected_autoinvoke},
                )

    def test_every_command_routes_to_its_skill_and_catalog_description_is_bounded(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        catalog = json.loads((artifact / "catalog.json").read_text(encoding="utf-8"))
        for name in skill_inventory(ROOT):
            with self.subTest(command=name):
                contents = (artifact / "commands" / f"{name}.md").read_text(encoding="utf-8")
                errors: list[str] = []
                parsed = _parse_overlay_frontmatter(contents, f"command {name}", errors)
                self.assertEqual(errors, [])
                assert parsed is not None
                scalars, _mappings, _block = parsed
                self.assertEqual(set(scalars), {"description"})
                description = scalars["description"]
                self.assertGreaterEqual(len(description), 1)
                self.assertLessEqual(len(description), OPENCODE_DESCRIPTION_MAX_LENGTH)
                self.assertEqual(description, catalog["commands"][name]["description"])
                for marker in (f"`{name}`", "skill tool", "$ARGUMENTS"):
                    self.assertIn(marker, contents)

    def test_every_agent_matches_pure_renderer_and_canonical_profile(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        rendered = render_agents(ROOT)
        self.assertEqual(set(rendered), set(AGENTS))
        spec = json.loads((CODEX_ROOT / "opencode" / "agents.json").read_text(encoding="utf-8"))
        active = spec["model_profiles"][spec["default_model_profile"]]
        catalog = json.loads((artifact / "catalog.json").read_text(encoding="utf-8"))
        for name in AGENTS:
            with self.subTest(agent=name):
                profile = tomllib.loads(
                    (CODEX_ROOT / "assets" / "agents" / f"{name}.toml").read_text(encoding="utf-8")
                )
                contents = (artifact / "agents" / f"{name}.md").read_text(encoding="utf-8")
                self.assertEqual(contents, rendered[name])
                errors: list[str] = []
                parsed = _parse_overlay_frontmatter(contents, f"agent {name}", errors)
                self.assertEqual(errors, [])
                assert parsed is not None
                scalars, mappings, _block = parsed
                self.assertEqual(scalars.get("mode"), "subagent")
                self.assertEqual(scalars.get("model"), active["model"])
                self.assertEqual(scalars.get("reasoningEffort"), active["reasoningEffort"])
                self.assertGreaterEqual(len(scalars["description"]), 1)
                self.assertLessEqual(len(scalars["description"]), OPENCODE_DESCRIPTION_MAX_LENGTH)
                self.assertIn("permission", mappings)
                self.assertEqual(scalars["description"], catalog["agents"][name]["description"])
                self.assertIn("task: deny", contents)
                self.assertIn("question: deny", contents)
                if name in ("expskill-implementer", "expskill-designer"):
                    self.assertIn("edit: allow", contents)
                    self.assertIn('git push *": deny', contents)
                else:
                    self.assertIn("edit: deny", contents)
                first_sentence = str(profile["developer_instructions"]).strip().split("\n")[0].strip().lower()
                self.assertIn(first_sentence, " ".join(contents.lower().split()))

    def test_provider_switch_renders_every_agent_with_new_model(self) -> None:
        root = self.copy_repository()
        spec_path = root / "packages" / "expskill" / "opencode" / "agents.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["default_model_profile"] = "opencode-free"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        rendered = render_agents(root)
        expected = spec["model_profiles"]["opencode-free"]["model"]
        self.assertEqual(set(rendered), set(AGENTS))
        for name, contents in rendered.items():
            with self.subTest(agent=name):
                self.assertIn(f"model: {expected}", contents.splitlines())

    def test_dynamic_new_skill_is_included_without_hardcoded_inventory(self) -> None:
        root = self.copy_repository()
        skill = root / "packages" / "expskill" / "skills" / "future-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: future-skill\ndescription: Future skill.\nmetadata:\n  opencode/slash: \"true\"\n  opencode/autoinvoke: \"false\"\n---\n\nFuture.\n",
            encoding="utf-8",
        )
        _temporary, artifact = self.build_artifact(root)
        self.assertTrue((artifact / "commands" / "future-skill.md").is_file())
        catalog = json.loads((artifact / "catalog.json").read_text(encoding="utf-8"))
        self.assertIn("future-skill", catalog["commands"])

    def test_explorer_is_the_named_high_reasoning_external_research_route(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        spec = json.loads((CODEX_ROOT / "opencode" / "agents.json").read_text(encoding="utf-8"))
        for profile in spec["model_profiles"].values():
            self.assertEqual(profile["reasoningEffort"], "xhigh")
        explorer = (artifact / "agents" / "expskill-explorer.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "explicitly assigned external-research lane",
            "available web tools",
            "direct primary or authoritative sources",
            "never inspect sibling output",
            "return evidence only",
        ):
            self.assertIn(phrase, explorer)

    def test_package_version_matches_codex_base_version(self) -> None:
        package = json.loads((CODEX_ROOT / "opencode" / "package.json").read_text(encoding="utf-8"))
        manifest = json.loads((CODEX_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(package["name"], "opencode-expskill")
        self.assertEqual(package["version"], str(manifest["version"]).split("+")[0])

    def test_execution_policy_is_copied_to_artifact(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        canonical = (CODEX_ROOT / "assets" / "execution-policy.json").read_bytes()
        self.assertEqual((artifact / "assets" / "execution-policy.json").read_bytes(), canonical)
        plugin = (artifact / "plugins" / "execution-policy.js").read_text(encoding="utf-8")
        self.assertIn("requestedAgent(output?.args)", plugin)
        self.assertNotIn("recordRetry", plugin)

    def test_validation_does_not_require_checked_in_generated_mirrors(self) -> None:
        self.assertEqual(validate_repository(ROOT), ())
        self.assertFalse((CODEX_ROOT / "opencode" / "commands").exists())
        self.assertFalse((CODEX_ROOT / "opencode" / "agents").exists())

    def test_validation_reports_malformed_overlay_closing(self) -> None:
        root = self.copy_repository()
        overlay = root / "packages" / "expskill" / "opencode" / "agents.json"
        spec = json.loads(overlay.read_text(encoding="utf-8"))
        del spec["agents"]["expskill-review"]["closing"]
        overlay.write_text(json.dumps(spec), encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("closing" in error for error in errors), errors)

    def test_source_mutation_changes_fresh_artifact_only(self) -> None:
        root = self.copy_repository()
        skill = root / "packages" / "expskill" / "skills" / "unslop" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace("Cut AI tells", "Changed skill marker"),
            encoding="utf-8",
        )
        _temporary, artifact = self.build_artifact(root)
        self.assertIn("Changed skill marker", (artifact / "commands" / "unslop.md").read_text(encoding="utf-8"))
        self.assertIn("Changed skill marker", (artifact / "catalog.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
