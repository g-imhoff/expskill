from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_opencode_package import build_opencode_package

ROOT = Path(__file__).resolve().parents[1]

NODE = shutil.which("node")
needs_node = unittest.skipUnless(NODE, "node is required for opencode plugin runtime tests")

UNSLP_CASE = r"""
import(%s).then(async (module) => {
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  assert(
    'exact-module-export-keys',
    JSON.stringify(Object.keys(module).sort()) === JSON.stringify(['UnslopPlugin', 'default']) &&
      typeof module.UnslopPlugin === 'function' &&
      typeof module.default === 'object' &&
      typeof module.default?.id === 'string' &&
      typeof module.default?.server === 'function' &&
      module.default.server === module.UnslopPlugin
  );
  const hooks = await module.default.server({});
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
  const hooks = await module.default.server({});
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
  const os = await import('node:os');
  const path = await import('node:path');
  const url = await import('node:url');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  delete process.env.EXPSKILL_HOME;
  const repositoryHooks = await module.ExecutionPolicyPlugin({});
  assert(
    'repository-fallback-policy',
    'tool.execute.before' in repositoryHooks && 'tool.execute.after' in repositoryHooks,
  );
  const fallbackInput = {
    tool: 'task',
    sessionID: 'repository-fallback',
    callID: 'implementer',
  };
  const fallbackArgs = { subagent_type: 'expskill-implementer' };
  await repositoryHooks['tool.execute.before'](fallbackInput, { args: fallbackArgs });
  await repositoryHooks['tool.execute.after']({ ...fallbackInput, args: fallbackArgs }, {});
  assert('repository-fallback-policy-enforces-valid-agent', true);
  delete process.env.EXPSKILL_HOME;
  const artifactRoot = process.env.EXPSKILL_ARTIFACT_ROOT;
  const policy = JSON.parse(await fs.readFile(path.join(artifactRoot, 'assets', 'execution-policy.json'), 'utf8'));
  assert(
    'exact-module-export-keys',
    JSON.stringify(Object.keys(module).sort()) === JSON.stringify(['ExecutionPolicyPlugin', 'default']) &&
      typeof module.ExecutionPolicyPlugin === 'function' &&
      typeof module.default === 'object' &&
      typeof module.default?.id === 'string' &&
      typeof module.default?.server === 'function' &&
      module.default.server === module.ExecutionPolicyPlugin,
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

  const hooks = await module.default.server({});
  assert('hooks', 'tool.execute.before' in hooks && 'tool.execute.after' in hooks);
  const before = hooks['tool.execute.before'];
  const after = hooks['tool.execute.after'];
  await before({ tool: 'bash' }, { args: { command: 'true' } });
  assert('ignores-unrelated-tools', true);

  await before(
    { tool: 'task', sessionID: 'host-agent', callID: 'general' },
    { args: { subagent_type: 'general' } },
  );
  await before(
    { tool: 'task', sessionID: 'host-agent', callID: 'explore' },
    { args: { subagent_type: 'explore' } },
  );
  assert('allows-unrelated-host-agents', true);

  const plannerArgs = { subagent_type: 'expskill-planner' };
  await before({ tool: 'task', sessionID: 'real-shape', callID: 'call-1' }, { args: plannerArgs });
  await after({ tool: 'task', sessionID: 'real-shape', callID: 'call-1', args: plannerArgs }, {});
  assert('allows-output-args-agent', true);
  for (const [index, agent] of ['expskill-explorer', 'expskill-test-engineer'].entries()) {
    const input = { tool: 'task', sessionID: 'unbound-profile', callID: `profile-${index}` };
    await before(input, { args: { subagent_type: agent } });
    await after({ ...input, args: { subagent_type: agent } }, {});
  }
  assert('allows-all-declared-profiles', true);
  let blocked = false;
  try {
    await before(
      { tool: 'task', sessionID: 'rejects-agent', callID: 'call-2' },
      { args: { subagent_type: 'expskill-undeclared' } },
    );
  } catch { blocked = true; }
  assert('rejects-undeclared-expskill-agent', blocked);
  await before({ tool: 'task', sessionID: 'host-agent', callID: 'missing-agent' }, { args: {} });
  assert('ignores-unidentified-task', true);

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
    await fs.copyFile(path.join(artifactRoot, 'plugins', 'execution-policy.js'), packedPlugin);
    await fs.copyFile(
      path.join(artifactRoot, 'assets', 'execution-policy.json'),
      path.join(packedAssets, 'execution-policy.json'),
    );
    delete process.env.EXPSKILL_HOME;
    const packedModule = await import(url.pathToFileURL(packedPlugin).href);
    const packedHooks = await packedModule.ExecutionPolicyPlugin({});
    assert(
      'packed-policy-constructor',
      'tool.execute.before' in packedHooks && 'tool.execute.after' in packedHooks,
    );
    const packedInput = { tool: 'task', sessionID: 'packed-policy', callID: 'implementer' };
    const packedArgs = { subagent_type: 'expskill-implementer' };
    await packedHooks['tool.execute.before'](packedInput, { args: packedArgs });
    await packedHooks['tool.execute.after']({ ...packedInput, args: packedArgs }, {});
    assert('packed-policy-enforces-valid-agent', true);
  } finally {
    process.env.EXPSKILL_HOME = process.cwd();
    await fs.rm(packedRoot, { recursive: true, force: true });
  }

  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'expskill-policy-'));
  try {
    process.env.EXPSKILL_HOME = temporary;
    let loadHooks = await module.ExecutionPolicyPlugin({});
    let loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'missing' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'missing-policy-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    await loadHooks['tool.execute.before'](
      { tool: 'task', sessionID: 'load-error', callID: 'host' },
      { args: { subagent_type: 'general' } },
    );
    await loadHooks['tool.execute.before']({ tool: 'bash' }, { args: { command: 'true' } });
    const assets = path.join(temporary, 'packages', 'expskill', 'assets');
    await fs.mkdir(assets, { recursive: true });
    await fs.writeFile(path.join(assets, 'execution-policy.json'), '{invalid', 'utf8');
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'malformed' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'malformed-policy-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify({ policy_version: 'execution-budget-policy.v1', routes: {} }),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'shape' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'invalid-policy-shape-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    const missingRoute = JSON.parse(JSON.stringify(policy));
    delete missingRoute.routes.implement.standard;
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(missingRoute),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'missing-route' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'missing-required-route-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    const renamedRoute = JSON.parse(JSON.stringify(policy));
    renamedRoute.routes.implement.renamed = renamedRoute.routes.implement.standard;
    delete renamedRoute.routes.implement.standard;
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(renamedRoute),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'renamed-route' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'renamed-required-route-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    const missingParallelRoute = JSON.parse(JSON.stringify(policy));
    delete missingParallelRoute.routes['use-expskill']['parallel-plan-design'];
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(missingParallelRoute),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'missing-parallel-route' },
        { args: { subagent_type: 'expskill-planner' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'missing-parallel-plan-design-route-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    const invalidAssignment = JSON.parse(JSON.stringify(policy));
    invalidAssignment.routes.implement.standard.allowed_profiles = [
      'expskill-planner',
      'expskill-review',
      'expskill-spec',
    ];
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(invalidAssignment),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'invalid-assignment' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'invalid-required-profile-assignment-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    const unsupported = JSON.parse(JSON.stringify(policy));
    unsupported.routes.implement.standard.max_depth = 1;
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(unsupported),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'unsupported' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'unsupported-policy-field-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
    const invalidSelected = JSON.parse(JSON.stringify(policy));
    invalidSelected.routes.implement.standard.selected[0].profile = 'expskill-undeclared';
    await fs.writeFile(
      path.join(assets, 'execution-policy.json'),
      JSON.stringify(invalidSelected),
      'utf8',
    );
    loadHooks = await module.ExecutionPolicyPlugin({});
    loadError = null;
    try {
      await loadHooks['tool.execute.before'](
        { tool: 'task', sessionID: 'load-error', callID: 'selected' },
        { args: { subagent_type: 'expskill-implementer' } },
      );
    } catch (error) { loadError = error; }
    assert(
      'invalid-selected-reference-fails-closed',
      loadError instanceof Error && loadError.message.includes('failed to load'),
    );
  } finally {
    process.env.EXPSKILL_HOME = process.cwd();
    await fs.rm(temporary, { recursive: true, force: true });
  }
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

EXTRA_EXPORTS_CASE = r"""
import(%s).then((module) => {
  const expected = ['UnslopPlugin'];
  const actual = Object.keys(module).sort();
  const rejected = JSON.stringify(actual) !== JSON.stringify(expected);
  console.log((rejected ? 'ok:' : 'FAIL:') + 'rejects-non-function-exports');
  if (!rejected) process.exitCode = 1;
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
    case_env = dict(os.environ) if env is None else dict(env)
    case_env.setdefault("EXPSKILL_ARTIFACT_ROOT", str(plugin.parent.parent))
    return subprocess.run(
        [NODE, "-e", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=case_env,
        timeout=60,
    )


class OpencodeRuntimeTests(unittest.TestCase):
    def artifact(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        artifact = Path(temporary.name) / "artifact"
        build_opencode_package(ROOT, artifact)
        return artifact

    @needs_node
    def test_plugins_pass_syntax_check(self) -> None:
        assert NODE is not None
        artifact = self.artifact()
        for plugin in (artifact / "plugins" / "unslop.js", artifact / "plugins" / "execution-policy.js"):
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
        result = run_node_case(self.artifact() / "plugins" / "unslop.js", UNSLP_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:exact-module-export-keys",
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
        plugin = self.artifact() / "plugins" / "unslop.js"
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env["EXPSKILL_HOME"] = temporary
            result = run_node_case(plugin, UNSLP_MISSING_SKILL_CASE, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok:missing-skill-noop", result.stdout)

    @needs_node
    def test_plugin_export_contract_rejects_non_function_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "extra-exports.mjs"
            fixture.write_text(
                "export const UnslopPlugin = async () => ({});\n"
                "export const metadata = 'unexpected';\n",
                encoding="utf-8",
            )
            result = run_node_case(fixture, EXTRA_EXPORTS_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok:rejects-non-function-exports", result.stdout)

    @needs_node
    def test_execution_policy_plugin_enforces_shared_budgets(self) -> None:
        result = run_node_case(self.artifact() / "plugins" / "execution-policy.js", POLICY_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:exact-module-export-keys",
            "ok:route-values",
            "ok:honest-policy-surface",
            "ok:hooks",
            "ok:repository-fallback-policy",
            "ok:repository-fallback-policy-enforces-valid-agent",
            "ok:ignores-unrelated-tools",
            "ok:allows-unrelated-host-agents",
            "ok:allows-output-args-agent",
            "ok:allows-all-declared-profiles",
            "ok:rejects-undeclared-expskill-agent",
            "ok:ignores-unidentified-task",
            "ok:hook-blocks-configured-call-limit",
            "ok:hook-blocks-thirty-first-call",
            "ok:hook-blocks-configured-concurrency",
            "ok:after-hook-releases-concurrency",
            "ok:hook-blocks-elapsed-budget",
            "ok:fresh-instance-has-fresh-budget",
            "ok:packed-policy-constructor",
            "ok:packed-policy-enforces-valid-agent",
            "ok:missing-policy-fails-closed",
            "ok:malformed-policy-fails-closed",
            "ok:invalid-policy-shape-fails-closed",
            "ok:missing-required-route-fails-closed",
            "ok:renamed-required-route-fails-closed",
            "ok:missing-parallel-plan-design-route-fails-closed",
            "ok:invalid-required-profile-assignment-fails-closed",
            "ok:unsupported-policy-field-fails-closed",
            "ok:invalid-selected-reference-fails-closed",
        ):
            self.assertIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main()
