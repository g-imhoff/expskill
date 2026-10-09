"""Design to Plan handoff contract: shared expectations enforced on both sides.

Issue #14: the Design output once failed the Plan typed Design join and
needed manual repair to reach implement. These tests pin the lighter
contract chosen in docs/specs/design-plan-handoff-contract.md: no shared
folder, just declared fields plus validation before delivery and at the
join. If either helper drifts from
plugins/expskill/content/scripts/design_plan_handoff.py, this file fails.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.design_state_test_support import passing_technical
from tests.test_plan_graph import minimal_graph


ROOT = Path(__file__).resolve().parents[1]
DESIGN_HELPER = ROOT / "plugins" / "expskill" / "content" / "scripts" / "design_state.py"
PLAN_HELPER = ROOT / "plugins" / "expskill" / "content" / "scripts" / "plan_graph.py"
CONTRACT_MODULE = ROOT / "plugins" / "expskill" / "content" / "scripts" / "design_plan_handoff.py"

TARGET_BRANCH = "feature/config-validation"
DESIGN_BRANCH = "expskill/design/ui"
DIGEST = hashlib.sha256(b"checkout-contract").hexdigest()


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, input=input_text, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def design_cli(state_home: Path, command: str, payload: dict) -> dict:
    result = subprocess.run(
        [sys.executable, str(DESIGN_HELPER), command,
         "--state-home", str(state_home)],
        input=json.dumps(payload), text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def make_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", TARGET_BRANCH)
    git(repo, "config", "user.name", "Handoff Tests")
    git(repo, "config", "user.email", "handoff@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    baseline = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", DESIGN_BRANCH)
    return repo, baseline


def confirm_design_brief(design, state_home: Path, workflow_id: str, revision: int) -> dict:
    return design.confirm_brief(
        workflow_id=workflow_id,
        expected_revision=revision,
        brief={
            "objective": "Create the approved checkout UI",
            "requirements": ["Keep the existing submit behavior"],
            "responsive_expectations": {
                "compact": "Keep the control visible when the label wraps",
                "intermediate": "Use the project row spacing",
                "wide": "Respect the project content width",
            },
            "non_goals": ["Do not change routing"],
            "source": {
                "kind": "plan-graph",
                "workflow_id": "a" * 32,
                "revision": 3,
                "digest": DIGEST,
            },
        },
        confirmed=True,
        state_home=state_home,
    )


def bound_components(design, brief_digest: str) -> dict:
    code_digest = hashlib.sha256(b"CheckoutForm-code").hexdigest()
    contract_digest = hashlib.sha256(b"CheckoutForm-contract").hexdigest()
    return {
        "components": {
            "CheckoutForm": {
                "id": "CheckoutForm",
                "code_digest": code_digest,
                "contract_digest": contract_digest,
                "brief_digest": brief_digest,
                "evidence_ids": ["CheckoutForm-render"],
                "dependency_ids": ["CheckoutForm-dependency"],
                "approval_id": "CheckoutForm-approval",
            }
        },
        "dependencies": {
            "CheckoutForm-dependency": {
                "id": "CheckoutForm-dependency",
                "digest": hashlib.sha256(b"CheckoutForm-dependency").hexdigest(),
                "component_ids": ["CheckoutForm"],
            }
        },
        "evidence": {
            "CheckoutForm-render": {
                "id": "CheckoutForm-render",
                "component_id": "CheckoutForm",
                "digest": hashlib.sha256(b"CheckoutForm-evidence").hexdigest(),
                "code_digest": code_digest,
                "contract_digest": contract_digest,
                "brief_digest": brief_digest,
                "widths": ["compact", "intermediate", "wide"],
                "themes": ["light", "dark"],
                "states": ["default", "error"],
                "technical": passing_technical(
                    hashlib.sha256(b"CheckoutForm-technical").hexdigest(),
                    "render-and-interaction-gates",
                ),
            }
        },
        "approvals": {
            "CheckoutForm-approval": {
                "id": "CheckoutForm-approval",
                "component_id": "CheckoutForm",
                "code_digest": code_digest,
                "contract_digest": contract_digest,
                "brief_digest": brief_digest,
                "evidence_ids": ["CheckoutForm-render"],
                "dependency_ids": ["CheckoutForm-dependency"],
                "decision": "approved",
            }
        },
    }


def prepare_routed_design(tmp_path: Path):
    """Prepare an approved fixture and return its pending delivery payload."""
    design = load("handoff_design", DESIGN_HELPER)
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, baseline = make_repo(tmp_path)
    state_home = tmp_path / "design-state"
    state_home.mkdir()
    initialized = design.initialize_workflow(
        repository=repo,
        branch=DESIGN_BRANCH,
        worktree=repo,
        baseline=baseline,
        dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
        ui_contract={"digest": DIGEST, "outcome": "approved checkout UI"},
        scope={"components": ["CheckoutForm"], "exclusions": ["routing"]},
        invocation_mode="routed",
        state_home=state_home,
    )
    workflow_id = initialized["workflow_id"]
    confirmed = confirm_design_brief(
        design, state_home, workflow_id, initialized["revision"])
    (repo / "src" / "config.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Design candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    checkpointed = design.checkpoint_candidate(
        workflow_id=workflow_id,
        expected_revision=confirmed["revision"],
        candidate_commit=candidate,
        state_home=state_home,
    )
    bound = design.apply_updates(
        workflow_id=workflow_id,
        expected_revision=checkpointed["revision"],
        updates=bound_components(design, confirmed["brief_digest"]),
        state_home=state_home,
    )
    code_digest = hashlib.sha256(b"CheckoutForm-code").hexdigest()
    evidence_digest = hashlib.sha256(b"CheckoutForm-evidence").hexdigest()
    payload = {
        "workflow_id": workflow_id,
        "expected_revision": bound["revision"],
        "candidate_payload": {"files": [
            {"path": "CheckoutForm.tsx", "digest": code_digest,
             "classification": "component"}]},
        "review_evidence": {"files": [
            {"path": "CheckoutForm-review.json", "digest": evidence_digest,
             "classification": "review"}]},
        "manifest": {"files": [
            {"path": "manifest.json", "digest": "3" * 64,
             "classification": "manifest"}]},
    }
    return design, repo, state_home, baseline, candidate, payload


def deliver_routed_design(tmp_path: Path) -> tuple[dict, Path, Path, Path, str, str]:
    """Run the full routed Design flow and return the CLI delivery receipt."""
    _, repo, state_home, baseline, candidate, payload = prepare_routed_design(tmp_path)
    receipt = design_cli(state_home, "deliver", payload)
    assert receipt["lifecycle"] == "delivered"
    return receipt, repo, state_home, tmp_path, baseline, candidate


@pytest.fixture(scope="module")
def delivery_receipt(tmp_path_factory):
    receipt, *_ = deliver_routed_design(tmp_path_factory.mktemp("receipt-types"))
    return receipt


@pytest.mark.parametrize(("path", "value"), [
    (("workflow_id",), int("1" * 32)),
    (("workflow_id",), "A" * 32),
    (("workflow_id",), None),
    (("schema_version",), True),
    (("schema_version",), 1.0),
    (("revision",), True),
    (("revision",), 1.0),
    (("revision",), 0),
    (("identity", "unexpected"), "extra"),
    (("identity", "branch"), "x" * 245),
    *((("identity", field), value)
      for field in ("repository", "worktree", "branch")
      for value in (None, "", " \t", "bad\x00text", "x" * 16_385)),
], ids=lambda value: (
    ".".join(value) if isinstance(value, tuple)
    else "overlong-text" if isinstance(value, str) and len(value) > 100
    else repr(value)
))
def test_receipt_validators_reject_mistyped_and_extra_fields(
    delivery_receipt: dict, path: tuple[str, ...], value: object,
) -> None:
    contract = load("handoff_contract_types", CONTRACT_MODULE)
    plan = load("handoff_plan_types", PLAN_HELPER)
    mutated = copy.deepcopy(delivery_receipt)
    target = mutated if len(path) == 1 else mutated[path[0]]
    target[path[-1]] = value
    assert contract.validate_delivery_receipt_shape(mutated), path
    with pytest.raises(plan.PlanGraphError):
        plan.record_design_join_from_delivery(
            workflow_id="f" * 32, plan_revision=1,
            design_delivery_receipt=mutated,
        )


@pytest.mark.parametrize("nested", [False, True])
def test_receipt_validators_reject_nonstring_field_names(delivery_receipt, nested):
    contract = load("handoff_contract_keys", CONTRACT_MODULE)
    plan = load("handoff_plan_keys", PLAN_HELPER)
    mutated = copy.deepcopy(delivery_receipt)
    target = mutated["identity"] if nested else mutated
    target[0] = "unexpected"
    assert contract.validate_delivery_receipt_shape(mutated)
    with pytest.raises(plan.PlanGraphError):
        plan._validate_design_delivery_receipt(mutated)


@pytest.mark.parametrize(("field", "maximum"), [
    ("repository", 16_384), ("worktree", 16_384), ("branch", 244),
])
def test_receipt_text_limits_agree_at_the_boundary(delivery_receipt, field, maximum):
    contract = load("handoff_contract_text", CONTRACT_MODULE)
    plan = load("handoff_plan_text", PLAN_HELPER)
    mutated = copy.deepcopy(delivery_receipt)
    mutated["identity"][field] = "x" * maximum
    assert contract.validate_delivery_receipt_shape(mutated) == []
    plan._validate_design_delivery_receipt(mutated)


def test_malformed_receipt_cannot_make_a_plan_ready(tmp_path: Path) -> None:
    plan = load("handoff_plan_reject_join", PLAN_HELPER)
    receipt, repo, _, _, _, _ = deliver_routed_design(tmp_path)
    git(repo, "checkout", "--quiet", TARGET_BRANCH)
    state_home = tmp_path / "plan-state"
    plan.initialize_workflow(repo, TARGET_BRANCH, required_graph(), state_home)
    graph = plan.load_workflow(repo, TARGET_BRANCH, state_home)
    refresh_audit(plan, repo, state_home, graph)
    before = plan.load_workflow(repo, TARGET_BRANCH, state_home)
    assert before["lifecycle"]["derived_state"] == "not-ready"
    for field, value in (("workflow_id", int("1" * 32)), ("schema_version", True)):
        mutated = copy.deepcopy(receipt)
        mutated[field] = value
        with pytest.raises(plan.PlanGraphError, match=field):
            join = plan.record_design_join_from_delivery(
                workflow_id=before["workflow_id"],
                plan_revision=before["graph_revision"],
                design_delivery_receipt=mutated,
            )
            record = copy.deepcopy(before["design_join"])
            record.update(receipt=join, fresh=True)
            operation = plan.issue_operation_receipt(
                operation="record-design-join", workflow_id=before["workflow_id"],
                prior_graph_revision=before["graph_revision"], target=["design_join"],
                record_version=record["record_version"], value=record,
            )
            plan.apply_updates(
                repo, TARGET_BRANCH, before["workflow_id"], before["graph_revision"],
                [{"op": "record-design-join", "path": ["design_join"], "value": record,
                  "prior_graph_revision": before["graph_revision"],
                  "record_version": record["record_version"], "receipt": operation}],
                state_home,
            )
        assert plan.load_workflow(repo, TARGET_BRANCH, state_home) == before


def test_preflight_before_and_after_delivery(tmp_path: Path) -> None:
    design, _, state_home, baseline, _, payload = prepare_routed_design(tmp_path)
    workflow_id = payload["workflow_id"]
    before = design.load_workflow(workflow_id=workflow_id, state_home=state_home)
    check = {"workflow_id": workflow_id, "plan_baseline": baseline,
             "plan_target_branch": TARGET_BRANCH}
    pending = subprocess.run(
        [sys.executable, str(DESIGN_HELPER), "preflight", "--state-home", str(state_home)],
        input=json.dumps(check), text=True, capture_output=True, check=False,
    )
    assert pending.returncode == 1, pending.stderr
    result = json.loads(pending.stdout)
    assert result["eligible"] is False
    assert result["problems"] == [
        "lifecycle is 'active'; the Plan join needs 'delivered' (deliver first)"
    ]
    assert design.load_workflow(workflow_id=workflow_id, state_home=state_home) == before
    receipt = design_cli(state_home, "deliver", payload)
    complete = design_cli(state_home, "preflight", check)
    assert complete["eligible"] is True
    assert complete["problems"] == []
    plan = load("handoff_plan_preflight_sequence", PLAN_HELPER)
    plan.record_design_join_from_delivery(
        workflow_id="f" * 32, plan_revision=1, design_delivery_receipt=receipt,
    )


def required_graph():
    graph = minimal_graph()
    graph["design_join"] = {
        "required": True,
        "record_version": 1,
        "receipt": None,
        "fresh": False,
        "operation_receipt": None,
    }
    return graph


def refresh_audit(plan, repo: Path, state_home: Path, graph: dict) -> None:
    audit = copy.deepcopy(graph["audit"])
    audit.update(fresh=True, independent=True,
                 graph_revision=graph["graph_revision"])
    receipt = plan.issue_operation_receipt(
        operation="refresh-audit",
        workflow_id=graph["workflow_id"],
        prior_graph_revision=graph["graph_revision"],
        target=["audit"],
        record_version=audit["record_version"],
        value=audit,
    )
    plan.apply_updates(
        repo, TARGET_BRANCH, graph["workflow_id"], graph["graph_revision"],
        [{"op": "refresh-audit", "path": ["audit"], "value": audit,
          "prior_graph_revision": graph["graph_revision"],
          "record_version": audit["record_version"], "receipt": receipt}],
        state_home,
    )


def test_helpers_match_the_shared_receipt_field_contract(tmp_path: Path) -> None:
    contract = load("handoff_contract", CONTRACT_MODULE)
    plan = load("handoff_plan_fields", PLAN_HELPER)
    receipt, *_ = deliver_routed_design(tmp_path / "parity")
    assert contract.validate_delivery_receipt_shape(receipt) == []
    for field in sorted(contract.DELIVERY_RECEIPT_FIELDS):
        mutated = {k: v for k, v in receipt.items() if k != field}
        with pytest.raises(plan.PlanGraphError) as error:
            plan._validate_design_delivery_receipt(mutated)
        assert field in str(error.value), field
        assert field in " ".join(contract.validate_delivery_receipt_shape(mutated))
    for field in sorted(contract.DELIVERY_IDENTITY_FIELDS):
        mutated = copy.deepcopy(receipt)
        del mutated["identity"][field]
        with pytest.raises(plan.PlanGraphError) as error:
            plan._validate_design_delivery_receipt(mutated)
        assert field in str(error.value), field
        assert field in " ".join(contract.validate_delivery_receipt_shape(mutated))
    bloated = dict(receipt)
    bloated["router_note"] = "hand-typed extra"
    with pytest.raises(plan.PlanGraphError, match="router_note"):
        plan._validate_design_delivery_receipt(bloated)
    assert any("router_note" in problem
               for problem in contract.validate_delivery_receipt_shape(bloated))


def test_routed_delivery_joins_plan_with_no_manual_repair(tmp_path: Path) -> None:
    """End-to-end regression for issue #14: Design output joins Plan as-is."""
    design = load("handoff_design_e2e", DESIGN_HELPER)
    plan = load("handoff_plan_e2e", PLAN_HELPER)
    receipt, repo, _, _, baseline, candidate = deliver_routed_design(tmp_path)

    preflight = design.preflight_plan_join(
        workflow_id=receipt["workflow_id"],
        state_home=tmp_path / "design-state",
        plan_baseline=baseline,
        plan_target_branch=TARGET_BRANCH,
    )
    assert preflight["eligible"], preflight["problems"]

    git(repo, "checkout", "--quiet", TARGET_BRANCH)
    state_home = tmp_path / "plan-state"
    plan.initialize_workflow(repo, TARGET_BRANCH, required_graph(), state_home)
    graph = plan.load_workflow(repo, TARGET_BRANCH, state_home)
    refresh_audit(plan, repo, state_home, graph)
    graph = plan.load_workflow(repo, TARGET_BRANCH, state_home)

    join_receipt = plan.record_design_join_from_delivery(
        workflow_id=graph["workflow_id"],
        plan_revision=graph["graph_revision"],
        design_delivery_receipt=receipt,
    )
    assert join_receipt["candidate_commit"] == candidate
    record = copy.deepcopy(graph["design_join"])
    record.update(receipt=join_receipt, fresh=True)
    operation = plan.issue_operation_receipt(
        operation="record-design-join",
        workflow_id=graph["workflow_id"],
        prior_graph_revision=graph["graph_revision"],
        target=["design_join"],
        record_version=record["record_version"],
        value=record,
    )
    result = plan.apply_updates(
        repo, TARGET_BRANCH, graph["workflow_id"], graph["graph_revision"],
        [{"op": "record-design-join", "path": ["design_join"], "value": record,
          "prior_graph_revision": graph["graph_revision"],
          "record_version": record["record_version"], "receipt": operation}],
        state_home,
    )
    assert result.state == "ready"


