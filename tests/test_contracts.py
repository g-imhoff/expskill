from __future__ import annotations

import ast
import json
import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "codex-dev-flow"
ROUTER_ROOT = PLUGIN_ROOT / "skills" / "route-code-change"
QUICK_ROOT = PLUGIN_ROOT / "skills" / "quick-code-change"
FULL_ROOT = PLUGIN_ROOT / "skills" / "full-code-change"
EXPECTED_AGENTS = {
    "devflow-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "devflow-test-engineer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-reviewer": ("gpt-5.6-sol", "xhigh", "read-only"),
    "devflow-verifier": ("gpt-5.6-luna", "max", "workspace-write"),
}
EXPECTED_SKILLS = {
    "full-code-change",
    "quick-code-change",
    "route-code-change",
}


def _parse_yaml_scalar(raw_value: str) -> object:
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {'"', "'"}:
        return ast.literal_eval(raw_value)
    return raw_value


def _parse_yaml_mapping(contents: str) -> dict[str, object]:
    result: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, result)]
    for line in contents.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key:
            raise ValueError(f"unsupported YAML line: {line!r}")
        while stack[-1][0] >= indentation:
            stack.pop()
        parent = stack[-1][1]
        if raw_value.strip():
            parent[key] = _parse_yaml_scalar(raw_value.strip())
            continue
        child: dict[str, object] = {}
        parent[key] = child
        stack.append((indentation, child))
    return result


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

    def load_skill_frontmatter(self, skill_root: Path) -> dict[str, object]:
        lines = (skill_root / "SKILL.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        return _parse_yaml_mapping("\n".join(lines[1:end]))

    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual(validate_repository(ROOT), ())

    def test_missing_skills_directory_is_rejected(self) -> None:
        root = self.copy_repository()
        skills_path = root / "plugins" / "codex-dev-flow" / "skills"
        shutil.rmtree(skills_path)
        errors = validate_repository(root)
        self.assertIn(f"skills directory is missing: {skills_path}", errors)

    def test_skills_file_is_rejected(self) -> None:
        root = self.copy_repository()
        skills_path = root / "plugins" / "codex-dev-flow" / "skills"
        shutil.rmtree(skills_path, ignore_errors=True)
        skills_path.write_text("not a directory\n", encoding="utf-8")
        errors = validate_repository(root)
        self.assertIn(f"skills path must be a directory: {skills_path}", errors)

    def test_missing_required_skill_is_rejected(self) -> None:
        root = self.copy_repository()
        missing = root / "plugins" / "codex-dev-flow" / "skills" / "quick-code-change"
        shutil.rmtree(missing)
        errors = validate_repository(root)
        self.assertTrue(any("quick-code-change" in error and "missing" in error for error in errors))

    def test_unexpected_skill_is_rejected(self) -> None:
        root = self.copy_repository()
        unexpected = root / "plugins" / "codex-dev-flow" / "skills" / "surprise"
        unexpected.mkdir()
        (unexpected / "SKILL.md").write_text(
            "---\nname: surprise\ndescription: Unexpected skill\n---\n\nBody.\n",
            encoding="utf-8",
        )
        errors = validate_repository(root)
        self.assertTrue(any("surprise" in error and "unexpected" in error for error in errors))

    def test_required_skill_file_is_rejected(self) -> None:
        root = self.copy_repository()
        skill_path = root / "plugins" / "codex-dev-flow" / "skills" / "quick-code-change"
        shutil.rmtree(skill_path)
        skill_path.write_text("not a directory\n", encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("quick-code-change" in error and "directory" in error for error in errors))

    def test_required_skill_without_skill_markdown_is_rejected(self) -> None:
        root = self.copy_repository()
        skill_path = root / "plugins" / "codex-dev-flow" / "skills" / "quick-code-change"
        (skill_path / "SKILL.md").unlink()
        errors = validate_repository(root)
        self.assertTrue(any("quick-code-change" in error and "SKILL.md" in error for error in errors))

    def test_repository_contains_exact_required_skill_roster(self) -> None:
        skills_root = PLUGIN_ROOT / "skills"
        self.assertEqual({path.name for path in skills_root.iterdir()}, EXPECTED_SKILLS)

    def test_plugin_and_marketplace_identities_are_exact(self) -> None:
        manifest = self.load_manifest(ROOT)
        marketplace = self.load_marketplace(ROOT)
        self.assertEqual(manifest["name"], "codex-dev-flow")
        self.assertEqual(manifest["version"].split("+", 1)[0], "0.1.0")
        self.assertEqual(manifest["repository"], "https://github.com/g-imhoff/codex-dev-flow")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["interface"]["category"], "Developer Tools")
        self.assertEqual(marketplace["name"], "codex-dev-flow")
        self.assertEqual(marketplace["plugins"][0]["name"], "codex-dev-flow")
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/codex-dev-flow")
        self.assertEqual(marketplace["plugins"][0]["category"], "Developer Tools")

    def test_codex_cachebuster_versions_are_valid(self) -> None:
        for version in ("0.1.0", "0.1.0+codex.cache-1", "0.1.0+codex.a.b-2"):
            with self.subTest(version=version):
                root = self.copy_repository()
                manifest_path = root / "plugins" / "codex-dev-flow" / ".codex-plugin" / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["version"] = version
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                self.assertEqual(validate_repository(root), ())

    def test_invalid_codex_cachebuster_versions_are_rejected(self) -> None:
        invalid_versions = (
            "0.1.1",
            "0.1.0+other.cache",
            "0.1.0+codex.",
            "0.1.0+codex.a..b",
            "0.1.0+codex.a b",
            "0.1.0+codex.a/b",
            "0.1.0+codex.a_b",
        )
        for version in invalid_versions:
            with self.subTest(version=version):
                root = self.copy_repository()
                manifest_path = root / "plugins" / "codex-dev-flow" / ".codex-plugin" / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["version"] = version
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                errors = validate_repository(root)
                self.assertTrue(any("version" in error for error in errors))

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
            directory.mkdir(parents=True)
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

    def test_route_skill_frontmatter_matches_directory(self) -> None:
        frontmatter = self.load_skill_frontmatter(ROUTER_ROOT)
        self.assertEqual(frontmatter["name"], ROUTER_ROOT.name)
        self.assertTrue(frontmatter["description"])

    def test_route_skill_ui_metadata_enables_implicit_invocation(self) -> None:
        metadata = _parse_yaml_mapping(
            (ROUTER_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata,
            {
                "interface": {
                    "display_name": "Route Code Change",
                    "short_description": "Choose the proportionate development route",
                    "default_prompt": "Use $route-code-change to route this coding change.",
                },
                "policy": {"allow_implicit_invocation": True},
            },
        )

    def test_route_skill_body_stays_under_200_words(self) -> None:
        contents = (ROUTER_ROOT / "SKILL.md").read_text(encoding="utf-8")
        _, _, body = contents.split("---", 2)
        self.assertLess(len(body.split()), 200)

    def test_quick_skill_frontmatter_matches_directory_and_explicit_route(self) -> None:
        self.assertTrue((QUICK_ROOT / "SKILL.md").is_file())
        frontmatter = self.load_skill_frontmatter(QUICK_ROOT)
        self.assertEqual(frontmatter["name"], QUICK_ROOT.name)
        description = frontmatter["description"]
        self.assertTrue(description)
        self.assertIn("explicitly selected Quick route", description)
        self.assertIn("router handoff", description)

    def test_quick_skill_ui_metadata_disables_implicit_invocation(self) -> None:
        self.assertTrue((QUICK_ROOT / "agents" / "openai.yaml").is_file())
        metadata = _parse_yaml_mapping(
            (QUICK_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata,
            {
                "interface": {
                    "display_name": "Quick Code Change",
                    "short_description": "Implement a bounded change with independent gates",
                    "default_prompt": "Use $quick-code-change for this accepted Quick change.",
                },
                "policy": {"allow_implicit_invocation": False},
            },
        )

    def test_quick_skill_body_stays_under_200_words(self) -> None:
        self.assertTrue((QUICK_ROOT / "SKILL.md").is_file())
        contents = (QUICK_ROOT / "SKILL.md").read_text(encoding="utf-8")
        _, _, body = contents.split("---", 2)
        self.assertLess(len(body.split()), 200)

    def test_quick_pressure_dispatches_context_free_reviewer_and_verifier(self) -> None:
        body = (QUICK_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
        for profile in ("devflow-reviewer", "devflow-verifier"):
            self.assertIn(f"`{profile}`", body)
            self.assertRegex(
                body,
                re.compile(
                    rf"agent_type[^\n]+{profile}[^\n]+fork_turns[^\n]+none",
                    re.IGNORECASE,
                ),
            )
        self.assertIn("concurrent", body.lower())
        self.assertNotIn("read_only_agent.py", body)

    def test_full_skill_frontmatter_matches_directory_and_explicit_route(self) -> None:
        self.assertTrue((FULL_ROOT / "SKILL.md").is_file())
        frontmatter = self.load_skill_frontmatter(FULL_ROOT)
        self.assertEqual(frontmatter["name"], FULL_ROOT.name)
        description = frontmatter["description"]
        self.assertTrue(description)
        self.assertIn("explicitly selected Full route", description)
        self.assertIn("router handoff", description)

    def test_full_skill_ui_metadata_disables_implicit_invocation(self) -> None:
        self.assertTrue((FULL_ROOT / "agents" / "openai.yaml").is_file())
        metadata = _parse_yaml_mapping(
            (FULL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata,
            {
                "interface": {
                    "display_name": "Full Code Change",
                    "short_description": "Plan and execute a parallel gated change",
                    "default_prompt": "Use $full-code-change for this accepted Full change.",
                },
                "policy": {"allow_implicit_invocation": False},
            },
        )

    def test_full_skill_body_stays_under_500_words(self) -> None:
        self.assertTrue((FULL_ROOT / "SKILL.md").is_file())
        contents = (FULL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        _, _, body = contents.split("---", 2)
        self.assertLess(len(body.split()), 500)

    def test_full_pressure_dispatches_every_profile_with_context_free_routing(self) -> None:
        body = (FULL_ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
        for profile in (
            "devflow-explorer",
            "devflow-test-engineer",
            "devflow-implementer",
            "devflow-reviewer",
            "devflow-verifier",
        ):
            self.assertIn(f"`{profile}`", body)
            self.assertRegex(
                body,
                re.compile(
                    rf"agent_type[^\n]+{profile}[^\n]+fork_turns[^\n]+none",
                    re.IGNORECASE,
                ),
            )
        self.assertIn("concurrent", body.lower())
        self.assertNotIn("read_only_agent.py", body)

    def test_unsupported_read_only_agent_runner_is_removed(self) -> None:
        self.assertFalse((PLUGIN_ROOT / "scripts" / "read_only_agent.py").exists())
        self.assertFalse((ROOT / "tests" / "test_read_only_agent.py").exists())

    def test_repository_docs_describe_context_free_named_agent_isolation(self) -> None:
        for relative_path in (
            "docs/specs/2026-08-09-codex-dev-flow-design.md",
            "docs/plans/2026-08-09-codex-dev-flow-implementation.md",
        ):
            body = (ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertIn("context-free", body)
                self.assertNotIn("read_only_agent.py", body)
                self.assertNotRegex(body, re.compile(r"isolated (?:read-only )?(?:Codex )?process", re.I))

    def test_repository_docs_do_not_reference_retired_workflow(self) -> None:
        references = []
        retired_name = "super" + "powers"
        for docs_root in (ROOT / "docs" / "plans", ROOT / "docs" / "specs"):
            for path in docs_root.glob("*.md"):
                if retired_name in path.read_text(encoding="utf-8").lower():
                    references.append(path.relative_to(ROOT))
        self.assertEqual(references, [])


if __name__ == "__main__":
    unittest.main()
