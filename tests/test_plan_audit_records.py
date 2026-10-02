from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from tests import test_plan_graph_stage10 as base
from tests.plan_audit_fixture import artifact, result_for

_graph = base._graph
BRANCH = base.BRANCH


class PlanAuditRecordsTests(unittest.TestCase):
    setUp = base.TransactionLayerTests.setUp
    tearDown = base.TransactionLayerTests.tearDown
    _git = base.TransactionLayerTests._git
    _typed_update = base.TransactionLayerTests._typed_update

    def reserve(self, graph, dispatch_id="first", purpose="initial"):
        return self.helper.reserve_plan_audit(self.repo, BRANCH, self.receipt.workflow_id,
            graph["graph_revision"], dispatch_id, self.accepted_input, purpose,
            "Test-only actual canonical audit reservation", self.state_home)

    def record(self, dispatch, findings=None, resolutions=None, **changes):
        output = artifact(self.root / f"{dispatch['dispatch_id']}-output.json", {"findings": findings or []})
        result = result_for(dispatch, output, findings=findings, resolutions=resolutions)
        result.update(changes)
        receipt = self.helper.record_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id,
            dispatch["dispatch_id"], result, self.state_home)
        return result, receipt

    def change_negative(self, wording):
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.helper.apply_updates(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"],
            [{"op": "set", "path": ["proof", "P1", "planned_method", "negative"], "value": wording}], self.state_home)
        return self.helper.load_workflow(self.repo, BRANCH, self.state_home)

    def history(self):
        return self.helper.load_plan_audits(self.repo, BRANCH, self.receipt.workflow_id, self.state_home)

    def start(self):
        graph = _graph()
        graph["audit"] = {"breadth": True, "reason": "Producer and consumer validation boundaries"}
        self.receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.input_path = self.root / "accepted-input.json"
        self.input_path.write_text(json.dumps(graph), encoding="utf-8")
        self.input_path.chmod(0o600)
        self.accepted_input = {"path": str(self.input_path), "digest": hashlib.sha256(self.input_path.read_bytes()).hexdigest()}
        return self.helper.load_workflow(self.repo, BRANCH, self.state_home)

    def test_raw_refresh_cannot_claim_independence_without_durable_actor_pins_and_output(self):
        graph = self.start()
        audit = dict(graph["audit"], fresh=True, independent=True, graph_revision=graph["graph_revision"])
        with self.assertRaisesRegex(self.helper.PlanGraphError, "durable dispatch"):
            receipt = self.helper.issue_operation_receipt(operation="refresh-audit", workflow_id=self.receipt.workflow_id,
                prior_graph_revision=graph["graph_revision"], target=["audit"], record_version=audit["record_version"], value=audit)
            self.helper.apply_updates(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"],
                [{"op": "refresh-audit", "path": ["audit"], "value": audit, "prior_graph_revision": graph["graph_revision"],
                  "record_version": audit["record_version"], "receipt": receipt}], self.state_home)

    def test_reservation_survives_pause_and_replacement_without_replenishment(self):
        graph = self.start()
        reserved = self.reserve(graph)
        dispatch = reserved["dispatch"]
        self.assertEqual(reserved["accounting"]["spent_calls"], 1)
        self.assertEqual(reserved["accounting"]["remaining"], {"calls": 2, "seconds": 600})
        self.assertEqual(json.loads(Path(dispatch["graph_snapshot"]["path"]).read_text()), graph)
        self.helper.bind_plan_audit_actor(self.repo, BRANCH, self.receipt.workflow_id, "first", "actual-session-1", self.state_home)
        self.helper.pause_workflow(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"], self.state_home)
        replacement = base._load_helper(f"audit_replacement_{id(self)}")
        history = replacement.load_plan_audits(self.repo, BRANCH, self.receipt.workflow_id, self.state_home)
        self.assertEqual(history["outstanding_dispatch_ids"], ["first"])
        self.assertEqual(history["dispatches"]["first"]["actor_session_id"], "actual-session-1")
        self.assertEqual(history["dispatches"]["first"]["graph_snapshot"], dispatch["graph_snapshot"])
        current = replacement.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "outstanding"):
            self.reserve(current, "second", "correction")
        self.helper.close_plan_audit_dispatch(self.repo, BRANCH, self.receipt.workflow_id, "first", "interrupted", "Actual session interrupted before a result", self.state_home)
        self.assertEqual(self.history()["spent_calls"], 1)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "actually changed"):
            self.reserve(current, "second", "correction")

    def test_unattached_result_retains_findings_after_rotations_and_refuses_omission(self):
        graph = self.start()
        dispatch = self.reserve(graph)["dispatch"]
        finding = {"id": "F1", "severity": "high", "description": "Missing rejected malformed-input behavior", "evidence": ["E1"], "disposition": "open"}
        first_result, first_history = self.record(dispatch, [finding])
        first_bytes = Path(dispatch["graph_snapshot"]["path"]).read_bytes()
        for number in range(4):
            graph = self.change_negative(f"Malformed configuration is rejected with reason {number}")
        retained = self.history()
        self.assertEqual(retained["dispatches"]["first"]["result"], first_result)
        self.assertEqual(retained["retained_findings"], [finding])
        self.assertEqual(Path(dispatch["graph_snapshot"]["path"]).read_bytes(), first_bytes)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "current graph meaning"):
            self.helper.apply_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"], "first", self.state_home)
        correction = self.reserve(graph, "correction", "correction")["dispatch"]
        with self.assertRaisesRegex(self.helper.PlanGraphError, "discard retained"):
            self.record(correction)
        rejected = self.history()
        self.assertEqual(rejected["spent_calls"], 2)
        self.assertEqual(rejected["dispatches"]["correction"]["status"], "rejected")
        self.assertEqual(rejected["retained_findings"], [finding])

    def test_correction_checks_keep_three_call_ceiling_and_require_named_extension(self):
        graph = self.start()
        ids = ["z-initial", "a-correction", "m-correction"]
        for index, dispatch_id in enumerate(ids):
            if index:
                graph = self.change_negative(f"Malformed configuration is rejected with correction {index}")
            dispatch = self.reserve(graph, dispatch_id, "initial" if not index else "correction")["dispatch"]
            self.record(dispatch)
        graph = self.change_negative("Unknown configuration is rejected after authorized correction")
        with self.assertRaisesRegex(self.helper.PlanGraphError, "capacity is exhausted"):
            self.reserve(graph, "fourth", "correction")
        self.assertEqual(self.history()["remaining"], {"calls": 0, "seconds": 0})
        extended = self.helper.extend_plan_audit_budget(self.repo, BRANCH, self.receipt.workflow_id,
            graph["graph_revision"], "user-extension", "Explicit test-only user-approved correction extension", 4, 1200, self.state_home)
        self.assertEqual(extended["spent_calls"], 3)
        fourth = self.reserve(graph, "fourth", "correction")
        self.assertEqual(fourth["dispatch"]["ordinal"], 4)
        self.assertEqual(fourth["accounting"]["spent_calls"], 4)

    def test_stale_pins_missing_output_and_false_independence_remain_consumed(self):
        for name in ("stale-pin", "missing-output", "false-independent"):
            with self.subTest(name=name):
                self.state_home = self.root / name
                graph = self.start()
                dispatch = self.reserve(graph)["dispatch"]
                self.helper.bind_plan_audit_actor(self.repo, BRANCH, self.receipt.workflow_id, "first", f"actual-{name}", self.state_home)
                output = artifact(self.root / f"{name}-output.json", {"status": "test-only reply"})
                result = result_for(dispatch, output, actor=f"actual-{name}")
                if name == "stale-pin":
                    result["graph_revision"] += 1
                elif name == "missing-output":
                    Path(output["path"]).unlink()
                else:
                    result["independent"] = False
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.record_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id, "first", result, self.state_home)
                history = self.history()
                self.assertEqual(history["spent_calls"], 1)
                self.assertEqual(history["dispatches"]["first"]["actor_session_id"], f"actual-{name}")
                self.assertEqual(history["dispatches"]["first"]["status"], "rejected")
                with self.assertRaisesRegex(self.helper.PlanGraphError, "accepted result"):
                    self.helper.apply_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"], "first", self.state_home)

    def test_attached_audit_stales_on_output_tamper_but_not_unrelated_commit(self):
        graph = self.start()
        dispatch = self.reserve(graph)["dispatch"]
        result, history = self.record(dispatch)
        applied = self.helper.apply_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id,
            graph["graph_revision"], "first", self.state_home)
        self.assertEqual(applied.state, "ready")
        (self.repo / "unrelated.txt").write_text("Unrelated product file\n", encoding="utf-8")
        self._git("add", "unrelated.txt")
        self._git("commit", "-m", "unrelated change")
        current = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(current["audit"]["fresh"])
        Path(result["output"]["path"]).write_text("Changed original output", encoding="utf-8")
        current = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(current["audit"]["fresh"])
        self.assertFalse(current["audit"]["independent"])
        self.assertEqual(self.history()["dispatches"]["first"]["result"], result)

    def test_scope_growth_requires_named_extension_without_resetting_calls(self):
        graph = self.start()
        dispatch = self.reserve(graph)["dispatch"]
        self.record(dispatch)
        self.helper.classify_plan_audit(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"],
            True, False, True, "New user-approved irreversible compatibility boundary", self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "named extension"):
            self.reserve(graph, "broader", "correction")
        self.helper.extend_plan_audit_budget(self.repo, BRANCH, self.receipt.workflow_id, graph["graph_revision"],
            "scope-extension", "User explicitly approved the named high-consequence scope extension", 4, 1200, self.state_home)
        broad = self.reserve(graph, "broader", "correction")
        self.assertEqual(broad["accounting"]["spent_calls"], 2)

    def test_changed_source_bytes_need_fresh_check_after_typed_evidence_refresh(self):
        graph = self.start()
        dispatch = self.reserve(graph)["dispatch"]
        self.record(dispatch)
        self.helper.apply_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id,
            graph["graph_revision"], "first", self.state_home)
        (self.repo / "config.py").write_text("CONFIG = {'new': True}\n", encoding="utf-8")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(graph["evidence"]["E1"]["fresh"])
        evidence = dict(graph["evidence"]["E1"], fresh=True, observed_at="2026-10-02T12:00:00Z")
        self._typed_update(self.receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"], evidence)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(graph["audit"]["fresh"])
        fresh = self.reserve(graph, "source-correction", "correction")
        self.assertEqual(fresh["accounting"]["spent_calls"], 2)

    def test_same_meaning_retry_and_reused_actual_session_are_rejected(self):
        graph = self.start()
        dispatch = self.reserve(graph)["dispatch"]
        self.record(dispatch)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "actually changed"):
            self.reserve(graph, "retry", "correction")
        graph = self.change_negative("Unknown input is rejected after canonical correction")
        correction = self.reserve(graph, "correction", "correction")["dispatch"]
        with self.assertRaisesRegex(self.helper.PlanGraphError, "distinct actor/session"):
            self.record(correction, actor_session_id="test-only-auditor-first")
        self.assertEqual(self.history()["spent_calls"], 2)
        self.assertEqual(self.history()["dispatches"]["correction"]["status"], "rejected")

    def test_finding_meaning_cannot_be_replaced_by_fresh_correction_result(self):
        graph = self.start()
        dispatch = self.reserve(graph)["dispatch"]
        finding = {"id": "F1", "severity": "high", "description": "Malformed input has no rejection proof", "evidence": ["E1"], "disposition": "open"}
        self.record(dispatch, [finding])
        graph = self.change_negative("Malformed input is rejected with a reason")
        correction = self.reserve(graph, "correction", "correction")["dispatch"]
        changed = dict(finding, description="Unrelated low-risk wording issue", disposition="resolved")
        with self.assertRaisesRegex(self.helper.PlanGraphError, "replace retained finding meaning"):
            self.record(correction, [changed], [{"finding_id": "F1", "disposition": "resolved", "evidence": ["E1"]}])
        self.assertEqual(self.history()["retained_findings"], [finding])

    def test_legacy_audit_without_dispatch_recovers_stale_without_new_allowance(self):
        graph = self.start()
        audit = dict(graph["audit"], fresh=True, independent=True, graph_revision=graph["graph_revision"])
        audit.pop("dispatch_id")
        audit.pop("result_digest")
        audit["operation_receipt"] = self.helper.issue_operation_receipt(operation="refresh-audit",
            workflow_id=self.receipt.workflow_id, prior_graph_revision=graph["graph_revision"],
            target=["audit"], record_version=audit["record_version"], value=audit)
        graph["audit"] = audit
        graph["graph_revision"] += 1
        graph["lifecycle"]["derived_state"] = "ready"
        basis_root = self.state_home / "acceptance-bases"
        basis_root.mkdir(mode=0o700)
        basis_directory = basis_root / self.receipt.path.parent.name
        basis_directory.mkdir(mode=0o700)
        legacy_basis = artifact(basis_directory / f"{self.receipt.workflow_id}.json", graph)
        self.assertEqual(self.helper.load_acceptance_basis(Path(legacy_basis["path"]), legacy_basis["digest"]), graph)
        self.receipt.path.write_text(json.dumps(graph), encoding="utf-8")
        recovered = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(recovered["audit"]["fresh"])
        self.assertFalse(recovered["audit"]["independent"])
        with self.assertRaisesRegex(self.helper.PlanGraphError, "legacy audit consumption is unknown"):
            self.reserve(recovered)

    def test_malformed_result_and_changed_frozen_artifact_keep_original_reservation(self):
        for failure in ("malformed", "frozen-tamper"):
            with self.subTest(failure=failure):
                self.state_home = self.root / failure
                graph = self.start()
                dispatch = self.reserve(graph)["dispatch"]
                output = artifact(self.root / f"{failure}-raw.json", {"reply": "test-only actual retained reply"})
                result = result_for(dispatch, output)
                if failure == "malformed":
                    result.pop("constraints")
                else:
                    Path(dispatch["graph_snapshot"]["path"]).write_text("{}", encoding="utf-8")
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.record_plan_audit_result(self.repo, BRANCH, self.receipt.workflow_id, "first", result, self.state_home)
                history = self.history()
                self.assertEqual(history["dispatches"]["first"]["graph_snapshot"], dispatch["graph_snapshot"])
                self.assertEqual(history["dispatches"]["first"]["result"]["output"], output)
                self.assertEqual(history["spent_calls"], 1)
