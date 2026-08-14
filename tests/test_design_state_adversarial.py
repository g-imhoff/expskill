from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.design_state_test_support import passing_technical
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins" / "codex-dev-flow" / "scripts" / "design_state.py"
DIGEST = "a" * 64


def _valid_updates(component_names: tuple[str, ...] = ("CheckoutForm", "Unrelated"), suffix: str = "") -> dict[str, dict[str, dict[str, object]]]:
    """Complete V2 component/evidence/dependency/approval bindings."""
    components: dict[str, dict[str, object]] = {}
    dependencies: dict[str, dict[str, object]] = {}
    evidence: dict[str, dict[str, object]] = {}
    approvals: dict[str, dict[str, object]] = {}
    for name in component_names:
        code_digest = hashlib.sha256(f"{name}-code-{suffix}".encode()).hexdigest()
        contract_digest = hashlib.sha256(f"{name}-contract".encode()).hexdigest()
        evidence_id, dependency_id, approval_id = f"{name}-evidence", f"{name}-dependency", f"{name}-approval"
        components[name] = {"id": name, "code_digest": code_digest, "contract_digest": contract_digest, "evidence_ids": [evidence_id], "dependency_ids": [dependency_id], "approval_id": approval_id}
        dependencies[dependency_id] = {"id": dependency_id, "digest": hashlib.sha256(f"{name}-dependency".encode()).hexdigest(), "component_ids": [name]}
        evidence_digest = hashlib.sha256(f"{name}-evidence".encode()).hexdigest()
        evidence[evidence_id] = {"id": evidence_id, "component_id": name, "digest": evidence_digest, "code_digest": code_digest, "contract_digest": contract_digest, "widths": ["compact", "intermediate", "wide"], "themes": ["light", "dark"], "states": ["default", "error"], "technical": passing_technical(evidence_digest)}
        approvals[approval_id] = {"id": approval_id, "component_id": name, "code_digest": code_digest, "contract_digest": contract_digest, "evidence_ids": [evidence_id], "dependency_ids": [dependency_id], "decision": "approved"}
    return {"components": components, "dependencies": dependencies, "evidence": evidence, "approvals": approvals}


