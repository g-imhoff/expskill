"""V2 setup contract for the OpenCode package.

The V1 ``server()`` hook map (``config`` / ``experimental.*`` /
``tool.execute.*``) does not run on OpenCode 2.x. The package must also
expose ``setup(ctx)`` registering skills, commands, agents, session hooks,
and tool hooks through the V2 domain APIs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_opencode_package import build_opencode_package
from scripts.render_opencode import skill_inventory

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
needs_node = unittest.skipUnless(NODE, "node is required for opencode V2 setup tests")

V2_CASE = r"""
import(%s).then(async (module) => {
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  assert(
    'dual-export-shape',
    typeof module.default?.setup === 'function' &&
      typeof module.default?.server === 'function' &&
      module.default.id === 'opencode-expskill' &&
      typeof module.ExpSkillSetup === 'function',
  );
  const skills = new Map();
  const commands = new Map();
  const agentUpdates = [];
  const prompts = [];
  const hooks = { session: [], tool: [] };
  const fakes = {};
  const fakeCtx = {
    skill: {
      transform: async (cb) => {
        const editor = {
          get: (id) => skills.get(id),
          add: (skill) => { skills.set(skill.id, skill); },
          update: () => {},
          remove: () => {},
          list: () => [...skills.values()],
        };
        cb(editor);
      },
    },
    command: {
      transform: async (cb) => {
        const editor = { add: (definition) => { commands.set(definition.name, definition); } };
        cb(editor);
      },
    },
    agent: {
      transform: async (cb) => {
        const store = new Map([
          ['expskill-planner', {
            description: 'old', mode: 'subagent',
            model: { providerID: 'opencode-go', id: 'muse-spark-1.3-contributor' },
            system: 'old', permissions: [],
            request: { settings: {}, headers: {}, body: {} },
          }],
        ]);
        const editor = {
          get: (id) => store.get(id),
          update: (id, update) => { const draft = store.get(id); update(draft); agentUpdates.push(id); },
          remove: () => {}, list: () => [...store.values()], default: () => {},
        };
        cb(editor);
        fakes.agentStore = store;
      },
    },
    session: {
      hook: async (name, cb) => { hooks.session.push(name); fakes['session:' + name] = cb; },
      prompt: async (input) => { prompts.push(input); return {}; },
    },
    tool: {
      hook: async (name, cb) => { hooks.tool.push(name); fakes['tool:' + name] = cb; },
    },
  };
  await module.default.setup(fakeCtx);
  const expectedSkills = %s;
  assert('all-skills-registered', JSON.stringify([...skills.keys()].sort()) === JSON.stringify(expectedSkills));
  assert('grill-me-registered', skills.has('grill-me') && skills.get('grill-me').description.length > 0);
  assert('autonomous-run-registered', skills.has('autonomous-run'));
  assert(
    'skill-paths-and-content',
    [...skills.values()].every((skill) => typeof skill.path === 'string' && skill.path.endsWith('SKILL.md') && skill.content.length > 0),
  );
  assert(
    'explicit-only-autoinvoke',
    [...skills.values()].filter((skill) => skill.id !== 'use-expskill').every((skill) => skill.autoinvoke === false),
  );
  assert('all-commands-registered', JSON.stringify([...commands.keys()].sort()) === JSON.stringify(expectedSkills));
  await commands.get('grill-me').execute({ sessionID: 's1', prompt: { text: 'my design' }, delivery: 'steer' });
  assert(
    'command-executes-prompt',
    prompts.length === 1 && prompts[0].sessionID === 's1' && prompts[0].text.includes('my design') && prompts[0].text.includes('grill-me'),
  );
  assert(
    'session-hooks',
    hooks.session.includes('context') && hooks.session.includes('compaction'),
  );
  const contextEvent = { system: [] };
  await fakes['session:context'](contextEvent);
  assert(
    'context-injects-unslop',
    contextEvent.system.length === 1 && contextEvent.system[0].text.includes('<unslop-scope>'),
  );
  const compactionEvent = { system: [] };
  await fakes['session:compaction'](compactionEvent);
  assert('compaction-reminder', compactionEvent.system.length === 1);
  assert(
    'tool-hooks',
    hooks.tool.includes('execute.before') && hooks.tool.includes('execute.after'),
  );
  const before = fakes['tool:execute.before'];
  const after = fakes['tool:execute.after'];
  await before({ tool: 'subagent', sessionID: 'policy', id: 'call-1', input: { subagent_type: 'expskill-planner' } });
  await after({ tool: 'subagent', sessionID: 'policy', id: 'call-1', input: { subagent_type: 'expskill-planner' }, status: 'completed', result: {} });
  assert('policy-allows-declared-agent', true);
  let blocked = false;
  try {
    await before({ tool: 'subagent', sessionID: 'policy', id: 'call-2', input: { subagent_type: 'expskill-undeclared' } });
  } catch { blocked = true; }
  assert('policy-denies-undeclared-agent', blocked);
  await before({ tool: 'read', sessionID: 'policy', id: 'call-3', input: {} });
  assert('policy-ignores-unrelated-tools', true);
  assert('agent-update-attempted', agentUpdates.includes('expskill-planner'));
  const updated = fakes.agentStore.get('expskill-planner');
  assert(
    'agent-update-v2-shape',
    typeof updated.system === 'string' && updated.system.length > 0 &&
      Array.isArray(updated.permissions) && updated.permissions.length > 0 &&
      updated.model.providerID === 'opencode-go' && typeof updated.model.id === 'string',
  );
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""


def run_node_case(plugin: Path, expected: list[str]) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    script = V2_CASE % (repr(plugin.as_uri()), expected)
    return subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )


class OpencodeV2SetupTests(unittest.TestCase):
    def test_v2_setup_registers_skills_commands_agents_and_hooks(self) -> None:
        if not NODE:
            self.skipTest("node is required")
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact"
            build_opencode_package(ROOT, artifact)
            expected = sorted(skill_inventory(ROOT))
            self.assertIn("grill-me", expected)
            self.assertIn("autonomous-run", expected)
            result = run_node_case(artifact / "index.js", expected)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:dual-export-shape",
            "ok:all-skills-registered",
            "ok:grill-me-registered",
            "ok:autonomous-run-registered",
            "ok:skill-paths-and-content",
            "ok:explicit-only-autoinvoke",
            "ok:all-commands-registered",
            "ok:command-executes-prompt",
            "ok:session-hooks",
            "ok:context-injects-unslop",
            "ok:compaction-reminder",
            "ok:tool-hooks",
            "ok:policy-allows-declared-agent",
            "ok:policy-denies-undeclared-agent",
            "ok:policy-ignores-unrelated-tools",
            "ok:agent-update-attempted",
            "ok:agent-update-v2-shape",
        ):
            self.assertIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main()
