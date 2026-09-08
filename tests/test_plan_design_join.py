from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_plan_graph import minimal_graph


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages" / "expskill" / "scripts" / "plan_graph.py"
DIGEST = "a" * 64
BRANCH = "feature/config-validation"


def load_helper():
    spec = importlib.util.spec_from_file_location("plan_design_join", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, input=input_text, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", BRANCH)
    git(repo, "config", "user.name", "Plan Tests")
    git(repo, "config", "user.email", "plan@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    return repo, git(repo, "rev-parse", "HEAD")


def design_commit(
    repo: Path,
    baseline: str,
    parent: str | None = None,
    message: str = "Design candidate",
) -> str:
    tree = git(repo, "rev-parse", f"{baseline}^{{tree}}")
    commit = git(repo, "commit-tree", tree, "-p", parent or baseline, input_text=f"{message}\n")
    git(repo, "branch", "-f", "expskill/design/ui", commit)
    return commit


def required_graph() -> dict[str, object]:
    graph = minimal_graph()
    graph["design_join"] = {
        "required": True,
        "record_version": 1,
        "receipt": None,
        "fresh": False,
        "operation_receipt": None,
    }
    return graph


def refresh_audit(module, repo: Path, state_home: Path, graph: dict) -> None:
    audit = copy.deepcopy(graph["audit"])
    audit.update(fresh=True, independent=True, graph_revision=graph["graph_revision"])
    receipt = module.issue_operation_receipt(
        operation="refresh-audit",
        workflow_id=graph["workflow_id"],
        prior_graph_revision=graph["graph_revision"],
        target=["audit"],
        record_version=audit["record_version"],
        value=audit,
    )
    module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [{
            "op": "refresh-audit",
            "path": ["audit"],
            "value": audit,
            "prior_graph_revision": graph["graph_revision"],
            "record_version": audit["record_version"],
            "receipt": receipt,
        }],
        state_home,
    )


def join_update(module, graph: dict, candidate: str, **overrides):
    values = {
        "workflow_id": graph["workflow_id"],
        "plan_revision": graph["graph_revision"],
        "baseline": graph["baseline"]["repository_revision"],
        "design_workflow_id": "b" * 32,
        "design_revision": 4,
        "design_branch": "expskill/design/ui",
        "candidate_commit": candidate,
        "brief_digest": DIGEST,
        "approval_digest": "b" * 64,
        "manifest_digest": "c" * 64,
        "approved": True,
    }
    values.update(overrides)
    delivery = {
        "schema_version": 1,
        "operation": "deliver",
        "workflow_id": values["design_workflow_id"],
        "revision": values["design_revision"],
        "lifecycle": "delivered",
        "identity": {
            "repository": "/private/design-worktree",
            "branch": values["design_branch"],
            "worktree": "/private/design-worktree",
            "baseline": values["baseline"],
            "head": values["candidate_commit"],
            "dirty_fingerprint": "d" * 64,
            "ui_contract_digest": "e" * 64,
        },
        "state_digest": "f" * 64,
        "candidate_digest": "1" * 64,
        "candidate_inventory_digest": "2" * 64,
        "review_evidence_digest": "3" * 64,
        "manifest_digest": values["manifest_digest"],
        "evidence_digest": "4" * 64,
        "approval_digest": values["approval_digest"],
        "dependency_digest": "5" * 64,
        "brief_digest": values["brief_digest"],
        "candidate_commit": values["candidate_commit"],
    }
    values["design_delivery_receipt"] = delivery
    receipt = module.issue_design_join_receipt(**values)
    record = copy.deepcopy(graph["design_join"])
    record.update(receipt=receipt, fresh=True)
    operation = module.issue_operation_receipt(
        operation="record-design-join",
        workflow_id=graph["workflow_id"],
        prior_graph_revision=graph["graph_revision"],
        target=["design_join"],
        record_version=record["record_version"],
        value=record,
    )
    return {
        "op": "record-design-join",
        "path": ["design_join"],
        "value": record,
        "prior_graph_revision": graph["graph_revision"],
        "record_version": record["record_version"],
        "receipt": operation,
    }


def test_non_ui_graph_remains_ready_without_design_join(tmp_path: Path) -> None:
    module = load_helper()
    repo, _ = repository(tmp_path)
    receipt = module.initialize_workflow(repo, BRANCH, minimal_graph(), tmp_path / "state")
    assert receipt.state == "ready"
    graph = module.load_workflow(repo, BRANCH, tmp_path / "state")
    assert graph["design_join"]["required"] is False


def test_saved_pre_join_graph_loads_with_disabled_join_defaults(tmp_path: Path) -> None:
    module = load_helper()
    repo, _ = repository(tmp_path)
    receipt = module.initialize_workflow(repo, BRANCH, minimal_graph(), tmp_path / "state")
    stored = json.loads(receipt.path.read_text(encoding="utf-8"))
    stored.pop("design_join")
    receipt.path.write_text(json.dumps(stored), encoding="utf-8")

    graph = module.load_workflow(repo, BRANCH, tmp_path / "state")
    assert graph["design_join"] == {
        "required": False,
        "record_version": 1,
        "receipt": None,
        "fresh": True,
        "operation_receipt": None,
    }


def test_required_design_join_blocks_until_current_approved_receipt(tmp_path: Path) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    receipt = module.initialize_workflow(repo, BRANCH, required_graph(), state_home)
    assert receipt.state == "not-ready"
    graph = module.load_workflow(repo, BRANCH, state_home)
    refresh_audit(module, repo, state_home, graph)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    result = module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [join_update(module, graph, candidate)],
        state_home,
    )
    assert result.state == "ready"