def _load(name: str = "design_state_adversarial") -> object:
    spec = importlib.util.spec_from_file_location(name, HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load state helper: {HELPER}")
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
    return repo, subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _start(module: object, root: Path) -> tuple[dict, Path, Path, str]:
    repo, baseline = _repo(root)
    state_home = root / "state"
    receipt = module.initialize_workflow(
        repository=repo,
        branch="feature/design",
        worktree=repo,
        baseline=baseline,
        dirty_fingerprint=hashlib.sha256(b"clean").hexdigest(),
        ui_contract={"digest": hashlib.sha256(b"contract").hexdigest(), "outcome": "checkout"},
        scope={"components": ["CheckoutForm"], "exclusions": ["route"]},
        state_home=state_home,
    )
    return receipt, repo, state_home, str(receipt["workflow_id"])


def _process_apply(state_home: str, workflow_id: str, revision: int, marker: str, queue: object) -> None:
    module = _load(f"design_state_worker_{marker}")
    try:
        result = module.apply_updates(
            workflow_id=workflow_id,
            expected_revision=revision,
            updates=_valid_updates(("CheckoutForm",), marker),
            state_home=Path(state_home),
        )
        queue.put(("ok", result))
    except Exception as error:
        queue.put(("error", type(error).__name__, str(error)))


class DesignStateAdversarialTests(unittest.TestCase):
    def test_workflow_id_is_fixed_format_and_all_state_paths_are_confined(self) -> None:
        """Regression: traversal identifiers must not escape the Design state root."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            self.assertRegex(workflow_id, r"^[0-9a-f]{32}$")
            for invalid in ("../escape", "../../escape", "a/b", "a b", "A" * 32, "a" * 31):
                with self.subTest(invalid=invalid), self.assertRaises(Exception):
                    module.load_workflow(workflow_id=invalid, state_home=state_home)
            outside = root / "escape"
            self.assertFalse(outside.exists())

    def test_design_root_target_and_temporary_symlinks_are_rejected(self) -> None:
        """Regression: symlinked roots, targets, temporary files, and predecessors must not be followed."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            linked_home = root / "linked-state"
            (linked_home / "design").parent.mkdir()
            (linked_home / "design").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(Exception):
                _start(module, linked_home)
            receipt, _, state_home, workflow_id = _start(module, root / "normal")
            target = state_home / "design" / workflow_id
            previous = target.with_suffix(".previous")
            target_data = target.read_bytes()
            outside_file = root / "outside-target"
            outside_file.write_bytes(b"sentinel")
            temporary_link = target.with_suffix(".tmp")
            temporary_link.symlink_to(outside_file)
            with self.assertRaises(Exception):
                module.apply_updates(
                    workflow_id=workflow_id,
                    expected_revision=receipt["revision"],
                    updates=_valid_updates(("CheckoutForm",), "temp-link"),
                    state_home=state_home,
                )
            self.assertEqual(outside_file.read_bytes(), b"sentinel")
            self.assertTrue(target.exists() or previous.exists())
            target.write_bytes(target_data)
            target.unlink()
            target.symlink_to(outside_file)
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)

    def test_state_schema_is_closed_and_identity_is_revalidated(self) -> None:
        """Regression: an integer revision or caller-supplied identity must not make arbitrary JSON authoritative."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, repo, state_home, workflow_id = _start(module, root)
            target = state_home / "design" / workflow_id
            target.write_text(json.dumps({"revision": 99}), encoding="utf-8")
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            receipt, repo, state_home, workflow_id = _start(module, root / "identity")
            with self.assertRaises(Exception):
                module.load_workflow(
                    workflow_id=workflow_id,
                    state_home=state_home,
                    repository=repo,
                    branch="feature/design",
                    worktree=root / "wrong-worktree",
                    baseline="0" * 40,
                    dirty_fingerprint="0" * 64,
                    ui_contract={"digest": "0" * 64},
                )

    def test_duplicate_or_malformed_generations_fail_closed(self) -> None:
        """Regression: ambiguous, malformed, or substituted state must never be selected silently."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            target = state_home / "design" / workflow_id
            duplicate = state_home / "design" / ("f" * 32)
            shutil.copyfile(target, duplicate)
            with self.assertRaises(Exception):
                module.discover_workflow(repository=root / "repo", branch="feature/design", state_home=state_home)
            duplicate.unlink()
            malformed = state_home / "design" / ("e" * 32)
            malformed.write_text("not-json", encoding="utf-8")
            with self.assertRaises(Exception):
                module.discover_workflow(repository=root / "repo", branch="feature/design", state_home=state_home)

    def test_expected_revision_cas_is_cross_process_not_process_local(self) -> None:
        """Regression: two processes using one expected revision must yield exactly one committed update."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            context = multiprocessing.get_context("fork")
            queue = context.Queue()
            processes = [
                context.Process(target=_process_apply, args=(str(state_home), workflow_id, receipt["revision"], marker, queue))
                for marker in ("one", "two")
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
            results = [queue.get(timeout=2) for _ in processes]
            self.assertEqual(sum(result[0] == "ok" for result in results), 1, results)
            loaded = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertEqual(loaded["revision"], receipt["revision"] + 1)

    def test_atomic_write_fault_preserves_live_generation_and_recovers_previous(self) -> None:
        """Regression: replacement must not move the only valid generation away before durable commit."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            target = state_home / "design" / workflow_id
            original_replace = module.os.replace
            calls = {"count": 0}

            def fault_once(source: Path, destination: Path) -> None:
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("injected final replace fault")
                original_replace(source, destination)

            with mock.patch.object(module.os, "replace", side_effect=fault_once):
                with self.assertRaises(Exception):
                    module.apply_updates(
                        workflow_id=workflow_id,
                        expected_revision=receipt["revision"],
                        updates=_valid_updates(("CheckoutForm",), "changed"),
                        state_home=state_home,
                    )
            self.assertTrue(target.is_file(), "live generation was moved away before replacement")
            self.assertGreaterEqual(calls["count"], 2, "fault hook did not reach final replacement")
            self.assertEqual(module.load_workflow(workflow_id=workflow_id, state_home=state_home)["revision"], receipt["revision"])
            target.write_text("{malformed", encoding="utf-8")
            with self.assertRaises(Exception):
                module.load_workflow(workflow_id=workflow_id, state_home=state_home)

    def test_typed_bindings_and_causal_selective_invalidation_are_required(self) -> None:
        """Regression: component, contract, dependency, evidence, and approval changes must stale causally related work only."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root / "seed")
            seed = module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates=_valid_updates(), state_home=state_home)
            seeded_state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertNotIn("Unrelated", seeded_state.get("invalidations", {}), "seed fixture must not pre-mark unrelated control")
            contract = module.apply_updates(workflow_id=workflow_id, expected_revision=seed["revision"], updates={"ui_contract": {"digest": "b" * 64, "outcome": "checkout"}}, state_home=state_home)
            contract_state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
            self.assertIn("CheckoutForm", contract_state["invalidations"])
            self.assertIn("Unrelated", contract_state["invalidations"])

            for kind, change in (
                ("dependency", {"dependencies": {"CheckoutForm-dependency": {"id": "CheckoutForm-dependency", "digest": "b" * 64, "component_ids": ["CheckoutForm"]}}}),
                ("evidence", {"evidence": {"CheckoutForm-evidence": {**_valid_updates(("CheckoutForm",))["evidence"]["CheckoutForm-evidence"], "digest": "b" * 64}}}),
            ):
                with self.subTest(kind=kind):
                    local_receipt, _, local_home, local_id = _start(module, root / kind)
                    local_seed = module.apply_updates(workflow_id=local_id, expected_revision=local_receipt["revision"], updates=_valid_updates(), state_home=local_home)
                    module.apply_updates(workflow_id=local_id, expected_revision=local_seed["revision"], updates=change, state_home=local_home)
                    state = module.load_workflow(workflow_id=local_id, state_home=local_home)
                    self.assertIn("CheckoutForm", state["invalidations"])
                    self.assertNotIn("Unrelated", state["invalidations"])
            with self.assertRaises(Exception):
                module.apply_updates(workflow_id=workflow_id, expected_revision=contract["revision"], updates={"components": {"Broken": {"id": "Broken", "code_digest": "c" * 64, "contract_digest": "c" * 64, "evidence_ids": ["missing"], "dependency_ids": [], "approval_id": "Broken-approval"}}}, state_home=state_home)

    def test_delivery_is_derived_exactly_and_delivered_state_is_immutable(self) -> None:
        """Regression: fabricated inventories and post-delivery mutation must not claim a delivered bundle."""
        module = _load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _, state_home, workflow_id = _start(module, root)
            insecure = {"components": {"CheckoutForm": {"id": "CheckoutForm", "code_digest": DIGEST, "eligible": True, "approved": True}}}
            insecure_receipt = module.apply_updates(workflow_id=workflow_id, expected_revision=receipt["revision"], updates=insecure, state_home=state_home)
            bad = {"files": "not-an-inventory"}
            with self.assertRaises(Exception):
                module.deliver_workflow(workflow_id=workflow_id, expected_revision=insecure_receipt["revision"], candidate_payload=bad, review_evidence=bad, manifest=bad, state_home=state_home)

            valid_receipt, _, valid_home, valid_id = _start(module, root / "valid-delivery")
            valid = _valid_updates(("CheckoutForm",))
            seeded = module.apply_updates(workflow_id=valid_id, expected_revision=valid_receipt["revision"], updates=valid, state_home=valid_home)
            code_digest = valid["components"]["CheckoutForm"]["code_digest"]
            evidence_digest = valid["evidence"]["CheckoutForm-evidence"]["digest"]
            contract_digest = valid["components"]["CheckoutForm"]["contract_digest"]
            candidate = {"files": [{"path": "CheckoutForm.tsx", "digest": code_digest, "classification": "component"}]}
            review = {"files": [{"path": "CheckoutForm-review.json", "digest": evidence_digest, "classification": "review"}]}
            manifest = {"files": [{"path": "manifest.json", "digest": contract_digest, "classification": "manifest"}]}
            delivered = module.deliver_workflow(workflow_id=valid_id, expected_revision=seeded["revision"], candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=valid_home)
            self.assertEqual(delivered["lifecycle"], "delivered")
            self.assertIn("candidate_digest", delivered)
            for operation, kwargs in (
                (module.apply_updates, {"updates": _valid_updates(("CheckoutForm",), "after-delivery")}),
                (module.pause_workflow, {}),
                (module.resume_workflow, {}),
                (module.discard_workflow, {"confirmed": True}),
            ):
                with self.subTest(operation=getattr(operation, "__name__", "operation")), self.assertRaises(Exception):
                    operation(workflow_id=valid_id, expected_revision=delivered["revision"], state_home=valid_home, **kwargs)


if __name__ == "__main__":
    unittest.main()
