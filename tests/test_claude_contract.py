from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_claude_package import BuildError, build_claude_package
from scripts.render_claude import (
    CLAUDE_READ_TOOLS,
    CLAUDE_WRITE_TOOLS,
    render_agents,
    skill_inventory,
)
from scripts.validate import _parse_frontmatter, _parse_overlay_frontmatter, validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
CLAUDE_ROOT = PLUGIN_ROOT / "claude"
AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
# Codex luna roles run on the haiku family; Codex sol roles run on opus.
# Read-only OpenCode permission maps collapse to the read tool set, while the
# workspace-write maps add Edit and Write.
FACETS = {
    "expskill-designer": ("haiku", CLAUDE_WRITE_TOOLS),
    "expskill-explorer": ("haiku", CLAUDE_READ_TOOLS),
    "expskill-implementer": ("haiku", CLAUDE_WRITE_TOOLS),
    "expskill-planner": ("haiku", CLAUDE_READ_TOOLS),
    "expskill-review": ("opus", CLAUDE_READ_TOOLS),
    "expskill-spec": ("opus", CLAUDE_READ_TOOLS),
    "expskill-test-engineer": ("haiku", CLAUDE_READ_TOOLS),
}


class ClaudeContractTests(unittest.TestCase):
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
        build_claude_package(root, artifact)
        self.addCleanup(temporary.cleanup)
        return temporary, artifact

    def plugin_dir(self, artifact: Path) -> Path:
        return artifact / "plugins" / "expskill"

    def test_no_claude_contract_error_on_valid_repository(self) -> None:
        errors = validate_repository(ROOT)
        self.assertFalse([error for error in errors if "claude" in error], errors)

    def test_default_validation_rejects_missing_whole_source_surface(self) -> None:
        for surface, expected_error in (
            ("plugins/expskill/claude", "claude package directory is missing"),
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
        skills = self.plugin_dir(artifact) / "skills"
        self.assertEqual(sorted(path.name for path in skills.iterdir()), list(names))
        for name in names:
            source = PLUGIN_ROOT / "content" / "skills" / name / "SKILL.md"
            exposed = skills / name / "SKILL.md"
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
                contents = (self.plugin_dir(artifact) / "agents" / f"{name}.md").read_text(encoding="utf-8")
                self.assertEqual(contents, rendered[name])
                errors: list[str] = []
                parsed = _parse_overlay_frontmatter(contents, f"agent {name}", errors)
                self.assertEqual(errors, [])
                assert parsed is not None
                scalars, mappings, _block = parsed
                model, tools = FACETS[name]
                self.assertEqual(scalars.get("name"), name)
                self.assertEqual(scalars.get("model"), model)
                self.assertEqual(mappings, {"tools"})
                for tool in tools:
                    self.assertIn(f"\n  - {tool}\n", contents)
                self.assertIn(content["agents"][name]["description"], contents)
                self.assertIn(content["agents"][name]["closing"], contents)
                self.assertIn("You run as a Claude Code subagent", contents)

    def test_manifest_matches_claude_plugin_schema_and_base_version(self) -> None:
        manifest = json.loads((CLAUDE_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        codex_manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["$schema"],
            "https://anthropic.com/claude-code/plugin.schema.json",
        )
        self.assertEqual(manifest["name"], "expskill")
        self.assertEqual(manifest["version"], str(codex_manifest["version"]).split("+")[0])
        self.assertEqual(manifest["license"], "MIT")

    def test_hooks_wire_session_start_and_pre_compact_to_scope_payloads(self) -> None:
        hooks = json.loads((CLAUDE_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(hooks.get("hooks", {})),
            {"SessionStart", "PreCompact"},
        )
        commands: list[str] = []
        for event in ("SessionStart", "PreCompact"):
            entries = hooks["hooks"][event]
            self.assertTrue(entries, event)
            for entry in entries:
                for hook in entry["hooks"]:
                    self.assertEqual(hook.get("type"), "command")
                    command = hook.get("command", "")
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
                    commands.append(command)
        self.assertTrue(any("inject_authoring.py" in command for command in commands))
        self.assertTrue(any("inject_unslop.py" in command for command in commands))

    def test_hook_scripts_use_claude_plugin_root(self) -> None:
        for script in ("inject_authoring.py", "inject_unslop.py"):
            with self.subTest(script=script):
                contents = (CLAUDE_ROOT / "hooks" / script).read_text(encoding="utf-8")
                self.assertIn("CLAUDE_PLUGIN_ROOT", contents)
                self.assertNotIn("PLUGIN_ROOT", contents.replace("CLAUDE_PLUGIN_ROOT", ""))

    def test_hook_scripts_execute_documented_envelope_behavior(self) -> None:
        markers = {
            "inject_authoring.py": "Never create documentation files",
            "inject_unslop.py": "$expskill:unslop",
        }
        environment = dict(os.environ)
        environment["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        for script, marker in markers.items():
            with self.subTest(script=script, event="SessionStart"):
                result = subprocess.run(
                    [sys.executable, str(CLAUDE_ROOT / "hooks" / script)],
                    input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
                self.assertIn(marker, output["hookSpecificOutput"]["additionalContext"])
            with self.subTest(script=script, event="PreCompact"):
                result = subprocess.run(
                    [sys.executable, str(CLAUDE_ROOT / "hooks" / script)],
                    input=json.dumps({"hook_event_name": "PreCompact", "trigger": "manual"}),
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(marker, result.stdout)
                self.assertNotIn("hookSpecificOutput", result.stdout)
                self.assertNotIn("additionalContext", result.stdout)
        # Cap source for the unslop payload: codex hooks.json additionalContextLimit.
        codex_hooks = json.loads((PLUGIN_ROOT / "codex" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        handlers = codex_hooks["hooks"]["SessionStart"][0]["hooks"]
        unslop_limit = next(
            handler["additionalContextLimit"] for handler in handlers if "inject_unslop.py" in handler["command"]
        )
        self.assertEqual(unslop_limit, 5000)
        root = self.copy_repository()
        plugin = root / "plugins" / "expskill"
        oversized_environment = dict(os.environ)
        oversized_environment["CLAUDE_PLUGIN_ROOT"] = str(plugin)
        # Authoring cap source: the 1000-character guard in the Claude inject_authoring.py.
        cases = (
            (
                "inject_authoring.py",
                plugin / "content" / "policies" / "authoring-runtime.json",
                {"schema_version": "authoring-runtime.v1", "instructions": "x" * 1001},
            ),
            (
                "inject_unslop.py",
                plugin / "content" / "policies" / "unslop-runtime.json",
                {
                    "schema_version": "unslop-runtime.v1",
                    "scope": "x" * (unslop_limit + 1),
                    "compaction_reminder": "reminder",
                },
            ),
        )
        for script, policy_path, payload in cases:
            with self.subTest(script=script, event="oversized"):
                policy_path.write_text(json.dumps(payload), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(root / "plugins" / "expskill" / "claude" / "hooks" / script)],
                    input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                    text=True,
                    capture_output=True,
                    env=oversized_environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_execution_policy_is_copied_to_artifact(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        canonical = (PLUGIN_ROOT / "content" / "policies" / "execution-policy.json").read_bytes()
        self.assertEqual(
            (self.plugin_dir(artifact) / "assets" / "execution-policy.json").read_bytes(),
            canonical,
        )

    def test_validation_does_not_require_checked_in_generated_mirrors(self) -> None:
        self.assertEqual(validate_repository(ROOT), ())
        self.assertFalse((CLAUDE_ROOT / "agents").exists())
        self.assertFalse((CLAUDE_ROOT / "skills").exists())

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
        overlay = root / "plugins" / "expskill" / "claude" / "agents.json"
        spec = json.loads(overlay.read_text(encoding="utf-8"))
        spec["agents"]["expskill-review"]["model"] = "haiku"
        overlay.write_text(json.dumps(spec), encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("claude agent" in error for error in errors), errors)

    def test_validation_rejects_hooks_missing_pre_compact(self) -> None:
        root = self.copy_repository()
        hooks_path = root / "plugins" / "expskill" / "claude" / "hooks" / "hooks.json"
        spec = json.loads(hooks_path.read_text(encoding="utf-8"))
        del spec["hooks"]["PreCompact"]
        hooks_path.write_text(json.dumps(spec), encoding="utf-8")
        errors = validate_repository(root)
        self.assertTrue(any("claude hooks" in error for error in errors), errors)

    def test_source_mutation_changes_fresh_artifact_only(self) -> None:
        root = self.copy_repository()
        skill = root / "plugins" / "expskill" / "content" / "skills" / "unslop" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace("Cut AI tells", "Changed skill marker"),
            encoding="utf-8",
        )
        _temporary, artifact = self.build_artifact(root)
        self.assertIn(
            "Changed skill marker",
            (self.plugin_dir(artifact) / "skills" / "unslop" / "SKILL.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
