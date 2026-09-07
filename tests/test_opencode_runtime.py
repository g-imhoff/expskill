from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNSLP_PLUGIN = ROOT / "packages" / "opencode" / "plugins" / "unslop.js"
POLICY_PLUGIN = ROOT / "packages" / "opencode" / "plugins" / "execution-policy.js"

NODE = shutil.which("node")
needs_node = unittest.skipUnless(NODE, "node is required for opencode plugin runtime tests")

UNSLP_CASE = """
import(%s).then(async (module) => {
  const hooks = await module.UnslopPlugin({});
  const output = { system: ['base instructions'] };
  const transform = hooks['experimental.chat.system.transform'];
  const compacting = hooks['experimental.session.compacting'];
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  await transform({ sessionID: 's1' }, output);
  assert('inject-once', output.system[0].startsWith('base instructions') && output.system[0].includes('<unslop-scope>'));
  assert('under-limit', output.system[0].length <= 5600);
  const once = output.system[0];
  await transform({ sessionID: 's1' }, output);
  assert('dedup-same-session', output.system[0] === once);
  await transform({ sessionID: 's2' }, output);
  assert('dedup-marker', output.system[0] === once);
  const context = { context: [] };
  await compacting({}, context);
  assert('compacting', context.context.length === 1);
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

POLICY_CASE = """
import(%s).then(async (module) => {
  const fs = await import('node:fs/promises');
  const os = await import('node:os');
  const path = await import('node:path');
  const url = await import('node:url');
  process.env.EXPSKILL_HOME = process.cwd();
  const policy = JSON.parse(await fs.readFile('packages/codex/assets/execution-policy.json', 'utf8'));
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  assert(
    'single-plugin-export',
    Object.keys(module).length === 1 && typeof module.ExecutionPolicyPlugin === 'function',
  );
  const implementPolicy = policy.routes.implement.standard;
  const planDesignPolicy = policy.routes['use-expskill']['parallel-plan-design'];
  assert(
    'route-values',
    implementPolicy.max_agent_calls === 30 &&
      implementPolicy.max_concurrency === 6 &&
      implementPolicy.max_elapsed_ms === 7200000 &&
      planDesignPolicy.max_agent_calls === 2,
  );
  assert(
    'honest-policy-surface',
    [implementPolicy, planDesignPolicy].every(
      (route) => !('max_depth' in route) && !('max_retries' in route),
    ),
  );

  const hooks = await module.ExecutionPolicyPlugin({});
  assert('hooks', 'tool.execute.before' in hooks && 'tool.execute.after' in hooks);
  const before = hooks['tool.execute.before'];
  const after = hooks['tool.execute.after'];
  await before({ tool: 'bash' }, { args: { command: 'true' } });
  assert('ignores-unrelated-tools', true);

  const plannerArgs = { subagent_type: 'expskill-planner' };
  await before({ tool: 'task', sessionID: 'real-shape', callID: 'call-1' }, { args: plannerArgs });
  await after({ tool: 'task', sessionID: 'real-shape', callID: 'call-1', args: plannerArgs }, {});
  assert('allows-output-args-agent', true);
  let blocked = false;
  try {
    await before(
      { tool: 'task', sessionID: 'rejects-agent', callID: 'call-2' },
      { args: { subagent_type: 'general' } },
    );
  } catch { blocked = true; }
  assert('rejects-unlisted-agent', blocked);
  blocked = false;
  try {
    await before({ tool: 'task', sessionID: 'rejects-agent', callID: 'missing-agent' }, { args: {} });
  } catch { blocked = true; }
  assert('rejects-missing-agent', blocked);

  await before({ tool: 'task', sessionID: 'call-limit', callID: 'call-3' }, { args: plannerArgs });
  await after({ tool: 'task', sessionID: 'call-limit', callID: 'call-3', args: plannerArgs }, {});
  await before({ tool: 'task', sessionID: 'call-limit', callID: 'call-4' }, { args: plannerArgs });
  await after({ tool: 'task', sessionID: 'call-limit', callID: 'call-4', args: plannerArgs }, {});
  blocked = false;
  try {
    await before({ tool: 'task', sessionID: 'call-limit', callID: 'call-5' }, { args: plannerArgs });
  } catch { blocked = true; }
  assert('hook-blocks-configured-call-limit', blocked);

  const implementerArgs = { subagent_type: 'expskill-implementer' };
  for (let index = 0; index < 30; index++) {
    const input = { tool: 'task', sessionID: 'implement-call-limit', callID: `implement-${index}` };
    await before(input, { args: implementerArgs });
    await after({ ...input, args: implementerArgs }, {});
  }
  blocked = false;
  try {
    await before(
      { tool: 'task', sessionID: 'implement-call-limit', callID: 'implement-blocked' },
      { args: implementerArgs },
    );
  } catch { blocked = true; }
  assert('hook-blocks-thirty-first-call', blocked);

  for (let index = 0; index < 6; index++) {
    await before(
      { tool: 'task', sessionID: 'hook-concurrency', callID: `parallel-${index}` },
      { args: implementerArgs },
    );
  }
  blocked = false;
  try {
    await before(
      { tool: 'task', sessionID: 'hook-concurrency', callID: 'parallel-blocked' },
      { args: implementerArgs },
    );
  } catch { blocked = true; }
  assert('hook-blocks-configured-concurrency', blocked);
  await after(
    { tool: 'task', sessionID: 'hook-concurrency', callID: 'parallel-0', args: implementerArgs },
    {},
  );
  await before(
    { tool: 'task', sessionID: 'hook-concurrency', callID: 'parallel-after' },
    { args: implementerArgs },
  );
  assert('after-hook-releases-concurrency', true);

  const originalNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    const elapsedHooks = await module.ExecutionPolicyPlugin({});
    const elapsedBefore = elapsedHooks['tool.execute.before'];
    const elapsedAfter = elapsedHooks['tool.execute.after'];
    const first = { tool: 'task', sessionID: 'elapsed', callID: 'elapsed-1' };
    await elapsedBefore(first, { args: implementerArgs });
    await elapsedAfter({ ...first, args: implementerArgs }, {});
    now += implementPolicy.max_elapsed_ms + 1;
    blocked = false;
    try {
      await elapsedBefore(
        { tool: 'task', sessionID: 'elapsed', callID: 'elapsed-2' },
        { args: implementerArgs },
      );
    } catch { blocked = true; }
    assert('hook-blocks-elapsed-budget', blocked);
  } finally {
    Date.now = originalNow;
  }

  const freshHooks = await module.ExecutionPolicyPlugin({});
  await freshHooks['tool.execute.before'](
    { tool: 'task', sessionID: 'call-limit', callID: 'fresh-call' },
    { args: plannerArgs },
  );
  assert('fresh-instance-has-fresh-budget', true);

  const packedRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'packed-opencode-plugin-'));
  try {
    const packedPlugins = path.join(packedRoot, 'plugins');
    const packedAssets = path.join(packedRoot, 'assets');
    await fs.mkdir(packedPlugins, { recursive: true });
    await fs.mkdir(packedAssets, { recursive: true });
    const packedPlugin = path.join(packedPlugins, 'execution-policy.mjs');
    await fs.copyFile('packages/opencode/plugins/execution-policy.js', packedPlugin);
    await fs.copyFile(
      'packages/codex/assets/execution-policy.json',
      path.join(packedAssets, 'execution-policy.json'),
    );
    delete process.env.EXPSKILL_HOME;
    const packedModule = await import(url.pathToFileURL(packedPlugin).href);
    const packedHooks = await packedModule.ExecutionPolicyPlugin({});
    assert(
      'packed-policy-constructor',
      'tool.execute.before' in packedHooks && 'tool.execute.after' in packedHooks,
    );
  } finally {
    process.env.EXPSKILL_HOME = process.cwd();
    await fs.rm(packedRoot, { recursive: true, force: true });
  }

  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'expskill-policy-'));
  try {
    process.env.EXPSKILL_HOME = temporary;
    blocked = false;
    try { await module.ExecutionPolicyPlugin({}); } catch { blocked = true; }
    assert('missing-policy-fails-closed', blocked);
    const assets = path.join(temporary, 'packages', 'codex', 'assets');
    await fs.mkdir(assets, { recursive: true });
    await fs.writeFile(path.join(assets, 'execution-policy.json'), '{invalid', 'utf8');
    blocked = false;
    try { await module.ExecutionPolicyPlugin({}); } catch { blocked = true; }
    assert('malformed-policy-fails-closed', blocked);
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify({ policy_version: 'execution-budget-policy.v1', routes: {} }),
      'utf8',
    );
    blocked = false;
    try { await module.ExecutionPolicyPlugin({}); } catch { blocked = true; }
    assert('invalid-policy-shape-fails-closed', blocked);
    const unsupported = JSON.parse(JSON.stringify(policy));
    unsupported.routes.implement.standard.max_depth = 1;
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(unsupported),
      'utf8',
    );
    blocked = false;
    try { await module.ExecutionPolicyPlugin({}); } catch { blocked = true; }
    assert('unsupported-policy-field-fails-closed', blocked);
  } finally {
    process.env.EXPSKILL_HOME = process.cwd();
    await fs.rm(temporary, { recursive: true, force: true });
  }
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""


