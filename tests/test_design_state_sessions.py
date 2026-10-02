from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from tests.design_state_test_support import passing_technical
from tests.test_design_state_parallel_join import DIGEST, git, initialize
from tests.test_plan_design_join import load_helper as load_plan_helper


def candidate_checkpoint(module, repo, home, receipt, content="candidate\n", amend=False):
    for path, text in {
        "component.txt": content,
        "review.txt": "synthetic review fixture\n",
        "manifest.txt": "synthetic manifest fixture\n",
    }.items():
        (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", "component.txt", "review.txt", "manifest.txt")
    git(repo, "commit", *(["--amend"] if amend else []), "-m", "design candidate")
    return module.checkpoint_candidate(
        workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"],
        candidate_commit=git(repo, "rev-parse", "HEAD"), state_home=home,
    )


def approval_records(module, repo, home, receipt, decision_reference="initial approval"):
    state = module.load_workflow(workflow_id=receipt["workflow_id"], state_home=home)
    inventories = []
    for path, classification in (("component.txt", "component"), ("review.txt", "review"), ("manifest.txt", "manifest")):
        inventories.append({"files": [{"path": path, "classification": classification, "digest": hashlib.sha256((repo / path).read_bytes()).hexdigest()}]})
    candidate, review, manifest = inventories
    check = module.record_check(
        workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"],
        argv=[sys.executable, "-c", "print('synthetic gate fixture')"],
        candidate_payload=candidate, state_home=home,
    )
    code = module.component_digest(candidate["files"])
    contract = manifest["files"][0]["digest"]
    technical = passing_technical(check["output_digest"], check["command"])
    technical["results"] = [check]
    binding = {"code_digest": code, "contract_digest": contract, "brief_digest": state["brief"]["digest"]}
    records = {
        "components": {"CheckoutForm": {"id": "CheckoutForm", **binding, "files": candidate["files"], "evidence_ids": ["E1"], "dependency_ids": ["D1"], "approval_id": "A1"}},
        "dependencies": {"D1": {"id": "D1", "digest": DIGEST, "component_ids": ["CheckoutForm"]}},
        "evidence": {"E1": {"id": "E1", **binding, "component_id": "CheckoutForm", "digest": review["files"][0]["digest"], "widths": ["compact", "intermediate", "wide"], "themes": ["light"], "states": ["default"], "technical": technical}},
        "approvals": {"A1": {"id": "A1", **binding, "component_id": "CheckoutForm", "evidence_ids": ["E1"], "dependency_ids": ["D1"], "decision": "approved", "provenance": {"kind": "trusted-attestation", "actor": "human", "authority_reference": "fixture user", "decision_reference": decision_reference}}},
        "delivery": {"classifications": {"candidate": "component", "review": "review", "manifest": "manifest"}, **{name: {"inventory_digest": module.canonical_digest(inventory), "files": inventory["files"]} for name, inventory in zip(("candidate", "review", "manifest"), inventories)}},
    }
    return records, inventories


def approve_candidate(module, repo, home, receipt, decision_reference="initial approval"):
    records, inventories = approval_records(module, repo, home, receipt, decision_reference)
    updated = module.apply_updates(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], updates=records, state_home=home)
    return updated, records, inventories


def deliver(module, home, receipt, inventories):
    candidate, review, manifest = inventories
    return module.deliver_workflow(
        workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"],
        candidate_payload=candidate, review_evidence=review, manifest=manifest, state_home=home,
    )