def test_preflight_names_the_exact_missing_field(tmp_path: Path) -> None:
    design = load("handoff_design_preflight", DESIGN_HELPER)
    repo, baseline = make_repo(tmp_path)
    state_home = tmp_path / "design-state"
    state_home.mkdir()
    initialized = design.initialize_workflow(
        repository=repo,
        branch=DESIGN_BRANCH,
        worktree=repo,
        baseline=baseline,
        dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
        ui_contract={"digest": DIGEST, "outcome": "approved checkout UI"},
        scope={"components": ["CheckoutForm"], "exclusions": ["routing"]},
        invocation_mode="routed",
        state_home=state_home,
    )
    early = design.preflight_plan_join(
        workflow_id=initialized["workflow_id"], state_home=state_home,
        plan_baseline=baseline, plan_target_branch=TARGET_BRANCH,
    )
    assert early["eligible"] is False
    joined = " ".join(early["problems"])
    assert "brief_digest" in joined
    assert "candidate_commit" in joined
    assert "delivered" in joined

    wrong_baseline = design.preflight_plan_join(
        workflow_id=initialized["workflow_id"], state_home=state_home,
        plan_baseline="d" * 40, plan_target_branch=TARGET_BRANCH,
    )
    assert any("baseline" in problem for problem in wrong_baseline["problems"])
    same_branch = design.preflight_plan_join(
        workflow_id=initialized["workflow_id"], state_home=state_home,
        plan_baseline=baseline, plan_target_branch=DESIGN_BRANCH,
    )
    assert any("isolated" in problem for problem in same_branch["problems"])