def run_node_case(plugin: Path, case: str) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    script = case % repr(plugin.as_uri())
    return subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )


class OpencodeRuntimeTests(unittest.TestCase):
    @needs_node
    def test_plugins_pass_syntax_check(self) -> None:
        assert NODE is not None
        for plugin in (UNSLP_PLUGIN, POLICY_PLUGIN):
            with self.subTest(plugin=plugin.name):
                result = subprocess.run(
                    [NODE, "--check", str(plugin)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    @needs_node
    def test_unslop_plugin_injects_shared_skill_once(self) -> None:
        result = run_node_case(UNSLP_PLUGIN, UNSLP_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:inject-once",
            "ok:under-limit",
            "ok:dedup-same-session",
            "ok:dedup-marker",
            "ok:compacting",
        ):
            self.assertIn(token, result.stdout)

    @needs_node
    def test_execution_policy_plugin_enforces_shared_budgets(self) -> None:
        result = run_node_case(POLICY_PLUGIN, POLICY_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:single-plugin-export",
            "ok:route-values",
            "ok:honest-policy-surface",
            "ok:hooks",
            "ok:ignores-unrelated-tools",
            "ok:allows-output-args-agent",
            "ok:rejects-unlisted-agent",
            "ok:rejects-missing-agent",
            "ok:hook-blocks-configured-call-limit",
            "ok:hook-blocks-thirty-first-call",
            "ok:hook-blocks-configured-concurrency",
            "ok:after-hook-releases-concurrency",
            "ok:hook-blocks-elapsed-budget",
            "ok:fresh-instance-has-fresh-budget",
            "ok:packed-policy-constructor",
            "ok:missing-policy-fails-closed",
            "ok:malformed-policy-fails-closed",
            "ok:invalid-policy-shape-fails-closed",
            "ok:unsupported-policy-field-fails-closed",
        ):
            self.assertIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main()
