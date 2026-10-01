from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
CODEX_HOOKS = PLUGIN_ROOT / "codex" / "hooks"
INJECT_SCRIPT = CODEX_HOOKS / "inject_unslop.py"
HELPER_MODULE = CODEX_HOOKS / "unslop_body.py"
HERMES_HOOK = PLUGIN_ROOT / "content" / "scripts" / "hermes_unslop.py"
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
    def test_session_start_output_is_scope_plus_skill_body(self) -> None:
        result = _run_inject({"hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["hookSpecificOutput"]["additionalContext"], _canonical_text()
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
    def test_v2_plugin_registers_request_hook_with_shared_text(self) -> None:
        text = OPENCODE_V2_PLUGIN.read_text(encoding="utf-8")
        self.assertIn("Plugin.define", text)
        self.assertIn('id: "expskill-unslop"', text)
        self.assertIn('hook("request"', text)
        self.assertIn("system.push", text)
        self.assertIn("unslop-scope", text)
        self.assertIn("frontmatter", text)

    def test_v2_plugin_does_not_bake_in_rule_text(self) -> None:
        text = OPENCODE_V2_PLUGIN.read_text(encoding="utf-8")
        self.assertNotIn("What makes this obviously AI generated?", text)
        policy = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
        scope = policy["scope"].strip()
        self.assertNotIn(scope, text)


class SharedTextDriftTests(unittest.TestCase):
    def test_hermes_and_codex_share_byte_identical_rule_text(self) -> None:
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
        self.assertEqual(codex_text, expected)
        self.assertEqual(hermes_text, expected)


if __name__ == "__main__":
    unittest.main()
