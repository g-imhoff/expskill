from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.validate import (
    _parse_frontmatter,
    _parse_overlay_frontmatter,
    validate_repository,
)


try:
    from scripts.sync_opencode_agents import render_all as _render_opencode_agents
except ModuleNotFoundError:
    from sync_opencode_agents import render_all as _render_opencode_agents


ROOT = Path(__file__).resolve().parents[1]
CODEX_ROOT = ROOT / "packages" / "codex"
OPENCODE_ROOT = ROOT / "packages" / "opencode"
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


class OpencodeContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "packages", temporary / "packages")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        shutil.copy2(ROOT / "README.md", temporary / "README.md")
        return temporary

    def test_no_opencode_contract_error_on_valid_repository(self) -> None:
        errors = validate_repository(ROOT)
        self.assertFalse(
            [error for error in errors if "opencode" in error],
            f"opencode contract errors: {[e for e in errors if 'opencode' in e]}",
        )

    def test_skills_entry_links_the_shared_codex_base(self) -> None:
        entry = OPENCODE_ROOT / "skills"
        self.assertTrue(entry.is_symlink(), "opencode skills entry must be a symlink")
        self.assertEqual(entry.resolve(), (CODEX_ROOT / "skills").resolve())

    def test_every_shared_skill_is_byte_identical_on_both_surfaces(self) -> None:
        for name in SKILLS:
            with self.subTest(skill=name):
                canonical = (CODEX_ROOT / "skills" / name / "SKILL.md").read_bytes()
                exposed = (OPENCODE_ROOT / "skills" / name / "SKILL.md").read_bytes()
                self.assertEqual(exposed, canonical)

    def test_every_shared_skill_declares_exact_opencode_metadata(self) -> None:
        for name in SKILLS:
            with self.subTest(skill=name):
                contents = (CODEX_ROOT / "skills" / name / "SKILL.md").read_text(
                    encoding="utf-8"
                )
                errors: list[str] = []
                frontmatter = _parse_frontmatter(contents, name, errors)
                self.assertEqual(errors, [])
                assert frontmatter is not None
                metadata = frontmatter.get("metadata")
                expected_autoinvoke = "true" if name == "use-expskill" else "false"
                self.assertEqual(
                    metadata,
                    {"opencode/slash": "true", "opencode/autoinvoke": expected_autoinvoke},
                )

    def test_every_command_routes_to_its_skill(self) -> None:
        for name in SKILLS:
            with self.subTest(command=name):
                contents = (OPENCODE_ROOT / "commands" / f"{name}.md").read_text(
                    encoding="utf-8"
                )
                errors: list[str] = []
                parsed = _parse_overlay_frontmatter(contents, f"command {name}", errors)
                self.assertEqual(errors, [])
                assert parsed is not None
                scalars, _mappings, _block = parsed
                self.assertEqual(set(scalars), {"description"})
                self.assertTrue(scalars["description"].strip())
                for marker in (f"`{name}`", "skill tool", "$ARGUMENTS"):
                    self.assertIn(marker, contents)

    def test_every_agent_matches_its_canonical_profile(self) -> None:
        import json as _json

        spec = _json.loads(
            (OPENCODE_ROOT / "agents.json").read_text(encoding="utf-8")
        )
        active = spec["model_profiles"][spec["default_model_profile"]]
        for name in AGENTS:
            with self.subTest(agent=name):
                profile = tomllib.loads(
                    (CODEX_ROOT / "assets" / "agents" / f"{name}.toml").read_text(
                        encoding="utf-8"
                    )
                )
                contents = (OPENCODE_ROOT / "agents" / f"{name}.md").read_text(
                    encoding="utf-8"
                )
                errors: list[str] = []
                parsed = _parse_overlay_frontmatter(contents, f"agent {name}", errors)
                self.assertEqual(errors, [])
                assert parsed is not None
                scalars, mappings, _block = parsed
                self.assertEqual(scalars.get("mode"), "subagent")
                self.assertEqual(scalars.get("model"), active["model"])
                self.assertEqual(scalars.get("reasoningEffort"), active["reasoningEffort"])
                self.assertEqual(scalars.get("description"), profile["description"])
                self.assertIn("permission", mappings)
                self.assertIn("task: deny", contents)
                self.assertIn("question: deny", contents)
                if name in ("expskill-implementer", "expskill-designer"):
                    self.assertIn("edit: allow", contents)
                    self.assertIn('git push *": deny', contents)
                else:
                    self.assertIn("edit: deny", contents)
                first_sentence = (
                    str(profile["developer_instructions"]).strip().split("\n")[0].strip().lower()
                )
                self.assertIn(first_sentence, " ".join(contents.lower().split()))

    def test_rendered_agents_match_checked_in_files(self) -> None:
        rendered = _render_opencode_agents(ROOT)
        self.assertEqual(set(rendered), set(AGENTS))
        for name, contents in rendered.items():
            with self.subTest(agent=name):
                current = (OPENCODE_ROOT / "agents" / f"{name}.md").read_text(
                    encoding="utf-8"
                )
                self.assertEqual(current, contents)

    def test_provider_switch_renders_every_agent_with_new_model(self) -> None:
        import json as _json
        import shutil as _shutil
        import tempfile as _tempfile

        with _tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agents_source = CODEX_ROOT / "assets" / "agents"
            agents_target = root / "packages" / "codex" / "assets" / "agents"
            agents_target.mkdir(parents=True)
            for profile in agents_source.glob("*.toml"):
                _shutil.copy2(profile, agents_target / profile.name)
            spec = _json.loads((OPENCODE_ROOT / "agents.json").read_text(encoding="utf-8"))
            spec["default_model_profile"] = "opencode-free"
            (root / "packages" / "opencode").mkdir(parents=True)
            (root / "packages" / "opencode" / "agents.json").write_text(
                _json.dumps(spec), encoding="utf-8"
            )
            rendered = _render_opencode_agents(root)
            expected = spec["model_profiles"]["opencode-free"]["model"]
            self.assertEqual(set(rendered), set(AGENTS))
            for name, contents in rendered.items():
                with self.subTest(agent=name):
                    self.assertIn(f"model: {expected}", contents.splitlines())

    def test_package_version_matches_codex_base_version(self) -> None:
        package = json.loads((OPENCODE_ROOT / "package.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (CODEX_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package["name"], "opencode-expskill")
        self.assertEqual(package["version"], str(manifest["version"]).split("+")[0])

    def test_diverged_shared_skill_is_rejected(self) -> None:
        root = self.copy_repository()
        diverged = root / "packages" / "opencode" / "skills" / "plan" / "SKILL.md"
        diverged.write_text(
            diverged.read_text(encoding="utf-8") + "\nExtra drift.\n",
            encoding="utf-8",
        )
        errors = validate_repository(root)
        self.assertTrue(
            any("shared skill" in error and "exact shared base" in error for error in errors),
            f"expected shared-base error, got: {errors}",
        )

    def test_missing_command_is_rejected(self) -> None:
        root = self.copy_repository()
        (root / "packages" / "opencode" / "commands" / "plan.md").unlink()
        errors = validate_repository(root)
        self.assertTrue(
            any("opencode command" in error and "missing" in error for error in errors),
            f"expected missing-command error, got: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