def test_direct_mode_output_is_never_join_eligible(tmp_path: Path) -> None:
    design = load("handoff_design_direct", DESIGN_HELPER)
    plan = load("handoff_plan_direct", PLAN_HELPER)
    repo, baseline = make_repo(tmp_path)
    state_home = tmp_path / "design-state"
    state_home.mkdir()
    initialized = design.initialize_workflow(
        repository=repo,
        branch=DESIGN_BRANCH,
        worktree=repo,
        baseline=baseline,
        dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
        ui_contract={"digest": DIGEST, "outcome": "approved checkout UI"},
        scope={"components": ["CheckoutForm"], "exclusions": ["routing"]},
        invocation_mode="direct",
        state_home=state_home,
    )
    readings = design.preflight_plan_join(
        workflow_id=initialized["workflow_id"], state_home=state_home)
    assert readings["eligible"] is False
    assert any("candidate_commit" in problem for problem in readings["problems"])

    receipt, *_ = deliver_routed_design(tmp_path / "routed")
    direct_shaped = {k: v for k, v in receipt.items()
                     if k not in ("brief_digest", "candidate_commit")}
    with pytest.raises(plan.PlanGraphError) as error:
        plan._validate_design_delivery_receipt(direct_shaped)
    assert "brief_digest" in str(error.value)
    assert "candidate_commit" in str(error.value)


