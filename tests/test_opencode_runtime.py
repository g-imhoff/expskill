from __future__ import annotations

import json
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
  assert('load-skill-at-start', payload.includes('Always load $expskill:unslop at the start of the conversation'));
  assert('load-skill-after-compaction', payload.includes('after each compaction, before writing user-facing prose'));
  assert('compact-load-instruction', completeBlock.length < 500 && !payload.includes('# Unslop'));

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
  assert('compacting', context.context.length === 1 && context.context[0].includes('After this compaction, reload $expskill:unslop before writing user-facing prose'));
  const afterCompaction = { system: ['compacted request instructions'] };
  await transform({ sessionID: 's1' }, afterCompaction);
  assert('reload-after-compaction', afterCompaction.system[0].includes('Always load $expskill:unslop'));
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
      implementPolicy.max_depth === 1 &&
      implementPolicy.max_retries === 3 &&
      planDesignPolicy.max_agent_calls === 2 &&
      planDesignPolicy.max_depth === 1 &&
      planDesignPolicy.max_retries === 0,
  );
  assert(
    'complete-policy-surface',
    [implementPolicy, planDesignPolicy].every(
      (route) => 'max_depth' in route && 'max_retries' in route,
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

  const runRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'execution-run-budget-'));
  try {
    process.env.EXPSKILL_RUN_ID = 'audit-resume';
    process.env.EXPSKILL_RUN_BUDGET_DIR = runRoot;
    for (let index = 0; index < 30; index += 1) {
      const runHooks = await module.ExecutionPolicyPlugin({});
      const input = { tool: 'task', sessionID: `resume-${index}`, callID: `run-${index}` };
      await runHooks['tool.execute.before'](input, { args: implementerArgs });
      await runHooks['tool.execute.after']({ ...input, args: implementerArgs }, {});
    }
    let blocked = false;
    const resumed = await module.ExecutionPolicyPlugin({});
    try {
      await resumed['tool.execute.before']({ tool: 'task', sessionID: 'last-resume', callID: 'run-31' }, { args: implementerArgs });
    } catch (error) { blocked = String(error).includes('cumulative run budget exhausted'); }
    assert('resumed-instances-share-thirty-call-budget', blocked);
    const files = (await fs.readdir(runRoot)).filter((name) => name.endsWith('.json'));
    const state = JSON.parse(await fs.readFile(path.join(runRoot, files[0]), 'utf8'));
    assert('persisted-count-is-thirty', state.calls === 30);
    const child = await import('node:child_process');
    const childScript = `import { ExecutionPolicyPlugin } from ${JSON.stringify(url.pathToFileURL(path.join(artifactRoot, 'plugins', 'execution-policy.js')).href)}; const hooks = await ExecutionPolicyPlugin({}); try { await hooks['tool.execute.before']({tool:'task',sessionID:'process-restart',callID:'process-restart'},{args:{subagent_type:'expskill-implementer'}}); process.exit(1); } catch (error) { process.exit(String(error).includes('cumulative run budget exhausted') ? 0 : 2); }`;
    const restart = child.spawnSync(process.execPath, ['--input-type=module', '-e', childScript], { env: process.env });
    assert('process-restart-preserves-run-budget', restart.status === 0);
    await fs.writeFile(path.join(runRoot, files[0]), '{}');
    blocked = false;
    try {
      const corrupt = await module.ExecutionPolicyPlugin({});
      await corrupt['tool.execute.before']({ tool: 'task', sessionID: 'corrupt', callID: 'corrupt' }, { args: implementerArgs });
    } catch (error) { blocked = String(error).includes('run budget state is invalid'); }
    assert('corrupt-run-budget-fails-closed', blocked);
    process.env.EXPSKILL_RUN_ID = '../escape';
    const unsafe = await module.ExecutionPolicyPlugin({});
    blocked = false;
    try { await unsafe['tool.execute.before']({ tool: 'task', sessionID: 'unsafe', callID: 'unsafe' }, { args: implementerArgs }); }
    catch (error) { blocked = String(error).includes('safe EXPSKILL_RUN_ID'); }
    assert('unsafe-run-identity-fails-closed', blocked);
  } finally {
    delete process.env.EXPSKILL_RUN_ID;
    delete process.env.EXPSKILL_RUN_BUDGET_DIR;
    await fs.rm(runRoot, { recursive: true, force: true });
  }

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
    const assets = path.join(temporary, 'plugins', 'expskill', 'assets');
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
    unsupported.routes.implement.standard.unexpected = true;
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
    for (const [label, field, value] of [
      ['invalid-max-depth', 'max_depth', -1],
      ['invalid-max-retries', 'max_retries', 1.5],
    ]) {
      const invalidBudget = JSON.parse(JSON.stringify(policy));
      invalidBudget.routes.implement.standard[field] = value;
      await fs.writeFile(
        path.join(assets, 'execution-policy.json'),
        JSON.stringify(invalidBudget),
        'utf8',
      );
      loadHooks = await module.ExecutionPolicyPlugin({});
      loadError = null;
      try {
        await loadHooks['tool.execute.before'](
          { tool: 'task', sessionID: 'load-error', callID: label },
          { args: { subagent_type: 'expskill-implementer' } },
        );
      } catch (error) { loadError = error; }
      assert(
        `${label}-fails-closed`,
        loadError instanceof Error && loadError.message.includes('failed to load'),
      );
    }
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

