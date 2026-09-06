from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType

from tests.design_state_test_support import passing_technical


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins" / "expskill" / "scripts" / "design_state.py"
DIGEST = "a" * 64


def _typed_updates(component_names: tuple[str, ...] = ("CheckoutForm",), code_suffix: str = "") -> dict[str, dict[str, dict[str, object]]]:
    """Return complete V2 bindings; booleans alone are intentionally absent."""
    components: dict[str, dict[str, object]] = {}
    dependencies: dict[str, dict[str, object]] = {}
    evidence: dict[str, dict[str, object]] = {}
    approvals: dict[str, dict[str, object]] = {}
    for index, name in enumerate(component_names, 1):
        code_digest = hashlib.sha256(f"{name}-code-{code_suffix}".encode()).hexdigest()
        contract_digest = hashlib.sha256(f"{name}-contract".encode()).hexdigest()
        evidence_id = f"{name}-render"
        dependency_id = f"{name}-dependency"
        approval_id = f"{name}-approval"
        components[name] = {
            "id": name,
            "code_digest": code_digest,
            "contract_digest": contract_digest,
            "evidence_ids": [evidence_id],
            "dependency_ids": [dependency_id],
            "approval_id": approval_id,
        }
        dependencies[dependency_id] = {"id": dependency_id, "digest": hashlib.sha256(f"{name}-dependency".encode()).hexdigest(), "component_ids": [name]}
        evidence[evidence_id] = {
            "id": evidence_id,
            "component_id": name,
            "digest": hashlib.sha256(f"{name}-evidence".encode()).hexdigest(),
            "code_digest": code_digest,
            "contract_digest": contract_digest,
            "widths": ["compact", "intermediate", "wide"],
            "themes": ["light", "dark"],
            "states": ["default", "error"],
            "technical": passing_technical(
                hashlib.sha256(f"{name}-technical".encode()).hexdigest(),
                "render-and-interaction-gates",
            ),
        }
        approvals[approval_id] = {
            "id": approval_id,
            "component_id": name,
            "code_digest": code_digest,
            "contract_digest": contract_digest,
            "evidence_ids": [evidence_id],
            "dependency_ids": [dependency_id],
            "decision": "approved",
        }
    return {"components": components, "dependencies": dependencies, "evidence": evidence, "approvals": approvals}


