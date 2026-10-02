from __future__ import annotations

import importlib.util
import hashlib
import json
import multiprocessing
import os
import queue
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins/expskill/content/scripts/plan_graph.py"
BRANCH = "feature/transaction-tests"


def _load_helper(name: str = "plan_graph_stage10") -> object:
    spec = importlib.util.spec_from_file_location(name, HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _graph() -> dict[str, object]:
    return {
        "schema_version": "plan-graph.v1",
        "outcomes": {
            "O1": {"kind": "outcome", "result": "Configuration is validated"}
        },
        "evidence": {
            "E1": {
                "kind": "repository",
                "fact": "Configuration enters through config.py",
                "source": "config.py",
                "fresh": True,
                "supports": ["T1"],
            }
        },
        "decisions": {},
        "work": {
            "T1": {
                "kind": "slice",
                "result": "Validate configuration",
                "covers": ["O1"],
                "requires": [],
                "based_on": ["E1"],
                "decisions": [],
                "proof": ["P1"],
                "repository_boundary": ["config.py"],
                "owner": "target",
                "concurrency": "serial",
            }
        },
        "proof": {
            "P1": {
                "claim": "Validation behavior is observable",
                "covers": ["O1"],
                "required_by": ["T1"],
                "planned_method": {
                    "surface": "configuration tests",
                    "positive": "valid input loads",
                    "negative": "invalid input is rejected",
                },
                "evidence": [],
            }
        },
        "git": {
            "target": {
                "branch": BRANCH,
                "protected": False,
                "reproducible": True,
                "dirty_dependency": False,
            },
            "lanes": {},
            "joins": {},
            "delivery": {"state": "planning"},
        },
        "projections": {
            "U1": {
                "covers": ["T1", "P1"],
                "version": 1,
                "decision_versions": {},
                "presented": True,
                "confirmed": True,
                "stale": False,
                "presentation": "Validate configuration, accepting valid input and rejecting invalid input through configuration tests.",
            }
        },
        "invalidations": [],
        "unresolved": [],
    }


def _initialize_process(
    helper_path: str,
    repo: str,
    state_home: str,
    graph: dict[str, object],
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    global HELPER
    HELPER = Path(helper_path)
    helper = _load_helper(f"plan_graph_init_{os.getpid()}")
    start.wait(10)
    try:
        receipt = helper.initialize_workflow(Path(repo), BRANCH, graph, Path(state_home))
        results.put(("success", receipt.workflow_id, receipt.revision))
    except helper.PlanGraphError as error:
        results.put(("conflict", type(error).__name__, str(error)))


def _update_process(
    helper_path: str,
    repo: str,
    state_home: str,
    workflow_id: str,
    value: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    global HELPER
    HELPER = Path(helper_path)
    helper = _load_helper(f"plan_graph_update_{os.getpid()}")
    start.wait(10)
    try:
        receipt = helper.apply_updates(
            Path(repo),
            BRANCH,
            workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": value}],
            Path(state_home),
        )
        results.put(("success", receipt.previous_revision, receipt.current_revision))
    except helper.RevisionConflict as error:
        results.put(("conflict", type(error).__name__, str(error)))


class TransactionLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state_home = self.root / "state"
        self._git("init", "-b", BRANCH)
        self._git("config", "user.name", "Plan Graph Test")
        self._git("config", "user.email", "plan@example.invalid")
        (self.repo / "config.py").write_text("CONFIG = {}\n", encoding="utf-8")
        self._git("add", "config.py")
        self._git("commit", "-m", "initial")
        self.helper = _load_helper(f"plan_graph_stage10_{id(self)}")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _initialize(self, home: Path | None = None) -> object:
        return self.helper.initialize_workflow(
            self.repo, BRANCH, _graph(), home or self.state_home
        )

    def _typed_update(
        self,
        workflow_id: str,
        graph: dict[str, object],
        operation: str,
        path: list[str],
        value: dict[str, object],
        home: Path | None = None,
    ) -> object:
        version_field = (
            "version"
            if operation
            in {"confirm-decision", "reconfirm-decision", "revalidate-decision", "regenerate-projection", "reconfirm-projection", "clarify-projection"}
            else "record_version"
        )
        version = value[version_field]
        receipt = self.helper.issue_operation_receipt(
            operation=operation,
            workflow_id=workflow_id,
            prior_graph_revision=graph["graph_revision"],
            target=path,
            record_version=version,
            value=value,
        )
        return self.helper.apply_updates(
            self.repo,
            BRANCH,
            workflow_id,
            graph["graph_revision"],
            [{"op": operation, "path": path, "value": value,
              "prior_graph_revision": graph["graph_revision"],
              "record_version": version, "receipt": receipt}],
            home or self.state_home,
        )

    def _register_provenance(
        self,
        workflow_id: str,
        revision: int,
        role: str,
        salt: str,
        source: str,
        home: Path | None = None,
    ) -> dict[str, object]:
        receipt = self.helper.issue_provenance_receipt(
            workflow_id=workflow_id,
            graph_revision=revision,
            role=role,
            session_id=f"session-{salt}",
            raw_evidence_digest=hashlib.sha256(salt.encode()).hexdigest(),
            source=source,
        )
        return self.helper.register_provenance_receipt(
            receipt, home or self.state_home
        )

    def _draft_delivery(self, receipt: object, home: Path | None = None) -> dict[str, object]:
        target_home = home or self.state_home
        head = self._git("rev-parse", "HEAD")
        provenance = {
            role: self._register_provenance(
                receipt.workflow_id,
                receipt.revision,
                role,
                f"{receipt.workflow_id}-{role}",
                f"{role}-raw",
                target_home,
            )
            for role in ("implement", "review", "verify", "integrate")
        }
        policy = self._register_provenance(
            receipt.workflow_id,
            receipt.revision,
            "provider-policy",
            f"{receipt.workflow_id}-policy",
            "pytest",
            target_home,
        )

        def gate(role: str) -> dict[str, object]:
            stored = provenance[role]
            value: dict[str, object] = {
                "role": role,
                "receipt_id": f"{role}-receipt",
                "provenance_id": stored["receipt_id"],
                "provenance_digest": stored["digest"],
                "provenance_session": stored["session_id"],
                "provenance_source": stored["source"],
                "raw_evidence_digest": stored["raw_evidence_digest"],
                "workflow_id": receipt.workflow_id,
                "graph_revision": receipt.revision,
                "branch": BRANCH,
                "commit": head,
                "work": ["T1"],
                "proof": ["P1"],
                "commands": ["python3 -m pytest"],
                "results": [{"command": "python3 -m pytest", "exit_code": 0, "output": "passed"}],
                "result": {"status": "pass"},
            }
            if role == "implement":
                value["implementation_evidence"] = "implementation evidence"
            elif role == "review":
                value.update(disposition="approve", findings=[])
            elif role == "verify":
                value["independent"] = True
            else:
                value["integrated_proof"] = "integrated proof"
            value["digest"] = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            return value

        return {
            "state": "draft",
            "request": {"id": "PR-1", "draft": True, "head": head},
            "first_coherent_commit": head,
            "exact_head": head,
            "gates": {name: gate(role) for name, role in {
                "implementation": "implement", "review": "review",
                "verification": "verify", "target_proof": "integrate"}.items()},
            "checks_policy": {"provider": "local", "source": "pytest", "request_id": "PR-1",
                "workflow_id": receipt.workflow_id, "graph_revision": receipt.revision,
                "branch": BRANCH, "head": head, "observed_at": "2026-01-01T00:00:00Z",
                "authoritative": True, "discovered_names": ["tests"],
                "provenance_id": policy["receipt_id"], "provenance_digest": policy["digest"],
                "provenance_session": policy["session_id"],
                "raw_evidence_digest": policy["raw_evidence_digest"]},
            "checks": [{"run_id": "run-1", "url": "https://example.invalid/run-1", "name": "tests",
                        "head": head, "conclusion": "success", "status": "pass", "result": "passed"}],
            "lanes_clean": True,
            "handoff": {"title": "Validate config", "summary": "All gates pass."},
        }

    def _process_results(
        self,
        target: object,
        argument_rows: list[tuple[object, ...]],
    ) -> list[tuple[object, ...]]:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(target=target, args=(*arguments, start, results))
            for arguments in argument_rows
        ]
        for process in processes:
            process.start()
        start.set()
        values: list[tuple[object, ...]] = []
        try:
            for _ in processes:
                values.append(results.get(timeout=20))
        except queue.Empty as error:
            self.fail(f"worker did not report a result: {error}")
        finally:
            for process in processes:
                process.join(20)
                if process.is_alive():
                    process.kill()
                    process.join()
                self.assertEqual(process.exitcode, 0)
        return values

    def test_checks_policy_requires_authoritative_binding_and_discovered_names(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        graph["git"]["delivery"] = {
            "state": "planning",
            "checks_policy": {"mode": "required", "names": []},
        }
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                [{"op": "set", "path": ["git", "delivery"], "value": graph["git"]["delivery"]}],
                self.state_home,
            )

    def test_delivery_rejects_generic_gate_without_role_receipt_digest(self) -> None:
        receipt = self._initialize()
        head = self._git("rev-parse", "HEAD")
        delivery = {"state": "draft", "request": {"id": "PR-1", "draft": True, "head": head},
                    "first_coherent_commit": head, "exact_head": head,
                    "gates": {role: {"role": role, "work": ["T1"], "proof": ["P1"]}
                              for role in ("implementation", "review", "verification", "target_proof")},
                    "checks": [], "checks_policy": {}, "lanes_clean": True,
                    "handoff": {"title": "x", "summary": "y"}}
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                [{"op": "set", "path": ["git", "delivery"], "value": delivery}], self.state_home)

    def test_semantic_evidence_update_invalidates_dependent_receipts_and_projection(self) -> None:
        receipt = self._initialize()
        evidence = self.helper.load_workflow(self.repo, BRANCH, self.state_home)["evidence"]["E1"]
        evidence["fact"] = "A materially different repository boundary"
        updated = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["evidence", "E1"], "value": evidence}],
            self.state_home,
        )
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(updated.state, "stale")
        self.assertTrue(graph["proof"]["P1"]["fresh"] is False)
        self.assertTrue(graph["projections"]["U1"]["stale"] is True)

    def test_graph_requires_typed_conditional_audit_record(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        graph.pop("audit", None)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.derive_plan_state(graph)

    def test_delivery_rejects_duplicate_receipt_ids_and_untrusted_checks_provider(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        graph["audit"] = {"classification": "tiny", "breadth": False, "complexity": False,
            "high_consequence": False, "reason": "bounded", "required": False, "graph_revision": 1,
            "evidence": [], "independent": False, "constraints": ["no research", "no redesign", "no edit", "no question", "no approve"],
            "findings": [], "resolutions": [], "fresh": True}
        graph["git"]["delivery"] = {"state": "planning", "checks_policy": {"provider": "fiction"}}
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                [{"op": "set", "path": ["git", "delivery"], "value": graph["git"]["delivery"]}], self.state_home)

    def test_broad_audit_cannot_downgrade_to_tiny_or_use_unknown_evidence(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        graph["audit"].update({"classification": "tiny", "breadth": True, "complexity": False,
            "high_consequence": False, "required": False, "graph_revision": 1})
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                [{"op": "set", "path": ["audit"], "value": graph["audit"]}], self.state_home)

    def test_cli_emits_canonical_closed_envelope_for_lifecycle(self) -> None:
        import io
        from contextlib import redirect_stdout
        payload = json.dumps(_graph())
        with redirect_stdout(io.StringIO()) as output:
            self.helper.main(["initialize", "--repo", str(self.repo), "--branch", BRANCH,
                              "--state-home", str(self.state_home), "--json", payload])
        envelope = json.loads(output.getvalue())
        self.assertEqual(set(envelope), {"schema_version", "operation", "repository", "branch",
            "workflow_id", "previous_revision", "current_revision", "before_state", "after_state",
            "derived_state", "before_graph_digest", "after_graph_digest", "head_commit",
            "baseline_commit"})
        self.assertEqual(envelope["operation"], "initialize")
        self.assertEqual(envelope["repository"], str(self.repo.resolve()))
        self.assertEqual(envelope["previous_revision"], 0)
        self.assertEqual(envelope["before_state"], "absent")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        digest = hashlib.sha256((json.dumps(graph, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
        self.assertEqual(envelope["after_graph_digest"], digest)
        self.assertIsNone(envelope["before_graph_digest"])
        self.assertEqual(envelope["baseline_commit"], self._git("rev-parse", "HEAD"))
        self.assertEqual(envelope["head_commit"], envelope["baseline_commit"])

    def test_cli_subprocess_receipts_bind_repository_baseline_and_created_branch(self) -> None:
        initialize_home = self.root / "cli-subprocess"
        process = subprocess.run(
            [sys.executable, str(HELPER), "initialize", "--repo", str(self.repo),
             "--branch", BRANCH, "--state-home", str(initialize_home),
             "--json", json.dumps(_graph())],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        receipt = json.loads(process.stdout)
        self.assertEqual(receipt["repository"], str(self.repo.resolve()))
        self.assertEqual(receipt["branch"], BRANCH)
        self.assertEqual(receipt["baseline_commit"], self._git("rev-parse", "HEAD"))
        self.assertEqual(receipt["head_commit"], receipt["baseline_commit"])

        baseline = self._git("rev-parse", "HEAD")
        self._git("switch", "--detach", baseline)
        branch_process = subprocess.run(
            [sys.executable, str(HELPER), "create-branch", "--repo", str(self.repo),
             "--branch", "feature/cli-created", "--baseline", baseline, "--yes"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(branch_process.returncode, 0, branch_process.stderr)
        branch_receipt = json.loads(branch_process.stdout)
        self.assertEqual(set(branch_receipt), {"schema_version", "operation", "repository",
                                               "branch", "baseline_commit", "head_commit"})
        self.assertEqual(branch_receipt["repository"], str(self.repo.resolve()))
        self.assertEqual(branch_receipt["baseline_commit"], baseline)
        self.assertEqual(branch_receipt["head_commit"], baseline)

    def test_cli_rejects_duplicate_or_oversized_json_without_state_write(self) -> None:
        import io
        from contextlib import redirect_stderr
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.main(["initialize", "--repo", str(self.repo), "--branch", BRANCH,
                              "--state-home", str(self.state_home), "--json", '{"x":1,"x":2}'])
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.main(["initialize", "--repo", str(self.repo), "--branch", BRANCH,
                              "--state-home", str(self.state_home), "--json", "x" * (1024 * 1024 + 1)])

    def test_ready_change_stale_then_typed_evidence_decision_proof_projection_audit_repair(self) -> None:
        template = _graph()
        template["evidence"]["E1"]["supports"] = ["D1", "T1"]
        template["evidence"]["E2"] = {
            "kind": "repository", "fact": "The startup caller remains unchanged",
            "source": "startup.py", "fresh": True, "supports": ["T1"],
        }
        template["decisions"]["D1"] = {
            "question": "Where is validation owned?", "choice": "config boundary",
            "alternatives": [{"id": "A", "status": "selected", "reason": "one boundary"},
                             {"id": "B", "status": "rejected", "reason": "duplicates behavior"}],
            "based_on": ["E1"], "material": True, "version": 1,
            "confirmed_version": 1, "stale": False, "invalidates": ["T1", "P1"],
            "consequences": ["callers retain one contract"],
        }
        template["work"]["T1"]["decisions"] = ["D1"]
        template["projections"]["U1"].update(covers=["D1", "T1", "P1"], decision_versions={"D1": 1})
        template["projections"]["U2"] = {"covers": ["O1"], "version": 1,
            "decision_versions": {}, "presented": True, "confirmed": True, "stale": False,
            "presentation": "Configuration is validated."}
        template["audit"] = {"breadth": True, "reason": "Changes ownership across configuration and startup callers"}
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, template, self.state_home)
        self.assertEqual(receipt.state, "stale")

        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        audit = json.loads(json.dumps(graph["audit"]))
        audit.update(fresh=True, independent=True, graph_revision=graph["graph_revision"])
        receipt = self._typed_update(receipt.workflow_id, graph, "refresh-audit", ["audit"], audit)
        self.assertEqual(receipt.state, "ready")

        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        changed = json.loads(json.dumps(graph["evidence"]["E1"]))
        changed["fact"] = "Validation has a materially different boundary"
        stale = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["evidence", "E1"], "value": changed}], self.state_home)
        self.assertEqual(stale.state, "stale")
        stale_graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(stale_graph["projections"]["U2"]["confirmed"])

        evidence = json.loads(json.dumps(stale_graph["evidence"]["E1"]))
        evidence.update(fresh=True, observed_at="2026-01-01T00:00:00Z")
        self._typed_update(receipt.workflow_id, stale_graph, "refresh-evidence", ["evidence", "E1"], evidence)
        stale_graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        stale = self.helper.discover_workflow(self.repo, BRANCH, self.state_home)
        decision = self.helper.issue_decision_revalidation(graph=stale_graph, decision_id="D1",
            classification="unchanged-meaning", reason="The revised evidence preserves config-boundary ownership.")
        proof = json.loads(json.dumps(stale_graph["proof"]["P1"]))
        proof.update(fresh=True, evidence=[{
            "workflow_id": receipt.workflow_id, "graph_revision": stale.revision,
            "node": "T1", "branch": BRANCH, "commit": self._git("rev-parse", "HEAD"),
            "check": "python3 -m pytest", "result": {"status": "pass", "exit_code": 0}}])
        audit = json.loads(json.dumps(stale_graph["audit"]))
        audit.update(fresh=True, independent=True, graph_revision=stale.revision)
        projection = json.loads(json.dumps(stale_graph["projections"]["U1"]))
        projection.update(stale=False, presented=True, confirmed=False,
                          decision_versions={"D1": decision["version"]})
        presentation_graph = json.loads(json.dumps(stale_graph))
        presentation_graph["projections"]["U1"] = projection
        projection["presentation"] = self.helper.issue_projection_presentation(
            graph=presentation_graph, projection_id="U1", text="Validate at the revised configuration boundary and prove its behavior.")

        def typed(operation: str, path: list[str], value: dict[str, object]) -> dict[str, object]:
            version = value["version"] if operation in {"revalidate-decision", "regenerate-projection"} else value["record_version"]
            operation_receipt = self.helper.issue_operation_receipt(
                operation=operation, workflow_id=receipt.workflow_id,
                prior_graph_revision=stale.revision, target=path,
                record_version=version, value=value)
            return {"op": operation, "path": path, "value": value,
                    "prior_graph_revision": stale.revision, "record_version": version,
                    "receipt": operation_receipt}

        repaired = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, stale.revision,
            [typed("revalidate-decision", ["decisions", "D1"], decision),
             typed("refresh-proof", ["proof", "P1"], proof),
             typed("refresh-audit", ["audit"], audit),
             typed("regenerate-projection", ["projections", "U1"], projection)],
            self.state_home,
        )
        self.assertEqual(repaired.state, "awaiting-user")
        regenerated = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        confirmed = json.loads(json.dumps(regenerated["projections"]["U1"]))
        confirmed["confirmed"] = True
        final = self._typed_update(
            receipt.workflow_id, regenerated, "reconfirm-projection",
            ["projections", "U1"], confirmed,
        )
        final_graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(final.state, "ready")
        self.assertEqual(self.helper.derive_plan_state(final_graph), "ready")
        self.assertTrue(final_graph["projections"]["U2"]["confirmed"])
        self.assertTrue(final_graph["evidence"]["E2"]["fresh"])
        self.assertEqual(
            final_graph["evidence"]["E2"]["fact"],
            "The startup caller remains unchanged",
        )
        self.assertTrue(final_graph["evidence"]["E1"]["operation_receipt"])
        self.assertTrue(final_graph["decisions"]["D1"]["operation_receipt"])
        self.assertTrue(final_graph["proof"]["P1"]["operation_receipt"])
        self.assertTrue(final_graph["audit"]["operation_receipt"])

    def test_revised_unexecuted_proof_plan_can_be_confirmed_without_execution(self) -> None:
        receipt = self._initialize()
        changed = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["outcomes", "O1", "result"],
              "value": "Configuration is validated without coercion"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        proof = dict(graph["proof"]["P1"], fresh=True)
        refreshed = self._typed_update(
            receipt.workflow_id, graph, "refresh-proof-plan", ["proof", "P1"], proof)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["proof"]["P1"]["evidence"], [])
        self.assertFalse(graph["proof"]["P1"]["execution_required"])
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False)
        projection["presentation"] = self.helper.issue_projection_presentation(
            graph=graph, projection_id="U1", text="Validate configuration without coercion and prove that behavior.")
        self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        confirmed = dict(graph["projections"]["U1"], confirmed=True)
        ready = self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], confirmed)
        self.assertEqual(ready.state, "ready")
        self.assertGreater(ready.revision, refreshed.revision)
        self.assertEqual(changed.state, "stale")

    def test_initial_projection_retains_exact_presented_words_and_digests(self) -> None:
        graph = _graph()
        wording = "Validate configuration without coercion. Preserve valid input."
        graph["projections"]["U1"]["presentation"] = wording
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        stored = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        presentation = stored["projections"]["U1"]["presentation"]
        self.assertEqual(receipt.state, "ready")
        self.assertEqual(presentation["text"], wording)
        self.assertEqual(presentation["text_digest"], hashlib.sha256(wording.encode("utf-8")).hexdigest())
        self.assertEqual(presentation["source_digest"], self.helper.issue_projection_presentation(
            graph=stored, projection_id="U1", text=wording)["source_digest"])

    def test_projection_confirmation_cannot_change_presented_words(self) -> None:
        graph = _graph()
        graph["projections"]["U1"]["presentation"] = "Validate configuration."
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["projections", "U1", "presented"], "value": False}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        wording = "Validate configuration and preserve valid inputs."
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False,
            presentation=self.helper.issue_projection_presentation(graph=graph, projection_id="U1", text=wording))
        regenerated = self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        self.assertEqual(regenerated.state, "awaiting-user")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        substituted = dict(graph["projections"]["U1"], confirmed=True,
            presentation=self.helper.issue_projection_presentation(graph=graph, projection_id="U1", text="Accept every input."))
        with self.assertRaisesRegex(self.helper.PlanGraphError, "outside its authority"):
            self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], substituted)
        confirmed = dict(graph["projections"]["U1"], confirmed=True)
        final = self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], confirmed)
        stored = self.helper.load_workflow(self.repo, BRANCH, self.state_home)["projections"]["U1"]
        self.assertEqual(final.state, "ready")
        self.assertEqual(stored["presentation"]["text"], wording)
        self.assertEqual(stored["operation_receipt"]["value_digest"], self.helper._record_payload_digest(stored))

    def test_fresh_draft_projection_can_be_presented_then_confirmed(self) -> None:
        graph = _graph()
        graph["projections"]["U1"].update(presented=False, confirmed=False)
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.assertEqual(receipt.state, "awaiting-user")
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(loaded["projections"]["U1"], presented=True)
        presented = self._typed_update(receipt.workflow_id, loaded, "regenerate-projection", ["projections", "U1"], projection)
        self.assertEqual(presented.state, "awaiting-user")
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(loaded["projections"]["U1"], confirmed=True)
        confirmed = self._typed_update(receipt.workflow_id, loaded, "reconfirm-projection", ["projections", "U1"], projection)
        self.assertEqual(confirmed.state, "ready")

    def test_fresh_material_decision_can_receive_initial_confirmation_without_invalidation(self) -> None:
        template = _graph()
        template["decisions"]["D1"] = {
            "question": "Choose the validation policy", "choice": "Reject invalid input",
            "alternatives": [], "based_on": ["E1"], "material": True,
            "version": 1, "confirmed_version": None, "stale": False,
        }
        template["work"]["T1"]["decisions"] = ["D1"]
        template["projections"]["U1"].update(covers=["D1", "T1", "P1"], decision_versions={"D1": 1}, confirmed=False)
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, template, self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        decision = self.helper.issue_decision_confirmation(graph=graph, decision_id="D1", projection_id="U1")
        self._typed_update(receipt.workflow_id, graph, "confirm-decision", ["decisions", "D1"], decision)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], confirmed=True)
        result = self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], projection)
        final = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(result.state, "ready")
        self.assertEqual(final["decisions"]["D1"]["version"], 1)
        self.assertFalse(final["decisions"]["D1"]["stale"])
        self.assertEqual(final["decisions"]["D1"]["confirmation"]["presentation"], final["projections"]["U1"]["presentation"])
        self.assertEqual(final["decisions"]["D1"]["confirmation"]["trust"], "coordinator-attestation")
        self.assertEqual(final["proof"], graph["proof"])
        approved = final["decisions"]["D1"]["confirmation"]
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, final["graph_revision"],
            [{"op": "set", "path": ["work", "T1", "result"], "value": "Validate configuration through an adapter"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["decisions"]["D1"]["confirmation"], approved)
        revalidated = self.helper.issue_decision_revalidation(graph=graph, decision_id="D1",
            classification="unchanged-meaning", reason="The adapter preserves the rejection policy.")
        self._typed_update(receipt.workflow_id, graph, "revalidate-decision", ["decisions", "D1"], revalidated)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self._typed_update(receipt.workflow_id, graph, "refresh-proof-plan", ["proof", "P1"], dict(graph["proof"]["P1"], fresh=True))
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False,
            presentation=self.helper.issue_projection_presentation(graph=graph, projection_id="U1", text="Validate configuration through the adapter and reject invalid input."))
        self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], dict(graph["projections"]["U1"], confirmed=True)).state, "ready")
        final = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(final["decisions"]["D1"]["confirmation"], approved)
        self.assertEqual(final["decisions"]["D1"]["confirmed_version"], 1)
        self.assertNotEqual(final["projections"]["U1"]["presentation"], approved["presentation"])
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, final["graph_revision"],
            [{"op": "set", "path": ["decisions", "D1", "choice"], "value": "Reject unknown and malformed input"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(graph["decisions"]["D1"]["stale"])
        self.assertEqual(graph["decisions"]["D1"]["version"], 2)
        self.assertEqual(graph["decisions"]["D1"]["confirmed_version"], 1)
        self._typed_update(receipt.workflow_id, graph, "refresh-proof-plan", ["proof", "P1"], dict(graph["proof"]["P1"], fresh=True))
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False, decision_versions={"D1": 2})
        presentation_graph = json.loads(json.dumps(graph))
        presentation_graph["projections"]["U1"] = projection
        projection["presentation"] = self.helper.issue_projection_presentation(graph=presentation_graph, projection_id="U1", text="Reject unknown and malformed input through the adapter.")
        self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "retained current presentation"):
            self._typed_update(receipt.workflow_id, graph, "reconfirm-decision", ["decisions", "D1"], dict(graph["decisions"]["D1"], stale=False, confirmed_version=2))
        decision = self.helper.issue_decision_confirmation(graph=graph, decision_id="D1", projection_id="U1")
        self._typed_update(receipt.workflow_id, graph, "reconfirm-decision", ["decisions", "D1"], decision)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], dict(graph["projections"]["U1"], confirmed=True)).state, "ready")
        final = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(final["decisions"]["D1"]["confirmation"]["presentation"], final["projections"]["U1"]["presentation"])

    def test_initial_decision_confirmation_rejects_missing_or_changed_presented_binding(self) -> None:
        template = _graph()
        template["decisions"]["D1"] = {
            "question": "Choose validation", "choice": "Reject invalid input",
            "alternatives": [], "based_on": ["E1"], "material": True,
            "version": 1, "confirmed_version": None, "stale": False,
        }
        template["work"]["T1"]["decisions"] = ["D1"]
        template["projections"]["U1"].update(covers=["D1", "T1", "P1"], decision_versions={"D1": 1}, confirmed=False)
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, template, self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        decision = self.helper.issue_decision_confirmation(graph=graph, decision_id="D1", projection_id="U1")
        missing = dict(decision)
        del missing["confirmation"]
        wrong_text = json.loads(json.dumps(decision))
        wrong_text["confirmation"]["presentation"]["text"] = "Accept invalid input."
        wrong_text["confirmation"]["presentation"]["text_digest"] = hashlib.sha256(b"Accept invalid input.").hexdigest()
        wrong_choice = dict(decision, choice="Accept invalid input")
        for value in (missing, wrong_text, wrong_choice):
            with self.subTest(value=value), self.assertRaises(self.helper.PlanGraphError):
                self._typed_update(receipt.workflow_id, graph, "confirm-decision", ["decisions", "D1"], value)
        for field, value in (("presented", False), ("stale", True), ("decision_versions", {"D1": 2}), ("presentation", None)):
            unavailable = json.loads(json.dumps(graph))
            unavailable["projections"]["U1"][field] = value
            with self.subTest(field=field), self.assertRaises(self.helper.PlanGraphError):
                self.helper.issue_decision_confirmation(graph=unavailable, decision_id="D1", projection_id="U1")
        self._typed_update(receipt.workflow_id, graph, "confirm-decision", ["decisions", "D1"], decision)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "not current"):
            self._typed_update(receipt.workflow_id, graph, "confirm-decision", ["decisions", "D1"], dict(graph["decisions"]["D1"]))
        downgraded = dict(graph["decisions"]["D1"])
        del downgraded["confirmation"]
        with self.assertRaisesRegex(self.helper.PlanGraphError, "typed confirmation"):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
                                      [{"op": "set", "path": ["decisions", "D1"], "value": downgraded}], self.state_home)

    def test_adaptive_material_decision_insertion_is_fresh_presentable_and_first_confirmable(self) -> None:
        receipt = self._initialize()
        decision = {
            "question": "Choose compatibility policy", "choice": "Reject unknown configuration keys",
            "alternatives": [], "based_on": ["E1"], "material": True,
            "version": 1, "confirmed_version": None, "stale": False,
            "invalidates": [], "consequences": [], "operation_receipt": None,
        }
        inserted = self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["decisions", "D1"], "value": decision},
             {"op": "set", "path": ["work", "T1", "decisions"], "value": ["D1"]},
             {"op": "set", "path": ["projections", "U1", "covers"], "value": ["D1", "T1", "P1"]}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(inserted.state, "stale")
        self.assertEqual(graph["decisions"]["D1"]["version"], 1)
        self.assertIsNone(graph["decisions"]["D1"]["confirmed_version"])
        self.assertFalse(graph["decisions"]["D1"]["stale"])
        self.assertFalse(graph["decisions"]["D1"].get("revalidation_required", False))
        self.assertFalse(graph["proof"]["P1"]["fresh"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        self._typed_update(receipt.workflow_id, graph, "refresh-proof-plan", ["proof", "P1"], dict(graph["proof"]["P1"], fresh=True))
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False, decision_versions={"D1": 1})
        presented_graph = json.loads(json.dumps(graph))
        presented_graph["projections"]["U1"] = projection
        projection["presentation"] = self.helper.issue_projection_presentation(graph=presented_graph, projection_id="U1", text="Validate configuration and reject unknown keys.")
        self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        decision = self.helper.issue_decision_confirmation(graph=graph, decision_id="D1", projection_id="U1")
        self._typed_update(receipt.workflow_id, graph, "confirm-decision", ["decisions", "D1"], decision)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        confirmed = self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], dict(graph["projections"]["U1"], confirmed=True))
        self.assertEqual(confirmed.state, "ready")
        final = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(final["decisions"]["D1"]["version"], 1)
        self.assertEqual(final["decisions"]["D1"]["confirmation"]["presentation"], final["projections"]["U1"]["presentation"])

    def test_adaptive_insertion_does_not_preserve_changed_existing_approval_or_accept_preconfirmed_new_choice(self) -> None:
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, self._shared_decision_graph(), self.state_home)
        decision = {
            "question": "Choose another policy", "choice": "Use bounded validation",
            "alternatives": [], "based_on": ["E1"], "material": True,
            "version": 1, "confirmed_version": None, "stale": False,
            "invalidates": [], "consequences": [], "operation_receipt": None,
        }
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["decisions", "D3"], "value": decision},
             {"op": "set", "path": ["decisions", "D1", "choice"], "value": "A materially changed choice"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["decisions"]["D3"]["version"], 1)
        self.assertFalse(graph["decisions"]["D3"]["stale"])
        self.assertEqual(graph["decisions"]["D1"]["version"], 2)
        self.assertEqual(graph["decisions"]["D1"]["confirmed_version"], 1)
        self.assertTrue(graph["decisions"]["D1"]["stale"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        self.assertTrue(graph["projections"]["U3"]["confirmed"])
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
            [{"op": "set", "path": ["decisions", "D4"], "value": dict(decision, confirmed_version=1)}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(graph["decisions"]["D4"]["stale"])
        self.assertNotEqual(graph["decisions"]["D4"]["version"], graph["decisions"]["D4"]["confirmed_version"])
        self.assertNotEqual(graph["lifecycle"]["derived_state"], "ready")

    def test_original_accepted_basis_survives_source_and_execution_rotations_unchanged(self) -> None:
        receipt = self._initialize()
        original = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        basis = self.helper.freeze_acceptance_basis(self.repo, BRANCH, receipt.workflow_id, receipt.revision, self.state_home)
        basis_path = Path(basis["path"])
        basis_digest = basis["digest"]
        self.assertNotEqual(basis_path.parent, receipt.path.parent)
        self.assertEqual(self.helper.freeze_acceptance_basis(self.repo, BRANCH, receipt.workflow_id, receipt.revision, self.state_home), basis)
        (self.repo / "config.py").write_text("CONFIG = {'implemented': True}\n", encoding="utf-8")
        self._git("add", "config.py")
        self._git("commit", "-m", "authorized implementation")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self._typed_update(receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"],
            dict(graph["evidence"]["E1"], fresh=True, revision=self._git("rev-parse", "HEAD"), observed_at="2026-10-02T14:00:00Z"))
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        command = [sys.executable, "-c", "import config; assert config.CONFIG['implemented'] is True"]
        result = subprocess.run(command, cwd=self.repo, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = dict(graph["proof"]["P1"], fresh=True, evidence=[{
            "workflow_id": receipt.workflow_id, "graph_revision": graph["graph_revision"],
            "node": "T1", "branch": BRANCH, "commit": self._git("rev-parse", "HEAD"),
            "check": shlex.join(command), "result": {"status": "pass", "exit_code": result.returncode},
        }])
        self._typed_update(receipt.workflow_id, graph, "refresh-proof", ["proof", "P1"], proof)
        current = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        previous = json.loads(receipt.previous_path.read_text(encoding="utf-8"))
        self.assertGreater(current["graph_revision"], original["graph_revision"] + 1)
        self.assertGreater(previous["graph_revision"], original["graph_revision"])
        self.assertTrue(current["proof"]["P1"]["execution_required"])
        self.assertEqual(hashlib.sha256(basis_path.read_bytes()).hexdigest(), basis_digest)
        self.assertEqual(json.loads(basis_path.read_text(encoding="utf-8")), original)
        self.assertEqual(self.helper.load_acceptance_basis(basis_path, basis_digest), original)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "already frozen"):
            self.helper.freeze_acceptance_basis(self.repo, BRANCH, receipt.workflow_id, current["graph_revision"], self.state_home)
        self.helper.discard_workflow(self.repo, BRANCH, receipt.workflow_id, current["graph_revision"], True, self.state_home)
        self.assertFalse(receipt.path.exists())
        self.assertEqual(self.helper.load_acceptance_basis(basis_path, basis_digest), original)

    def test_acceptance_freeze_rejects_wrong_identity_stale_revision_and_changed_or_unconfirmed_grounding(self) -> None:
        receipt = self._initialize()
        for workflow_id, revision in (("0" * 32, receipt.revision), (receipt.workflow_id, receipt.revision + 1)):
            with self.subTest(workflow_id=workflow_id, revision=revision), self.assertRaises(self.helper.PlanGraphError):
                self.helper.freeze_acceptance_basis(self.repo, BRANCH, workflow_id, revision, self.state_home)
        self.assertFalse((self.state_home / "acceptance-bases").exists())
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["projections", "U1", "confirmed"], "value": False}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "current ready"):
            self.helper.freeze_acceptance_basis(self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"], self.state_home)
        home = self.root / "changed-before-freeze"
        ready = self._initialize(home)
        (self.repo / "config.py").write_text("CONFIG = {'edited_before_freeze': True}\n", encoding="utf-8")
        with self.assertRaises(self.helper.RevisionConflict):
            self.helper.freeze_acceptance_basis(self.repo, BRANCH, ready.workflow_id, ready.revision, home)
        graph = self.helper.load_workflow(self.repo, BRANCH, home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "current ready"):
            self.helper.freeze_acceptance_basis(self.repo, BRANCH, ready.workflow_id, graph["graph_revision"], home)
        self.assertFalse((home / "acceptance-bases").exists())

    def test_acceptance_cli_export_is_complete_and_detects_changed_bytes_or_symlink_substitution(self) -> None:
        receipt = self._initialize()
        exported = subprocess.run([sys.executable, str(HELPER), "freeze-acceptance", "--repo", str(self.repo),
                                   "--branch", BRANCH, "--workflow-id", receipt.workflow_id, "--revision", str(receipt.revision),
                                   "--state-home", str(self.state_home)], capture_output=True, text=True, check=False)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        basis = json.loads(exported.stdout)
        self.assertEqual(basis["schema_version"], "plan-acceptance-basis-receipt.v1")
        path = Path(basis["path"])
        original_bytes = path.read_bytes()
        original = self.helper.load_acceptance_basis(path, basis["digest"])
        self.assertEqual(original, self.helper.load_workflow(self.repo, BRANCH, self.state_home))
        self.assertEqual(basis["graph_digest"], self.helper._canonical_digest(original))
        path.write_bytes(original_bytes + b"\n")
        with self.assertRaisesRegex(self.helper.PlanGraphError, "retained digest"):
            self.helper.load_acceptance_basis(path, basis["digest"])
        with self.assertRaisesRegex(self.helper.PlanGraphError, "bytes were altered"):
            self.helper.freeze_acceptance_basis(self.repo, BRANCH, receipt.workflow_id, receipt.revision, self.state_home)
        self.assertEqual(path.read_bytes(), original_bytes + b"\n")
        path.unlink()
        path.symlink_to(receipt.path)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_acceptance_basis(path, basis["digest"])
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.freeze_acceptance_basis(self.repo, BRANCH, receipt.workflow_id, receipt.revision, self.state_home)

    def test_projection_rejects_presentation_bound_to_superseded_graph_meaning(self) -> None:
        graph = _graph()
        graph["projections"]["U1"]["presentation"] = "Validate configuration."
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "Accept malformed configuration"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        old_presentation = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "graph meaning is stale"):
            self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], old_presentation)

    def test_legacy_ready_projection_requires_real_presentation_and_confirmation(self) -> None:
        graph = _graph()
        graph["projections"]["U1"]["presentation"] = "Validate configuration."
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        legacy = json.loads(receipt.path.read_text(encoding="utf-8"))
        del legacy["projections"]["U1"]["presentation"]
        receipt.path.write_text(json.dumps(legacy), encoding="utf-8")
        before = receipt.path.read_bytes()
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(loaded["lifecycle"]["derived_state"], "awaiting-user")
        self.assertNotIn("presentation", loaded["projections"]["U1"])
        self.assertEqual(before, receipt.path.read_bytes())
        projection = dict(loaded["projections"]["U1"], presented=True, confirmed=False,
            presentation=self.helper.issue_projection_presentation(
                graph=loaded, projection_id="U1", text="Validate configuration and preserve valid inputs."))
        regenerated = self._typed_update(receipt.workflow_id, loaded, "regenerate-projection", ["projections", "U1"], projection)
        self.assertEqual(regenerated.state, "awaiting-user")
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(loaded["projections"]["U1"], confirmed=True)
        confirmed = self._typed_update(receipt.workflow_id, loaded, "reconfirm-projection", ["projections", "U1"], projection)
        self.assertEqual(confirmed.state, "ready")

    def test_projection_wording_edits_revoke_confirmation_and_detect_digest_tampering(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        revised = self.helper.issue_projection_presentation(
            graph=graph, projection_id="U1", text="Reject invalid configuration and preserve valid input.")
        changed = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["projections", "U1", "presentation"], "value": revised}], self.state_home)
        stored = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(changed.state, "stale")
        self.assertFalse(stored["projections"]["U1"]["confirmed"])
        self.assertEqual(stored["projections"]["U1"]["version"], 2)
        stored["projections"]["U1"]["presentation"]["text"] = "Silently accept invalid configuration."
        changed.path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaisesRegex(self.helper.PlanGraphError, "text digest mismatch"):
            self.helper.load_workflow(self.repo, BRANCH, self.state_home)

    def test_explicit_wording_clarification_retains_approved_text_and_literal_history(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        approved = dict(graph["projections"]["U1"]["presentation"])
        for wording in ("Reject invalid configuration and preserve valid input.", "Invalid input fails validation; valid input loads."):
            value = dict(graph["projections"]["U1"])
            value["clarifications"] = value.get("clarifications", []) + [{
                "presentation": self.helper.issue_projection_presentation(graph=graph, projection_id="U1", text=wording),
                "approved_presentation": approved,
                "classification": "unchanged-meaning",
                "trust": "coordinator-attestation",
                "reason": "The user requested clearer wording without changing the accepted validation behavior.",
                "prior_graph_revision": graph["graph_revision"],
            }]
            result = self._typed_update(receipt.workflow_id, graph, "clarify-projection", ["projections", "U1"], value)
            graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
            self.assertEqual(result.state, "ready")
            self.assertEqual(graph["projections"]["U1"]["presentation"], approved)
            self.assertEqual(graph["projections"]["U1"]["version"], 1)
            self.assertTrue(graph["projections"]["U1"]["confirmed"])
            self.assertEqual(graph["projections"]["U1"]["clarifications"][-1]["presentation"]["text"], wording)
            self.assertEqual(graph["projections"]["U1"]["operation_receipt"]["value_digest"], self.helper._record_payload_digest(graph["projections"]["U1"]))
        self.assertEqual(len(graph["projections"]["U1"]["clarifications"]), 2)

    def test_clarification_api_requires_explicit_classification_and_current_approval(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        kwargs = {"graph": graph, "projection_id": "U1", "text": "Valid input loads; invalid input fails.", "reason": "The user requested clearer wording."}
        with self.assertRaisesRegex(self.helper.PlanGraphError, "explicit unchanged-meaning"):
            self.helper.issue_projection_clarification(**kwargs, classification="changed-meaning")
        for change in ({"confirmed": False}, {"stale": True}, {"presentation": None}):
            with self.subTest(change=change):
                changed = json.loads(json.dumps(graph))
                changed["projections"]["U1"].update(change)
                with self.assertRaisesRegex(self.helper.PlanGraphError, "current confirmed presentation"):
                    self.helper.issue_projection_clarification(**dict(kwargs, graph=changed), classification="unchanged-meaning")
        self.assertEqual(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["graph_revision"], receipt.revision)

    def test_clarification_rejects_changed_source_tampered_wording_and_unsupported_trust(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        value = self.helper.issue_projection_clarification(graph=graph, projection_id="U1",
            text="Valid input loads; invalid input fails.", classification="unchanged-meaning", reason="Clearer wording requested.")
        for field, replacement in (("text", "Accept invalid input."), ("source_digest", "0" * 64), ("trust", "verified-semantic-equivalence")):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(value))
                row = changed["clarifications"][-1]
                if field == "trust":
                    row[field] = replacement
                else:
                    row["presentation"][field] = replacement
                before = receipt.path.read_bytes()
                with self.assertRaises(self.helper.PlanGraphError):
                    self._typed_update(receipt.workflow_id, graph, "clarify-projection", ["projections", "U1"], changed)
                self.assertEqual(receipt.path.read_bytes(), before)
        with self.assertRaises(self.helper.PlanGraphError):
            operation = self.helper.issue_operation_receipt(operation="clarify-projection", workflow_id=receipt.workflow_id,
                prior_graph_revision=graph["graph_revision"], target=["projections", "U1"], record_version=value["version"], value=value)
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"], [
                {"op": "set", "path": ["work", "T1", "result"], "value": "Accept invalid input"},
                {"op": "clarify-projection", "path": ["projections", "U1"], "value": value,
                 "prior_graph_revision": graph["graph_revision"], "record_version": value["version"], "receipt": operation}], self.state_home)

    def test_clarification_history_cannot_be_erased_or_rewritten_by_later_operations(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        value = self.helper.issue_projection_clarification(graph=graph, projection_id="U1",
            text="Valid input loads; invalid input fails.", classification="unchanged-meaning", reason="Clearer wording requested.")
        clarified = self._typed_update(receipt.workflow_id, graph, "clarify-projection", ["projections", "U1"], value)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "history changes require"):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, clarified.revision,
                [{"op": "set", "path": ["projections", "U1", "clarifications"], "value": []}], self.state_home)
        next_value = self.helper.issue_projection_clarification(graph=graph, projection_id="U1",
            text="Validation preserves valid inputs and rejects invalid inputs.", classification="unchanged-meaning", reason="A second wording clarification.")
        next_value["clarifications"][0]["reason"] = "Rewrite the old history"
        with self.assertRaisesRegex(self.helper.PlanGraphError, "one appended history record"):
            self._typed_update(receipt.workflow_id, graph, "clarify-projection", ["projections", "U1"], next_value)
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, clarified.revision,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "Apply a changed validation policy"}], self.state_home)
        stale = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(stale["projections"]["U1"]["confirmed"])
        self.assertEqual(stale["projections"]["U1"]["clarifications"], graph["projections"]["U1"]["clarifications"])
        proof = dict(stale["proof"]["P1"], fresh=True)
        self._typed_update(receipt.workflow_id, stale, "refresh-proof-plan", ["proof", "P1"], proof)
        current = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(current["projections"]["U1"], stale=False, presented=True, confirmed=False,
            presentation=self.helper.issue_projection_presentation(graph=current, projection_id="U1", text="Apply the changed validation policy."))
        regenerated = self._typed_update(receipt.workflow_id, current, "regenerate-projection", ["projections", "U1"], projection)
        self.assertEqual(regenerated.state, "awaiting-user")
        current = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "current confirmed presentation"):
            self.helper.issue_projection_clarification(graph=current, projection_id="U1", text="Clarify the new policy.",
                classification="unchanged-meaning", reason="A wording clarification cannot replace missing assent.")
        confirmed = dict(current["projections"]["U1"], confirmed=True)
        self.assertEqual(self._typed_update(receipt.workflow_id, current, "reconfirm-projection", ["projections", "U1"], confirmed).state, "ready")
        self.assertEqual(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["projections"]["U1"]["clarifications"], graph["projections"]["U1"]["clarifications"])

    def test_projection_digest_binds_referenced_outcomes_and_evidence(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        original = self.helper.issue_projection_presentation(graph=graph, projection_id="U1", text="Validate configuration.")
        for family, record_id, field in (("outcomes", "O1", "result"), ("evidence", "E1", "fact")):
            with self.subTest(family=family):
                changed = json.loads(json.dumps(graph))
                changed[family][record_id][field] = "Materially different accepted meaning"
                revised = self.helper.issue_projection_presentation(graph=changed, projection_id="U1", text=original["text"])
                self.assertEqual(original["text_digest"], revised["text_digest"])
                self.assertNotEqual(original["source_digest"], revised["source_digest"])
        self.assertEqual(receipt.state, "ready")

    def test_legacy_repository_revision_digests_remain_readable_and_reconcile_without_rewriting_assent(self) -> None:
        receipt = self._initialize()
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        presentation = graph["projections"]["U1"]["presentation"]
        presentation["source_digest"] = self.helper._projection_source_digest(graph, graph["projections"]["U1"], digest_version=1)
        del presentation["source_digest_version"]
        receipt.path.write_text(json.dumps(graph), encoding="utf-8")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["lifecycle"]["derived_state"], "ready")
        approved = dict(graph["projections"]["U1"]["presentation"])
        clarification = self.helper.issue_projection_clarification(graph=graph, projection_id="U1", text="Accept valid configuration and reject invalid configuration.",
            classification="unchanged-meaning", reason="Clarify the existing policy.")
        self._typed_update(receipt.workflow_id, graph, "clarify-projection", ["projections", "U1"], clarification)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["projections"]["U1"]["presentation"], approved)
        self.assertNotIn("source_digest_version", graph["projections"]["U1"]["clarifications"][-1]["presentation"])
        (self.repo / "config.py").write_text("CONFIG = {'approved_correction': True}\n", encoding="utf-8")
        self._git("add", "config.py")
        self._git("commit", "-m", "approved correction")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self._typed_update(receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"],
            dict(graph["evidence"]["E1"], fresh=True, revision=self._git("rev-parse", "HEAD"), observed_at="2026-10-02T12:00:00Z"))
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        self.assertEqual(graph["projections"]["U1"]["presentation"], approved)
        self._typed_update(receipt.workflow_id, graph, "refresh-proof-plan", ["proof", "P1"], dict(graph["proof"]["P1"], fresh=True))
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False,
            presentation=self.helper.issue_projection_presentation(graph=graph, projection_id="U1", text=approved["text"]))
        self.assertEqual(projection["presentation"]["source_digest_version"], 2)
        result = self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        self.assertEqual(result.state, "awaiting-user")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        result = self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], dict(graph["projections"]["U1"], confirmed=True))
        self.assertEqual(result.state, "ready")

    def test_proof_freshness_change_preserves_valid_dependency_presentation_binding(self) -> None:
        graph = self._three_node_graph()
        graph["work"]["T2"]["requires"] = ["T1"]
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        changed = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["proof", "P1", "fresh"], "value": False}], self.state_home)
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(changed.state, "stale")
        self.assertTrue(loaded["projections"]["U2"]["confirmed"])
        self.assertEqual(loaded["projections"]["U2"]["presentation"]["source_digest"],
            self.helper.issue_projection_presentation(graph=loaded, projection_id="U2", text="Consumer 2 works.")["source_digest"])

    def _three_node_graph(self) -> dict[str, object]:
        graph = _graph()
        for number in (2, 3):
            outcome, evidence, work, proof, projection = (f"{prefix}{number}" for prefix in "OETPU")
            graph["outcomes"][outcome] = {"kind": "outcome", "result": f"Consumer {number} works"}
            graph["evidence"][evidence] = dict(graph["evidence"]["E1"],
                source=f"consumer{number}.py", supports=[work])
            graph["work"][work] = dict(graph["work"]["T1"], covers=[outcome],
                based_on=[evidence], proof=[proof], repository_boundary=[f"consumer{number}.py"])
            graph["proof"][proof] = json.loads(json.dumps(graph["proof"]["P1"]))
            graph["proof"][proof].update(covers=[outcome], required_by=[work])
            graph["projections"][projection] = dict(graph["projections"]["U1"], covers=[work, proof],
                presentation=f"Consumer {number} works and has planned configuration proof.")
        return graph

    def _shared_decision_graph(self) -> dict[str, object]:
        graph = self._three_node_graph()
        for number in (1, 2):
            graph["decisions"][f"D{number}"] = {
                "question": f"Choose policy {number}", "choice": f"Retain policy {number}",
                "alternatives": [], "based_on": [f"E{number}"], "material": True,
                "version": 1, "confirmed_version": 1, "stale": False,
            }
        graph["work"]["T1"]["decisions"] = ["D1", "D2"]
        graph["work"]["T2"]["decisions"] = ["D1"]
        graph["work"]["T3"]["decisions"] = ["D2"]
        graph["projections"]["U1"].update(covers=["D1", "D2", "T1", "P1"], decision_versions={"D1": 1, "D2": 1})
        graph["projections"]["U2"].update(covers=["D1", "T2", "P2"], decision_versions={"D1": 1})
        graph["projections"]["U3"].update(covers=["D2", "T3", "P3"], decision_versions={"D2": 1})
        return graph

    def test_technical_slice_change_preserves_shared_decision_versions_and_unrelated_slices(self) -> None:
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, self._shared_decision_graph(), self.state_home)
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "Validate configuration through the revised adapter"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        for decision in graph["decisions"].values():
            self.assertEqual(decision["version"], 1)
            self.assertEqual(decision["confirmed_version"], 1)
            self.assertFalse(decision["stale"])
            self.assertTrue(decision["revalidation_required"])
        self.assertFalse(graph["proof"]["P1"]["fresh"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        for number in (2, 3):
            self.assertTrue(graph["proof"][f"P{number}"]["fresh"])
            self.assertTrue(graph["projections"][f"U{number}"]["confirmed"])

    def test_dependency_revalidation_requires_current_grounding_explicit_classification_and_typed_authority(self) -> None:
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, self._shared_decision_graph(), self.state_home)
        (self.repo / "config.py").write_text("CONFIG = {'authorized_edit': True}\n", encoding="utf-8")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "refreshed dependency evidence"):
            self.helper.issue_decision_revalidation(graph=graph, decision_id="D1", classification="unchanged-meaning", reason="Grounding is still stale.")
        evidence = dict(graph["evidence"]["E1"], fresh=True, observed_at="2026-10-02T12:00:00Z")
        self._typed_update(receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"], evidence)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "explicit unchanged-meaning"):
            self.helper.issue_decision_revalidation(graph=graph, decision_id="D1", classification="equivalent", reason="Unsupported classification.")
        value = self.helper.issue_decision_revalidation(graph=graph, decision_id="D1", classification="unchanged-meaning", reason="The authorized correction preserves policy 1.")
        self.assertEqual(value["revalidation"]["trust"], "coordinator-attestation")
        for field, replacement in (("trust", "verified-semantic-equivalence"), ("decision_digest", "0" * 64), ("dependency_digest", "0" * 64)):
            bad = json.loads(json.dumps(value))
            bad["revalidation"][field] = replacement
            with self.subTest(field=field), self.assertRaises(self.helper.PlanGraphError):
                self._typed_update(receipt.workflow_id, graph, "revalidate-decision", ["decisions", "D1"], bad)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "typed revalidation"):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
                [{"op": "set", "path": ["decisions", "D1"], "value": value}], self.state_home)
        changed_choice = dict(value, choice="A different policy")
        with self.assertRaisesRegex(self.helper.PlanGraphError, "outside its authority"):
            self._typed_update(receipt.workflow_id, graph, "revalidate-decision", ["decisions", "D1"], changed_choice)
        self._typed_update(receipt.workflow_id, graph, "revalidate-decision", ["decisions", "D1"], value)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["decisions"]["D1"]["version"], 1)
        self.assertEqual(graph["lifecycle"]["derived_state"], "stale")
        self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
            [{"op": "set", "path": ["decisions", "D1", "choice"], "value": "A changed rejection policy"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["decisions"]["D1"]["version"], 2)
        self.assertEqual(graph["decisions"]["D1"]["confirmed_version"], 1)
        self.assertTrue(graph["decisions"]["D1"]["stale"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        self.assertFalse(graph["projections"]["U2"]["confirmed"])
        self.assertTrue(graph["projections"]["U3"]["confirmed"])
        with self.assertRaisesRegex(self.helper.PlanGraphError, "unchanged decision"):
            self.helper.issue_decision_revalidation(graph=graph, decision_id="D1", classification="unchanged-meaning", reason="Changed choices need fresh assent.")

    def test_approved_source_edit_can_refresh_grounding_without_fabricated_reconfirmation(self) -> None:
        for committed in (False, True):
            with self.subTest(committed=committed):
                home = self.root / f"source-edit-{committed}"
                receipt = self.helper.initialize_workflow(self.repo, BRANCH, self._shared_decision_graph(), home)
                before = self.helper.load_workflow(self.repo, BRANCH, home)
                (self.repo / "config.py").write_text(f"CONFIG = {{'authorized_correction': {committed}}}\n", encoding="utf-8")
                if committed:
                    self._git("add", "config.py")
                    self._git("commit", "-m", "authorized correction")
                graph = self.helper.load_workflow(self.repo, BRANCH, home)
                self.assertEqual(graph["decisions"]["D1"]["version"], 1)
                self.assertEqual(graph["decisions"]["D1"]["confirmed_version"], 1)
                self.assertFalse(graph["decisions"]["D1"]["stale"])
                self.assertTrue(graph["decisions"]["D1"]["revalidation_required"])
                self.assertTrue(graph["projections"]["U1"]["confirmed"])
                self.assertEqual(graph["projections"]["U1"]["presentation"], before["projections"]["U1"]["presentation"])
                self.assertEqual(graph["lifecycle"]["derived_state"], "stale")
                evidence = dict(graph["evidence"]["E1"], fresh=True, revision=self._git("rev-parse", "HEAD"), observed_at="2026-10-02T12:00:00Z")
                self._typed_update(receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"], evidence, home)
                graph = self.helper.load_workflow(self.repo, BRANCH, home)
                for decision_id, decision in list(graph["decisions"].items()):
                    if decision.get("revalidation_required"):
                        value = self.helper.issue_decision_revalidation(graph=graph, decision_id=decision_id,
                            classification="unchanged-meaning", reason="Authorized source correction preserves the chosen policy.")
                        self._typed_update(receipt.workflow_id, graph, "revalidate-decision", ["decisions", decision_id], value, home)
                        graph = self.helper.load_workflow(self.repo, BRANCH, home)
                for proof_id, proof in list(graph["proof"].items()):
                    if not proof["fresh"]:
                        self._typed_update(receipt.workflow_id, graph, "refresh-proof-plan", ["proof", proof_id], dict(proof, fresh=True), home)
                        graph = self.helper.load_workflow(self.repo, BRANCH, home)
                self.assertEqual(graph["lifecycle"]["derived_state"], "ready")
                for projection_id, projection in graph["projections"].items():
                    self.assertEqual(projection, before["projections"][projection_id])

    def test_producer_meaning_change_stales_transitive_consumer_proof_and_confirmation(self) -> None:
        graph = self._three_node_graph()
        graph["work"]["T2"]["requires"] = ["T1"]
        graph["work"]["T3"]["requires"] = ["T2"]
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["work", "T1", "result"],
              "value": "Produce a materially different result contract"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        for number in (1, 2, 3):
            self.assertFalse(graph["proof"][f"P{number}"]["fresh"])
            self.assertFalse(graph["projections"][f"U{number}"]["confirmed"])

    def test_crossed_proof_attachments_cannot_cover_unrelated_work_outcomes(self) -> None:
        graph = self._three_node_graph()
        graph["work"]["T1"]["proof"] = ["P2"]
        graph["work"]["T2"]["proof"] = ["P1"]
        graph["proof"]["P1"]["required_by"] = ["T2"]
        graph["proof"]["P2"]["required_by"] = ["T1"]
        with self.assertRaisesRegex(self.helper.PlanGraphError, "work outcome lacks attached or downstream join proof"):
            self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)

    def test_shared_proof_can_cover_each_attached_work_outcome(self) -> None:
        graph = self._three_node_graph()
        graph["work"]["T2"]["proof"] = ["P1"]
        graph["proof"]["P1"].update(covers=["O1", "O2"], required_by=["T1", "T2"])
        del graph["proof"]["P2"]
        graph["projections"]["U2"]["covers"] = ["T2", "P1"]
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.assertEqual(receipt.state, "ready")

    def test_downstream_join_proof_can_cover_upstream_outcome(self) -> None:
        graph = self._three_node_graph()
        graph["outcomes"]["C1"] = {"kind": "constraint", "result": "Local validation remains compatible"}
        graph["work"]["T1"]["covers"] = ["O1", "C1"]
        graph["proof"]["P1"]["covers"] = ["C1"]
        graph["work"]["T2"].update(kind="join", requires=["T1"], covers=["O1", "O2"])
        graph["proof"]["P2"]["covers"] = ["O1", "O2"]
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.assertEqual(receipt.state, "ready")

    def test_unrelated_join_cannot_supply_missing_upstream_proof(self) -> None:
        graph = self._three_node_graph()
        graph["proof"]["P1"]["covers"] = ["O2"]
        graph["work"]["T2"].update(kind="join", covers=["O1", "O2"])
        graph["proof"]["P2"]["covers"] = ["O1", "O2"]
        with self.assertRaisesRegex(self.helper.PlanGraphError, "work outcome lacks attached or downstream join proof"):
            self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)

    def test_transitive_join_proof_covers_outcome_after_intermediate_work(self) -> None:
        graph = self._three_node_graph()
        graph["outcomes"]["C1"] = {"kind": "constraint", "result": "Local validation remains compatible"}
        graph["work"]["T1"]["covers"] = ["O1", "C1"]
        graph["proof"]["P1"]["covers"] = ["C1"]
        graph["work"]["T2"]["requires"] = ["T1"]
        graph["work"]["T3"].update(kind="join", requires=["T2"], covers=["O1", "O3"])
        graph["proof"]["P3"]["covers"] = ["O1", "O3"]
        self.assertEqual(self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home).state, "ready")

    def test_explicit_invalidation_edges_propagate_across_record_families(self) -> None:
        graph = self._three_node_graph()
        graph["decisions"]["D1"] = {
            "question": "Choose validation policy", "choice": "Strict validation",
            "alternatives": [], "based_on": ["E1"], "material": True,
            "version": 1, "confirmed_version": 1, "stale": False,
            "invalidates": ["E2"], "consequences": []}
        graph["projections"]["U1"].update(covers=["D1", "T1", "P1"], decision_versions={"D1": 1})
        graph["invalidations"] = [{"source": "P2", "targets": ["E3"]}]
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["decisions", "D1", "choice"],
              "value": "Compatibility validation"}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        for number in (2, 3):
            self.assertFalse(graph["evidence"][f"E{number}"]["fresh"])
            self.assertFalse(graph["proof"][f"P{number}"]["fresh"])
            self.assertFalse(graph["projections"][f"U{number}"]["confirmed"])

    def test_executed_proof_cannot_downgrade_to_an_unexecuted_plan(self) -> None:
        receipt = self._initialize()
        self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["proof", "P1", "evidence"], "value": [{
                "workflow_id": receipt.workflow_id, "graph_revision": receipt.revision,
                "node": "T1", "branch": BRANCH, "commit": self._git("rev-parse", "HEAD"),
                "check": "python3 -m pytest", "result": {"status": "pass", "exit_code": 0}}]}],
            self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(graph["proof"]["P1"]["execution_required"])
        self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
            [{"op": "set", "path": ["outcomes", "O1", "result"], "value": "Revised outcome"},
             {"op": "set", "path": ["proof", "P1", "evidence"], "value": []}], self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        proof = dict(graph["proof"]["P1"], fresh=True)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "unexecuted planning obligation"):
            self._typed_update(receipt.workflow_id, graph, "refresh-proof-plan", ["proof", "P1"], proof)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "exact execution evidence"):
            self._typed_update(receipt.workflow_id, graph, "refresh-proof", ["proof", "P1"], proof)
        replacement = dict(graph["proof"]["P1"], execution_required=False)
        with self.assertRaisesRegex(self.helper.PlanGraphError, "execution requirement cannot be downgraded"):
            self.helper.apply_updates(
                self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
                [{"op": "set", "path": ["proof", "P1"], "value": replacement}], self.state_home)

    def test_typed_refresh_malformed_wrong_target_and_stale_receipt_reject(self) -> None:
        receipt = self._initialize()
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                [{"op": "reconfirm-decision", "path": ["evidence", "E1"], "value": {}}], self.state_home)

        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        evidence = json.loads(json.dumps(graph["evidence"]["E1"]))
        receipt_value = self.helper.issue_operation_receipt(
            operation="refresh-evidence", workflow_id=receipt.workflow_id,
            prior_graph_revision=1, target=["evidence", "E1"],
            record_version=evidence["record_version"], value=evidence)
        base = {"op": "refresh-evidence", "path": ["evidence", "E1"], "value": evidence,
                "prior_graph_revision": 1, "record_version": evidence["record_version"],
                "receipt": receipt_value}
        mutants = []
        wrong_target = json.loads(json.dumps(base)); wrong_target["receipt"]["target"] = ["evidence", "other"]
        wrong_target["receipt"]["digest"] = hashlib.sha256(json.dumps(
            {k: v for k, v in wrong_target["receipt"].items() if k != "digest"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        mutants.append(wrong_target)
        wrong_version = json.loads(json.dumps(base)); wrong_version["record_version"] += 1
        mutants.append(wrong_version)
        malformed = json.loads(json.dumps(base)); malformed["receipt"].pop("value_digest")
        mutants.append(malformed)
        for mutant in mutants:
            with self.assertRaises(self.helper.PlanGraphError):
                self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, 1,
                                          [mutant], self.state_home)

        advanced = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, 1,
            [{"op": "set", "path": ["work", "T1", "result"],
              "value": "materially revised implementation boundary"}],
            self.state_home,
        )
        before = advanced.path.read_bytes()
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo, BRANCH, receipt.workflow_id, advanced.revision,
                [base], self.state_home)
        self.assertEqual(advanced.path.read_bytes(), before)

    def test_outcome_and_projection_meaning_changes_cannot_preserve_readiness(self) -> None:
        receipt = self._initialize()
        changed = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            receipt.revision,
            [
                {
                    "op": "set",
                    "path": ["outcomes", "O1", "result"],
                    "value": "Configuration is validated without coercion",
                }
            ],
            self.state_home,
        )
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(changed.state, "stale")
        self.assertFalse(graph["proof"]["P1"]["fresh"])
        self.assertTrue(graph["projections"]["U1"]["stale"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])

        # Replacing an exact projection record is not a shortcut around its
        # typed regeneration and confirmation operations. The replacement is
        # accepted as graph editing, then immediately versioned and staled.
        replacement = dict(graph["projections"]["U1"])
        replacement.update({"stale": False, "confirmed": True})
        self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            changed.revision,
            [
                {
                    "op": "set",
                    "path": ["projections", "U1"],
                    "value": replacement,
                }
            ],
            self.state_home,
        )
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(graph["projections"]["U1"]["stale"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        self.assertIsNone(graph["projections"]["U1"]["operation_receipt"])

    def test_tiny_settled_serial_plan_needs_no_audit_for_external_grounding_or_rejected_alternative(self) -> None:
        for mode in ("external", "alternative", "both"):
            with self.subTest(mode=mode):
                graph = _graph()
                if mode in {"external", "both"}:
                    graph["evidence"]["E2"] = {
                        "kind": "external", "fact": "The chosen parser rejects unknown keys",
                        "source": "https://example.invalid/parser/official-contract", "version": "1",
                        "fresh": True, "supports": ["T1"], "limitations": ["Repository wiring is grounded separately"],
                    }
                    graph["work"]["T1"]["based_on"] = ["E1", "E2"]
                if mode in {"alternative", "both"}:
                    graph["decisions"]["D1"] = {
                        "question": "Choose the validation seam", "choice": "Use the existing seam",
                        "alternatives": [{"id": "A", "status": "selected", "reason": "Preserves the current caller contract"},
                                         {"id": "B", "status": "rejected", "reason": "Duplicates validation in the caller"}],
                        "based_on": ["E1"], "material": True, "version": 1, "confirmed_version": 1, "stale": False,
                    }
                    graph["work"]["T1"]["decisions"] = ["D1"]
                    graph["projections"]["U1"].update(covers=["D1", "T1", "P1"], decision_versions={"D1": 1})
                home = self.root / f"tiny-{mode}"
                receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, home)
                loaded = self.helper.load_workflow(self.repo, BRANCH, home)
                self.assertEqual(receipt.state, "ready")
                self.assertEqual(loaded["audit"]["classification"], "tiny")
                self.assertFalse(loaded["audit"]["required"])
                self.assertIsNone(loaded["audit"]["operation_receipt"])
                self.assertEqual(loaded["work"]["T1"]["concurrency"], "serial")

    def test_parallel_ownership_and_required_design_join_enforce_independent_audit_despite_tiny_claim(self) -> None:
        for mode in ("parallel", "design-join"):
            with self.subTest(mode=mode):
                graph = self._three_node_graph()
                graph["audit"] = {"classification": "tiny", "breadth": False, "complexity": False,
                                  "high_consequence": False, "required": False}
                if mode == "parallel":
                    graph["work"]["T2"]["concurrency"] = "parallel-candidate"
                    graph["work"]["T3"]["concurrency"] = "parallel-candidate"
                else:
                    graph["design_join"] = self.helper._empty_design_join()
                    graph["design_join"].update(required=True, fresh=False)
                home = self.root / f"complex-{mode}"
                receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, home)
                loaded = self.helper.load_workflow(self.repo, BRANCH, home)
                self.assertTrue(loaded["audit"]["required"])
                self.assertTrue(loaded["audit"]["complexity"])
                self.assertEqual(loaded["audit"]["classification"], "complex")
                self.assertFalse(loaded["audit"]["fresh"])
                self.assertNotEqual(receipt.state, "ready")
                attempted = dict(loaded["audit"], fresh=True, independent=False, graph_revision=loaded["graph_revision"])
                with self.assertRaisesRegex(self.helper.PlanGraphError, "fresh and independent"):
                    self._typed_update(receipt.workflow_id, loaded, "refresh-audit", ["audit"], attempted, home)

    def test_semantics_derived_audit_floor_and_resolved_findings_readiness(self) -> None:
        template = self._three_node_graph()
        template["evidence"]["E1"]["supports"] = ["D1", "T1"]
        template["decisions"]["D1"] = {
            "question": "Choose a boundary", "choice": "A",
            "alternatives": [{"id": "A", "status": "selected", "reason": "stable"},
                             {"id": "B", "status": "rejected", "reason": "coupled"}],
            "based_on": ["E1"], "material": True, "version": 1,
            "confirmed_version": 1, "stale": False, "invalidates": ["T1", "P1"],
            "consequences": ["changes ownership"],
        }
        template["work"]["T1"]["decisions"] = ["D1"]
        template["projections"]["U1"].update(covers=["D1", "T1", "P1"], decision_versions={"D1": 1})
        template["work"]["T2"]["decisions"] = ["D1"]
        template["work"]["T3"]["decisions"] = ["D1"]
        template["audit"] = {"classification": "broad", "breadth": True,
            "complexity": False, "high_consequence": False, "required": True,
            "reason": "Changes validation ownership across producer and two independent consumer boundaries"}
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, template, self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(graph["audit"]["required"])
        self.assertTrue(graph["audit"]["breadth"])
        self.assertEqual(graph["audit"]["reason"], template["audit"]["reason"])
        self.assertEqual(receipt.state, "stale")

        audited = json.loads(json.dumps(graph["audit"]))
        audited.update(fresh=True, independent=True, graph_revision=1,
                       findings=[{"id": "F1", "severity": "high", "evidence": ["E1"],
                                  "disposition": "open"}], resolutions=[])
        open_receipt = self._typed_update(
            receipt.workflow_id, graph, "refresh-audit", ["audit"], audited)
        self.assertEqual(open_receipt.state, "stale")
        open_graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        resolved = json.loads(json.dumps(open_graph["audit"]))
        resolved["findings"][0]["disposition"] = "resolved"
        resolved["resolutions"] = [{"finding_id": "F1", "disposition": "resolved",
                                     "evidence": ["E1"]}]
        resolved["graph_revision"] = open_graph["graph_revision"]
        ready = self._typed_update(
            receipt.workflow_id, open_graph, "resolve-finding", ["audit"], resolved)
        self.assertEqual(ready.state, "ready")

        lowered = json.loads(json.dumps(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["audit"]))
        lowered.update(classification="tiny", breadth=False, complexity=False,
                       high_consequence=False, required=False)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo, BRANCH, receipt.workflow_id, ready.revision,
                [{"op": "set", "path": ["audit", "required"], "value": False}],
                self.state_home)

        escalation_home = self.root / "audit-escalation"
        tiny = self._initialize(escalation_home)
        tiny_graph = self.helper.load_workflow(self.repo, BRANCH, escalation_home)
        escalated = json.loads(json.dumps(tiny_graph["audit"]))
        escalated.update(
            classification="high-consequence",
            high_consequence=True,
            required=True,
            reason="typed irreversible compatibility consequence",
            independent=True,
            fresh=True,
            graph_revision=tiny.revision,
            evidence=["E1"],
        )
        escalated_receipt = self._typed_update(
            tiny.workflow_id,
            tiny_graph,
            "refresh-audit",
            ["audit"],
            escalated,
            escalation_home,
        )
        escalated_graph = self.helper.load_workflow(
            self.repo, BRANCH, escalation_home
        )
        self.assertEqual(escalated_receipt.state, "ready")
        self.assertTrue(escalated_graph["audit"]["high_consequence"])
        self.assertTrue(escalated_graph["audit"]["required"])
        lowered_consequence = json.loads(json.dumps(escalated_graph["audit"]))
        lowered_consequence.update(
            classification="broad", high_consequence=False, breadth=True
        )
        downgrade_receipt = self.helper.issue_operation_receipt(
            operation="refresh-audit",
            workflow_id=tiny.workflow_id,
            prior_graph_revision=escalated_receipt.revision,
            target=["audit"],
            record_version=lowered_consequence["record_version"],
            value=lowered_consequence,
        )
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo, BRANCH, tiny.workflow_id, escalated_receipt.revision,
                [{"op": "refresh-audit", "path": ["audit"],
                  "value": lowered_consequence,
                  "prior_graph_revision": escalated_receipt.revision,
                  "record_version": lowered_consequence["record_version"],
                  "receipt": downgrade_receipt}], escalation_home)

    def test_private_provenance_issue_register_and_graph_only_fabrication_reject(self) -> None:
        item = self.helper.issue_provenance_receipt(workflow_id="a" * 32, graph_revision=1,
            role="review", session_id="s1", raw_evidence_digest="b" * 64, source="local")
        stored = self.helper.register_provenance_receipt(item, self.state_home)
        self.assertEqual(stored["receipt_id"], item["receipt_id"])
        self.assertEqual(self.helper.load_provenance_receipt(item["receipt_id"], self.state_home), item)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.register_provenance_receipt({**item, "digest": "c" * 64}, self.state_home)

        receipt = self._initialize(self.root / "provenance-accepted")
        accepted_home = self.root / "provenance-accepted"
        delivery = self._draft_delivery(receipt, accepted_home)
        draft = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["git", "delivery"], "value": delivery}],
            accepted_home)
        self.assertEqual(draft.revision, 2)
        stored = self.helper.load_workflow(self.repo, BRANCH, accepted_home)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.derive_plan_state(stored)

        for name, mutate in {
            "unregistered": lambda value: value["gates"]["implementation"].update(
                provenance_id="fabricated", provenance_digest="f" * 64),
            "role-swap": lambda value: value["gates"]["implementation"].update(
                provenance_id=value["gates"]["review"]["provenance_id"],
                provenance_digest=value["gates"]["review"]["provenance_digest"],
                provenance_session=value["gates"]["review"]["provenance_session"],
                provenance_source=value["gates"]["review"]["provenance_source"],
                raw_evidence_digest=value["gates"]["review"]["raw_evidence_digest"]),
            "session-substitution": lambda value: value["gates"]["implementation"].update(
                provenance_session="session-substituted"),
            "policy-role-substitution": lambda value: value["checks_policy"].update(
                provenance_id=value["gates"]["implementation"]["provenance_id"],
                provenance_digest=value["gates"]["implementation"]["provenance_digest"],
                provenance_session=value["gates"]["implementation"]["provenance_session"],
                raw_evidence_digest=value["gates"]["implementation"]["raw_evidence_digest"]),
        }.items():
            with self.subTest(name=name):
                home = self.root / f"provenance-{name}"
                case_receipt = self._initialize(home)
                mutant = self._draft_delivery(case_receipt, home)
                mutate(mutant)
                for gate in mutant["gates"].values():
                    gate.pop("digest", None)
                    gate["digest"] = hashlib.sha256(json.dumps(
                        gate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.apply_updates(
                        self.repo, BRANCH, case_receipt.workflow_id, case_receipt.revision,
                        [{"op": "set", "path": ["git", "delivery"], "value": mutant}], home)
                self.assertEqual(
                    self.helper.load_workflow(self.repo, BRANCH, home)["git"]["delivery"],
                    {"state": "planning"},
                )

    def test_provenance_symlink_fifo_hardlink_and_store_substitution_preserve_foreign_state(self) -> None:
        def registered(home: Path, salt: str) -> tuple[dict[str, object], Path]:
            value = self.helper.issue_provenance_receipt(
                workflow_id="a" * 32,
                graph_revision=1,
                role="review",
                session_id=f"session-{salt}",
                raw_evidence_digest=hashlib.sha256(salt.encode()).hexdigest(),
                source="review-raw",
            )
            self.helper.register_provenance_receipt(value, home)
            return value, home / "provenance" / f"{value['receipt_id']}.json"

        for kind in ("symlink", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                home = self.root / f"provenance-entry-{kind}"
                value, path = registered(home, kind)
                outside = self.root / f"outside-provenance-{kind}"
                outside.write_bytes(b"outside-provenance-must-survive")
                outside.chmod(0o600)
                outside_inode = outside.stat().st_ino
                outside_mode = stat.S_IMODE(outside.stat().st_mode)
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(outside)
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                else:
                    os.link(outside, path)
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.load_provenance_receipt(value["receipt_id"], home)
                self.assertEqual(outside.read_bytes(), b"outside-provenance-must-survive")
                self.assertEqual(outside.stat().st_ino, outside_inode)
                self.assertEqual(stat.S_IMODE(outside.stat().st_mode), outside_mode)

        home = self.root / "provenance-store-race"
        value, _ = registered(home, "store-race")
        store = home / "provenance"
        owned_store = home / "provenance-owned"
        foreign_store = self.root / "foreign-provenance-store"
        foreign_store.mkdir(mode=0o700)
        sentinel = foreign_store / "sentinel.txt"
        sentinel.write_bytes(b"foreign-store-must-survive")
        foreign_inode = foreign_store.stat().st_ino
        real_stat = self.helper.os.stat
        injected = False

        def substitute_store(path: object, *args: object, **kwargs: object) -> object:
            nonlocal injected
            if path == "provenance" and kwargs.get("follow_symlinks") is False and not injected:
                injected = True
                store.rename(owned_store)
                foreign_store.rename(store)
            return real_stat(path, *args, **kwargs)

        try:
            with mock.patch.object(self.helper.os, "stat", side_effect=substitute_store):
                supported = set(self.helper.os.supports_dir_fd) | {self.helper.os.stat}
                with mock.patch.object(self.helper.os, "supports_dir_fd", supported):
                    with self.assertRaises(self.helper.PlanGraphError):
                        self.helper.load_provenance_receipt(value["receipt_id"], home)
        finally:
            if store.exists() and not foreign_store.exists():
                store.rename(foreign_store)
            if owned_store.exists() and not store.exists():
                owned_store.rename(store)
        self.assertTrue(injected)
        self.assertEqual(sentinel.read_bytes(), b"foreign-store-must-survive")
        self.assertEqual(foreign_store.stat().st_ino, foreign_inode)

    def test_tomb_substitution_boundaries_preserve_foreign_state_and_idempotent_retry(self) -> None:
        real_rename = self.helper.os.rename
        real_open = self.helper.os.open
        real_unlink = self.helper.os.unlink
        real_rmdir = self.helper.os.rmdir
        real_fsync = self.helper.os.fsync

        for boundary in ("rename", "open", "unlink", "rmdir", "final-fsync"):
            with self.subTest(boundary=boundary):
                home = self.root / f"tomb-{boundary}"
                receipt = self._initialize(home)
                delivery = self._draft_delivery(receipt, home)
                draft = self.helper.apply_updates(
                    self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                    [{"op": "set", "path": ["git", "delivery"], "value": delivery}], home)
                workspace = draft.path.parent
                root = workspace.parent
                key = workspace.name
                tomb_name = f"{key}.deleting"
                tomb_path = root / tomb_name
                owned_path = root / f"{key}.owned-injection"
                foreign = self.root / f"foreign-{boundary}"
                foreign_is_file = boundary in {"rename", "open"}
                if foreign_is_file:
                    foreign.write_bytes(b"foreign-state-must-survive")
                    sentinel = foreign
                else:
                    foreign.mkdir()
                    sentinel = foreign / "sentinel.txt"
                    sentinel.write_bytes(b"foreign-state-must-survive")
                foreign_mode = stat.S_IMODE(foreign.stat().st_mode)
                foreign_inode = foreign.stat().st_ino
                injected = False
                removed = False
                substituted = False

                def substitute_name() -> None:
                    nonlocal substituted
                    real_rename(tomb_path, owned_path)
                    real_rename(foreign, tomb_path)
                    substituted = True

                def restore_names() -> None:
                    nonlocal substituted
                    real_rename(tomb_path, foreign)
                    real_rename(owned_path, tomb_path)
                    substituted = False

                def rename_fault(source: object, destination: object, *args: object, **kwargs: object) -> object:
                    nonlocal injected
                    if not injected and source == key and destination == tomb_name:
                        injected = True
                        real_rename(source, destination, *args, **kwargs)
                        substitute_name()
                        raise OSError("injected rename-boundary failure")
                    return real_rename(source, destination, *args, **kwargs)

                def open_fault(path: object, flags: int, *args: object, **kwargs: object) -> object:
                    nonlocal injected
                    if not injected and path == tomb_name:
                        injected = True
                        substitute_name()
                        return real_open(path, flags, *args, **kwargs)
                    return real_open(path, flags, *args, **kwargs)

                def unlink_fault(path: object, *args: object, **kwargs: object) -> object:
                    nonlocal injected
                    if not injected and path == "current.yaml":
                        injected = True
                        substitute_name()
                        return real_unlink(path, *args, **kwargs)
                    return real_unlink(path, *args, **kwargs)

                def rmdir_fault(path: object, *args: object, **kwargs: object) -> object:
                    nonlocal injected, removed
                    if boundary == "rmdir" and not injected and path == tomb_name:
                        injected = True
                        substitute_name()
                        return real_rmdir(path, *args, **kwargs)
                    result = real_rmdir(path, *args, **kwargs)
                    if boundary == "final-fsync" and path == tomb_name:
                        removed = True
                    return result

                def fsync_fault(fd: int) -> object:
                    nonlocal injected, substituted
                    if boundary == "final-fsync" and removed and not injected:
                        injected = True
                        real_rename(foreign, tomb_path)
                        substituted = True
                        raise OSError("injected final-parent-fsync failure")
                    return real_fsync(fd)

                patches = {
                    "rename": mock.patch.object(self.helper.os, "rename", side_effect=rename_fault),
                    "open": mock.patch.object(self.helper.os, "open", side_effect=open_fault),
                    "unlink": mock.patch.object(self.helper.os, "unlink", side_effect=unlink_fault),
                    "rmdir": mock.patch.object(self.helper.os, "rmdir", side_effect=rmdir_fault),
                    "final-fsync": mock.patch.multiple(
                        self.helper.os,
                        rmdir=mock.DEFAULT,
                        fsync=mock.DEFAULT,
                    ),
                }
                try:
                    if boundary == "final-fsync":
                        with mock.patch.object(self.helper.os, "rmdir", side_effect=rmdir_fault), \
                             mock.patch.object(self.helper.os, "fsync", side_effect=fsync_fault):
                            supported = set(self.helper.os.supports_dir_fd) | {self.helper.os.rmdir}
                            with mock.patch.object(self.helper.os, "supports_dir_fd", supported):
                                with self.assertRaises(self.helper.PlanGraphError):
                                    self.helper.complete_for_human_review(
                                        self.repo, BRANCH, receipt.workflow_id, draft.revision, home)
                    else:
                        with patches[boundary]:
                            supported = set(self.helper.os.supports_dir_fd) | {
                                getattr(self.helper.os, boundary)
                            }
                            with mock.patch.object(self.helper.os, "supports_dir_fd", supported):
                                try:
                                    self.helper.complete_for_human_review(
                                        self.repo, BRANCH, receipt.workflow_id, draft.revision, home)
                                except self.helper.PlanGraphError:
                                    if not injected:
                                        raise
                finally:
                    if substituted and tomb_path.exists() and not foreign.exists():
                        real_rename(tomb_path, foreign)
                        substituted = False
                    if owned_path.exists() and not workspace.exists():
                        real_rename(owned_path, workspace)

                self.assertTrue(injected, f"{boundary} fault did not reach its boundary")
                self.assertEqual(sentinel.read_bytes(), b"foreign-state-must-survive")
                self.assertEqual(stat.S_IMODE(foreign.stat().st_mode), foreign_mode)
                self.assertEqual(foreign.stat().st_ino, foreign_inode)
                active_exists = draft.path.is_file()
                terminal_files = list((root / "terminal").glob(f"*-{receipt.workflow_id}.yaml"))
                self.assertTrue(active_exists or terminal_files)
                if active_exists:
                    self.assertEqual(
                        self.helper.load_workflow(self.repo, BRANCH, home)["workflow_id"],
                        receipt.workflow_id,
                    )
                else:
                    terminal_payload = json.loads(terminal_files[0].read_text(encoding="utf-8"))
                    self.assertIn(
                        terminal_payload["state"],
                        {"preparing-human-review", "ready-for-human-review"},
                    )
                first_retry = self.helper.complete_for_human_review(
                    self.repo, BRANCH, receipt.workflow_id, draft.revision, home)
                second_retry = self.helper.complete_for_human_review(
                    self.repo, BRANCH, receipt.workflow_id, draft.revision, home)
                self.assertEqual(first_retry.state, "ready-for-human-review")
                self.assertEqual(second_retry.state, "ready-for-human-review")
                self.assertEqual(first_retry.terminal_path, second_retry.terminal_path)
                self.assertEqual(sentinel.read_bytes(), b"foreign-state-must-survive")

    def test_two_process_initializers_create_exactly_one_workflow(self) -> None:
        common = (str(HELPER), str(self.repo), str(self.state_home), _graph())
        values = self._process_results(_initialize_process, [common, common])
        self.assertEqual(sorted(value[0] for value in values), ["conflict", "success"])
        receipt = self.helper.discover_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(receipt.revision, 1)
        self.assertEqual(
            {path.name for path in receipt.path.parent.iterdir()}, {"current.yaml"}
        )

    def test_two_process_same_revision_updates_have_one_winner_and_clean_rotation(self) -> None:
        first = self._initialize()
        common = (str(HELPER), str(self.repo), str(self.state_home), first.workflow_id)
        values = self._process_results(
            _update_process,
            [(*common, "writer-one"), (*common, "writer-two")],
        )
        self.assertEqual(sorted(value[0] for value in values), ["conflict", "success"])
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(loaded["graph_revision"], 2)
        self.assertIn(loaded["work"]["T1"]["result"], {"writer-one", "writer-two"})
        self.assertEqual(
            json.loads(first.previous_path.read_text(encoding="utf-8"))["graph_revision"],
            1,
        )
        self.assertEqual(
            {path.name for path in first.path.parent.iterdir()},
            {"current.yaml", "previous.yaml"},
        )

    def test_identity_uses_common_git_directory_and_aliases_are_deterministic(self) -> None:
        alias = self.root / "repo-alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        receipt = self.helper.initialize_workflow(alias, BRANCH, _graph(), self.state_home)
        common = self._git("rev-parse", "--path-format=absolute", "--git-common-dir")
        expected = __import__("hashlib").sha256(
            (str(Path(common).resolve()) + "\0" + BRANCH).encode()
        ).hexdigest()
        self.assertEqual(receipt.path.parent.name, expected)
        self.assertEqual(
            self.helper.discover_workflow(self.repo, BRANCH, self.state_home).workflow_id,
            receipt.workflow_id,
        )

    def test_every_state_component_attack_fails_without_outside_mutation(self) -> None:
        outside = self.root / "outside"
        outside.write_text("owned by caller\n", encoding="utf-8")

        attacks: list[tuple[str, object]] = []

        state_link = self.root / "state-link"
        state_link.symlink_to(self.root, target_is_directory=True)
        attacks.append(("state-root-symlink", state_link))

        graphs_link_home = self.root / "graphs-link-home"
        graphs_link_home.mkdir(mode=0o700)
        (graphs_link_home / "plan-graphs").symlink_to(self.root, target_is_directory=True)
        attacks.append(("graphs-root-symlink", graphs_link_home))

        wrong_mode = self.root / "wrong-mode-home"
        wrong_mode.mkdir(mode=0o755)
        wrong_mode.chmod(0o755)
        attacks.append(("state-root-mode", wrong_mode))

        for name, home in attacks:
            with self.subTest(name=name):
                with self.assertRaises(self.helper.PlanGraphError):
                    self._initialize(Path(home))
                self.assertEqual(outside.read_text(encoding="utf-8"), "owned by caller\n")

        receipt = self._initialize()
        graph_root = receipt.path.parent.parent
        lock_path = graph_root / f"{receipt.path.parent.name}.lock"
        self.helper.discard_workflow(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision, True, self.state_home
        )

        # A stable-lock substitution is rejected and never chmodded or followed.
        lock_path.unlink()
        lock_path.symlink_to(outside)
        before_mode = stat.S_IMODE(outside.stat().st_mode)
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize()
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), before_mode)
        self.assertEqual(outside.read_text(encoding="utf-8"), "owned by caller\n")

    def test_current_previous_and_workflow_nonregular_entries_fail_closed(self) -> None:
        factories = {
            "symlink": lambda path: path.symlink_to(self.root / "outside"),
            "fifo": lambda path: os.mkfifo(path, 0o600),
            "directory": lambda path: path.mkdir(mode=0o700),
        }
        (self.root / "outside").write_text("outside\n", encoding="utf-8")
        for target in ("current.yaml", "previous.yaml"):
            for kind, factory in factories.items():
                with self.subTest(target=target, kind=kind):
                    home = self.root / f"attack-{target}-{kind}"
                    receipt = self._initialize(home)
                    if target == "previous.yaml":
                        receipt = self.helper.apply_updates(
                            self.repo,
                            BRANCH,
                            receipt.workflow_id,
                            1,
                            [{"op": "set", "path": ["work", "T1", "result"], "value": "v2"}],
                            home,
                        )
                    victim = receipt.path.parent / target
                    victim.unlink()
                    factory(victim)
                    with self.assertRaises(self.helper.PlanGraphError):
                        self.helper.load_workflow(self.repo, BRANCH, home)
                    self.assertEqual((self.root / "outside").read_text(), "outside\n")

    def test_reconcile_accepts_only_immediate_disjoint_paths(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "first update"}],
            self.state_home,
        )
        disjoint = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["projections", "U1", "confirmed"], "value": False}],
            self.state_home,
            reconcile_disjoint=True,
        )
        self.assertTrue(disjoint.reconciled)
        self.assertEqual(disjoint.previous_revision, 2)
        self.assertEqual(disjoint.current_revision, 3)
        self.assertEqual(disjoint.applied_paths, (("projections", "U1", "confirmed"),))

        with self.assertRaises(self.helper.RevisionConflict):
            self.helper.apply_updates(
                self.repo,
                BRANCH,
                receipt.workflow_id,
                1,
                [{"op": "set", "path": ["outcomes", "O1", "result"], "value": "old"}],
                self.state_home,
                reconcile_disjoint=True,
            )

        overlap_home = self.root / "overlap"
        first = self._initialize(overlap_home)
        self.helper.apply_updates(
            self.repo,
            BRANCH,
            first.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "new"}],
            overlap_home,
        )
        for path in (["work", "T1"], ["work", "T1", "result"]):
            with self.subTest(path=path):
                with self.assertRaises(self.helper.RevisionConflict):
                    self.helper.apply_updates(
                        self.repo,
                        BRANCH,
                        first.workflow_id,
                        1,
                        [{"op": "set", "path": path, "value": {} if len(path) == 2 else "stale"}],
                        overlap_home,
                        reconcile_disjoint=True,
                    )

    def test_recovery_creates_a_new_monotonic_generation_and_validates_identity(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "revision-two"}],
            self.state_home,
        )
        second.path.write_text("corrupt", encoding="utf-8")
        second.path.chmod(0o600)
        recovered = self.helper.recover_workflow(
            self.repo, BRANCH, receipt.workflow_id, self.state_home
        )
        self.assertGreater(recovered.revision, second.revision)
        self.assertEqual(recovered.previous_revision, 1)
        self.assertEqual(
            self.helper.load_workflow(self.repo, BRANCH, self.state_home)["work"]["T1"]["result"],
            _graph()["work"]["T1"]["result"],
        )

    def test_recovery_requires_reconciliation_and_fresh_projection_confirmation(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["outcomes", "O1", "result"],
              "value": "Configuration is validated without coercion"}], self.state_home)
        second.path.write_text("corrupt", encoding="utf-8")
        recovered = self.helper.recover_workflow(self.repo, BRANCH, receipt.workflow_id, self.state_home)
        self.assertEqual(recovered.state, "awaiting-user")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertEqual(graph["unresolved"][0]["kind"], "recovery-reconciliation")
        self.assertTrue(graph["projections"]["U1"]["stale"])
        self.assertFalse(graph["projections"]["U1"]["confirmed"])
        self.assertEqual(graph["outcomes"]["O1"]["result"], _graph()["outcomes"]["O1"]["result"])
        cleared = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, graph["graph_revision"],
            [{"op": "set", "path": ["unresolved"], "value": []}], self.state_home)
        self.assertEqual(cleared.state, "stale")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], stale=False, presented=True, confirmed=False)
        projection["presentation"] = self.helper.issue_projection_presentation(
            graph=graph, projection_id="U1", text="Validate configuration and prove the recovered plan's behavior.")
        regenerated = self._typed_update(receipt.workflow_id, graph, "regenerate-projection", ["projections", "U1"], projection)
        self.assertEqual(regenerated.state, "awaiting-user")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        projection = dict(graph["projections"]["U1"], confirmed=True)
        confirmed = self._typed_update(receipt.workflow_id, graph, "reconfirm-projection", ["projections", "U1"], projection)
        self.assertEqual(confirmed.state, "ready")

    def test_recovery_stales_repository_evidence_changed_since_checkpoint(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "Revised work"}], self.state_home)
        (self.repo / "config.py").write_text("CONFIG = {'changed': True}\n", encoding="utf-8")
        second.path.write_text("corrupt", encoding="utf-8")
        self.helper.recover_workflow(self.repo, BRANCH, receipt.workflow_id, self.state_home)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(graph["evidence"]["E1"]["fresh"])

    def test_resume_is_one_revision_and_stales_only_relevant_repository_evidence(self) -> None:
        graph = _graph()
        graph["evidence"]["E2"] = {
            "kind": "repository",
            "fact": "An unrelated module exists",
            "source": "unrelated.py",
            "fresh": True,
            "supports": [],
        }
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        paused = self.helper.pause_workflow(
            self.repo, BRANCH, receipt.workflow_id, receipt.revision, self.state_home
        )
        (self.repo / "config.py").write_text("CONFIG = {'changed': True}\n", encoding="utf-8")
        self._git("add", "config.py")
        self._git("commit", "-m", "change relevant source")
        resumed = self.helper.resume_workflow(
            self.repo, BRANCH, receipt.workflow_id, paused.revision, self.state_home
        )
        self.assertEqual(resumed.revision, paused.revision + 1)
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(loaded["evidence"]["E1"]["fresh"])
        self.assertTrue(loaded["evidence"]["E2"]["fresh"])

    def test_active_load_and_discover_invalidate_only_changed_repository_sources(self) -> None:
        for caller in ("load_workflow", "discover_workflow"):
            with self.subTest(caller=caller):
                home = self.root / caller
                graph = self._three_node_graph()
                receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, home)
                (self.repo / "config.py").write_text(f"CONFIG = {{'{caller}': True}}\n", encoding="utf-8")
                self._git("add", "config.py")
                self._git("commit", "-m", caller)
                result = getattr(self.helper, caller)(self.repo, BRANCH, home)
                self.assertEqual(result["lifecycle"]["derived_state"] if isinstance(result, dict) else result.state, "stale")
                loaded = self.helper.load_workflow(self.repo, BRANCH, home)
                self.assertEqual(loaded["graph_revision"], receipt.revision + 1)
                self.assertFalse(loaded["evidence"]["E1"]["fresh"])
                self.assertFalse(loaded["proof"]["P1"]["fresh"])
                self.assertTrue(loaded["projections"]["U1"]["confirmed"])
                self.assertTrue(loaded["evidence"]["E2"]["fresh"])
                self.assertTrue(loaded["projections"]["U2"]["confirmed"])

    def test_active_load_detects_changed_bytes_of_already_dirty_source(self) -> None:
        (self.repo / "config.py").write_text("CONFIG = {'first': True}\n", encoding="utf-8")
        receipt = self._initialize()
        self.assertEqual(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["graph_revision"], receipt.revision)
        first_status = self._git("status", "--porcelain=v1")
        (self.repo / "config.py").write_text("CONFIG = {'second': True}\n", encoding="utf-8")
        self.assertEqual(self._git("status", "--porcelain=v1"), first_status)
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(graph["evidence"]["E1"]["fresh"])
        self.assertEqual(graph["graph_revision"], receipt.revision + 1)

    def test_update_entry_rejects_revision_preceding_repository_invalidation(self) -> None:
        receipt = self._initialize()
        (self.repo / "config.py").write_text("CONFIG = {'changed': True}\n", encoding="utf-8")
        with self.assertRaises(self.helper.RevisionConflict):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, receipt.revision,
                [{"op": "set", "path": ["work", "T1", "result"], "value": "Revised wording"}], self.state_home)
        self.assertFalse(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["evidence"]["E1"]["fresh"])

    def test_refreshed_evidence_accepts_current_dirty_contents_until_they_change(self) -> None:
        receipt = self._initialize()
        (self.repo / "config.py").write_text("CONFIG = {'first': True}\n", encoding="utf-8")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        evidence = dict(graph["evidence"]["E1"], fresh=True, observed_at="2026-10-02T10:00:00Z")
        refreshed = self._typed_update(receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"], evidence)
        current = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertTrue(current["evidence"]["E1"]["fresh"])
        self.assertEqual(current["graph_revision"], refreshed.revision)
        (self.repo / "config.py").write_text("CONFIG = {'second': True}\n", encoding="utf-8")
        self.assertFalse(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["evidence"]["E1"]["fresh"])

    def test_legacy_dirty_grounding_without_content_snapshot_requires_refresh(self) -> None:
        (self.repo / "config.py").write_text("CONFIG = {'accepted_dirty': True}\n", encoding="utf-8")
        receipt = self._initialize()
        graph = json.loads(receipt.path.read_text(encoding="utf-8"))
        del graph["baseline"]["evidence_fingerprints"]
        receipt.path.write_text(json.dumps(graph), encoding="utf-8")
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(loaded["evidence"]["E1"]["fresh"])
        self.assertEqual(loaded["graph_revision"], receipt.revision + 1)

    def test_resume_retains_evidence_refreshed_after_original_baseline(self) -> None:
        receipt = self._initialize()
        (self.repo / "config.py").write_text("CONFIG = {'new_head': True}\n", encoding="utf-8")
        self._git("add", "config.py")
        self._git("commit", "-m", "revised source")
        graph = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        evidence = dict(graph["evidence"]["E1"], fresh=True, revision=self._git("rev-parse", "HEAD"), observed_at="2026-10-02T10:00:00Z")
        refreshed = self._typed_update(receipt.workflow_id, graph, "refresh-evidence", ["evidence", "E1"], evidence)
        paused = self.helper.pause_workflow(self.repo, BRANCH, receipt.workflow_id, refreshed.revision, self.state_home)
        self.helper.resume_workflow(self.repo, BRANCH, receipt.workflow_id, paused.revision, self.state_home)
        self.assertTrue(self.helper.load_workflow(self.repo, BRANCH, self.state_home)["evidence"]["E1"]["fresh"])

    def test_resume_normalizes_relative_absolute_and_anchored_repository_sources(self) -> None:
        sources = ["config.py:24", "config.py:24:7", "config.py#L24-L30",
                   str(self.repo / "config.py"), f"{self.repo / 'config.py'}:24"]
        for number, source in enumerate(sources):
            with self.subTest(source=source):
                (self.repo / "config.py").write_text("CONFIG = {}\n", encoding="utf-8")
                home = self.root / f"source-{number}"
                graph = _graph()
                graph["evidence"]["E1"]["source"] = source
                receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, home)
                paused = self.helper.pause_workflow(self.repo, BRANCH, receipt.workflow_id, receipt.revision, home)
                (self.repo / "config.py").write_text("CONFIG = {'changed': True}\n", encoding="utf-8")
                resumed = self.helper.resume_workflow(self.repo, BRANCH, receipt.workflow_id, paused.revision, home)
                loaded = self.helper.load_workflow(self.repo, BRANCH, home)
                self.assertEqual(resumed.state, "stale")
                self.assertFalse(loaded["evidence"]["E1"]["fresh"])
                self.assertEqual(loaded["evidence"]["E1"]["source"], source)

    def test_repository_evidence_outside_repository_is_rejected(self) -> None:
        for number, source in enumerate((str(self.root / "outside.py"), "../outside.py:12")):
            with self.subTest(source=source):
                graph = _graph()
                graph["evidence"]["E1"]["source"] = source
                with self.assertRaisesRegex(self.helper.PlanGraphError, "outside repository"):
                    self.helper.initialize_workflow(self.repo, BRANCH, graph, self.root / f"outside-source-{number}")

    def test_resume_preserves_literal_filename_with_numeric_colon_suffix(self) -> None:
        path = self.repo / "config.py:24"
        path.write_text("CONFIG = {}\n", encoding="utf-8")
        graph = _graph()
        graph["evidence"]["E1"]["source"] = path.name
        receipt = self.helper.initialize_workflow(self.repo, BRANCH, graph, self.state_home)
        paused = self.helper.pause_workflow(self.repo, BRANCH, receipt.workflow_id, receipt.revision, self.state_home)
        path.write_text("CONFIG = {'changed': True}\n", encoding="utf-8")
        self.helper.resume_workflow(self.repo, BRANCH, receipt.workflow_id, paused.revision, self.state_home)
        loaded = self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        self.assertFalse(loaded["evidence"]["E1"]["fresh"])

    def test_receipt_schema_rejects_independent_mutants(self) -> None:
        required = {
            "workflow_id": None,
            "graph_revision": 1,
            "node": "T1",
            "branch": BRANCH,
            "commit": self._git("rev-parse", "HEAD"),
            "check": "python3 -m pytest test_config.py",
            "result": {"status": "pass", "exit_code": 0},
        }
        mutants = {
            "missing-check": lambda value: value.pop("check"),
            "empty-check": lambda value: value.update(check=""),
            "wrong-node": lambda value: value.update(node="missing"),
            "short-commit": lambda value: value.update(commit="a" * 12),
            "missing-exit": lambda value: value["result"].pop("exit_code"),
            "boolean-exit": lambda value: value["result"].update(exit_code=False),
            "nonzero": lambda value: value["result"].update(exit_code=1),
            "wrong-status": lambda value: value["result"].update(status="passed"),
        }
        for name, mutate in mutants.items():
            with self.subTest(name=name):
                home = self.root / f"receipt-{name}"
                receipt = self._initialize(home)
                evidence = json.loads(json.dumps(required))
                evidence["workflow_id"] = receipt.workflow_id
                mutate(evidence)
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.apply_updates(
                        self.repo,
                        BRANCH,
                        receipt.workflow_id,
                        1,
                        [{"op": "set", "path": ["proof", "P1", "evidence"], "value": [evidence]}],
                        home,
                    )

    def test_malformed_graphs_raise_only_plan_graph_error(self) -> None:
        malformed = [
            None,
            [],
            {},
            {"schema_version": "plan-graph.v1", "outcomes": None},
            {**_graph(), "work": []},
            {**_graph(), "git": {"target": None}},
        ]
        for index, graph in enumerate(malformed):
            with self.subTest(index=index):
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.initialize_workflow(
                        self.repo, BRANCH, graph, self.root / f"malformed-{index}"
                    )
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.derive_plan_state(graph)

    def test_atomic_current_failure_preserves_a_valid_current_and_no_temp(self) -> None:
        receipt = self._initialize()
        before = receipt.path.read_bytes()
        real_replace = self.helper.os.replace

        def fail_current(source: object, destination: object, **kwargs: object) -> object:
            if destination == "current.yaml":
                raise OSError("injected")
            return real_replace(source, destination, **kwargs)

        with mock.patch.object(self.helper.os, "replace", side_effect=fail_current):
            with self.assertRaises(self.helper.PlanGraphError):
                self.helper.apply_updates(
                    self.repo,
                    BRANCH,
                    receipt.workflow_id,
                    1,
                    [{"op": "set", "path": ["work", "T1", "result"], "value": "new"}],
                    self.state_home,
                )
        self.assertEqual(receipt.path.read_bytes(), before)
        self.assertEqual(
            {path.name for path in receipt.path.parent.iterdir()},
            {"current.yaml", "previous.yaml"},
        )

    def test_terminal_receipt_is_durable_after_generation_deletion(self) -> None:
        receipt = self._initialize()
        head = self._git("rev-parse", "HEAD")
        delivery = self._draft_delivery(receipt)
        draft = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["git", "delivery"], "value": delivery}],
            self.state_home,
        )
        terminal = self.helper.complete_for_human_review(
            self.repo, BRANCH, receipt.workflow_id, draft.revision, self.state_home
        )
        self.assertTrue(terminal.path.is_file())
        self.assertEqual(terminal.previous_revision, draft.revision)
        self.assertEqual(terminal.current_revision, draft.revision + 1)
        self.assertFalse(draft.path.parent.exists())
        payload = json.loads(terminal.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["workflow_id"], receipt.workflow_id)
        self.assertEqual(payload["request_id"], "PR-1")
        self.assertEqual(
            set(payload["provenance"]),
            {"implementation", "review", "verification", "target_proof", "checks_policy"},
        )
        retry = self.helper.complete_for_human_review(
            self.repo, BRANCH, receipt.workflow_id, draft.revision, self.state_home
        )
        self.assertEqual(retry.terminal_path, terminal.terminal_path)
        provenance_id = payload["provenance"]["review"]["provenance_id"]
        provenance_path = self.state_home / "provenance" / f"{provenance_id}.json"
        provenance_path.unlink()
        before_terminal = terminal.path.read_bytes()
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.complete_for_human_review(
                self.repo, BRANCH, receipt.workflow_id, draft.revision, self.state_home
            )
        self.assertEqual(terminal.path.read_bytes(), before_terminal)

    def test_empty_checks_require_explicit_verified_empty_policy(self) -> None:
        receipt = self._initialize()
        head = self._git("rev-parse", "HEAD")

        def gate(role: str) -> dict[str, object]:
            role = {"implementation": "implement", "review": "review",
                    "verification": "verify", "target_proof": "integrate"}[role]
            return {"role": role, "workflow_id": receipt.workflow_id,
                    "graph_revision": 1, "branch": BRANCH, "commit": head,
                    "work": ["T1"], "proof": ["P1"],
                    "commands": ["python3 -m pytest"],
                    "results": [{"command": "python3 -m pytest", "exit_code": 0, "output": "passed"}],
                    "result": {"status": "pass"}}

        delivery = {"state": "draft", "request": {"id": "PR-empty", "draft": True, "head": head},
                    "first_coherent_commit": head, "exact_head": head,
                    "gates": {name: gate(name) for name in ("implementation", "review", "verification", "target_proof")},
                    "checks": [], "lanes_clean": True,
                    "handoff": {"title": "x", "summary": "y"}}
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(self.repo, BRANCH, receipt.workflow_id, 1,
                [{"op": "set", "path": ["git", "delivery"], "value": delivery}], self.state_home)

    def test_discard_injected_unlink_failure_restores_exact_recoverable_state(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "revision two"}],
            self.state_home,
        )
        before_current = second.path.read_bytes()
        before_previous = second.previous_path.read_bytes()
        real_unlink = self.helper.os.unlink

        def fail_current(path: object, **kwargs: object) -> object:
            if path == "current.yaml":
                raise OSError("injected current deletion failure")
            return real_unlink(path, **kwargs)

        with mock.patch.object(self.helper.os, "unlink", side_effect=fail_current):
            with self.assertRaises(self.helper.PlanGraphError):
                self.helper.discard_workflow(
                    self.repo,
                    BRANCH,
                    receipt.workflow_id,
                    second.revision,
                    True,
                    self.state_home,
                )
        self.assertEqual(second.path.read_bytes(), before_current)
        self.assertEqual(second.previous_path.read_bytes(), before_previous)
        self.assertEqual(
            self.helper.load_workflow(self.repo, BRANCH, self.state_home)["graph_revision"],
            2,
        )

    def test_duplicate_identity_and_missing_current_fail_closed_but_recover_exactly(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "revision two"}],
            self.state_home,
        )
        duplicate = second.path.parent.parent / ("f" * 64)
        shutil.copytree(second.path.parent, duplicate)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.discover_workflow(self.repo, BRANCH, self.state_home)
        shutil.rmtree(duplicate)

        second.path.unlink()
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(self.repo, BRANCH, self.state_home)
        recovered = self.helper.recover_workflow(
            self.repo, BRANCH, receipt.workflow_id, self.state_home
        )
        self.assertEqual(recovered.revision, 3)
        self.assertEqual(recovered.previous_revision, 1)

    def test_stable_lock_and_private_directory_modes_and_types_are_fail_closed(self) -> None:
        common = Path(self._git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        key = __import__("hashlib").sha256((str(common) + "\0" + BRANCH).encode()).hexdigest()

        for kind in ("fifo", "directory", "wrong-mode"):
            with self.subTest(kind=kind):
                home = self.root / f"lock-{kind}"
                graph_root = home / "plan-graphs"
                graph_root.mkdir(parents=True, mode=0o700)
                lock = graph_root / f"{key}.lock"
                if kind == "fifo":
                    os.mkfifo(lock, 0o600)
                elif kind == "directory":
                    lock.mkdir(mode=0o700)
                else:
                    lock.write_text("", encoding="utf-8")
                    lock.chmod(0o644)
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.initialize_workflow(self.repo, BRANCH, _graph(), home)

        workflow_home = self.root / "workflow-mode"
        graph_root = workflow_home / "plan-graphs"
        graph_root.mkdir(parents=True, mode=0o700)
        workflow = graph_root / key
        workflow.mkdir(mode=0o755)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.initialize_workflow(self.repo, BRANCH, _graph(), workflow_home)

    def test_evidence_receipt_remains_valid_after_later_disjoint_graph_revision(self) -> None:
        receipt = self._initialize()
        evidence = {
            "workflow_id": receipt.workflow_id,
            "graph_revision": 1,
            "node": "T1",
            "branch": BRANCH,
            "commit": self._git("rev-parse", "HEAD"),
            "check": "python3 -m pytest test_config.py",
            "result": {"status": "pass", "exit_code": 0},
        }
        second = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["proof", "P1", "evidence"], "value": [evidence]}],
            self.state_home,
        )
        third = self.helper.apply_updates(
            self.repo,
            BRANCH,
            receipt.workflow_id,
            second.revision,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "clarified result"}],
            self.state_home,
        )
        self.assertEqual(third.revision, 3)
        self.assertEqual(
            self.helper.load_workflow(self.repo, BRANCH, self.state_home)["proof"]["P1"]["evidence"],
            [evidence],
        )

    def test_external_evidence_and_invalidation_references_are_typed(self) -> None:
        external = _graph()
        external["evidence"]["E2"] = {
            "kind": "external",
            "fact": "External mechanism behavior",
            "source": "https://example.invalid/spec",
            "fresh": True,
            "supports": [],
        }
        invalidation = _graph()
        invalidation["decisions"]["D1"] = {
            "question": "Which mechanism?",
            "choice": "The existing boundary",
            "alternatives": [],
            "based_on": ["E1"],
            "material": False,
            "version": 1,
            "confirmed_version": None,
            "stale": False,
            "invalidates": ["missing"],
        }
        for name, graph in (("external-version", external), ("invalidation", invalidation)):
            with self.subTest(name=name):
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.initialize_workflow(
                        self.repo, BRANCH, graph, self.root / f"typed-{name}"
                    )

    def test_branch_rejects_abbreviated_commit_without_ref_mutation(self) -> None:
        self._git("switch", "-C", "main")
        before = self._git("for-each-ref", "--format=%(refname):%(objectname)", "refs/heads")
        abbreviated = self._git("rev-parse", "--short", "HEAD")
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.create_workflow_branch(
                self.repo, "feature/short-baseline", abbreviated, confirmed=True
            )
        self.assertEqual(
            self._git("for-each-ref", "--format=%(refname):%(objectname)", "refs/heads"),
            before,
        )


if __name__ == "__main__":
    unittest.main()