@pytest.mark.parametrize(
    "update",
    [
        {
            "op": "set",
            "path": ["design_join"],
            "value": {
                "required": False,
                "record_version": 1,
                "receipt": None,
                "fresh": True,
                "operation_receipt": None,
            },
        },
        {"op": "set", "path": ["design_join", "required"], "value": False},
    ],
)
def test_required_design_join_cannot_be_downgraded(
    tmp_path: Path,
    update: dict[str, object],
) -> None:
    module = load_helper()
    repo, _ = repository(tmp_path)
    state_home = tmp_path / "state"
    receipt = module.initialize_workflow(repo, BRANCH, required_graph(), state_home)

    with pytest.raises(module.PlanGraphError, match="broad family|cannot be downgraded"):
        module.apply_updates(
            repo,
            BRANCH,
            receipt.workflow_id,
            receipt.revision,
            [update],
            state_home,
        )


@pytest.mark.parametrize("failure", ["stale", "baseline", "multiple", "tampered"])
def test_required_design_join_rejects_wrong_or_stale_receipts(tmp_path: Path, failure: str) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    module.initialize_workflow(repo, BRANCH, required_graph(), state_home)
    graph = module.load_workflow(repo, BRANCH, state_home)
    refresh_audit(module, repo, state_home, graph)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    overrides = {}
    if failure == "stale":
        overrides["plan_revision"] = graph["graph_revision"] - 1
    elif failure == "baseline":
        overrides["baseline"] = "d" * 40
    elif failure == "multiple":
        candidate = design_commit(repo, baseline, parent=candidate)
    update = join_update(module, graph, candidate, **overrides)
    if failure == "tampered":
        update["value"]["receipt"]["manifest_digest"] = "e" * 64
    with pytest.raises(module.PlanGraphError):
        module.apply_updates(
            repo,
            BRANCH,
            graph["workflow_id"],
            graph["graph_revision"],
            [update],
            state_home,
        )


def test_design_join_builder_rejects_unapproved_result(tmp_path: Path) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    graph = required_graph()
    initialized = module.initialize_workflow(repo, BRANCH, graph, tmp_path / "state")
    loaded = module.load_workflow(repo, BRANCH, tmp_path / "state")
    candidate = design_commit(repo, baseline)
    with pytest.raises(module.PlanGraphError):
        join_update(module, loaded, candidate, approved=False)
    assert initialized.state == "not-ready"


def test_design_join_rejects_candidate_after_design_branch_moves(tmp_path: Path) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    module.initialize_workflow(repo, BRANCH, required_graph(), state_home)
    graph = module.load_workflow(repo, BRANCH, state_home)
    refresh_audit(module, repo, state_home, graph)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    update = join_update(module, graph, candidate)
    design_commit(repo, baseline, message="Amended Design candidate")

    with pytest.raises(module.PlanGraphError, match="branch tip"):
        module.apply_updates(
            repo,
            BRANCH,
            graph["workflow_id"],
            graph["graph_revision"],
            [update],
            state_home,
        )