NATIVE_ROOT_CASE = r"""
import(%s).then(async (module) => {
  const fs = await import('node:fs/promises');
  const path = await import('node:path');
  const url = await import('node:url');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const expectedAgent = (value) => {
    const copy = clone(value);
    const { reasoningEffort } = copy;
    delete copy.reasoningEffort;
    copy.options = { reasoningEffort };
    return copy;
  };
  const pluginUrl = process.env.EXPSKILL_TEST_PLUGIN_URL;
  const packageRoot = path.dirname(url.fileURLToPath(pluginUrl));
  const catalog = JSON.parse(await fs.readFile(path.join(packageRoot, 'catalog.json'), 'utf8'));
  const expectedCommands = Object.keys(catalog.commands).sort();
  const expectedAgents = Object.keys(catalog.agents).sort();
  assert(
    'root-export-shape',
    JSON.stringify(Object.keys(module).sort()) ===
      JSON.stringify(['ExecutionPolicyPlugin', 'ExpSkillPlugin', 'UnslopPlugin', 'default']) &&
      typeof module.ExpSkillPlugin === 'function' &&
      typeof module.ExecutionPolicyPlugin === 'function' &&
      typeof module.UnslopPlugin === 'function' &&
      typeof module.default === 'object' &&
      module.default.id === 'opencode-expskill' &&
      module.default.server === module.ExpSkillPlugin,
  );

  const hooks = await module.default.server({});
  assert(
    'composed-hooks',
    typeof hooks.config === 'function' &&
      typeof hooks['experimental.chat.system.transform'] === 'function' &&
      typeof hooks['experimental.session.compacting'] === 'function' &&
      typeof hooks['tool.execute.before'] === 'function' &&
      typeof hooks['tool.execute.after'] === 'function',
  );
  assert(
    'catalog-inventory-shape',
    expectedCommands.length === 15 && expectedAgents.length === 7,
  );

  const userCommand = { description: 'user command', template: 'user template' };
  const userAgent = { description: 'user agent', mode: 'primary', prompt: 'user prompt' };
  const config = {
    $schema: 'https://opencode.ai/config.json',
    model: 'user/provider-model',
    permission: { edit: 'deny' },
    command: { [expectedCommands[0]]: userCommand, unrelated: { template: 'keep' } },
    agent: { [expectedAgents[0]]: userAgent, unrelated: { mode: 'primary' } },
    skills: { paths: [path.join(packageRoot, 'custom-skills')] },
  };
  const originalUnrelated = clone({
    $schema: config.$schema,
    model: config.model,
    permission: config.permission,
    unrelatedCommand: config.command.unrelated,
    unrelatedAgent: config.agent.unrelated,
    customSkillPath: config.skills.paths[0],
  });
  await hooks.config(config);
  assert(
    'all-catalog-commands',
    Object.keys(config.command).filter((name) => name !== 'unrelated').length === expectedCommands.length &&
      expectedCommands.every((name) => name in config.command),
  );
  assert(
    'all-catalog-agents',
    Object.keys(config.agent).filter((name) => name !== 'unrelated').length === expectedAgents.length &&
      expectedAgents.every((name) => name in config.agent),
  );
  assert(
    'catalog-values-preserved',
    expectedCommands.filter((name) => name !== expectedCommands[0]).every(
      (name) => JSON.stringify(config.command[name]) === JSON.stringify(catalog.commands[name]),
    ) &&
      expectedAgents.filter((name) => name !== expectedAgents[0]).every(
        (name) => JSON.stringify(config.agent[name]) === JSON.stringify(expectedAgent(catalog.agents[name])),
      ),
  );
  assert('user-command-wins', config.command[expectedCommands[0]] === userCommand);
  assert('user-agent-wins', config.agent[expectedAgents[0]] === userAgent);
  const bundledSkills = path.join(packageRoot, 'skills');
  assert(
    'bundled-skills-path',
    config.skills.paths.filter((entry) => entry === bundledSkills).length === 1 &&
      config.skills.paths.at(-1) === bundledSkills &&
      !config.skills.paths.some((entry) => entry.includes('/plugins/expskill/skills')),
  );
  const duplicatePathConfig = {
    command: {},
    agent: {},
    skills: { paths: [bundledSkills, bundledSkills, bundledSkills + '/'] },
  };
  await hooks.config(duplicatePathConfig);
  assert(
    'bundled-skills-path-deduplicated',
    JSON.stringify(duplicatePathConfig.skills.paths) === JSON.stringify([bundledSkills]),
  );
  const duplicatePathSnapshot = JSON.stringify(duplicatePathConfig);
  await hooks.config(duplicatePathConfig);
  assert(
    'bundled-skills-path-idempotent',
    JSON.stringify(duplicatePathConfig) === duplicatePathSnapshot,
  );
  assert(
    'unrelated-config-preserved',
    JSON.stringify({
      $schema: config.$schema,
      model: config.model,
      permission: config.permission,
      unrelatedCommand: config.command.unrelated,
      unrelatedAgent: config.agent.unrelated,
      customSkillPath: config.skills.paths[0],
    }) === JSON.stringify(originalUnrelated),
  );
  const firstSnapshot = JSON.stringify(config);
  await hooks.config(config);
  assert('config-hook-idempotent', JSON.stringify(config) === firstSnapshot);

  const output = { system: ['base instructions'] };
  await hooks['experimental.chat.system.transform']({ sessionID: 'root-unslop' }, output);
  assert('composed-unslop-effect', output.system.join('\n').includes('<unslop-scope>'));
  const context = { context: [] };
  await hooks['experimental.session.compacting']({ sessionID: 'root-compact' }, context);
  assert('composed-compacting-effect', context.context.length === 2);
  const policyArgs = { subagent_type: 'expskill-implementer' };
  await hooks['tool.execute.before'](
    { tool: 'task', sessionID: 'root-policy', callID: 'valid' },
    { args: policyArgs },
  );
  await hooks['tool.execute.after'](
    { tool: 'task', sessionID: 'root-policy', callID: 'valid', args: policyArgs },
    {},
  );
  let blocked = false;
  try {
    await hooks['tool.execute.before'](
      { tool: 'task', sessionID: 'root-policy', callID: 'invalid' },
      { args: { subagent_type: 'expskill-undeclared' } },
    );
  } catch { blocked = true; }
  assert('composed-policy-effect', blocked);
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

NATIVE_AGENT_OPTIONS_CASE = r"""
import(%s).then(async (module) => {
  const fs = await import('node:fs/promises');
  const path = await import('node:path');
  const url = await import('node:url');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const pluginUrl = process.env.EXPSKILL_TEST_PLUGIN_URL;
  const packageRoot = path.dirname(url.fileURLToPath(pluginUrl));
  const catalog = JSON.parse(await fs.readFile(path.join(packageRoot, 'catalog.json'), 'utf8'));
  const catalogSnapshot = JSON.stringify(catalog);
  const hooks = await module.default.server({});
  const bundledConfig = { command: {}, agent: {}, skills: { paths: [] } };
  await hooks.config(bundledConfig);
  for (const [name, source] of Object.entries(catalog.agents)) {
    const published = bundledConfig.agent[name];
    const options = published?.options;
    assert(
      `${name}-options-plain-object`,
      options !== null && typeof options === 'object' && !Array.isArray(options) &&
        Object.getPrototypeOf(options) === Object.prototype,
    );
    assert(
      `${name}-options-exact-reasoning-effort`,
      JSON.stringify(Object.keys(options ?? {}).sort()) === JSON.stringify(['reasoningEffort']) &&
        options?.reasoningEffort === source.reasoningEffort,
    );
    assert(
      `${name}-no-direct-reasoning-effort`,
      !Object.prototype.hasOwnProperty.call(published ?? {}, 'reasoningEffort'),
    );
  }
  assert('loaded-catalog-not-mutated', JSON.stringify(catalog) === catalogSnapshot);

  const collisionName = Object.keys(catalog.agents)[0];
  const userOptions = { reasoningEffort: 'user-option' };
  const userAgent = {
    description: 'user agent',
    mode: 'primary',
    prompt: 'user prompt',
    reasoningEffort: 'user-direct',
    options: userOptions,
  };
  const collisionConfig = {
    command: {},
    agent: { [collisionName]: userAgent },
    skills: { paths: [] },
  };
  await hooks.config(collisionConfig);
  assert(
    'explicit-agent-collision-untouched',
    collisionConfig.agent[collisionName] === userAgent &&
      collisionConfig.agent[collisionName].reasoningEffort === 'user-direct' &&
      collisionConfig.agent[collisionName].options === userOptions,
  );
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

NATIVE_ROOT_MALFORMED_CASE = r"""
import(%s).then(async (module) => {
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const hooks = await module.default.server({});
  const config = {
    $schema: 'https://opencode.ai/config.json',
    model: 'user/provider-model',
    command: { keep: { template: 'keep' } },
    agent: { keep: { mode: 'primary' } },
    skills: { paths: ['/tmp/user-skills'] },
  };
  const snapshot = JSON.stringify(config);
  let error = null;
  try {
    await hooks.config(config);
  } catch (cause) { error = cause; }
  assert('malformed-catalog-does-not-throw', error === null);
  assert('malformed-catalog-no-partial-registration', JSON.stringify(config) === snapshot);
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

NATIVE_ROOT_CATALOG_REJECTION_CASE = r"""
import(%s).then(async (module) => {
  const fs = await import('node:fs/promises');
  const path = await import('node:path');
  const url = await import('node:url');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const pluginUrl = process.env.EXPSKILL_TEST_PLUGIN_URL;
  const packageRoot = path.dirname(url.fileURLToPath(pluginUrl));
  JSON.parse(await fs.readFile(path.join(packageRoot, 'catalog.json'), 'utf8'));
  const hooks = await module.default.server({});
  const command = { keep: { template: 'keep' } };
  const agent = { keep: { mode: 'primary' } };
  const skills = { paths: ['/tmp/user-skills'] };
  const config = { command, agent, skills, marker: { keep: true } };
  const refs = { config, command, agent, skills, marker: config.marker };
  const snapshot = JSON.stringify(config);
  let error = null;
  try { await hooks.config(config); } catch (cause) { error = cause; }
  assert('catalog-rejection-does-not-throw', error === null);
  assert(
    'catalog-rejection-is-transactional',
    JSON.stringify(config) === snapshot &&
      config.command === refs.command &&
      config.agent === refs.agent &&
      config.skills === refs.skills &&
      config.marker === refs.marker,
  );
}).catch((error) => { console.error('FAIL:load', error); process.exit(1); });
"""

NATIVE_ROOT_CONFIG_BOUNDARIES_CASE = r"""
import(%s).then(async (module) => {
  const fs = await import('node:fs/promises');
  const path = await import('node:path');
  const url = await import('node:url');
  const assert = (name, condition) => {
    console.log((condition ? 'ok:' : 'FAIL:') + name);
    if (!condition) process.exitCode = 1;
  };
  const pluginUrl = process.env.EXPSKILL_TEST_PLUGIN_URL;
  const packageRoot = path.dirname(url.fileURLToPath(pluginUrl));
  const catalog = JSON.parse(await fs.readFile(path.join(packageRoot, 'catalog.json'), 'utf8'));
  const commandName = Object.keys(catalog.commands)[0];
  const agentName = Object.keys(catalog.agents)[0];
  const hooks = await module.default.server({});
  const clone = (value) => JSON.stringify(value);
  const noop = async (name, config, nested = [], observe = true) => {
    const before = observe ? clone(config) : null;
    const refs = observe ? [config, config.command, config.agent, config.skills, ...nested] : [];
    let error = null;
    try { await hooks.config(config); } catch (cause) { error = cause; }
    assert(`${name}-does-not-throw`, error === null);
    if (observe) {
      assert(
        `${name}-is-transactional`,
        clone(config) === before &&
          refs.every((ref, index) => index === 0 ? config === ref : (
            index === 1 ? config.command === ref :
            index === 2 ? config.agent === ref :
            index === 3 ? config.skills === ref : true
          )),
      );
    }
  };

  const frozenAgent = {};
  Object.freeze(frozenAgent);
  await noop('frozen-agent-after-commands', {
    command: { keep: { template: 'keep' } },
    agent: frozenAgent,
    skills: { paths: ['/tmp/user-skills'] },
  }, [frozenAgent]);

  // Custom prototypes are outside the supported config boundary. They must be
  // rejected before publication so inherited state cannot be copied or mutated.
  const inheritedCommand = { inherited: { template: 'must survive' } };
  const inheritedRoot = Object.create({ command: inheritedCommand });
  inheritedRoot.agent = {};
  inheritedRoot.skills = { paths: [] };
  await noop('prototype-root', inheritedRoot);
  assert(
    'prototype-root-not-mutated',
    JSON.stringify(inheritedCommand) === JSON.stringify({ inherited: { template: 'must survive' } }) &&
      !Object.prototype.hasOwnProperty.call(inheritedRoot, 'command'),
  );

  const inheritedAgent = { inherited: { mode: 'must survive' } };
  const prototypeAgent = Object.create(inheritedAgent);
  prototypeAgent.keep = { mode: 'primary' };
  const prototypeAgentRoot = { command: {}, agent: prototypeAgent, skills: { paths: [] } };
  await noop('prototype-agent-container', prototypeAgentRoot, [prototypeAgent]);
  assert(
    'prototype-agent-not-mutated',
    JSON.stringify(inheritedAgent) === JSON.stringify({ inherited: { mode: 'must survive' } }) &&
      prototypeAgentRoot.agent === prototypeAgent,
  );

  const inheritedPathArray = ['/tmp/inherited-paths'];
  const prototypeSkills = Object.create({ paths: inheritedPathArray });
  const prototypeSkillsRoot = { command: {}, agent: {}, skills: prototypeSkills };
  await noop('prototype-skills-container', prototypeSkillsRoot, [prototypeSkills]);
  assert(
    'prototype-skills-not-mutated',
    JSON.stringify(inheritedPathArray) === JSON.stringify(['/tmp/inherited-paths']) &&
      prototypeSkillsRoot.skills === prototypeSkills &&
      !Object.prototype.hasOwnProperty.call(prototypeSkills, 'paths'),
  );

  for (const [name, config] of [
    ['array-command', { command: [], agent: {}, skills: { paths: [] } }],
    ['string-command', { command: 'invalid', agent: {}, skills: { paths: [] } }],
    ['array-agent', { command: {}, agent: [], skills: { paths: [] } }],
    ['string-agent', { command: {}, agent: 'invalid', skills: { paths: [] } }],
    ['string-skills', { command: {}, agent: {}, skills: 'invalid' }],
    ['string-paths', { command: {}, agent: {}, skills: { paths: 'invalid' } }],
    ['date-command', { command: new Date(0), agent: {}, skills: { paths: [] } }],
  ]) await noop(name, config);

  let getterReads = 0;
  const accessorConfig = { agent: {}, skills: { paths: [] } };
  Object.defineProperty(accessorConfig, 'command', {
    enumerable: true,
    configurable: true,
    get() { getterReads += 1; return {}; },
  });
  await noop('accessor-command', accessorConfig, [], false);
  assert('accessor-command-not-read', getterReads === 0);

  let nestedGetterReads = 0;
  const accessorCommand = {};
  Object.defineProperty(accessorCommand, 'keep', {
    enumerable: true,
    configurable: true,
    get() { nestedGetterReads += 1; return { template: 'must not read' }; },
  });
  await noop('accessor-command-entry', {
    command: accessorCommand,
    agent: {},
    skills: { paths: [] },
  }, [], false);
  assert('accessor-command-entry-not-read', nestedGetterReads === 0);

  await noop('frozen-root', Object.freeze({ marker: true }));
  await noop('non-extensible-root', Object.preventExtensions({ marker: true }));

  const proxyTarget = {};
  const proxyRoot = new Proxy(proxyTarget, {
    defineProperty() { throw new Error('publication blocked'); },
    set() { throw new Error('publication blocked'); },
  });
  await noop('proxy-root-publication', proxyRoot);
  assert('proxy-root-target-unchanged', Object.keys(proxyTarget).length === 0);

  let setterCalls = 0;
  const setterRoot = {};
  Object.defineProperty(setterRoot, 'command', {
    enumerable: true,
    configurable: true,
    get() { throw new Error('getter must not run'); },
    set() { setterCalls += 1; },
  });
  await noop('setter-root-publication', setterRoot, [], false);
  assert('setter-root-not-called', setterCalls === 0);

  const nonWritableRoot = { agent: {}, skills: { paths: [] } };
  const nonWritableCommand = {};
  Object.defineProperty(nonWritableRoot, 'command', {
    value: nonWritableCommand,
    enumerable: true,
    writable: false,
    configurable: true,
  });
  await noop('non-writable-root-command', nonWritableRoot, [nonWritableCommand]);
  assert(
    'non-writable-root-command-not-mutated',
    nonWritableRoot.command === nonWritableCommand,
  );

  const nonConfigurableRoot = { command: {}, agent: {}, skills: { paths: [] } };
  Object.defineProperty(nonConfigurableRoot, 'agent', {
    value: nonConfigurableRoot.agent,
    enumerable: true,
    writable: false,
    configurable: false,
  });
  await noop('non-configurable-root-agent', nonConfigurableRoot);
  assert('non-configurable-root-agent-not-mutated', !Object.prototype.hasOwnProperty.call(
    nonConfigurableRoot.agent,
    agentName,
  ));

  const writableNonConfigurableRoot = { command: {}, agent: {}, skills: { paths: [] } };
  Object.defineProperty(writableNonConfigurableRoot, 'command', {
    value: writableNonConfigurableRoot.command,
    enumerable: true,
    writable: true,
    configurable: false,
  });
  let writableNonConfigurableError = null;
  try { await hooks.config(writableNonConfigurableRoot); } catch (cause) {
    writableNonConfigurableError = cause;
  }
  assert('writable-non-configurable-root-does-not-throw', writableNonConfigurableError === null);
  assert(
    'writable-non-configurable-root-publishes',
    writableNonConfigurableRoot.command !== undefined &&
      Object.prototype.hasOwnProperty.call(writableNonConfigurableRoot.command, commandName) &&
      Object.getOwnPropertyDescriptor(writableNonConfigurableRoot, 'command').configurable === false,
  );

  const descriptorCommandValue = { source: 'user command' };
  const descriptorCommandContainer = {};
  Object.defineProperty(descriptorCommandContainer, commandName, {
    value: descriptorCommandValue,
    enumerable: true,
    writable: false,
    configurable: false,
  });
  const descriptorAgentValue = { source: 'user agent' };
  const descriptorAgentContainer = {};
  Object.defineProperty(descriptorAgentContainer, agentName, {
    value: descriptorAgentValue,
    enumerable: true,
    writable: false,
    configurable: false,
  });
  const descriptorConfig = {
    command: descriptorCommandContainer,
    agent: descriptorAgentContainer,
    skills: { paths: [] },
  };
  await hooks.config(descriptorConfig);
  const commandDescriptor = Object.getOwnPropertyDescriptor(descriptorConfig.command, commandName);
  const agentDescriptor = Object.getOwnPropertyDescriptor(descriptorConfig.agent, agentName);
  assert(
    'user-command-entry-descriptor-preserved',
    commandDescriptor?.value === descriptorCommandValue &&
      commandDescriptor.enumerable === true &&
      commandDescriptor.writable === false &&
      commandDescriptor.configurable === false,
  );
  assert(
    'user-agent-entry-descriptor-preserved',
    agentDescriptor?.value === descriptorAgentValue &&
      agentDescriptor.enumerable === true &&
      agentDescriptor.writable === false &&
      agentDescriptor.configurable === false,
  );
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
    case_env.setdefault("EXPSKILL_TEST_PLUGIN_URL", plugin.as_uri())
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
            "ok:load-skill-at-start",
            "ok:load-skill-after-compaction",
            "ok:compact-load-instruction",
            "ok:dedup-current-output",
            "ok:inject-fresh-request",
            "ok:one-block-per-output",
            "ok:compacting",
            "ok:reload-after-compaction",
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
            "ok:complete-policy-surface",
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
            "ok:resumed-instances-share-thirty-call-budget",
            "ok:persisted-count-is-thirty",
            "ok:process-restart-preserves-run-budget",
            "ok:corrupt-run-budget-fails-closed",
            "ok:unsafe-run-identity-fails-closed",
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
            "ok:invalid-max-depth-fails-closed",
            "ok:invalid-max-retries-fails-closed",
            "ok:invalid-selected-reference-fails-closed",
        ):
            self.assertIn(token, result.stdout)

    @needs_node
    def test_native_root_plugin_registers_catalog_and_composes_hooks(self) -> None:
        result = run_node_case(self.artifact() / "index.js", NATIVE_ROOT_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:root-export-shape",
            "ok:composed-hooks",
            "ok:catalog-inventory-shape",
            "ok:all-catalog-commands",
            "ok:all-catalog-agents",
            "ok:catalog-values-preserved",
            "ok:user-command-wins",
            "ok:user-agent-wins",
            "ok:bundled-skills-path",
            "ok:bundled-skills-path-deduplicated",
            "ok:bundled-skills-path-idempotent",
            "ok:unrelated-config-preserved",
            "ok:config-hook-idempotent",
            "ok:composed-unslop-effect",
            "ok:composed-compacting-effect",
            "ok:composed-policy-effect",
        ):
            self.assertIn(token, result.stdout)

    @needs_node
    def test_native_root_normalizes_bundled_agent_reasoning_options(self) -> None:
        result = run_node_case(self.artifact() / "index.js", NATIVE_AGENT_OPTIONS_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in (
            "expskill-designer",
            "expskill-explorer",
            "expskill-implementer",
            "expskill-planner",
            "expskill-review",
            "expskill-spec",
            "expskill-test-engineer",
        ):
            for suffix in (
                "options-plain-object",
                "options-exact-reasoning-effort",
                "no-direct-reasoning-effort",
            ):
                with self.subTest(agent=name, assertion=suffix):
                    self.assertIn(f"ok:{name}-{suffix}", result.stdout)
        self.assertIn("ok:loaded-catalog-not-mutated", result.stdout)
        self.assertIn("ok:explicit-agent-collision-untouched", result.stdout)

    @needs_node
    def test_native_root_plugin_fails_closed_on_malformed_catalog(self) -> None:
        artifact = self.artifact()
        (artifact / "catalog.json").write_text("{invalid", encoding="utf-8")
        result = run_node_case(artifact / "index.js", NATIVE_ROOT_MALFORMED_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok:malformed-catalog-does-not-throw", result.stdout)
        self.assertIn("ok:malformed-catalog-no-partial-registration", result.stdout)

    @needs_node
    def test_native_root_rejects_catalog_variants_before_mutation(self) -> None:
        variants: list[tuple[str, object]] = []

        def extra_top_level(catalog: dict[str, object]) -> None:
            catalog["unexpected"] = True

        def missing_top_level(catalog: dict[str, object]) -> None:
            del catalog["agents"]

        def wrong_schema(catalog: dict[str, object]) -> None:
            catalog["schema_version"] = "opencode-runtime.v0"

        def missing_command(catalog: dict[str, object]) -> None:
            commands = catalog["commands"]
            assert isinstance(commands, dict)
            del commands[next(iter(commands))]

        def extra_command_field(catalog: dict[str, object]) -> None:
            commands = catalog["commands"]
            assert isinstance(commands, dict)
            commands[next(iter(commands))]["unexpected"] = True  # type: ignore[index]

        def extra_agent_field(catalog: dict[str, object]) -> None:
            agents = catalog["agents"]
            assert isinstance(agents, dict)
            agents[next(iter(agents))]["unexpected"] = True  # type: ignore[index]

        def thirteenth_command(catalog: dict[str, object]) -> None:
            commands = catalog["commands"]
            assert isinstance(commands, dict)
            commands["unexpected-command"] = dict(next(iter(commands.values())))

        def eighth_agent(catalog: dict[str, object]) -> None:
            agents = catalog["agents"]
            assert isinstance(agents, dict)
            agents["unexpected-agent"] = dict(next(iter(agents.values())))

        def dangerous_command_name(catalog: dict[str, object]) -> None:
            commands = catalog["commands"]
            assert isinstance(commands, dict)
            commands["__proto__"] = dict(next(iter(commands.values())))

        def command_array(catalog: dict[str, object]) -> None:
            catalog["commands"] = []

        def agent_array(catalog: dict[str, object]) -> None:
            catalog["agents"] = []

        def invalid_agent_mode(catalog: dict[str, object]) -> None:
            agents = catalog["agents"]
            assert isinstance(agents, dict)
            agents[next(iter(agents))]["mode"] = "primary"  # type: ignore[index]

        def invalid_permission_decision(catalog: dict[str, object]) -> None:
            agents = catalog["agents"]
            assert isinstance(agents, dict)
            permission = agents[next(iter(agents))]["permission"]  # type: ignore[index]
            assert isinstance(permission, dict)
            bash = permission["bash"]
            assert isinstance(bash, dict)
            bash["*"] = "maybe"

        def empty_permission(catalog: dict[str, object]) -> None:
            agents = catalog["agents"]
            assert isinstance(agents, dict)
            agents[next(iter(agents))]["permission"] = {}  # type: ignore[index]

        def overlong_command_description(catalog: dict[str, object]) -> None:
            commands = catalog["commands"]
            assert isinstance(commands, dict)
            commands[next(iter(commands))]["description"] = "x" * 161  # type: ignore[index]

        variants.extend(
            [
                ("extra-top-level", extra_top_level),
                ("missing-top-level", missing_top_level),
                ("wrong-schema", wrong_schema),
                ("missing-command", missing_command),
                ("extra-command-field", extra_command_field),
                ("extra-agent-field", extra_agent_field),
                ("thirteenth-command", thirteenth_command),
                ("eighth-agent", eighth_agent),
                ("dangerous-command-name", dangerous_command_name),
                ("command-array", command_array),
                ("agent-array", agent_array),
                ("invalid-agent-mode", invalid_agent_mode),
                ("invalid-permission-decision", invalid_permission_decision),
                ("empty-permission", empty_permission),
                ("overlong-command-description", overlong_command_description),
            ]
        )
        for name, mutate in variants:
            with self.subTest(variant=name):
                artifact = self.artifact()
                catalog_path = artifact / "catalog.json"
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                mutate(catalog)
                catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
                result = run_node_case(artifact / "index.js", NATIVE_ROOT_CATALOG_REJECTION_CASE)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("ok:catalog-rejection-does-not-throw", result.stdout)
                self.assertIn("ok:catalog-rejection-is-transactional", result.stdout)

    @needs_node
    def test_native_root_config_application_fails_closed_at_boundaries(self) -> None:
        result = run_node_case(self.artifact() / "index.js", NATIVE_ROOT_CONFIG_BOUNDARIES_CASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for token in (
            "ok:frozen-agent-after-commands-is-transactional",
            "ok:prototype-root-not-mutated",
            "ok:prototype-agent-not-mutated",
            "ok:prototype-skills-not-mutated",
            "ok:array-command-is-transactional",
            "ok:string-agent-is-transactional",
            "ok:string-paths-is-transactional",
            "ok:accessor-command-not-read",
            "ok:accessor-command-entry-not-read",
            "ok:frozen-root-is-transactional",
            "ok:non-extensible-root-is-transactional",
            "ok:proxy-root-target-unchanged",
            "ok:setter-root-not-called",
            "ok:non-writable-root-command-not-mutated",
            "ok:non-configurable-root-agent-not-mutated",
            "ok:writable-non-configurable-root-publishes",
            "ok:user-command-entry-descriptor-preserved",
            "ok:user-agent-entry-descriptor-preserved",
        ):
            self.assertIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main()