def test_join_errors_name_the_tampered_or_wrong_field(tmp_path: Path) -> None:
    plan = load("handoff_plan_errors", PLAN_HELPER)
    contract = load("handoff_contract_errors", CONTRACT_MODULE)
    receipt, *_ = deliver_routed_design(tmp_path)

    def join_kwargs(source: dict) -> dict:
        return {
            "workflow_id": "f" * 32,
            "plan_revision": 1,
            "baseline": source["identity"]["baseline"],
            "design_workflow_id": source["workflow_id"],
            "design_revision": source["revision"],
            "design_branch": source["identity"]["branch"],
            "candidate_commit": source["candidate_commit"],
            "brief_digest": source["brief_digest"],
            "approval_digest": source["approval_digest"],
            "manifest_digest": source["manifest_digest"],
            "approved": True,
        }

    tampered = copy.deepcopy(receipt)
    tampered["manifest_digest"] = "e" * 64
    with pytest.raises(plan.PlanGraphError) as error:
        plan.issue_design_join_receipt(
            design_delivery_receipt=tampered, **join_kwargs(receipt))
    assert "manifest_digest" in str(error.value)

    mutations = {
        "workflow_id": "c" * 32,
        "revision": receipt["revision"] + 1,
        "baseline": "d" * 40,
        "branch": "other/branch",
        "candidate_commit": "e" * 40,
        "brief_digest": "e" * 64,
        "approval_digest": "e" * 64,
        "manifest_digest": "e" * 64,
    }
    assert sorted(mutations) == sorted(contract.JOIN_BINDING_FIELDS)
    for field, mutated_value in mutations.items():
        drifted = copy.deepcopy(receipt)
        if field in ("baseline", "branch"):
            drifted["identity"][field] = mutated_value
        else:
            drifted[field] = mutated_value
        if field == "candidate_commit":
            drifted["identity"]["head"] = mutated_value
        with pytest.raises(plan.PlanGraphError) as error:
            plan.issue_design_join_receipt(
                design_delivery_receipt=drifted, **join_kwargs(receipt))
        assert field in str(error.value), field
    undelivered = copy.deepcopy(receipt)
    undelivered["lifecycle"] = "active"
    with pytest.raises(plan.PlanGraphError, match="lifecycle"):
        plan.record_design_join_from_delivery(
            workflow_id="f" * 32, plan_revision=1,
            design_delivery_receipt=undelivered,
        )