def test_successor_preserves_delivered_history_and_plan_join_receipt(tmp_path: Path):
    module, repo, home, receipt, baseline = initialize(tmp_path)
    checkpoint = candidate_checkpoint(module, repo, home, receipt)
    approved, _, inventories = approve_candidate(module, repo, home, checkpoint)
    delivered = deliver(module, home, approved, inventories)
    stored_path = home / "design" / receipt["workflow_id"]
    stored = stored_path.read_bytes()
    cli_receipt = {
        "schema_version": 1, "operation": "deliver", "workflow_id": delivered["workflow_id"],
        "revision": delivered["revision"], "lifecycle": "delivered", "identity": delivered["identity"],
        "state_digest": hashlib.sha256(json.dumps(delivered, sort_keys=True).encode()).hexdigest(),
        **{key: value for key, value in delivered.items() if key.endswith("_digest") or key == "candidate_commit"},
    }
    successor = module.initialize_workflow(
        repository=repo, branch="expskill/design/ui", worktree=repo,
        baseline=git(repo, "rev-parse", "HEAD"), dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
        ui_contract={"digest": DIGEST}, scope={"components": ["NextComponent"]}, state_home=home,
    )
    assert successor["workflow_id"] != receipt["workflow_id"]
    assert module.discover_workflow(repository=repo, branch="expskill/design/ui", state_home=home)["workflow_id"] == successor["workflow_id"]
    (repo / "component.txt").write_text("successor component\n", encoding="utf-8")
    git(repo, "add", "component.txt")
    git(repo, "commit", "-m", "successor candidate")
    historical = module.load_workflow(workflow_id=receipt["workflow_id"], state_home=home)
    assert historical["candidate"]["commit"] == delivered["candidate_commit"]
    assert historical["lifecycle"] == "delivered"
    with pytest.raises(ValueError, match="current generation is valid"):
        module.recover_workflow(workflow_id=receipt["workflow_id"], state_home=home)
    for operation, arguments in (
        (module.apply_updates, {"updates": {}}),
        (module.pause_workflow, {}),
        (module.resume_workflow, {}),
        (module.discard_workflow, {"confirmed": True}),
        (module.checkpoint_candidate, {"candidate_commit": git(repo, "rev-parse", "HEAD")}),
    ):
        with pytest.raises(ValueError):
            operation(workflow_id=receipt["workflow_id"], expected_revision=delivered["revision"], state_home=home, **arguments)
    assert stored_path.read_bytes() == stored
    plan = load_plan_helper()
    join = plan.issue_design_join_receipt(
        workflow_id="a" * 32, plan_revision=3, baseline=baseline,
        design_workflow_id=delivered["workflow_id"], design_revision=delivered["revision"],
        design_branch="expskill/design/ui", candidate_commit=delivered["candidate_commit"],
        brief_digest=delivered["brief_digest"], approval_digest=delivered["approval_digest"],
        manifest_digest=delivered["manifest_digest"], approved=True, design_delivery_receipt=cli_receipt,
    )
    assert join["design_delivery_receipt"] == cli_receipt


@pytest.mark.parametrize("lifecycle", ["active", "paused"])
def test_existing_owner_blocks_successor(tmp_path: Path, lifecycle: str):
    module, repo, home, receipt, baseline = initialize(tmp_path)
    if lifecycle == "paused":
        module.pause_workflow(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], state_home=home)
    with pytest.raises(ValueError, match="active workflow exists"):
        module.initialize_workflow(
            repository=repo, branch="expskill/design/ui", worktree=repo, baseline=baseline,
            dirty_fingerprint=hashlib.sha256(b"").hexdigest(), ui_contract={"digest": DIGEST},
            scope={"components": ["CompetingComponent"]}, state_home=home,
        )


@pytest.mark.parametrize("kind", ["file", "symlink", "public-directory"])
def test_discovery_rejects_unsafe_command_record_container(tmp_path: Path, kind: str):
    module, repo, home, _, _ = initialize(tmp_path)
    records = home / "design" / "records"
    if kind == "file":
        records.write_text("invalid container", encoding="utf-8")
    elif kind == "symlink":
        records.symlink_to(repo, target_is_directory=True)
    else:
        records.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        module.discover_workflow(repository=repo, branch="expskill/design/ui", state_home=home)
