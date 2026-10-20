from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
OPENCODE_INDEX = PLUGIN_ROOT / "opencode" / "index.js"
CODEX_HOOKS = PLUGIN_ROOT / "codex" / "hooks"
INJECT_SCRIPT = CODEX_HOOKS / "inject_unslop.py"
HELPER_MODULE = CODEX_HOOKS / "unslop_body.py"
HERMES_HOOK = PLUGIN_ROOT / "content" / "scripts" / "hermes_unslop.py"
CLAUDE_HOOKS = PLUGIN_ROOT / "claude" / "hooks"
CLAUDE_INJECT = CLAUDE_HOOKS / "inject_unslop.py"
CLAUDE_HOOKS_JSON = CLAUDE_HOOKS / "hooks.json"
OPENCODE_V2_PLUGIN = PLUGIN_ROOT / "opencode" / "plugins" / "unslop-v2.ts"
SKILL_MD = PLUGIN_ROOT / "content" / "skills" / "unslop" / "SKILL.md"
POLICY_JSON = PLUGIN_ROOT / "content" / "policies" / "unslop-runtime.json"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _windows(*paths):
    words = re.findall(r"\w+", "".join(p.read_text("utf-8") for p in paths).lower())
    return set(zip(*(words[i:] for i in range(6))))


def _canonical_text() -> str:
    helper = _load_module("unslop_body_canonical", HELPER_MODULE)
    body = helper.skill_body(SKILL_MD.read_text(encoding="utf-8"))
    scope = helper.runtime_scope(POLICY_JSON)
    return scope + body


