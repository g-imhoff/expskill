from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests import test_plan_audit_records as audit_tests

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'plugins/expskill/opencode/plugins/execution-policy.js'


class PlanEvidencePermissionsTests(unittest.TestCase):
    setUp = audit_tests.PlanAuditRecordsTests.setUp
    tearDown = audit_tests.PlanAuditRecordsTests.tearDown
    _git = audit_tests.PlanAuditRecordsTests._git
    start = audit_tests.PlanAuditRecordsTests.start
    reserve = audit_tests.PlanAuditRecordsTests.reserve
    history = audit_tests.PlanAuditRecordsTests.history
    change_negative = audit_tests.PlanAuditRecordsTests.change_negative
    def run_hook(self, purpose, ledger, dispatch_id, revision, *, agent='expskill-explorer', caller='expskill-planner', extra=None, repeat=False, history=None, typed=True):
        envelope = {'schema_version': 'plan-evidence-dispatch.v1', 'purpose': purpose,
                    'ledger': str(ledger), 'dispatch_id': dispatch_id, 'reservation_revision': revision}
        args = {'subagent_type': agent, 'description': 'Bounded evidence service',
                'prompt': ('EXPSKILL_PLAN_EVIDENCE ' + json.dumps(envelope) + '\nInspect only the reserved evidence brief.') if typed else 'Inspect the assigned brief.'}
        args.update(extra or {})
        script = r'''
const module = await import(process.env.HOOK);
const args = JSON.parse(process.env.ARGS);
const input = {tool:'task',sessionID:'planner-session',callID:'actual-call'};
const data = process.env.HISTORY ? JSON.parse(process.env.HISTORY) : [{info:{role:'assistant',agent:process.env.CALLER,sessionID:input.sessionID,path:{cwd:process.env.REPO}},parts:[{type:'tool',tool:'task',sessionID:input.sessionID,callID:input.callID}]}];
const client = {session:{messages:async () => {if(data==='unavailable') throw new Error('unrelated caller lookup should not run'); return {data};}}};
let outcome;
try {
 const hooks = await module.ExecutionPolicyPlugin({client});
 await hooks['tool.execute.before'](input,{args});
 if(process.env.REPEAT==='true') {
  const replacement = await module.ExecutionPolicyPlugin({client});
  await replacement['tool.execute.before'](input,{args});
 }
 outcome = {allowed:true,args};
} catch(error) {outcome={allowed:false,error:error.message};}
console.log(JSON.stringify(outcome));
'''
        env = os.environ.copy()
        env.update(HOOK=HOOK.as_uri(), EXPSKILL_HOME=str(ROOT), ARGS=json.dumps(args), CALLER=caller,
                   REPO=str(self.repo), REPEAT=str(repeat).lower())
        if history is not None:
            env['HISTORY'] = json.dumps(history)
        completed = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                                   text=True, capture_output=True, env=env, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_scoped_permission_allows_explorer_and_denies_other_tasks_and_edits(self):
        spec = json.loads((ROOT / 'plugins/expskill/opencode/agents.json').read_text())
        permissions = spec['agents']['expskill-planner']['permission']
        self.assertEqual(permissions['task'], {'*': 'deny', 'expskill-explorer': 'allow'})
        self.assertEqual(permissions['edit'], 'deny')
        explorer = spec['agents']['expskill-explorer']['permission']
        self.assertEqual(explorer['external_directory'], 'ask')
        self.assertEqual(explorer['edit'], 'deny')
        self.assertEqual(explorer['task'], 'deny')
        self.assertEqual(explorer['bash']['*'], 'deny')

    def test_actual_planner_hook_rejects_unrelated_task_even_with_forged_caller_args(self):
        result = self.run_hook('research', self.root / 'missing.json', 'r1', 1,
                               agent='expskill-implementer', extra={'agent':'router'})
        self.assertFalse(result['allowed'], result)

    def test_reserved_independent_audit_can_dispatch_with_frozen_sources(self):
        graph = self.start()
        reserved = self.reserve(graph)
        ledger = reserved['accounting']
        result = self.run_hook('plan-audit', ledger['locator'], 'first', ledger['revision'])
        self.assertTrue(result['allowed'], result)
        self.assertIn(reserved['dispatch']['graph_snapshot']['path'], result['args']['prompt'])
        self.assertIn('No further delegation', result['args']['prompt'])
        self.assertEqual(self.history()['spent_calls'], 1)

    def test_launch_claim_survives_hook_replacement_without_new_capacity(self):
        graph = self.start()
        reserved = self.reserve(graph)['accounting']
        result = self.run_hook('plan-audit', reserved['locator'], 'first', reserved['revision'], repeat=True)
        self.assertFalse(result['allowed'], result)
        self.assertIn('already dispatched', result['error'])
        self.assertEqual(self.history()['spent_calls'], 1)

    def test_unreserved_or_resumed_audit_is_rejected(self):
        graph = self.start()
        reserved = self.reserve(graph)['accounting']
        for dispatch, extra in [('missing', None), ('first', {'task_id':'old-conversation'})]:
            result = self.run_hook('plan-audit', reserved['locator'], dispatch, reserved['revision'], extra=extra)
            self.assertFalse(result['allowed'], result)

    def test_reserved_research_uses_original_cumulative_pool(self):
        import importlib.util
        import sys
        helper_path = ROOT / 'plugins/expskill/content/scripts/research_budget.py'
        spec = importlib.util.spec_from_file_location('permission_research', helper_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        sys.path.insert(0, str(helper_path.parent))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        initial = module.initialize(self.repo, 'feature/transaction-tests', 'research-original', state_home=self.state_home)
        reserved = module.apply(self.repo, 'feature/transaction-tests', 'research-original', initial['revision'],
                                {'op':'reserve','dispatch_id':'r1','question':'Check the external parser compatibility reference'}, state_home=self.state_home)
        result = self.run_hook('research', reserved['locator'], 'r1', reserved['revision'])
        self.assertTrue(result['allowed'], result)
        self.assertIn('Check the external parser compatibility reference', result['args']['prompt'])
        self.assertIn('No repository access', result['args']['prompt'])
        retained = module.load(self.repo, 'feature/transaction-tests', 'research-original', state_home=self.state_home)
        self.assertEqual(retained['spent_turns'], 1)
        self.assertEqual(retained['outstanding_dispatch_ids'], ['r1'])

    def test_changed_frozen_audit_bytes_block_launch_and_retain_consumption(self):
        graph = self.start()
        reserved = self.reserve(graph)
        Path(reserved['dispatch']['graph_snapshot']['path']).write_text('{}')
        accounting = reserved['accounting']
        result = self.run_hook('plan-audit', accounting['locator'], 'first', accounting['revision'])
        self.assertFalse(result['allowed'], result)
        self.assertIn('changed frozen audit artifact', result['error'])
        self.assertEqual(self.history()['spent_calls'], 1)

    def test_current_graph_change_blocks_old_audit_reservation(self):
        graph = self.start()
        reserved = self.reserve(graph)['accounting']
        self.change_negative('Reject an invalid empty parser configuration before startup')
        result = self.run_hook('plan-audit', reserved['locator'], 'first', reserved['revision'])
        self.assertFalse(result['allowed'], result)
        self.assertIn('current graph', result['error'])
        self.assertEqual(self.history()['outstanding_dispatch_ids'], ['first'])

    def test_nonplanner_cannot_spoof_scoped_service_dispatch(self):
        graph = self.start()
        reserved = self.reserve(graph)['accounting']
        result = self.run_hook('plan-audit', reserved['locator'], 'first', reserved['revision'], caller='expskill-designer')
        self.assertFalse(result['allowed'], result)
        self.assertIn('actual planner caller', result['error'])

    def test_public_validator_rejects_broadened_or_reordered_planner_permissions(self):
        import tempfile
        from scripts.validate import _validate_opencode_agent_spec
        original = json.loads((ROOT / 'plugins/expskill/opencode/agents.json').read_text())
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'agents.json'
            for task in ({'*':'allow','expskill-explorer':'allow'},
                         {'expskill-explorer':'allow','*':'deny'},
                         {'*':'deny','expskill-explorer':'allow','expskill-implementer':'allow'}):
                original['agents']['expskill-planner']['permission']['task'] = task
                target.write_text(json.dumps(original))
                errors = []
                _validate_opencode_agent_spec(Path(temporary), errors)
                self.assertTrue(any('scoped explorer service' in error for error in errors), errors)

    def active_caller(self, **changes):
        info = {'role':'assistant','agent':'expskill-planner','sessionID':'planner-session',
                'path':{'cwd':str(self.repo)},'time':{'created':123}}
        info.update(changes)
        return {'info':info,'parts':[]}

    def test_current_tool_part_can_be_pending_without_blocking_reserved_audit(self):
        graph = self.start()
        reserved = self.reserve(graph)
        accounting = reserved['accounting']
        result = self.run_hook('plan-audit', accounting['locator'], 'first', accounting['revision'],
                               history=[self.active_caller()])
        self.assertTrue(result['allowed'], result)
        self.assertIn(reserved['dispatch']['graph_snapshot']['path'], result['args']['prompt'])

    def test_active_caller_fallback_rejects_wrong_session_user_completed_and_ambiguous_history(self):
        graph = self.start()
        accounting = self.reserve(graph)['accounting']
        histories = ([self.active_caller(sessionID='another-session')],
                     [self.active_caller(role='user')],
                     [self.active_caller(time={'created':123,'completed':124})],
                     [self.active_caller(), self.active_caller()])
        for history in histories:
            result = self.run_hook('plan-audit', accounting['locator'], 'first', accounting['revision'], history=history)
            self.assertFalse(result['allowed'], result)
        self.assertEqual(self.history()['spent_calls'], 1)

    def test_unrelated_host_task_does_not_depend_on_current_tool_part(self):
        result = self.run_hook('research', self.root / 'unused', 'unused', 1,
                               agent='general', typed=False, history='unavailable')
        self.assertTrue(result['allowed'], result)
        permissions = json.loads((ROOT / 'plugins/expskill/opencode/agents.json').read_text())
        rules = permissions['agents']['expskill-planner']['permission']['task']
        effective = next(action for pattern, action in reversed(list(rules.items())) if pattern in ('*','general'))
        self.assertEqual(effective, 'deny')
