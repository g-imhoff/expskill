from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNSLP_PLUGIN = ROOT / "packages" / "opencode" / "plugins" / "unslop.js"
POLICY_PLUGIN = ROOT / "packages" / "opencode" / "plugins" / "execution-policy.js"

NODE = shutil.which("node")
needs_node = unittest.skipUnless(NODE, "node is required for opencode plugin runtime tests")

UNSLP_CASE = r"""
import(%s).then(async (module) => {
  const callableExports = Object.entries(module).filter(([, value]) => typeof value === 'function');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  assert(
    'single-callable-export',
    callableExports.length === 1 && callableExports[0][0] === 'UnslopPlugin'
  );
  const hooks = await module.UnslopPlugin({});
  const transform = hooks['experimental.chat.system.transform'];
  const compacting = hooks['experimental.session.compacting'];

  const firstOutput = { system: ['base instructions'] };
  await transform({ sessionID: 's1' }, firstOutput);
  const firstText = firstOutput.system.join('\n');
  const match = firstText.match(/(?:^|\n)<unslop-scope>\n([\s\S]*?)\n<\/unslop-scope>(?:$|\n)/);
  const openingTags = firstText.match(/^<unslop-scope>$/gm) ?? [];
  const closingTags = firstText.match(/^<\/unslop-scope>$/gm) ?? [];
  assert(
    'well-formed-marker',
    match !== null &&
      openingTags.length === 1 &&
      closingTags.length === 1 &&
      !firstText.includes('<<unslop-scope>>') &&
      !firstText.includes('</<unslop-scope>>')
  );
  const payload = match?.[1] ?? '';
  const completeBlock = `<unslop-scope>\n${payload}\n</unslop-scope>`;
  assert('under-limit', completeBlock.length <= 5000);
  const numberedRules = [...payload.matchAll(/^(\d+)\. \*\*[^*]+\*\*/gm)].map((entry) => Number(entry[1]));
  assert('all-numbered-rules', JSON.stringify(numberedRules) === JSON.stringify(Array.from({ length: 31 }, (_, index) => index + 1)));
  assert('terminal-rule-complete', payload.endsWith('The fancier synonym is rarely clearer.'));
  assert('self-audit-preserved', payload.includes('What makes this obviously AI generated?') && payload.includes('Fix remaining tells.'));

  const firstSnapshot = JSON.stringify(firstOutput.system);
  await transform({ sessionID: 's1' }, firstOutput);
  assert('dedup-current-output', JSON.stringify(firstOutput.system) === firstSnapshot);

  const freshOutput = { system: ['fresh request instructions'] };
  await transform({ sessionID: 's1' }, freshOutput);
  const freshText = freshOutput.system.join('\n');
  assert('inject-fresh-request', freshText.includes('\n<unslop-scope>\n'));
  assert('one-block-per-output', (freshText.match(/<unslop-scope>/g) ?? []).length === 1);

  const context = { context: [] };
  await compacting({}, context);
  assert('compacting', context.context.length === 1 && context.context[0].includes('Preserve the Unslop prose-style rules'));
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

UNSLP_MISSING_SKILL_CASE = """
import(%s).then(async (module) => {
  const hooks = await module.UnslopPlugin({});
  const output = { system: ['base instructions'] };
  await hooks['experimental.chat.system.transform']({ sessionID: 'missing' }, output);
  const unchanged = output.system.length === 1 && output.system[0] === 'base instructions';
  console.log((unchanged ? 'ok:' : 'FAIL:') + 'missing-skill-noop');
  if (!unchanged) process.exitCode = 1;
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

POLICY_CASE = """
import(%s).then(async (module) => {
  const fs = await import('node:fs/promises');
  const policy = JSON.parse(await fs.readFile('packages/codex/assets/execution-policy.json', 'utf8'));
  const budget = module.routePolicy(policy, 'implement', 'standard');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  assert('route-values', budget.maxAgentCalls === 30 && budget.maxConcurrency === 6 && budget.maxRetries === 3 && budget.maxElapsedMs === 7200000);
  const planDesign = module.routePolicy(policy, 'use-expskill', 'parallel-plan-design');
  assert('plan-design-route', planDesign.maxAgentCalls === 2 && planDesign.maxConcurrency === 2 && planDesign.maxRetries === 0 && planDesign.allowedProfiles.length === 2);
  const table = module.routeBudgets(policy);
  assert('route-table', table['expskill-planner'].route === 'use-expskill.parallel-plan-design' && table['expskill-implementer'].route === 'implement.standard');
  const tracker = module.createBudgetTracker(budget);
  for (let index = 0; index < 30; index++) {
    tracker.beforeCall('s', 'expskill-implementer', 1000);
    tracker.afterCall('s');
  }
  assert('thirty-calls', tracker.snapshot('s').calls === 30);
  let blocked = false;
  try { tracker.beforeCall('s', 'expskill-implementer', 1000); } catch { blocked = true; }
  assert('blocks-31st-call', blocked);
  const parallel = module.createBudgetTracker(budget);
  for (let index = 0; index < 6; index++) parallel.beforeCall('c', 'expskill-review', 1000);
  blocked = false;
  try { parallel.beforeCall('c', 'expskill-spec', 1000); } catch { blocked = true; }
  assert('blocks-7th-concurrent', blocked);
  blocked = false;
  try { tracker.beforeCall('s', 'expskill-planner', 1000); } catch { blocked = true; }
  assert('blocks-foreign-agent', blocked);
  blocked = false;
  try { tracker.beforeCall('s', 'expskill-implementer', 1000 + 7200000 + 1); } catch { blocked = true; }
  assert('blocks-elapsed', blocked);
  const retries = module.createBudgetTracker(budget);
  retries.recordRetry('r', 'node-1');
  retries.recordRetry('r', 'node-1');
  retries.recordRetry('r', 'node-1');
  blocked = false;
  try { retries.recordRetry('r', 'node-1'); } catch { blocked = true; }
  assert('blocks-fourth-retry', blocked);
  const hooks = await module.ExecutionPolicyPlugin({});
  assert('hooks', 'tool.execute.before' in hooks && 'tool.execute.after' in hooks);
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""


def run_node_case(
    plugin: Path,
    case: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert NODE is not None
    script = case % repr(plugin.as_uri())
    return subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
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
    def test_unslop_plugin_injects_complete_scope_once_per_request(self) -> None:
        result = run_node_case(UNSLP_PLUGIN, UNSLP_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:single-callable-export",
            "ok:well-formed-marker",
            "ok:under-limit",
            "ok:all-numbered-rules",
            "ok:terminal-rule-complete",
            "ok:self-audit-preserved",
            "ok:dedup-current-output",
            "ok:inject-fresh-request",
            "ok:one-block-per-output",
            "ok:compacting",
        ):
            self.assertIn(token, result.stdout)

    @needs_node
    def test_unslop_plugin_ignores_a_missing_shared_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env["EXPSKILL_HOME"] = temporary
            result = run_node_case(UNSLP_PLUGIN, UNSLP_MISSING_SKILL_CASE, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok:missing-skill-noop", result.stdout)

    @needs_node
    def test_execution_policy_plugin_enforces_shared_budgets(self) -> None:
        result = run_node_case(POLICY_PLUGIN, POLICY_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:route-values",
            "ok:plan-design-route",
            "ok:route-table",
            "ok:thirty-calls",
            "ok:blocks-31st-call",
            "ok:blocks-7th-concurrent",
            "ok:blocks-foreign-agent",
            "ok:blocks-elapsed",
            "ok:blocks-fourth-retry",
            "ok:hooks",
        ):
            self.assertIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main()
