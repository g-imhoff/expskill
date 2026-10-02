from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_design_state_parallel_join import DIGEST, git, initialize
from tests.test_design_state_sessions import approve_candidate, candidate_checkpoint, deliver
from tests.test_plan_design_join import load_helper as load_plan_helper


OWNED_PATHS = ["component.txt", "review.txt", "manifest.txt"]


def delivered_workflow(tmp_path):
    module, repo, home, receipt, baseline = initialize(tmp_path, owned_paths=OWNED_PATHS)
    checkpoint = candidate_checkpoint(module, repo, home, receipt)
    approved, _, inventories = approve_candidate(module, repo, home, checkpoint)
    delivered = deliver(module, home, approved, inventories)
    return module, repo, home, delivered, baseline


def replace_owned_file(repo, kind):
    target = repo / "component.txt"
    target.unlink()
    if kind == "directory":
        target.mkdir()
        (target / "later.txt").write_text("subsequent project content\n", encoding="utf-8")
    else:
        outside = repo.parent / "outside.txt"
        outside.write_text("unrelated target bytes\n", encoding="utf-8")
        target.symlink_to(outside)


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_delivered_owned_scope_survives_live_path_replacement_and_successor(tmp_path: Path, kind: str):
    module, repo, home, delivered, baseline = delivered_workflow(tmp_path)
    state_path = home / "design" / delivered["workflow_id"]
    stored = state_path.read_bytes()
    cli_receipt = {
        "schema_version": 1, "operation": "deliver", "workflow_id": delivered["workflow_id"],
        "revision": delivered["revision"], "lifecycle": "delivered", "identity": delivered["identity"],
        "state_digest": hashlib.sha256(json.dumps(delivered, sort_keys=True).encode()).hexdigest(),
        **{key: value for key, value in delivered.items() if key.endswith("_digest") or key == "candidate_commit"},
    }
    replace_owned_file(repo, kind)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "later project shape")
    historical = module.load_workflow(workflow_id=delivered["workflow_id"], state_home=home)
    assert historical["lifecycle"] == "delivered"
    assert historical["scope"]["owned_paths"] == OWNED_PATHS
    for item in historical["candidate_payload"]["files"]:
        original = git(repo, "show", f"{delivered['candidate_commit']}:{item['path']}") + "\n"
        assert hashlib.sha256(original.encode()).hexdigest() == item["digest"]
    plan = load_plan_helper()
    joined = plan.issue_design_join_receipt(
        workflow_id="a" * 32, plan_revision=3, baseline=baseline,
        design_workflow_id=delivered["workflow_id"], design_revision=delivered["revision"],
        design_branch="expskill/design/ui", candidate_commit=delivered["candidate_commit"],
        brief_digest=delivered["brief_digest"], approval_digest=delivered["approval_digest"],
        manifest_digest=delivered["manifest_digest"], approved=True, design_delivery_receipt=cli_receipt,
    )
    assert joined["design_delivery_receipt"] == cli_receipt
    with pytest.raises(ValueError):
        module.initialize_workflow(
            repository=repo, branch="expskill/design/ui", worktree=repo,
            baseline=git(repo, "rev-parse", "HEAD"), dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
            ui_contract={"digest": DIGEST}, scope={"components": ["UnsafeOwner"], "owned_paths": ["component.txt"]}, state_home=home,
        )
    successor = module.initialize_workflow(
        repository=repo, branch="expskill/design/ui", worktree=repo,
        baseline=git(repo, "rev-parse", "HEAD"), dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
        ui_contract={"digest": DIGEST}, scope={"components": ["NextComponent"], "owned_paths": ["next.txt"]}, state_home=home,
    )
    assert successor["workflow_id"] != delivered["workflow_id"]
    assert module.discover_workflow(repository=repo, branch="expskill/design/ui", state_home=home)["workflow_id"] == successor["workflow_id"]
    with pytest.raises(ValueError, match="current generation is valid"):
        module.recover_workflow(workflow_id=delivered["workflow_id"], state_home=home)
    assert state_path.read_bytes() == stored


@pytest.mark.parametrize("kind", ["directory", "symlink"])
@pytest.mark.parametrize("lifecycle", ["active", "paused"])
def test_live_owner_loads_and_writes_reject_replaced_owned_file(tmp_path: Path, kind: str, lifecycle: str):
    module, repo, home, receipt, _ = initialize(tmp_path, owned_paths=OWNED_PATHS)
    if lifecycle == "paused":
        receipt = module.pause_workflow(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], state_home=home)
    state_path = home / "design" / receipt["workflow_id"]
    stored = state_path.read_bytes()
    replace_owned_file(repo, kind)
    for operation, kwargs in ((module.load_workflow, {}), (module.apply_updates, {"expected_revision": receipt["revision"], "updates": {}})):
        with pytest.raises(ValueError):
            operation(workflow_id=receipt["workflow_id"], state_home=home, **kwargs)
    assert state_path.read_bytes() == stored


@pytest.mark.parametrize("owned_paths", [["../escape"], ["/absolute"], ["component.*"], ["component.txt", "COMPONENT.TXT"]])
def test_delivered_history_still_rejects_invalid_scope_syntax(tmp_path: Path, owned_paths):
    module, _, home, delivered, _ = delivered_workflow(tmp_path)
    path = home / "design" / delivered["workflow_id"]
    state = json.loads(path.read_bytes())
    state["scope"]["owned_paths"] = owned_paths
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_workflow(workflow_id=delivered["workflow_id"], state_home=home)


@pytest.mark.parametrize("mutant", ["inventory-path", "inventory-digest", "component-digest"])
def test_delivered_history_still_rejects_corrupted_inventory_bindings(tmp_path: Path, mutant: str):
    module, _, home, delivered, _ = delivered_workflow(tmp_path)
    path = home / "design" / delivered["workflow_id"]
    state = copy.deepcopy(json.loads(path.read_bytes()))
    if mutant == "inventory-path":
        state["candidate_payload"]["files"][0]["path"] = "../escape"
    elif mutant == "inventory-digest":
        state["delivery"]["candidate"]["inventory_digest"] = "0" * 64
    else:
        state["components"]["CheckoutForm"]["code_digest"] = "0" * 64
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_workflow(workflow_id=delivered["workflow_id"], state_home=home)
