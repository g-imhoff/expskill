from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.design_state_test_support import TECHNICAL_GATE_NAMES, passing_technical, prepare_delivery_fixture, confirm_fixture_workflow, FIXTURE_BRIEF_DIGEST


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins" / "expskill" / "content" / "scripts" / "design_state.py"
DIGEST = "a" * 64


def _load(name: str = "design_state_contract_v4") -> object:
    spec = importlib.util.spec_from_file_location(name, HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("state helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=tests", "-c", "user.email=tests@example.invalid", "commit", "--quiet", "-m", "fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "switch", "--quiet", "-c", "feature/design"], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return repo, head


def _start(module: object, root: Path) -> tuple[dict, Path, Path, str]:
    repo, head = _repo(root)
    state_home = root / "state"
    receipt = module.initialize_workflow(repository=repo, branch="feature/design", worktree=repo, baseline=head, dirty_fingerprint=hashlib.sha256(b"").hexdigest(), ui_contract={"digest": DIGEST, "outcome": "checkout"}, scope={"components": ["CheckoutForm"], "exclusions": ["route"]}, state_home=state_home)
    receipt = confirm_fixture_workflow(module, receipt, state_home)
    return receipt, repo, state_home, str(receipt["workflow_id"])


def _records() -> dict[str, dict]:
    code = hashlib.sha256(b"checkout-code").hexdigest()
    contract = hashlib.sha256(b"checkout-contract").hexdigest()
    evidence = hashlib.sha256(b"checkout-evidence").hexdigest()
    records = {
        "components": {"CheckoutForm": {"id": "CheckoutForm", "code_digest": code, "contract_digest": contract, "evidence_ids": ["E1"], "dependency_ids": ["D1"], "approval_id": "A1"}},
        "dependencies": {"D1": {"id": "D1", "digest": hashlib.sha256(b"dependency").hexdigest(), "component_ids": ["CheckoutForm"]}},
        "evidence": {"E1": {"id": "E1", "component_id": "CheckoutForm", "digest": evidence, "code_digest": code, "contract_digest": contract, "widths": ["compact", "intermediate", "wide"], "themes": ["light", "dark"], "states": ["default", "error"], "technical": passing_technical(evidence)}},
        "approvals": {"A1": {"id": "A1", "component_id": "CheckoutForm", "code_digest": code, "contract_digest": contract, "evidence_ids": ["E1"], "dependency_ids": ["D1"], "decision": "approved"}},
    }

    for collection in ("components", "evidence", "approvals"):
        for item in records[collection].values(): item["brief_digest"] = FIXTURE_BRIEF_DIGEST
    return records


def _layers(records: dict[str, dict]) -> tuple[dict, dict, dict]:
    component = records["components"]["CheckoutForm"]
    evidence = records["evidence"]["E1"]
    return (
        {"files": component.get("files", [{"path": "components/CheckoutForm.tsx", "digest": component["code_digest"], "classification": "component"}])},
        {"files": [{"path": "evidence/E1.json", "digest": evidence["digest"], "classification": "review"}]},
        {"files": [{"path": "contracts/CheckoutForm.json", "digest": component["contract_digest"], "classification": "manifest"}]},
    )


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _delivery(candidate: dict, review: dict, manifest: dict) -> dict:
    return {"classifications": {"candidate": "component", "review": "review", "manifest": "manifest"}, "candidate": {"inventory_digest": _digest(candidate), "files": candidate["files"]}, "review": {"inventory_digest": _digest(review), "files": review["files"]}, "manifest": {"inventory_digest": _digest(manifest), "files": manifest["files"]}}


def _seed(module: object, receipt: dict, home: Path, workflow: str) -> tuple[dict, dict, dict, dict]:
    records = _records()
    candidate, review, manifest = _layers(records)
    prepare_delivery_fixture(module, receipt, home, workflow, records, (candidate, review, manifest))
    updates = dict(records)
    updates["delivery"] = _delivery(candidate, review, manifest)
    updated = module.apply_updates(workflow_id=workflow, expected_revision=receipt["revision"], updates=updates, state_home=home)
    return updated, records, candidate, review, manifest


class DesignStateContractV4Tests(unittest.TestCase):
    def test_every_named_quality_gate_is_required_before_approval(self) -> None:
        """Regression: a generic successful command cannot stand in for the failed calibration dimensions."""
        module = _load()
        for gate_name in sorted(TECHNICAL_GATE_NAMES):
            with self.subTest(gate=gate_name), tempfile.TemporaryDirectory() as temporary:
                receipt, _, home, workflow = _start(module, Path(temporary))
                records = _records()
                del records["evidence"]["E1"]["technical"]["gates"][gate_name]
                with self.assertRaises(Exception):
                    module.apply_updates(
                        workflow_id=workflow,
                        expected_revision=receipt["revision"],
                        updates=records,
                        state_home=home,
                    )

    def test_variant_five_failures_are_retained_but_cannot_be_approved(self) -> None:
        """Regression: overflow, critical accessibility, motion, or format failures must enter correction."""
        module = _load()
        for failure in ("format", "responsive", "accessibility", "reduced_motion"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                receipt, _, home, workflow = _start(module, Path(temporary))
                records = _records()
                technical = records["evidence"]["E1"]["technical"]
                technical["status"] = "fail"
                technical["results"][0]["exit"] = 1
                technical["gates"][failure]["status"] = "fail"
                if failure == "responsive":
                    technical["gates"][failure]["details"]["page_overflow"]["compact"] = True
                elif failure == "accessibility":
                    technical["gates"][failure]["details"]["critical"] = 1
                updates = copy.deepcopy(records)
                updates["approvals"] = {}
                stored = module.apply_updates(
                    workflow_id=workflow,
                    expected_revision=receipt["revision"],
                    updates=updates,
                    state_home=home,
                )
                observed = module.load_workflow(workflow_id=workflow, state_home=home)
                self.assertEqual(observed["evidence"]["E1"]["technical"]["status"], "fail")
                with self.assertRaises(Exception):
                    module.apply_updates(
                        workflow_id=workflow,
                        expected_revision=stored["revision"],
                        updates={"approvals": records["approvals"]},
                        state_home=home,
                    )

    def test_tooling_gate_may_be_inapplicable_only_with_bound_reason(self) -> None:
        """Regression: projects without a tool need an explicit inspected exception, not a silent omission."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, home, workflow = _start(module, Path(temporary))
            records = _records()
            gate = records["evidence"]["E1"]["technical"]["gates"]["type"]
            gate["status"] = "not-applicable"
            gate["details"] = {"reason": "project has no type system"}
            module.apply_updates(
                workflow_id=workflow,
                expected_revision=receipt["revision"],
                updates=records,
                state_home=home,
            )

        for details in ({}, {"reason": ""}):
            with self.subTest(details=details), tempfile.TemporaryDirectory() as temporary:
                receipt, _, home, workflow = _start(module, Path(temporary))
                records = _records()
                gate = records["evidence"]["E1"]["technical"]["gates"]["type"]
                gate["status"] = "not-applicable"
                gate["details"] = details
                with self.assertRaises(Exception):
                    module.apply_updates(
                        workflow_id=workflow,
                        expected_revision=receipt["revision"],
                        updates=records,
                        state_home=home,
                    )

    def test_recovery_no_follow_current_and_predecessor_boundaries_are_independent(self) -> None:
        """Regression: current/predecessor symlink, nonregular, and unsafe modes fail closed without outside mutation."""
        module = _load()
        for target_kind in ("current-symlink", "current-directory", "current-mode", "previous-symlink", "previous-directory", "previous-mode"):
            with self.subTest(target=target_kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                receipt, _, home, workflow = _start(module, root)
                current = module.apply_updates(workflow_id=workflow, expected_revision=receipt["revision"], updates={"scope": {"components": ["CheckoutForm"], "exclusions": ["route"]}}, state_home=home)
                design = home / "design"
                target = design / workflow
                previous = design / f"{workflow}.previous"
                target.write_text("{broken", encoding="utf-8")
                outside = root / "outside"
                outside.write_bytes(b"do-not-touch")
                if target_kind == "current-symlink":
                    target.unlink(); target.symlink_to(outside)
                elif target_kind == "current-directory":
                    target.unlink(); target.mkdir()
                elif target_kind == "current-mode":
                    target.chmod(0o666)
                elif target_kind == "previous-symlink":
                    previous.unlink(); previous.symlink_to(outside)
                elif target_kind == "previous-directory":
                    previous.unlink(); previous.mkdir()
                else:
                    previous.chmod(0o666)
                outside_before = outside.read_bytes()
                target_before = None if target.is_dir() else (target.read_bytes() if target.exists() and not target.is_symlink() else None)
                with self.assertRaises(Exception, msg=target_kind):
                    module.recover_workflow(workflow_id=workflow, state_home=home)
                self.assertEqual(outside.read_bytes(), outside_before)
                if target_kind.endswith("symlink"):
                    self.assertTrue(target.is_symlink() if target_kind.startswith("current") else previous.is_symlink())
                elif target_kind == "current-directory":
                    self.assertTrue(target.is_dir())
                elif target_kind == "previous-directory":
                    self.assertTrue(previous.is_dir())
                elif target_kind == "current-mode":
                    self.assertEqual(target.stat().st_mode & 0o777, 0o666)
                else:
                    self.assertEqual(previous.stat().st_mode & 0o777, 0o666)

    def test_active_payload_layers_are_none_and_delivered_layers_equal_predeclared_closed_inventory(self) -> None:
        """Regression: lifecycle and exact predeclared candidate/review/manifest layers cannot drift."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, home, workflow = _start(module, Path(temporary) / "valid")
            updated, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
            active = module.load_workflow(workflow_id=workflow, state_home=home)
            self.assertIsNone(active["candidate_payload"]); self.assertIsNone(active["review_evidence"]); self.assertIsNone(active["manifest"])
            module.deliver_workflow(workflow_id=workflow, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
            delivered = module.load_workflow(workflow_id=workflow, state_home=home)
            self.assertEqual(delivered["delivery"], _delivery(candidate, review, manifest))
            for layer in ("candidate", "review", "manifest"):
                for mutant in ("wrong-type", "unknown-key", "wrong-path", "wrong-digest", "wrong-classification"):
                    with self.subTest(layer=layer, mutant=mutant), tempfile.TemporaryDirectory() as mutant_tmp:
                        mutant_home = Path(mutant_tmp) / "state"
                        mutant_root = Path(mutant_tmp)
                        # Recreate a clean delivered workflow for each independent mutant.
                        r, _, h, w = _start(module, mutant_root)
                        u, rec, can, rev, man = _seed(module, r, h, w)
                        module.deliver_workflow(workflow_id=w, expected_revision=u["revision"], candidate_payload=can, review_evidence=rev, manifest=man, state_home=h)
                        target = h / "design" / w
                        state = json.loads(target.read_text(encoding="utf-8"))
                        value = copy.deepcopy(state["delivery"][layer])
                        if mutant == "wrong-type": value = []
                        elif mutant == "unknown-key": value["unexpected"] = True
                        elif mutant == "wrong-path": value["files"][0]["path"] = "substituted/path"
                        elif mutant == "wrong-digest": value["files"][0]["digest"] = DIGEST
                        else: value["files"][0]["classification"] = "manifest" if layer == "candidate" else "component"
                        state["delivery"][layer] = value
                        target.write_text(json.dumps(state), encoding="utf-8")
                        with self.assertRaises(Exception, msg=f"{layer}:{mutant}"):
                            module.load_workflow(workflow_id=w, state_home=h)

    def test_legacy_delivery_shapes_cannot_bypass_malformed_bindings(self) -> None:
        """Regression: list/classifications-only delivery compatibility must not skip domain validation."""
        module = _load()
        for shape in (["component", "review", "manifest"], {"classifications": ["component", "review", "manifest"]}):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as temporary:
                receipt, _, home, workflow = _start(module, Path(temporary))
                target = home / "design" / workflow
                state = json.loads(target.read_text(encoding="utf-8"))
                state["delivery"] = shape
                state["components"] = {"CheckoutForm": {"id": "CheckoutForm", "unknown": True}}
                state["evidence"] = {"E1": {"id": "E1", "component_id": "CheckoutForm"}}
                state["approvals"] = {"A1": {"id": "A1", "component_id": "CheckoutForm", "decision": "approved"}}
                target.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(Exception, msg=str(shape)):
                    module.load_workflow(workflow_id=workflow, state_home=home)

    def test_legacy_shapes_and_each_payload_layer_fail_independently(self) -> None:
        module = _load()
        for shape in (["component", "review", "manifest"], {"classifications": ["component", "review", "manifest"]}):
            with tempfile.TemporaryDirectory() as temporary:
                receipt, _, home, workflow = _start(module, Path(temporary))
                updated, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
                target = home / "design" / workflow
                state = json.loads(target.read_text(encoding="utf-8")); state["delivery"] = shape; target.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(Exception): module.load_workflow(workflow_id=workflow, state_home=home)
        for layer in ("candidate_payload", "review_evidence", "manifest"):
            with tempfile.TemporaryDirectory() as temporary:
                receipt, _, home, workflow = _start(module, Path(temporary))
                updated, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
                target = home / "design" / workflow
                state = json.loads(target.read_text(encoding="utf-8")); state[layer] = {"unexpected": True}; target.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(Exception): module.load_workflow(workflow_id=workflow, state_home=home)
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, home, workflow = _start(module, Path(temporary))
            updated, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
            target = home / "design" / workflow
            state = json.loads(target.read_text(encoding="utf-8"))
            state["delivery"] = {"classifications": {"candidate": "component", "review": "review", "manifest": "manifest"}}
            state["candidate_payload"] = {"unexpected": True}
            target.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow, state_home=home)


if __name__ == "__main__":
    unittest.main()


def test_real_artifacts_records_and_approval_provenance_are_required(tmp_path):
    import pytest

    module = _load("design_state_real_evidence")
    receipt, repo, home, workflow = _start(module, tmp_path)
    updated, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
    target = home / "design" / workflow
    original = target.read_bytes()
    component = repo / candidate["files"][0]["path"]
    component.write_bytes(component.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="artifact bytes"):
        module.deliver_workflow(workflow_id=workflow, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
    assert target.read_bytes() == original
    component.write_bytes(b"fixture-artifact:" + _records()["components"]["CheckoutForm"]["code_digest"].encode())
    result = records["evidence"]["E1"]["technical"]["results"][0]
    output = home / "design" / "records" / (result["record_id"] + ".output")
    assert output.read_bytes() == b"fixture-check-output\n"
    output.write_bytes(b"unrelated output")
    with pytest.raises(ValueError, match="altered command record"):
        module.deliver_workflow(workflow_id=workflow, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
    assert target.read_bytes() == original
    output.write_bytes(b"fixture-check-output\n")
    mutated = json.loads(original)
    del mutated["approvals"]["A1"]["provenance"]
    target.write_text(json.dumps(mutated))
    with pytest.raises(ValueError, match="attestation provenance"):
        module.deliver_workflow(workflow_id=workflow, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
    target.write_bytes(original)
    module.deliver_workflow(workflow_id=workflow, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)


def test_caller_only_command_claim_cannot_deliver(tmp_path):
    import pytest

    module = _load("design_state_legacy_claim")
    receipt, _, home, workflow = _start(module, tmp_path)
    updated, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
    target = home / "design" / workflow
    state = json.loads(target.read_bytes())
    del state["evidence"]["E1"]["technical"]["results"][0]["record_id"]
    target.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="recorded checks"):
        module.deliver_workflow(workflow_id=workflow, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)


def test_unconfirmed_direct_workflow_cannot_approve_or_deliver(tmp_path):
    import pytest

    module = _load("design_state_unconfirmed_delivery")
    repo, head = _repo(tmp_path)
    home = tmp_path / "state"
    receipt = module.initialize_workflow(repository=repo, branch="feature/design", worktree=repo, baseline=head, dirty_fingerprint=hashlib.sha256(b"").hexdigest(), ui_contract={"digest": DIGEST}, scope={"components": ["CheckoutForm"]}, state_home=home)
    with pytest.raises(ValueError, match="confirmed design brief"):
        module.apply_updates(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], updates=_records(), state_home=home)
    candidate, review, manifest = _layers(_records())
    with pytest.raises(ValueError, match="confirmed design brief"):
        module.deliver_workflow(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
    assert module.load_workflow(workflow_id=receipt["workflow_id"], state_home=home)["revision"] == receipt["revision"]


def test_normal_updates_block_ineligible_and_unresolved_material_delivery(tmp_path):
    import pytest

    module = _load("design_state_material_delivery")
    receipt, _, home, workflow = _start(module, tmp_path)
    current, records, candidate, review, manifest = _seed(module, receipt, home, workflow)
    component = {**records["components"]["CheckoutForm"], "eligible": False}
    current = module.apply_updates(workflow_id=workflow, expected_revision=current["revision"], updates={"components": {"CheckoutForm": component}}, state_home=home)
    with pytest.raises(ValueError, match="ineligible"):
        module.deliver_workflow(workflow_id=workflow, expected_revision=current["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
    component["eligible"] = True
    question = {"id": "scope", "material": True, "resolved": False, "decision_reference": ""}
    current = module.apply_updates(workflow_id=workflow, expected_revision=current["revision"], updates={"components": {"CheckoutForm": component}, "questions": {"scope": question}}, state_home=home)
    with pytest.raises(ValueError, match="unresolved material"):
        module.deliver_workflow(workflow_id=workflow, expected_revision=current["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)
    question.update(resolved=True, decision_reference="user confirmed existing scope")
    current = module.apply_updates(workflow_id=workflow, expected_revision=current["revision"], updates={**records, "components": {"CheckoutForm": component}, "questions": {"scope": question}, "delivery": _delivery(candidate, review, manifest)}, state_home=home)
    assert module.deliver_workflow(workflow_id=workflow, expected_revision=current["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)["brief_digest"] == FIXTURE_BRIEF_DIGEST


def test_multifile_component_delivery_binds_complete_supporting_file_union(tmp_path):
    import pytest

    module = _load("design_state_multifile")
    receipt, _, home, workflow = _start(module, tmp_path)
    records = _records()
    files = [{"path": path, "digest": hashlib.sha256(path.encode()).hexdigest(), "classification": "component"} for path in ("components/CheckoutForm.tsx", "components/CheckoutForm.css", "components/index.ts", "components/CheckoutForm.test.tsx", "assets/checkout.svg")]
    records["components"]["CheckoutForm"]["files"] = files
    records["components"]["CheckoutForm"]["code_digest"] = module.component_digest(files)
    candidate, review, manifest = _layers(records)
    prepare_delivery_fixture(module, receipt, home, workflow, records, (candidate, review, manifest))
    current = module.apply_updates(workflow_id=workflow, expected_revision=receipt["revision"], updates={**records, "delivery": _delivery(candidate, review, manifest)}, state_home=home)
    for omitted in candidate["files"]:
        incomplete = {"files": [item for item in candidate["files"] if item != omitted]}
        with pytest.raises(ValueError, match="complete component file union"):
            module.deliver_workflow(workflow_id=workflow, expected_revision=current["revision"], candidate_payload=incomplete, review_evidence=review, manifest=manifest, state_home=home)
    component = records["components"]["CheckoutForm"]
    assert component["code_digest"] == module.component_digest(list(reversed(component["files"])))
    assert len(component["files"]) == 5
    assert module.deliver_workflow(workflow_id=workflow, expected_revision=current["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home)["lifecycle"] == "delivered"