def test_shared_contract_cli_checks_a_receipt(tmp_path: Path) -> None:
    receipt, *_ = deliver_routed_design(tmp_path)
    check = subprocess.run(
        [sys.executable, str(CONTRACT_MODULE), "check"],
        input=json.dumps(receipt), text=True,
        capture_output=True, check=False,
    )
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["eligible"] is True
    missing = {k: v for k, v in receipt.items() if k != "candidate_commit"}
    extra_identity = copy.deepcopy(receipt)
    extra_identity["identity"]["unexpected"] = "extra"
    numeric_workflow = dict(receipt, workflow_id=int("1" * 32))
    for broken, field in ((missing, "candidate_commit"),
                          (extra_identity, "unexpected"),
                          (numeric_workflow, "workflow_id")):
        refused = subprocess.run(
            [sys.executable, str(CONTRACT_MODULE), "check"],
            input=json.dumps(broken), text=True,
            capture_output=True, check=False,
        )
        assert refused.returncode == 1
        assert field in refused.stdout


def test_delivered_design_state_reloads_with_persisted_inventories(tmp_path: Path) -> None:
    """Regression: a delivered workflow must stay readable for later checks."""
    design = load("handoff_design_reload", DESIGN_HELPER)
    receipt, _, state_home, _, _, _ = deliver_routed_design(tmp_path)
    reloaded = design.load_workflow(
        workflow_id=receipt["workflow_id"], state_home=state_home)
    assert reloaded["lifecycle"] == "delivered"
    for layer in ("candidate", "review", "manifest"):
        assert set(reloaded["delivery"][layer]) == {"inventory_digest", "files"}
    preflight = design.preflight_plan_join(
        workflow_id=receipt["workflow_id"], state_home=state_home)
    assert preflight["eligible"], preflight["problems"]


def test_skill_docs_point_at_the_shared_contract() -> None:
    design_skill = (ROOT / "plugins" / "expskill" / "content" / "skills"
                    / "design" / "SKILL.md").read_text(encoding="utf-8")
    plan_skill = (ROOT / "plugins" / "expskill" / "content" / "skills"
                  / "plan" / "SKILL.md").read_text(encoding="utf-8")
    assert "docs/specs/design-plan-handoff-contract.md" in design_skill
    assert "docs/specs/design-plan-handoff-contract.md" in plan_skill
    assert "preflight" in design_skill
    assert "An active lifecycle is the only expected pre-delivery problem" in design_skill
    assert "Fix every other problem before delivery" in design_skill
    assert "After delivery, run `preflight` again and require `eligible: true`" in design_skill
    assert "Use the `deliver` CLI stdout as the receipt, unchanged" in design_skill
    assert "record_design_join_from_delivery" in plan_skill
