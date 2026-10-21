from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from tests.build_cache import checkout_copy, shared_artifact
from tests.test_hermes_package import hermes_agent_runtime


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
POLICY = PLUGIN / "content" / "policies" / "authoring-runtime.json"
UNSLOP_POLICY = PLUGIN / "content" / "policies" / "unslop-runtime.json"
UNSLOP_SKILL = PLUGIN / "content" / "skills" / "unslop" / "SKILL.md"
RULE = json.loads(POLICY.read_text())["instructions"]


def _unslop_scope() -> str:
    return json.loads(UNSLOP_POLICY.read_text())["scope"]


def _unslop_body() -> str:
    lines = UNSLOP_SKILL.read_text().splitlines()
    return "\n".join(lines[lines.index("---", 1) + 1:]).strip()


def _unslop_expected() -> str:
    return " ".join((_unslop_scope() + "\n\n" + _unslop_body()).split())


class AuthoringRuntimeTests(unittest.TestCase):
    def test_authoring_rule_has_its_own_policy(self) -> None:
        policy = json.loads(POLICY.read_text())
        self.assertEqual(set(policy), {"schema_version", "instructions"})
        self.assertEqual(policy["schema_version"], "authoring-runtime.v1")
        self.assertTrue(policy["instructions"].strip())
        self.assertNotIn(RULE, UNSLOP_SKILL.read_text())

    def test_packages_and_fresh_agent_profiles_carry_the_rule(self) -> None:
        for host in ("codex", "opencode", "hermes"):
            with self.subTest(host=host):
                package = shared_artifact(host)
                self.assertEqual((package / "assets/authoring-runtime.json").read_bytes(), POLICY.read_bytes())
                profiles = list((package / "agents").iterdir())
                self.assertEqual(len(profiles), 7)
                for profile in profiles:
                    instructions = profile.read_text()
                    if host == "codex":
                        instructions = tomllib.loads(instructions)["developer_instructions"]
                    self.assertIn(RULE, instructions, profile.name)
                if host == "opencode":
                    catalog = json.loads((package / "catalog.json").read_text())
                    for name, agent in catalog["agents"].items():
                        self.assertIn(RULE, agent["prompt"], name)

    def test_codex_injects_the_rule_for_every_session_start_source(self) -> None:
        package = shared_artifact("codex")
        config = json.loads((package / "hooks/hooks.json").read_text())
        handlers = config["hooks"]["SessionStart"][0]["hooks"]
        self.assertTrue(any("inject_authoring.py" in handler["command"] for handler in handlers))
        env = dict(os.environ)
        env.pop("PLUGIN_ROOT", None)
        for source in ("startup", "resume", "clear", "compact"):
            with self.subTest(source=source):
                result = subprocess.run(
                    [sys.executable, str(package / "hooks/inject_authoring.py")],
                    input=json.dumps({"hook_event_name": "SessionStart", "source": source}),
                    capture_output=True, text=True, env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                self.assertEqual(context, RULE)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_opencode_injects_into_v1_and_v2_sessions_and_compaction(self) -> None:
        package = shared_artifact("opencode")
        script = r'''
import assert from "node:assert/strict";
const { default: plugin } = await import(process.argv[1]);
const rule = process.argv[2];
const unslopBody = process.argv[3];
const unslopScope = process.argv[4];
const hooks = await plugin.server({});
for (const sessionID of ["root", "fresh-child", "resumed"]) {
  const output = { system: ["base instructions"] };
  await hooks["experimental.chat.system.transform"]({ sessionID }, output);
  await hooks["experimental.chat.system.transform"]({ sessionID }, output);
  assert.equal(output.system.join("\n").split(rule).length - 1, 1);
  assert.ok(output.system.join("\n").includes("$expskill:unslop"));
}
const compact = { context: [] };
await hooks["experimental.session.compacting"]({}, compact);
assert.ok(compact.context.join("\n").includes(rule));
const registered = {};
const skills = new Map();
await plugin.setup({
  skill: { transform: async callback => callback({ get: id => skills.get(id), add: skill => skills.set(skill.id, skill) }) },
  session: { hook: async (name, callback) => { (registered[name] ??= []).push(callback); } },
});
const unslop = skills.get("expskill:unslop");
assert.equal(unslop.name, "unslop");
assert.equal(unslop.content, unslopBody);
assert.ok(unslop.path.endsWith("unslop/SKILL.md"));
for (const name of ["context", "compaction", "generate"]) {
  for (const system of [["base instructions"], [{ type: "text", text: "base instructions" }], []]) {
    for (const callback of registered[name]) await callback({ system });
    for (const callback of registered[name]) await callback({ system });
    const text = system.map(item => typeof item === "string" ? item : item.text).join("\n");
    assert.equal(text.split(rule).length - 1, 1, name);
    assert.ok(text.includes("$expskill:unslop"), name);
    assert.ok(text.includes(unslopScope), name);
    assert.ok(!text.includes(unslop.content), name);
    assert.ok(text.length < 500, name);
  }
}
const reminder = { context: [] };
for (const callback of registered.compaction) await callback(reminder);
assert.ok(reminder.context.join("\n").includes(rule));
'''
        env = dict(os.environ)
        env.pop("EXPSKILL_HOME", None)
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script,
             (package / "index.js").as_uri(), RULE, _unslop_body(), _unslop_scope()],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hermes_registers_a_durable_section_and_keeps_skills_available(self) -> None:
        package = shared_artifact("hermes")
        self.assertIn("name: expskill", (package / "plugin.yaml").read_text())
        spec = importlib.util.spec_from_file_location("hermes_authoring_test", package / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sections, skills = {}, {}

        class Context:
            def register_system_prompt_section(self, name, text, **options):
                sections[name] = (text, options)

            def register_skill(self, name, skill_md):
                skills[name] = skill_md

        module.register(Context())
        self.assertEqual(sections["expskill.authoring"][0], RULE)
        self.assertEqual(sections["expskill.authoring"][1]["position"], "after_memory")
        unslop = json.loads((package / "assets/unslop-runtime.json").read_text())
        loaded = "\n\n".join(text for name, (text, _) in sorted(sections.items()) if name.startswith("expskill.unslop"))
        self.assertTrue(loaded.startswith(unslop["scope"]))
        self.assertEqual(" ".join(loaded.split()), _unslop_expected())
        self.assertTrue(all(len(text) <= 4000 for text, _ in sections.values()))
        self.assertEqual(len(skills), 17)
        self.assertTrue(all(path.is_file() for path in skills.values()))

    def test_hermes_native_loader_and_core_preserve_the_section(self) -> None:
        runtime = hermes_agent_runtime()
        if runtime is None:
            self.skipTest("Hermes runtime is not installed")
        python, runtime_root = runtime
        package = shared_artifact("hermes")
        script = '''
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from hermes_cli.plugins import PluginManager, format_system_prompt_sections
from hermes_cli.plugins_discovery import scan_directory
from agent.system_prompt import _frozen_plugin_prompt_sections, restore_plugin_prompt_sections
manager = PluginManager()
manifests = scan_directory(Path(sys.argv[2]).parent, "user")
manifest = next(item for item in manifests if item.name == "expskill")
assert not manifest.portable
manager._load_plugin(manifest)
loaded = manager.list_plugins()
assert loaded[0]["enabled"], loaded
assert len(manager.list_plugin_skills("expskill")) == 16
rendered = manager.render_system_prompt_sections({})
assert rendered[0].content == sys.argv[3]
loaded = "\\n\\n".join(item.content for item in rendered[1:])
assert " ".join(loaded.split()) == sys.argv[4]
assert all(len(item.content) <= 4000 for item in rendered)
expected = [item.content for item in rendered]
prompt = format_system_prompt_sections(rendered) + "\\n\\nConversation started: test"
agent = SimpleNamespace(_cached_system_prompt=prompt)
assert [item.content for item in _frozen_plugin_prompt_sections(agent)] == expected
agent._cached_system_prompt = None
assert [item.content for item in _frozen_plugin_prompt_sections(agent)] == expected
resumed = SimpleNamespace()
restore_plugin_prompt_sections(resumed, prompt)
assert [item.content for item in _frozen_plugin_prompt_sections(resumed)] == expected
print(json.dumps({"enabled": True, "preserved": True}))
'''
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ)
            env["HERMES_HOME"] = str(Path(temporary) / "home")
            result = subprocess.run(
                [str(python), "-c", script, str(runtime_root), str(package), RULE, _unslop_expected()],
                capture_output=True, text=True, env=env, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_or_invalid_policy_cannot_silently_disable_injection(self) -> None:
        package = checkout_copy(shared_artifact("codex"), self)
        policy_path = package / "assets/authoring-runtime.json"
        env = dict(os.environ)
        env.pop("PLUGIN_ROOT", None)
        for invalid in (None, {}, {"schema_version": "authoring-runtime.v1", "instructions": ""}):
            with self.subTest(policy=invalid):
                if invalid is None:
                    policy_path.unlink(missing_ok=True)
                else:
                    policy_path.write_text(json.dumps(invalid))
                result = subprocess.run(
                    [sys.executable, str(package / "hooks/inject_authoring.py")],
                    input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                    capture_output=True, text=True, env=env,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("Authoring hook could not load", result.stderr)


if __name__ == "__main__":
    unittest.main()
