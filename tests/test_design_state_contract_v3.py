from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.design_state_test_support import passing_technical, prepare_delivery_fixture, confirm_fixture_workflow, FIXTURE_BRIEF_DIGEST


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins" / "expskill" / "content" / "scripts" / "design_state.py"
DIGEST = "a" * 64


def _load(name: str = "design_state_contract_v3") -> object:
    if not HELPER.is_file() or HELPER.is_symlink() or HELPER.stat().st_size == 0:
        raise AssertionError(f"invalid state helper: {HELPER}")
    spec = importlib.util.spec_from_file_location(name, HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("state helper has no loader")
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
    return repo, subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _dirty(repo: Path) -> str:
    status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"], text=True).encode()
    return hashlib.sha256(status).hexdigest()


def _start(module: object, root: Path) -> tuple[dict, Path, Path, str]:
    repo, head = _repo(root)
    state_home = root / "state"
    receipt = module.initialize_workflow(repository=repo, branch="feature/design", worktree=repo, baseline=head, dirty_fingerprint=_dirty(repo), ui_contract={"digest": DIGEST, "outcome": "checkout"}, scope={"components": ["CheckoutForm", "AccountCard"], "exclusions": ["route"]}, state_home=state_home)
    receipt = confirm_fixture_workflow(module, receipt, state_home)
    return receipt, repo, state_home, str(receipt["workflow_id"])


def _records(names: tuple[str, ...] = ("CheckoutForm", "AccountCard"), suffix: str = "") -> dict[str, dict]:
    components: dict[str, dict] = {}
    dependencies: dict[str, dict] = {}
    evidence: dict[str, dict] = {}
    approvals: dict[str, dict] = {}
    for name in names:
        code = hashlib.sha256(f"{name}:code:{suffix}".encode()).hexdigest()
        contract = hashlib.sha256(f"{name}:contract".encode()).hexdigest()
        evidence_id, dependency_id, approval_id = f"{name}-evidence", f"{name}-dependency", f"{name}-approval"
        evidence_digest = hashlib.sha256(f"{name}:evidence:{suffix}".encode()).hexdigest()
        components[name] = {"id": name, "code_digest": code, "contract_digest": contract, "evidence_ids": [evidence_id], "dependency_ids": [dependency_id], "approval_id": approval_id}
        dependencies[dependency_id] = {"id": dependency_id, "digest": hashlib.sha256(f"{name}:dependency".encode()).hexdigest(), "component_ids": [name]}
        evidence[evidence_id] = {"id": evidence_id, "component_id": name, "digest": evidence_digest, "code_digest": code, "contract_digest": contract, "widths": ["compact", "intermediate", "wide"], "themes": ["light", "dark"], "states": ["default", "loading", "error"], "technical": passing_technical(evidence_digest, "component-check")}
        approvals[approval_id] = {"id": approval_id, "component_id": name, "code_digest": code, "contract_digest": contract, "evidence_ids": [evidence_id], "dependency_ids": [dependency_id], "decision": "approved"}
    for collection in (components, evidence, approvals):
        for item in collection.values(): item["brief_digest"] = FIXTURE_BRIEF_DIGEST
    return {"components": components, "dependencies": dependencies, "evidence": evidence, "approvals": approvals}


def _seed(module: object, receipt: dict, state_home: Path, workflow_id: str, names: tuple[str, ...] = ("CheckoutForm", "AccountCard"), suffix: str = "") -> tuple[dict, dict]:
    records = _records(names, suffix)
    candidate, review, manifest = _layers(records)
    prepare_delivery_fixture(module, receipt, state_home, workflow_id, records, (candidate, review, manifest))
    records["selected_rules"] = {"responsive": {"id": "responsive", "reason": "actual pressure"}}
    records["seed_permission"] = {"source": "project-owned", "version": "current", "approved": True}
    records["questions"] = {}
    records["delivery"] = _expected_delivery(candidate, review, manifest)
    updated = module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates=records, state_home=state_home)
    return updated, records


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _layers(records: dict[str, dict]) -> tuple[dict, dict, dict]:
    components = records["components"]
    evidence = records["evidence"]
    candidate = {"files": sorted([{"path": f"components/{name}.tsx", "digest": value["code_digest"], "classification": "component"} for name, value in components.items()], key=lambda item: item["path"])}
    review = {"files": sorted([{"path": f"evidence/{eid}.json", "digest": value["digest"], "classification": "review"} for eid, value in evidence.items()], key=lambda item: item["path"])}
    manifest = {"files": sorted([{"path": f"contracts/{name}.json", "digest": value["contract_digest"], "classification": "manifest"} for name, value in components.items()], key=lambda item: item["path"])}
    return candidate, review, manifest


