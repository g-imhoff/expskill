from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.design_state_test_support import passing_technical
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages" / "codex" / "scripts" / "design_state.py"
DIGEST = "a" * 64


def _load(name: str = "design_state_contract_v2") -> object:
    if not HELPER.is_file():
        raise AssertionError(f"missing state helper: {HELPER}")
    spec = importlib.util.spec_from_file_location(name, HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("state helper has no import loader")
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
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "switch", "--quiet", "-c", "feature/design"], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return repo, head


def _dirty(repo: Path) -> str:
    status = subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"], text=True
    ).encode()
    return hashlib.sha256(status).hexdigest()


def _start(module: object, root: Path) -> tuple[dict, Path, Path, str]:
    repo, head = _repo(root)
    state_home = root / "state"
    contract = {"digest": DIGEST, "outcome": "checkout"}
    receipt = module.initialize_workflow(
        repository=repo,
        branch="feature/design",
        worktree=repo,
        baseline=head,
        dirty_fingerprint=_dirty(repo),
        ui_contract=contract,
        scope={"components": ["CheckoutForm"], "exclusions": ["route"]},
        state_home=state_home,
    )
    return receipt, repo, state_home, str(receipt["workflow_id"])


def _valid_inventory(path: str = "CheckoutForm.tsx", digest: str = DIGEST, classification: str = "component") -> dict:
    return {"files": [{"path": path, "digest": digest, "classification": classification}]}


def _valid_records(component_names: tuple[str, ...] = ("CheckoutForm",)) -> dict:
    """One closed, cross-bound component/evidence/dependency/approval fixture."""
    components = {}
    dependencies = {}
    evidence = {}
    approvals = {}
    for index, component_name in enumerate(component_names, start=1):
        component_digest = DIGEST if index == 1 else "b" * 64
        evidence_id, dependency_id, approval_id = f"E{index}", f"D{index}", f"A{index}"
        components[component_name] = {
            "id": component_name,
            "code_digest": component_digest,
            "contract_digest": DIGEST,
            "evidence_ids": [evidence_id],
            "dependency_ids": [dependency_id],
            "approval_id": approval_id,
        }
        dependencies[dependency_id] = {"id": dependency_id, "digest": DIGEST, "component_ids": [component_name]}
        evidence[evidence_id] = {
            "id": evidence_id,
            "component_id": component_name,
            "digest": DIGEST,
            "code_digest": component_digest,
            "contract_digest": DIGEST,
            "widths": ["compact", "intermediate", "wide"],
            "themes": ["light", "dark"],
            "states": ["default", "error", "loading"],
            "technical": passing_technical(DIGEST, "component-check"),
        }
        approvals[approval_id] = {
            "id": approval_id,
            "component_id": component_name,
            "code_digest": component_digest,
            "contract_digest": DIGEST,
            "evidence_ids": [evidence_id],
            "dependency_ids": [dependency_id],
            "decision": "approved",
        }
    candidate = _valid_inventory("CheckoutForm.tsx", DIGEST, "component")
    review = _valid_inventory("review.json", DIGEST, "review")
    manifest = _valid_inventory("manifest.json", DIGEST, "manifest")

    def inventory(value: dict) -> dict:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return {"inventory_digest": hashlib.sha256(encoded).hexdigest(), "files": value["files"]}

    return {
        "selected_rules": {"responsive": {"id": "responsive", "reason": "actual pressure"}},
        "seed_permission": {"source": "project-owned", "version": "current", "approved": True},
        "questions": {},
        "delivery": {
            "classifications": {
                "candidate": "component",
                "review": "review",
                "manifest": "manifest",
            },
            "candidate": inventory(candidate),
            "review": inventory(review),
            "manifest": inventory(manifest),
        },
        "components": components,
        "dependencies": dependencies,
        "evidence": evidence,
        "approvals": approvals,
    }