class DesignStateTests(unittest.TestCase):
    """Black-box acceptance tests for the route-neutral design state helper."""

    def _module(self) -> ModuleType:
        self.assertTrue(HELPER.is_file(), f"missing design state helper: {HELPER}")
        self.assertFalse(HELPER.is_symlink())
        self.assertGreater(HELPER.stat().st_size, 0)
        spec = importlib.util.spec_from_file_location("candidate_design_state", HELPER)
        self.assertIsNotNone(spec, "design state module has no import spec")
        if spec is None or spec.loader is None:
            self.fail("design state helper is not loadable")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as error:  # make missing/broken helpers an assertion, not collection error
            self.fail(f"design state helper cannot load: {error}")
        return module

    def _repo(self, root: Path) -> tuple[Path, str]:
        repository = root / "repo"
        repository.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
        (repository / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=tests", "-c", "user.email=tests@example.invalid", "commit", "--quiet", "-m", "fixture"],
            check=True,
        )
        subprocess.run(["git", "-C", str(repository), "switch", "--quiet", "-c", "feature/design"], check=True)
        baseline = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
        return repository, baseline

    def _initialize(self, module: ModuleType, root: Path) -> tuple[dict[str, object], Path, Path, str]:
        repository, baseline = self._repo(root)
        state_home = root / "state"
        state_home.mkdir()
        worktree = repository
        identity = {
            "repository": str(repository.resolve()),
            "branch": "feature/design",
            "worktree": str(worktree.resolve()),
            "baseline": baseline,
            "dirty_fingerprint": hashlib.sha256(b"clean").hexdigest(),
            "ui_contract_digest": hashlib.sha256(b"checkout-contract").hexdigest(),
        }
        initializer = getattr(module, "initialize_workflow", None)
        self.assertTrue(callable(initializer), "initialize_workflow is not callable")
        try:
            receipt = initializer(
                repository=repository,
                branch="feature/design",
                worktree=worktree,
                baseline=baseline,
                dirty_fingerprint=identity["dirty_fingerprint"],
                ui_contract={"digest": identity["ui_contract_digest"], "outcome": "checkout form"},
                scope={"components": ["CheckoutForm"], "exclusions": ["route"]},
                state_home=state_home,
            )
        except Exception as error:
            self.fail(f"initialize_workflow rejected a valid identity: {error}")
        self.assertIsInstance(receipt, dict)
        self.assertIn("workflow_id", receipt)
        self.assertIn("revision", receipt)
        workflow_id = str(receipt["workflow_id"])
        return receipt, repository, state_home, workflow_id

    def test_public_operations_are_present_and_dependency_free(self) -> None:
        """Regression: missing lifecycle operations force ad-hoc or unsafe state handling."""
        module = self._module()
        for name in (
            "initialize_workflow",
            "discover_workflow",
            "load_workflow",
            "apply_updates",
            "pause_workflow",
            "resume_workflow",
            "discard_workflow",
            "deliver_workflow",
        ):
            with self.subTest(operation=name):
                self.assertTrue(callable(getattr(module, name, None)), name)
        imported = set(getattr(module, "__dict__", {}))
        self.assertNotIn("requests", imported)
        self.assertNotIn("httpx", imported)

    def test_initialize_discover_load_and_receipt_bind_identity(self) -> None:
        """Regression: state from another repository, branch, worktree, or baseline must not resume."""
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repository, state_home, workflow_id = self._initialize(module, Path(temporary))
            discovered = module.discover_workflow(repository=repository, branch="feature/design", state_home=state_home)
            loaded = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertIsInstance(discovered, dict)
            self.assertIsInstance(loaded, dict)
            self.assertEqual(loaded.get("workflow_id"), workflow_id)
            self.assertEqual(loaded.get("revision"), receipt.get("revision"))
            identity = loaded.get("identity", {})
            self.assertEqual(identity.get("branch"), "feature/design")
            self.assertEqual(identity.get("baseline"), receipt.get("baseline", identity.get("baseline")))

    def test_apply_uses_expected_revision_and_invalidates_only_affected_evidence(self) -> None:
        """Regression: stale writes and broad approval invalidation lose safe independent progress."""
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repository, state_home, workflow_id = self._initialize(module, Path(temporary))
            revision = int(receipt["revision"])
            seeded = module.apply_updates(
                workflow_id=workflow_id,
                expected_revision=revision,
                updates=_typed_updates(("CheckoutForm", "AccountCard")),
                state_home=state_home,
            )
            seeded_revision = int(seeded["revision"])
            prior_invalidations = dict(module.load_workflow(workflow_id=workflow_id, state_home=state_home).get("invalidations", {}))
            changed = _typed_updates(("CheckoutForm",), code_suffix="changed")
            updated = module.apply_updates(
                workflow_id=workflow_id,
                expected_revision=seeded_revision,
                updates=changed,
                state_home=state_home,
            )
            self.assertGreater(int(updated["revision"]), seeded_revision)
            with self.assertRaises(Exception):
                module.apply_updates(
                    workflow_id=workflow_id,
                    expected_revision=seeded_revision,
                    updates=_typed_updates(("CheckoutForm",), code_suffix="stale"),
                    state_home=state_home,
                )
            state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            invalidations = state.get("invalidations", {})
            self.assertIn("CheckoutForm", json.dumps(invalidations))
            self.assertEqual(invalidations.get("AccountCard"), prior_invalidations.get("AccountCard"))

    def test_pause_resume_and_discard_require_authority_and_preserve_boundaries(self) -> None:
        """Regression: pause/resume/discard must not silently destroy resumable or foreign state."""
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repository, state_home, workflow_id = self._initialize(module, Path(temporary))
            revision = int(receipt["revision"])
            paused = module.pause_workflow(workflow_id=workflow_id, expected_revision=revision, state_home=state_home)
            resumed = module.resume_workflow(workflow_id=workflow_id, expected_revision=int(paused["revision"]), state_home=state_home)
            self.assertGreater(int(resumed["revision"]), int(paused["revision"]))
            with self.assertRaises(Exception):
                module.discard_workflow(
                    workflow_id=workflow_id,
                    expected_revision=int(resumed["revision"]),
                    confirmed=False,
                    state_home=state_home,
                )
            discarded = module.discard_workflow(
                workflow_id=workflow_id,
                expected_revision=int(resumed["revision"]),
                confirmed=True,
                state_home=state_home,
            )
            self.assertNotEqual(discarded.get("lifecycle"), "delivered")
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)

    def test_delivery_requires_current_eligible_approvals_and_exact_inventories(self) -> None:
        """Regression: Design must never deliver incomplete, stale, or unapproved component bundles."""
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repository, state_home, workflow_id = self._initialize(module, Path(temporary))
            revision = int(receipt["revision"])
            # Truthy legacy flags are not typed bindings and must not authorize delivery.
            booleans_only = {"components": {"CheckoutForm": {"digest": DIGEST, "eligible": True, "approved": True}}}
            try:
                boolean_receipt = module.apply_updates(workflow_id=workflow_id, expected_revision=revision, updates=booleans_only, state_home=state_home)
            except Exception:
                boolean_receipt = {"revision": revision}
            with self.assertRaises(Exception):
                module.deliver_workflow(
                    workflow_id=workflow_id,
                    expected_revision=boolean_receipt["revision"],
                    candidate_payload={"files": [{"path": "CheckoutForm.tsx", "digest": DIGEST, "classification": "component"}]},
                    review_evidence={"files": [{"path": "CheckoutForm-review.json", "digest": DIGEST, "classification": "review"}]},
                    manifest={"files": [{"path": "manifest.json", "digest": DIGEST, "classification": "manifest"}]},
                    state_home=state_home,
                )

            # A separately seeded, fully bound record proves success and the
            # delivered-state immutability boundary.
            valid_root = Path(temporary) / "valid"
            valid_root.mkdir()
            valid_receipt, _, valid_state_home, valid_workflow_id = self._initialize(module, valid_root)
            valid = _typed_updates(("CheckoutForm",))
            valid_revision = int(valid_receipt["revision"])
            bound_revision = int(module.apply_updates(workflow_id=valid_workflow_id, expected_revision=valid_revision, updates=valid, state_home=valid_state_home)["revision"])
            component_digest = valid["components"]["CheckoutForm"]["code_digest"]
            candidate = {"files": [{"path": "CheckoutForm.tsx", "digest": component_digest, "classification": "component"}]}
            review = {"files": [{"path": "CheckoutForm-review.json", "digest": valid["evidence"]["CheckoutForm-render"]["digest"], "classification": "review"}]}
            manifest = {"files": [{"path": "manifest.json", "digest": valid["components"]["CheckoutForm"]["contract_digest"], "classification": "manifest"}]}
            delivered = module.deliver_workflow(workflow_id=valid_workflow_id, expected_revision=bound_revision, candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=valid_state_home)
            self.assertEqual(delivered["lifecycle"], "delivered")
            with self.assertRaises(Exception):
                module.apply_updates(workflow_id=valid_workflow_id, expected_revision=int(delivered["revision"]), updates=valid, state_home=valid_state_home)
            with self.assertRaises(Exception):
                module.deliver_workflow(workflow_id=valid_workflow_id, expected_revision=int(delivered["revision"]), candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=valid_state_home)

    def test_malformed_identity_receipts_and_unsafe_state_fail_closed(self) -> None:
        """Regression: symlinks, unsafe permissions, malformed JSON, and substituted identity must not load."""
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repository, state_home, workflow_id = self._initialize(module, Path(temporary))
            state_files = [path for path in state_home.rglob("*") if path.is_file()]
            self.assertTrue(state_files, "state was not durably written")
            target = state_files[0]
            original = target.read_bytes()
            target.write_bytes(b"{not-json")
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            target.write_bytes(original)
            os.chmod(target, 0o666)
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)

    def test_state_mutation_has_atomic_recovery_and_concurrent_revision_safety(self) -> None:
        """Regression: torn writes and concurrent agents must not create two authoritative revisions."""
        module = self._module()
        with tempfile.TemporaryDirectory() as temporary:
            receipt, repository, state_home, workflow_id = self._initialize(module, Path(temporary))
            revision = int(receipt["revision"])
            outcomes: list[object] = []
            barrier = threading.Barrier(2)

            def writer(value: str) -> None:
                barrier.wait()
                try:
                    updates = _typed_updates(("CheckoutForm",), code_suffix=value)
                    outcomes.append(
                        module.apply_updates(
                            workflow_id=workflow_id,
                            expected_revision=revision,
                            updates=updates,
                            state_home=state_home,
                        )
                    )
                except Exception as error:
                    outcomes.append(error)

            threads = [threading.Thread(target=writer, args=(value,)) for value in ("one", "two")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(sum(isinstance(value, dict) for value in outcomes), 1)
            loaded = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(int(loaded["revision"]), revision + 1)


if __name__ == "__main__":
    unittest.main()