def test_integrated_design_candidate_survives_isolated_branch_cleanup(tmp_path: Path) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    module.initialize_workflow(repo, BRANCH, required_graph(), state_home)
    graph = module.load_workflow(repo, BRANCH, state_home)
    refresh_audit(module, repo, state_home, graph)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [join_update(module, graph, candidate)],
        state_home,
    )

    git(repo, "merge", "--ff-only", candidate)
    git(repo, "branch", "-d", "expskill/design/ui")

    loaded = module.load_workflow(repo, BRANCH, state_home)
    assert loaded["design_join"]["fresh"] is True
    assert loaded["design_join"]["receipt"]["candidate_commit"] == candidate


def test_design_join_builder_rejects_mismatched_delivery_revision(tmp_path: Path) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    module.initialize_workflow(repo, BRANCH, required_graph(), state_home)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    update = join_update(module, graph, candidate)
    receipt = update["value"]["receipt"]
    delivery = copy.deepcopy(receipt["design_delivery_receipt"])
    delivery["revision"] += 1

    with pytest.raises(module.PlanGraphError, match="binding mismatch"):
        module.issue_design_join_receipt(
            workflow_id=graph["workflow_id"],
            plan_revision=graph["graph_revision"],
            baseline=baseline,
            design_workflow_id=receipt["design_workflow_id"],
            design_revision=receipt["design_revision"],
            design_branch=receipt["design_branch"],
            candidate_commit=candidate,
            brief_digest=receipt["brief_digest"],
            approval_digest=receipt["approval_digest"],
            manifest_digest=receipt["manifest_digest"],
            approved=True,
            design_delivery_receipt=delivery,
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["proof", "P1", "planned_method", "positive"], "Changed positive proof"),
        (["git", "target", "reproducible"], False),
    ],
)
def test_semantic_plan_changes_stale_current_design_join(
    tmp_path: Path,
    path: list[str],
    value: object,
) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    module.initialize_workflow(repo, BRANCH, required_graph(), state_home)
    graph = module.load_workflow(repo, BRANCH, state_home)
    refresh_audit(module, repo, state_home, graph)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [join_update(module, graph, candidate)],
        state_home,
    )
    graph = module.load_workflow(repo, BRANCH, state_home)
    module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [{"op": "set", "path": path, "value": value}],
        state_home,
    )
    changed = module.load_workflow(repo, BRANCH, state_home)
    assert changed["design_join"]["receipt"] is None
    assert changed["design_join"]["fresh"] is False


def test_semantic_typed_evidence_refresh_stales_design_join(tmp_path: Path) -> None:
    module = load_helper()
    repo, baseline = repository(tmp_path)
    state_home = tmp_path / "state"
    graph_template = required_graph()
    graph_template["evidence"]["E1"]["fresh"] = False
    module.initialize_workflow(repo, BRANCH, graph_template, state_home)
    graph = module.load_workflow(repo, BRANCH, state_home)
    refresh_audit(module, repo, state_home, graph)
    graph = module.load_workflow(repo, BRANCH, state_home)
    candidate = design_commit(repo, baseline)
    module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [join_update(module, graph, candidate)],
        state_home,
    )
    graph = module.load_workflow(repo, BRANCH, state_home)
    evidence = copy.deepcopy(graph["evidence"]["E1"])
    evidence.update(
        fact="Configuration now enters through a different boundary",
        fresh=True,
        observed_at="2026-09-07T00:00:00Z",
    )
    operation = module.issue_operation_receipt(
        operation="refresh-evidence",
        workflow_id=graph["workflow_id"],
        prior_graph_revision=graph["graph_revision"],
        target=["evidence", "E1"],
        record_version=evidence["record_version"],
        value=evidence,
    )
    module.apply_updates(
        repo,
        BRANCH,
        graph["workflow_id"],
        graph["graph_revision"],
        [{
            "op": "refresh-evidence",
            "path": ["evidence", "E1"],
            "value": evidence,
            "prior_graph_revision": graph["graph_revision"],
            "record_version": evidence["record_version"],
            "receipt": operation,
        }],
        state_home,
    )

    changed = module.load_workflow(repo, BRANCH, state_home)
    assert changed["evidence"]["E1"]["fresh"] is True
    assert changed["design_join"]["receipt"] is None
    assert changed["design_join"]["fresh"] is False