def _expected_delivery(candidate: dict, review: dict, manifest: dict) -> dict:
    """Route-neutral closed state shape: exact layers, digests, and inventories."""
    return {
        "classifications": {"candidate": "component", "review": "review", "manifest": "manifest"},
        "candidate": {"inventory_digest": _digest(candidate), "files": candidate["files"]},
        "review": {"inventory_digest": _digest(review), "files": review["files"]},
        "manifest": {"inventory_digest": _digest(manifest), "files": manifest["files"]},
    }


class DesignStateContractV3Tests(unittest.TestCase):
    def test_recovery_rejects_valid_current_active_and_delivered_without_mutation(self) -> None:
        """Regression: recovery is for invalid current generations only, never rollback."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, state_home, workflow_id = _start(module, Path(temporary) / "active")
            current, records = _seed(module, receipt, state_home, workflow_id)
            target = state_home / "design" / workflow_id
            before = target.read_bytes()
            predecessor = (state_home / "design" / f"{workflow_id}.previous").read_bytes()
            with self.assertRaises(Exception):
                module.recover_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual((state_home / "design" / f"{workflow_id}.previous").read_bytes(), predecessor)
            valid_receipt, _, valid_home, valid_id = _start(module, Path(temporary) / "delivered")
            delivered_revision, valid_records = _seed(module, valid_receipt, valid_home, valid_id)
            candidate, review, manifest = _layers(valid_records)
            delivered = module.deliver_workflow(workflow_id=valid_id, expected_revision=delivered_revision["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=valid_home)
            delivered_target = valid_home / "design" / valid_id
            delivered_before = delivered_target.read_bytes()
            with self.assertRaises(Exception):
                module.recover_workflow(workflow_id=valid_id, state_home=valid_home)
            self.assertEqual(delivered_target.read_bytes(), delivered_before)

    def test_recovery_requires_one_identity_consistent_predecessor_and_strict_monotonic_revision(self) -> None:
        """Regression: only one valid predecessor may recover a torn current, advancing beyond known current revision."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repo, state_home, workflow_id = _start(module, Path(temporary))
            current, records = _seed(module, receipt, state_home, workflow_id)
            target = state_home / "design" / workflow_id
            # Keep the torn generation's revision observable while making its schema invalid.
            target.write_text(json.dumps({"revision": current["revision"], "workflow_id": workflow_id, "broken": True}), encoding="utf-8")
            recovered = module.recover_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertGreater(recovered["revision"], current["revision"])
            recovered_state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(recovered_state["revision"], recovered["revision"])
            self.assertEqual(recovered_state["identity"]["repository"], str(repo.resolve()))
            predecessor = state_home / "design" / f"{workflow_id}.previous"
            predecessor.write_text(json.dumps({"revision": 0, "workflow_id": workflow_id}), encoding="utf-8")
            target_before = target.read_bytes()
            with self.assertRaises(Exception):
                module.recover_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(target.read_bytes(), target_before)

    def test_delivery_requires_exact_closed_inventories_and_persists_route_neutral_shape(self) -> None:
        """Regression: every layer is exact, complete, digest-bound, and durably retained."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, state_home, workflow_id = _start(module, Path(temporary))
            updated, records = _seed(module, receipt, state_home, workflow_id)
            candidate, review, manifest = _layers(records)
            self.assertEqual(module.load_workflow(workflow_id=workflow_id, state_home=state_home)["delivery"], _expected_delivery(candidate, review, manifest))
            mutants: list[tuple[str, dict, dict, dict]] = []
            for layer_name, layer in (("candidate", candidate), ("review", review), ("manifest", manifest)):
                subset = copy.deepcopy(layer)
                subset["files"] = subset["files"][:-1]
                mutants.append((f"{layer_name}-subset", subset if layer_name == "candidate" else candidate, subset if layer_name == "review" else review, subset if layer_name == "manifest" else manifest))
                extra = copy.deepcopy(layer)
                extra["files"].append({"path": f"{layer_name}/extra.json", "digest": DIGEST, "classification": {"candidate": "component", "review": "review", "manifest": "manifest"}[layer_name]})
                mutants.append((f"{layer_name}-extra", extra if layer_name == "candidate" else candidate, extra if layer_name == "review" else review, extra if layer_name == "manifest" else manifest))
                unknown = copy.deepcopy(layer)
                unknown["unexpected"] = True
                mutants.append((f"{layer_name}-unknown", unknown if layer_name == "candidate" else candidate, unknown if layer_name == "review" else review, unknown if layer_name == "manifest" else manifest))
            substituted = copy.deepcopy(candidate)
            substituted["files"][0]["path"] = "components/Substituted.tsx"
            mutants.append(("candidate-path-substitution", substituted, review, manifest))
            for label, mutant_candidate, mutant_review, mutant_manifest in mutants:
                with self.subTest(label=label), self.assertRaises(Exception, msg=label):
                    module.deliver_workflow(workflow_id=workflow_id, expected_revision=updated["revision"], candidate_payload=mutant_candidate, review_evidence=mutant_review, manifest=mutant_manifest, state_home=state_home)
            delivered = module.deliver_workflow(workflow_id=workflow_id, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)
            state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(state["delivery"], _expected_delivery(candidate, review, manifest))
            self.assertEqual(state["candidate_payload"], candidate)
            self.assertEqual(state["review_evidence"], review)
            self.assertEqual(state["manifest"], manifest)
            self.assertEqual(delivered["candidate_inventory_digest"], _digest(candidate))
            self.assertEqual(delivered["review_evidence_digest"], _digest(review))
            self.assertEqual(delivered["manifest_digest"], _digest(manifest))

    def test_missing_evidence_digest_is_rejected_at_update_load_and_delivery(self) -> None:
        """Regression: evidence without its mandatory digest cannot become current or deliverable."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            records = _records(("CheckoutForm",))
            missing = copy.deepcopy(records["evidence"]["CheckoutForm-evidence"])
            del missing["digest"]
            for boundary in ("apply", "load", "delivery"):
                with self.subTest(boundary=boundary):
                    boundary_root = Path(temporary) / boundary
                    receipt, _, state_home, workflow_id = _start(module, boundary_root)
                    if boundary == "apply":
                        with self.assertRaises(Exception):
                            module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates={"components": records["components"], "dependencies": records["dependencies"], "evidence": {"CheckoutForm-evidence": missing}, "approvals": records["approvals"]}, state_home=state_home)
                        continue
                    current, valid_records = _seed(module, receipt, state_home, workflow_id, ("CheckoutForm",))
                    target = state_home / "design" / workflow_id
                    state = json.loads(target.read_text(encoding="utf-8"))
                    del state["evidence"]["CheckoutForm-evidence"]["digest"]
                    target.write_text(json.dumps(state), encoding="utf-8")
                    if boundary == "load":
                        with self.assertRaises(Exception):
                            module.load_workflow(workflow_id=workflow_id, state_home=state_home)
                    else:
                        candidate, review, manifest = _layers(valid_records)
                        with self.assertRaises(Exception):
                            module.deliver_workflow(workflow_id=workflow_id, expected_revision=current["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)

    def test_full_causal_refresh_clears_only_affected_invalidations_and_partial_stays_stale(self) -> None:
        """Regression: complete current bindings restore delivery only for the changed component."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, state_home, workflow_id = _start(module, Path(temporary))
            seeded, records = _seed(module, receipt, state_home, workflow_id)
            changed = copy.deepcopy(records["evidence"]["CheckoutForm-evidence"])
            changed["digest"] = "c" * 64
            stale = module.apply_updates(workflow_id=workflow_id, expected_revision=seeded["revision"], updates={"evidence": {"CheckoutForm-evidence": changed}}, state_home=state_home)
            stale_state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertIn("CheckoutForm", stale_state["invalidations"])
            self.assertNotIn("AccountCard", stale_state["invalidations"])
            candidate, review, manifest = _layers(records)
            with self.assertRaises(Exception):
                module.deliver_workflow(workflow_id=workflow_id, expected_revision=stale["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)
            refreshed = copy.deepcopy(records)
            refreshed["evidence"]["CheckoutForm-evidence"] = changed
            refreshed["approvals"]["CheckoutForm-approval"]["code_digest"] = refreshed["components"]["CheckoutForm"]["code_digest"]
            refreshed_layers = _layers(refreshed)
            prepare_delivery_fixture(module, stale, state_home, workflow_id, refreshed, refreshed_layers)
            changed = refreshed["evidence"]["CheckoutForm-evidence"]
            refreshed_receipt = module.apply_updates(workflow_id=workflow_id, expected_revision=stale["revision"], updates={**{key: refreshed[key] for key in ("components", "evidence", "dependencies", "approvals")}, "delivery": _expected_delivery(*refreshed_layers)}, state_home=state_home)
            refreshed_state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertNotIn("CheckoutForm", refreshed_state["invalidations"])
            self.assertNotIn("AccountCard", refreshed_state["invalidations"])
            module.deliver_workflow(workflow_id=workflow_id, expected_revision=refreshed_receipt["revision"], candidate_payload=refreshed_layers[0], review_evidence=refreshed_layers[1], manifest=refreshed_layers[2], state_home=state_home)

    def test_closed_nested_sections_reject_unknown_fields_types_at_initialize_update_and_load(self) -> None:
        """Regression: scope/rules/seed/questions/delivery/invalidations are closed recursively."""
        module = _load()
        base = {"repository": None, "branch": "feature/design", "worktree": None, "baseline": None, "dirty_fingerprint": None, "ui_contract": {"digest": DIGEST, "outcome": "checkout"}, "scope": {"components": ["CheckoutForm"], "exclusions": ["route"]}, "state_home": None}
        for field, value in (("scope", {"components": "CheckoutForm", "exclusions": [], "unknown": True}), ("selected_rules", {"x": {"id": "x", "unknown": True}}), ("seed_permission", {"source": "x", "unknown": True}), ("questions", {"q": {"id": "q", "unknown": True}}), ("delivery", {"classifications": ["component"], "unknown": True}), ("invalidations", {"CheckoutForm": {"reason": "x", "unknown": True}})):
            with self.subTest(stage="initialize", field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    repo, head = _repo(Path(temporary))
                    kwargs = dict(base, repository=repo, worktree=repo, baseline=head, dirty_fingerprint=_dirty(repo), state_home=Path(temporary) / "state")
                    if field == "scope":
                        kwargs["scope"] = value
                    else:
                        kwargs[field] = value
                    with self.assertRaises(Exception):
                        module.initialize_workflow(**kwargs)
            with self.subTest(stage="update", field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    receipt, _, state_home, workflow_id = _start(module, Path(temporary))
                    before = (state_home / "design" / workflow_id).read_bytes()
                    with self.assertRaises(Exception):
                        module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates={field: value}, state_home=state_home)
                    self.assertEqual((state_home / "design" / workflow_id).read_bytes(), before)
            with self.subTest(stage="load", field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    receipt, _, state_home, workflow_id = _start(module, Path(temporary))
                    target = state_home / "design" / workflow_id
                    state = json.loads(target.read_text(encoding="utf-8"))
                    state[field] = value
                    target.write_text(json.dumps(state), encoding="utf-8")
                    with self.assertRaises(Exception):
                        module.load_workflow(workflow_id=workflow_id, state_home=state_home)


if __name__ == "__main__":
    unittest.main()