def _seed_valid(module: object, receipt: dict, state_home: Path, workflow_id: str, component_names: tuple[str, ...] = ("CheckoutForm",)) -> dict:
    updates = _valid_records(component_names)
    return module.apply_updates(
        workflow_id=workflow_id,
        expected_revision=receipt["revision"],
        updates=updates,
        state_home=state_home,
    )


def _valid_layers() -> tuple[dict, dict, dict]:
    return (
        _valid_inventory("CheckoutForm.tsx", DIGEST, "component"),
        _valid_inventory("review.json", DIGEST, "review"),
        _valid_inventory("manifest.json", DIGEST, "manifest"),
    )


class DesignStateContractV2Tests(unittest.TestCase):
    def test_identity_is_derived_from_actual_git_and_false_baseline_or_dirty_rejects(self) -> None:
        """Regression: caller-asserted HEAD and dirty fingerprints must not bind private state."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root / "valid")
            kwargs = dict(
                repository=repo,
                branch="feature/design",
                worktree=repo,
                ui_contract={"digest": DIGEST},
                scope={},
                state_home=root / "state",
            )
            with self.assertRaises(Exception):
                module.initialize_workflow(baseline="0" * 40, dirty_fingerprint=_dirty(repo), **kwargs)
            with self.assertRaises(Exception):
                module.initialize_workflow(baseline=head, dirty_fingerprint="0" * 64, **kwargs)
            receipt = module.initialize_workflow(baseline=head, dirty_fingerprint=_dirty(repo), **kwargs)
            self.assertEqual(receipt["identity"]["head"], head)
            self.assertEqual(receipt["identity"]["dirty_fingerprint"], _dirty(repo))

    def test_external_head_branch_dirty_and_contract_changes_revalidate_each_operation(self) -> None:
        """Regression: unacknowledged external changes block every operation.

        Design-authored UI writes require a future typed workspace-refresh receipt;
        this gate must not make intended writes impossible.
        """
        mutations = ("head", "branch", "dirty")
        operations = ("load", "apply", "pause", "resume", "discard", "deliver")

        def invoke(module: object, operation: str, receipt: dict, repo: Path, state_home: Path, workflow_id: str) -> None:
            current = receipt
            if operation == "load":
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            elif operation == "apply":
                module.apply_updates(workflow_id=workflow_id, expected_revision=current["revision"], updates={"scope": {"components": ["CheckoutForm"], "exclusions": ["route"]}}, state_home=state_home)
            elif operation == "pause":
                module.pause_workflow(workflow_id=workflow_id, expected_revision=current["revision"], state_home=state_home)
            elif operation == "resume":
                current = module.pause_workflow(workflow_id=workflow_id, expected_revision=current["revision"], state_home=state_home)
                module.resume_workflow(workflow_id=workflow_id, expected_revision=current["revision"], state_home=state_home)
            elif operation == "discard":
                module.discard_workflow(workflow_id=workflow_id, expected_revision=current["revision"], confirmed=True, state_home=state_home)
            else:
                current = _seed_valid(module, current, state_home, workflow_id)
                candidate, review, manifest = _valid_layers()
                module.deliver_workflow(workflow_id=workflow_id, expected_revision=current["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)

        for mutation in mutations:
            for operation in operations:
                with self.subTest(mutation=mutation, operation=operation):
                    module = _load(f"design_state_v2_{mutation}_{operation}")
                    with tempfile.TemporaryDirectory() as temporary:
                        control_receipt, control_repo, control_state, control_workflow = _start(module, Path(temporary) / "control")
                        invoke(module, operation, control_receipt, control_repo, control_state, control_workflow)
                        receipt, repo, state_home, workflow_id = _start(module, Path(temporary) / "mutated")
                        if operation == "resume":
                            receipt = module.pause_workflow(workflow_id=workflow_id, expected_revision=receipt["revision"], state_home=state_home)
                        elif operation == "deliver":
                            receipt = _seed_valid(module, receipt, state_home, workflow_id)
                        if mutation == "head":
                            (repo / "head-change.txt").write_text("changed\n", encoding="utf-8")
                            subprocess.run(["git", "-C", str(repo), "add", "head-change.txt"], check=True)
                            subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "head-change"], check=True)
                        elif mutation == "branch":
                            subprocess.run(["git", "-C", str(repo), "switch", "--quiet", "-c", "other-branch"], check=True)
                        else:
                            (repo / "dirty-change.txt").write_text("changed\n", encoding="utf-8")
                        with self.assertRaises(Exception):
                            if operation == "load":
                                module.load_workflow(workflow_id=workflow_id, state_home=state_home)
                            elif operation == "apply":
                                module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates={"scope": {"components": ["CheckoutForm"], "exclusions": ["route"]}}, state_home=state_home)
                            elif operation == "pause":
                                module.pause_workflow(workflow_id=workflow_id, expected_revision=receipt["revision"], state_home=state_home)
                            elif operation == "resume":
                                module.resume_workflow(workflow_id=workflow_id, expected_revision=receipt["revision"], state_home=state_home)
                            elif operation == "discard":
                                module.discard_workflow(workflow_id=workflow_id, expected_revision=receipt["revision"], confirmed=True, state_home=state_home)
                            else:
                                candidate, review, manifest = _valid_layers()
                                module.deliver_workflow(workflow_id=workflow_id, expected_revision=receipt["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)

        module = _load("design_state_v2_contract_digest")
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repo, state_home, workflow_id = _start(module, Path(temporary))
            with self.assertRaisesRegex(Exception, "(?i)(identity|contract|digest)"):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home, repository=repo, branch="feature/design", worktree=repo, baseline=receipt["baseline"], dirty_fingerprint=receipt["identity"]["dirty_fingerprint"], ui_contract={"digest": "b" * 64})

    def test_closed_recursive_schema_rejects_each_unknown_nested_key_type_digest_and_reference(self) -> None:
        """Regression: shallow dictionaries and arbitrary booleans cannot fabricate Design state."""
        cases = (
            ("ui_contract", {"digest": DIGEST, "unknown": True}),
            ("scope", {"components": [], "unknown": True}),
            ("components", {"CheckoutForm": {"id": "CheckoutForm", "unknown": True}}),
            ("dependencies", {"D1": {"id": "D1", "digest": "bad"}}),
            ("evidence", {"E1": {"id": "E1", "component_ids": "CheckoutForm"}}),
            ("approvals", {"A1": {"id": "A1", "approved": True, "unknown": True}}),
            ("components", {"CheckoutForm": {"id": "CheckoutForm", "evidence_ids": ["missing"]}}),
        )
        for index, (key, value) in enumerate(cases):
            with self.subTest(case=index, key=key):
                module = _load(f"design_state_schema_v2_{index}")
                with tempfile.TemporaryDirectory() as temporary:
                    receipt, _, state_home, workflow_id = _start(module, Path(temporary))
                    with self.assertRaises(Exception):
                        module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates={key: value}, state_home=state_home)

    def test_initial_state_contains_all_closed_contract_sections(self) -> None:
        """Regression: selected rules, seed permission, questions, and delivery bindings cannot be omitted."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, state_home, workflow_id = _start(module, Path(temporary))
            state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            for key in ("selected_rules", "seed_permission", "questions", "delivery"):
                self.assertIn(key, state, key)

    def test_eligibility_is_derived_from_exact_bindings_not_booleans(self) -> None:
        """Regression: eligible/approved booleans alone must never authorize delivery."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, state_home, workflow_id = _start(module, Path(temporary))
            component = {"id": "CheckoutForm", "eligible": True, "approved": True}
            with self.assertRaises(Exception):
                updated = module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates={"components": {"CheckoutForm": component}}, state_home=state_home)
                module.deliver_workflow(workflow_id=workflow_id, expected_revision=updated["revision"], candidate_payload=_valid_inventory(), review_evidence=_valid_inventory("review", DIGEST, "review"), manifest=_valid_inventory("manifest", DIGEST, "manifest"), state_home=state_home)

    def test_causal_changes_invalidate_approval_and_preserve_unrelated_approval(self) -> None:
        """Regression: stale causal approval cannot deliver while unrelated current approval remains usable."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, state_home, workflow_id = _start(module, Path(temporary))
            records = _valid_records(("CheckoutForm", "Unrelated"))
            current = module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates=records, state_home=state_home)
            before = set(module.load_workflow(workflow_id=workflow_id, state_home=state_home).get("invalidations", {}))
            changed_evidence = dict(records["evidence"]["E1"])
            changed_evidence["digest"] = "c" * 64
            current = module.apply_updates(workflow_id=workflow_id, expected_revision=current["revision"], updates={"evidence": {"E1": changed_evidence}}, state_home=state_home)
            state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            after = set(state.get("invalidations", {}))
            self.assertIn("A1", after - before)
            self.assertNotIn("A2", after - before)
            with self.assertRaises(Exception):
                module.deliver_workflow(workflow_id=workflow_id, expected_revision=current["revision"], candidate_payload=_valid_inventory(), review_evidence=_valid_inventory("review", DIGEST, "review"), manifest=_valid_inventory("manifest", DIGEST, "manifest"), state_home=state_home)

    def test_delivery_rejects_each_inventory_fabrication_and_persists_three_layers_and_receipt(self) -> None:
        """Regression: missing, stale, duplicate, escaping, mismatched inventories and incomplete receipts cannot deliver."""
        module = _load()
        valid_candidate, valid_review, valid_manifest = _valid_layers()
        invalid = (
            ("empty", {"files": []}, valid_review, valid_manifest),
            ("traversal", {"files": [{"path": "../secret", "digest": DIGEST, "classification": "component"}]}, valid_review, valid_manifest),
            ("absolute", {"files": [{"path": "/secret", "digest": DIGEST, "classification": "component"}]}, valid_review, valid_manifest),
            ("duplicate", {"files": [{"path": "same", "digest": DIGEST, "classification": "component"}, {"path": "same", "digest": DIGEST, "classification": "component"}]}, valid_review, valid_manifest),
            ("case-normalized-duplicate", {"files": [{"path": "Same", "digest": DIGEST, "classification": "component"}, {"path": "same", "digest": DIGEST, "classification": "component"}]}, valid_review, valid_manifest),
            ("wrong-digest", {"files": [{"path": "CheckoutForm.tsx", "digest": "b" * 64, "classification": "component"}]}, valid_review, valid_manifest),
            ("unknown-item-key", {"files": [{"path": "CheckoutForm.tsx", "digest": DIGEST, "classification": "component", "unexpected": True}]}, valid_review, valid_manifest),
            ("non-list", {"files": "not-a-list"}, valid_review, valid_manifest),
            ("candidate-classification", {"files": [{"path": "CheckoutForm.tsx", "digest": DIGEST, "classification": "review"}]}, valid_review, valid_manifest),
            ("review-classification", valid_candidate, {"files": [{"path": "review.json", "digest": DIGEST, "classification": "component"}]}, valid_manifest),
            ("manifest-classification", valid_candidate, valid_review, {"files": [{"path": "manifest.json", "digest": DIGEST, "classification": "review"}]}),
        )
        for index, (label, candidate, review, manifest) in enumerate(invalid):
            with self.subTest(case=label):
                module = _load(f"design_state_inventory_v2_{index}")
                with tempfile.TemporaryDirectory() as temporary:
                    receipt, _, state_home, workflow_id = _start(module, Path(temporary))
                    updated = _seed_valid(module, receipt, state_home, workflow_id)
                    with self.assertRaises(Exception):
                        module.deliver_workflow(workflow_id=workflow_id, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)

    def test_delivery_persists_exact_layers_and_full_identity_evidence_receipt(self) -> None:
        """Regression: a delivered state must retain candidate/review/manifest layers and all binding identity."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repo, state_home, workflow_id = _start(module, Path(temporary))
            updated = _seed_valid(module, receipt, state_home, workflow_id)
            candidate, review, manifest = _valid_layers()
            delivered = module.deliver_workflow(workflow_id=workflow_id, expected_revision=updated["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=state_home)
            state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(
                set(state),
                {"schema_version", "workflow_id", "revision", "lifecycle", "identity", "ui_contract", "scope", "components", "dependencies", "evidence", "approvals", "invalidations", "selected_rules", "seed_permission", "questions", "delivery", "candidate_payload", "review_evidence", "manifest"},
            )
            self.assertEqual(state["candidate_payload"], candidate)
            self.assertEqual(state["review_evidence"], review)
            self.assertEqual(state["manifest"], manifest)
            self.assertEqual(
                set(delivered),
                {"schema_version", "workflow_id", "revision", "lifecycle", "identity", "ui_contract", "candidate_digest", "candidate_inventory_digest", "review_evidence_digest", "manifest_digest", "evidence_digest", "approval_digest", "dependency_digest"},
            )
            self.assertEqual(delivered["identity"]["repository"], str(repo.resolve()))
            self.assertEqual(delivered["identity"]["head"], state["identity"]["head"])
            self.assertEqual(delivered["lifecycle"], "delivered")

    def test_recovery_requires_one_identity_consistent_predecessor_and_monotonic_durable_revision(self) -> None:
        """Regression: one valid predecessor recovers; corrupt, mismatched, duplicate, or unknown generations fail."""
        module = _load()
        self.assertTrue(callable(getattr(module, "recover_workflow", None)), "recover_workflow CLI/API is absent")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            current = module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates={"scope": {"components": ["CheckoutForm"]}}, state_home=state_home)
            target = state_home / "design" / workflow_id
            target.write_text("{torn", encoding="utf-8")
            recovered = module.recover_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertGreater(recovered["revision"], current["revision"] - 1)
            predecessor = target.with_name(workflow_id + ".previous")
            predecessor.write_text(json.dumps({"revision": 0}), encoding="utf-8")
            with self.assertRaises(Exception):
                module.recover_workflow(workflow_id=workflow_id, state_home=state_home)

    def test_unsafe_modes_unknown_generations_and_name_swap_fail_closed(self) -> None:
        """Regression: unsafe private roots/locks and descriptor/name-swap races must not be accepted."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            design_root = state_home / "design"
            os.chmod(design_root, 0o755)
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            os.chmod(design_root, 0o700)
            (design_root / ".unknown-generation").write_text("unknown", encoding="utf-8")
            with self.assertRaises(Exception):
                module.discover_workflow(repository=root / "repo", branch="feature/design", state_home=state_home)
            (design_root / ".unknown-generation").unlink()
            lock = design_root / ".lock"
            lock.unlink(missing_ok=True)
            lock.symlink_to(root / "outside-lock")
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)

    def test_descriptor_relative_name_swap_between_classification_and_open_is_refused(self) -> None:
        """Regression: a same-content symlink inserted after classification must not be opened."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            design_root = state_home / "design"
            target = design_root / workflow_id
            outside = root / "outside-current"
            outside.write_bytes(target.read_bytes())
            fired = {"value": False}

            original_os_open = module.os.open
            original_path_open = Path.open

            def swap_os_open(path: object, *args: object, **kwargs: object) -> object:
                if isinstance(path, (str, bytes, os.PathLike)) and Path(path) == target and not fired["value"]:
                    target.unlink()
                    target.symlink_to(outside)
                    fired["value"] = True
                return original_os_open(path, *args, **kwargs)

            def swap_path_open(path: Path, *args: object, **kwargs: object) -> object:
                if path == target and not fired["value"]:
                    target.unlink()
                    target.symlink_to(outside)
                    fired["value"] = True
                return original_path_open(path, *args, **kwargs)

            with mock.patch.object(module.os, "open", side_effect=swap_os_open), mock.patch.object(Path, "open", autospec=True, side_effect=swap_path_open):
                with self.assertRaises(Exception):
                    module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertTrue(fired["value"], "name-swap hook did not fire")
            self.assertEqual(outside.read_bytes(), target.resolve().read_bytes())


if __name__ == "__main__":
    unittest.main()