def _run_inject(payload: dict | None, raw: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(INJECT_SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class StubCtx:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def register_hook(self, name: str, fn: object) -> None:
        self.calls.append((name, fn))


class CodexHookTests(unittest.TestCase):
    def test_session_start_output_is_scope_instruction(self) -> None:
        policy = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
        for source, expected in (
            ("startup", policy["scope"]),
            ("resume", policy["scope"]),
            ("clear", policy["scope"]),
            ("compact", policy["compaction_reminder"]),
        ):
            with self.subTest(source=source):
                result = _run_inject({"hook_event_name": "SessionStart", "source": source})
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(
                    payload["hookSpecificOutput"]["additionalContext"], expected
                )

    def test_non_session_start_is_silent(self) -> None:
        result = _run_inject({"hook_event_name": "UserPromptSubmit", "source": "startup"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_disallowed_source_is_silent(self) -> None:
        result = _run_inject({"hook_event_name": "SessionStart", "source": "other"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_invalid_json_is_silent(self) -> None:
        result = _run_inject(None, raw="{not json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class HermesHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("EXPSKILL_HOME")
        os.environ["EXPSKILL_HOME"] = str(PLUGIN_ROOT)
        self.module = _load_module("hermes_unslop_under_test", HERMES_HOOK)
        self.module.reset_cache()

    def tearDown(self) -> None:
        self.module.reset_cache()
        if self._old_home is None:
            os.environ.pop("EXPSKILL_HOME", None)
        else:
            os.environ["EXPSKILL_HOME"] = self._old_home

    def test_register_wires_both_hooks(self) -> None:
        ctx = StubCtx()
        self.module.register(ctx)
        self.assertEqual(
            [name for name, _ in ctx.calls], ["pre_llm_call", "on_session_start"]
        )

    def test_pre_llm_call_returns_shared_text(self) -> None:
        self.assertEqual(
            self.module.pre_llm_call(session_id="s1"), {"context": _canonical_text()}
        )

    def test_on_session_start_returns_none(self) -> None:
        self.assertIsNone(self.module.on_session_start(session_id="s1"))

    def test_missing_content_fails_soft(self) -> None:
        self.module._candidate_roots = lambda: [Path("/nonexistent-expskill-root")]  # type: ignore[method-assign]
        self.assertIsNone(self.module.pre_llm_call(session_id="s1"))
        self.assertIsNone(self.module.on_session_start(session_id="s1"))


class OpencodePluginTests(unittest.TestCase):
    def test_v2_plugin_registers_context_hooks_with_shared_text(self) -> None:
        text = OPENCODE_V2_PLUGIN.read_text(encoding="utf-8")
        self.assertIn("Plugin.define", text)
        self.assertIn('id: "expskill-unslop"', text)
        self.assertIn('hook("context"', text)
        self.assertNotIn('hook("request"', text)
        self.assertIn("system.push", text)
        self.assertIn("unslop-scope", text)
        self.assertIn("frontmatter", text)

    def test_v2_plugin_has_no_windows(self) -> None:
        self.assertFalse(_windows(SKILL_MD, POLICY_JSON) & _windows(OPENCODE_V2_PLUGIN))


class ClaudeHookTests(unittest.TestCase):
    def _run_claude_inject(self, payload: dict | None, raw: str | None = None):
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        stdin = raw if raw is not None else json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(CLAUDE_INJECT)],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_matcher_covers_all_documented_session_sources(self) -> None:
        matcher = json.loads(CLAUDE_HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["matcher"]
        for source in ("startup", "resume", "clear", "compact", "fork"):
            with self.subTest(source=source):
                self.assertTrue(re.search(matcher, source), f"matcher misses {source}")
        self.assertFalse(re.search(matcher, "bogus"))

    def test_session_start_sources_load_scope_or_reminder(self) -> None:
        policy = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
        for source, expected in (
            ("startup", policy["scope"]),
            ("resume", policy["scope"]),
            ("clear", policy["scope"]),
            ("fork", policy["scope"]),
            ("compact", policy["compaction_reminder"]),
        ):
            with self.subTest(source=source):
                result = self._run_claude_inject({"hook_event_name": "SessionStart", "source": source})
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(
                    payload["hookSpecificOutput"]["additionalContext"], expected
                )

    def test_pre_compact_returns_plain_reminder(self) -> None:
        policy = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
        for trigger in ("manual", "auto"):
            with self.subTest(trigger=trigger):
                result = self._run_claude_inject({"hook_event_name": "PreCompact", "trigger": trigger})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), policy["compaction_reminder"])

    def test_unknown_event_and_invalid_json_are_silent(self) -> None:
        result = self._run_claude_inject({"hook_event_name": "SessionStart", "source": "bogus"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        result = self._run_claude_inject(None, raw="{not json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


NODE = shutil.which("node")
needs_node = unittest.skipUnless(NODE, "node is required for opencode setup tests")

_SETUP_CASE = r"""
import(process.env.EXPSKILL_TEST_OPENCODE_INDEX).then(async (module) => {
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const hooks = {};
  const skills = new Map();
  const ctx = {
    skill: { transform: async (callback) => {
      const editor = {
        get: (id) => skills.get(id),
        add: (skill) => skills.set(skill.id, skill),
        update: (id, apply) => { const skill = skills.get(id); if (skill) apply(skill); },
        list: () => [...skills.values()],
      };
      await callback(editor);
    } },
    session: { hook: async (kind, fn) => { (hooks[kind] ||= []).push(fn); return { dispose() {} }; } },
    command: { transform: async (callback) => { await callback({ add() {} }); } },
    tool: { hook: async () => {} },
  };
  await module.default.setup(ctx);
  assert('setup-registers-context', Array.isArray(hooks.context) && hooks.context.length > 0);
  assert('setup-registers-compaction', Array.isArray(hooks.compaction) && hooks.compaction.length > 0);
  assert('setup-registers-generate', Array.isArray(hooks.generate) && hooks.generate.length > 0);
  const unslop = skills.get('expskill:unslop');
  assert('setup-registers-unslop-skill', !!unslop);
  assert('setup-unslop-autoinvoke', !!unslop && unslop.autoinvoke === true);
  const event = { system: [{ type: 'text', text: 'base' }] };
  for (const fn of hooks.context) await fn(event);
  const text = JSON.stringify(event.system);
  assert('setup-injects-unslop-scope', text.includes('<unslop-scope>') && text.includes('$expskill:unslop'));
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

_SETUP_RESILIENT_CASE = r"""
import(process.env.EXPSKILL_TEST_OPENCODE_INDEX).then(async (module) => {
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const hooks = {};
  const ctx = {
    skill: { transform: async () => { throw new Error('skills unavailable'); } },
    session: { hook: async (kind, fn) => { (hooks[kind] ||= []).push(fn); return { dispose() {} }; } },
    command: { transform: async () => { throw new Error('commands unavailable'); } },
    tool: { hook: async () => {} },
  };
  let error = null;
  try {
    await module.default.setup(ctx);
  } catch (cause) {
    error = cause;
  }
  assert('setup-survives-skill-failure', error === null);
  assert('setup-hooks-despite-skill-failure', Array.isArray(hooks.context) && hooks.context.length > 0);
  const event = { system: [{ type: 'text', text: 'base' }] };
  for (const fn of hooks.context) await fn(event);
  const text = JSON.stringify(event.system);
  assert('setup-injects-despite-skill-failure', text.includes('<unslop-scope>'));
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""


class OpencodeSetupTests(unittest.TestCase):
    def _run(self, case: str) -> "subprocess.CompletedProcess[str]":
        assert NODE is not None
        env = dict(os.environ)
        env["EXPSKILL_TEST_OPENCODE_INDEX"] = OPENCODE_INDEX.as_uri()
        return subprocess.run(
            [NODE, "-e", case],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
            timeout=60,
        )

    @needs_node
    def test_setup_registers_hooks_and_autoinvoke(self) -> None:
        result = self._run(_SETUP_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:setup-registers-context",
            "ok:setup-registers-compaction",
            "ok:setup-registers-generate",
            "ok:setup-registers-unslop-skill",
            "ok:setup-unslop-autoinvoke",
            "ok:setup-injects-unslop-scope",
        ):
            self.assertIn(token, result.stdout)

    @needs_node
    def test_setup_survives_skill_failure(self) -> None:
        result = self._run(_SETUP_RESILIENT_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:setup-survives-skill-failure",
            "ok:setup-hooks-despite-skill-failure",
            "ok:setup-injects-despite-skill-failure",
        ):
            self.assertIn(token, result.stdout)


class SharedTextDriftTests(unittest.TestCase):
    def test_hermes_carries_full_text_starting_with_codex_scope(self) -> None:
        expected = _canonical_text()
        result = _run_inject({"hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(result.returncode, 0, result.stderr)
        codex_text = json.loads(result.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        os.environ["EXPSKILL_HOME"] = str(PLUGIN_ROOT)
        try:
            hermes = _load_module("hermes_unslop_drift_probe", HERMES_HOOK)
            hermes.reset_cache()
            hermes_text = hermes.pre_llm_call(session_id="drift")["context"]
            hermes.reset_cache()
        finally:
            os.environ.pop("EXPSKILL_HOME", None)
        self.assertEqual(hermes_text, expected)
        self.assertTrue(hermes_text.startswith(codex_text))


if __name__ == "__main__":
    unittest.main()
