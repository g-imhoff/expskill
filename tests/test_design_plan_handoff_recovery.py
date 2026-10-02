"""Crash recovery and concurrent Git changes at the Design-to-Plan boundary."""

import copy
import json
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from tests import test_design_plan_handoff_contract as support


FAULT_WORKER = r'''
import importlib.util, io, json, os, signal, sys
from pathlib import Path
helper, mode, request = sys.argv[1:]
spec = importlib.util.spec_from_file_location("fault_subject", helper)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
request = json.loads(Path(request).read_text())
def crash():
    os.kill(os.getpid(), signal.SIGKILL)
if mode.startswith("design-"):
    original = module._durable_write
    def write(*args):
        if mode == "design-before-save": crash()
        original(*args)
        crash()
    module._durable_write = write
    sys.argv = [helper, "deliver", "--state-home", request["state_home"]]
    sys.stdin = io.StringIO(json.dumps(request["payload"]))
    raise SystemExit(module._cli())
original = module._rotate
def rotate(*args):
    original(*args)
    crash()
module._rotate = rotate
module.apply_updates(Path(request["repo"]), request["branch"], request["workflow_id"], request["revision"], request["updates"], Path(request["state_home"]))
'''


def design_call(home, payload):
    return subprocess.run(
        [sys.executable, str(support.DESIGN_HELPER), "deliver", "--state-home", str(home)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=20,
    )


def prepare(root):
    design, design_repo, home, baseline, candidate, payload = support.prepare_routed_design(root)
    repo = root / "plan-worktree"
    support.git(design_repo, "worktree", "add", str(repo), support.TARGET_BRANCH)
    return design, design_repo, repo, home, baseline, candidate, payload


def setup_plan(root, repo, receipt):
    plan = support.load("recovery_plan", support.PLAN_HELPER)
    home = root / "plan-state"
    plan.initialize_workflow(repo, support.TARGET_BRANCH, support.required_graph(), home)
    support.refresh_audit(plan, repo, home, plan.load_workflow(repo, support.TARGET_BRANCH, home))
    graph = plan.load_workflow(repo, support.TARGET_BRANCH, home)
    join = plan.record_design_join_from_delivery(
        workflow_id=graph["workflow_id"], plan_revision=graph["graph_revision"],
        design_delivery_receipt=receipt,
    )
    value = copy.deepcopy(graph["design_join"])
    value.update(receipt=join, fresh=True)
    operation = plan.issue_operation_receipt(
        operation="record-design-join", workflow_id=graph["workflow_id"],
        prior_graph_revision=graph["graph_revision"], target=["design_join"],
        record_version=value["record_version"], value=value,
    )
    updates = [{"op": "record-design-join", "path": ["design_join"], "value": value,
                "prior_graph_revision": graph["graph_revision"],
                "record_version": value["record_version"], "receipt": operation}]
    request = {"repo": str(repo), "branch": support.TARGET_BRANCH,
               "workflow_id": graph["workflow_id"], "revision": graph["graph_revision"],
               "updates": updates, "state_home": str(home)}
    return plan, home, graph, request


def join(plan, request):
    return plan.apply_updates(
        Path(request["repo"]), request["branch"], request["workflow_id"],
        request["revision"], request["updates"], Path(request["state_home"]),
    )


def kill_at_boundary(root, helper, mode, request):
    path = root / (mode + ".json")
    path.write_text(json.dumps(request))
    result = subprocess.run(
        [sys.executable, "-c", FAULT_WORKER, str(helper), mode, str(path)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("mode", ["design-before-save", "design-after-save"])
def test_interrupted_delivery_retries_to_a_ready_plan(tmp_path, mode):
    design, _, repo, home, _, _, payload = prepare(tmp_path)
    kill_at_boundary(tmp_path, support.DESIGN_HELPER, mode,
                     {"state_home": str(home), "payload": payload})
    after_crash = design.load_workflow(workflow_id=payload["workflow_id"], state_home=home)
    assert after_crash["lifecycle"] == ("active" if mode == "design-before-save" else "delivered")
    retry = design_call(home, payload)
    assert retry.returncode == 0, retry.stderr
    receipt = json.loads(retry.stdout)
    assert receipt["revision"] == payload["expected_revision"] + 1
    plan, phome, _, request = setup_plan(tmp_path, repo, receipt)
    assert join(plan, request).state == "ready"
    graph = plan.load_workflow(repo, support.TARGET_BRANCH, phome)
    assert graph["design_join"]["receipt"]["design_delivery_receipt"] == receipt


def test_duplicate_delivery_returns_identical_stdout_without_writes(tmp_path):
    _, _, _, home, _, _, payload = prepare(tmp_path)
    first = design_call(home, payload)
    assert first.returncode == 0, first.stderr
    files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (home / "design").iterdir() if p.name != ".lock"}
    retry = design_call(home, payload)
    assert retry.returncode == 0, retry.stderr
    assert retry.stdout == first.stdout
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == files


@pytest.mark.parametrize("field", ["expected_revision", "candidate_payload", "review_evidence", "manifest"])
def test_delivery_retry_rejects_changed_request(tmp_path, field):
    _, _, _, home, _, _, payload = prepare(tmp_path)
    first = design_call(home, payload)
    assert first.returncode == 0, first.stderr
    before = (home / "design" / payload["workflow_id"]).read_bytes()
    changed = copy.deepcopy(payload)
    if field == "expected_revision":
        changed[field] += 1
    else:
        changed[field]["files"][0]["path"] = "changed.txt"
    retry = design_call(home, changed)
    assert retry.returncode == 1
    assert "immutable or stale" in retry.stderr
    assert (home / "design" / payload["workflow_id"]).read_bytes() == before


def test_concurrent_delivery_returns_one_saved_receipt(tmp_path):
    _, _, _, home, _, _, payload = prepare(tmp_path)
    processes = [subprocess.Popen(
        [sys.executable, str(support.DESIGN_HELPER), "deliver", "--state-home", str(home)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) for _ in range(2)]
    try:
        for process in processes:
            process.stdin.write(json.dumps(payload))
            process.stdin.close()
            process.stdin = None
        results = [process.communicate(timeout=20) for process in processes]
        assert all(process.returncode == 0 for process in processes), results
        assert results[0][0] == results[1][0]
        assert json.loads(results[0][0])["revision"] == payload["expected_revision"] + 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.wait()


@pytest.mark.parametrize("crash", [False, True])
def test_plan_retry_after_saved_join_preserves_the_accepted_receipt(tmp_path, crash):
    _, design_repo, repo, home, _, candidate, payload = prepare(tmp_path)
    receipt = json.loads(design_call(home, payload).stdout)
    plan, phome, before, request = setup_plan(tmp_path, repo, receipt)
    if crash:
        kill_at_boundary(tmp_path, support.PLAN_HELPER, "plan-after-save", request)
    else:
        assert join(plan, request).state == "ready"
    saved = plan.load_workflow(repo, support.TARGET_BRANCH, phome)
    with pytest.raises(plan.RevisionConflict):
        join(plan, request)
    assert plan.load_workflow(repo, support.TARGET_BRANCH, phome) == saved
    assert saved["lifecycle"]["derived_state"] == "ready"
    assert saved["graph_revision"] == before["graph_revision"] + 1
    assert saved["design_join"]["receipt"]["design_delivery_receipt"] == receipt
    # The Git subprocess must release its prepared lock even if Plan is killed.
    support.git(design_repo, "update-ref", "refs/heads/" + support.DESIGN_BRANCH, candidate, candidate)


def prepared_join(tmp_path):
    _, design_repo, repo, home, baseline, candidate, payload = prepare(tmp_path)
    receipt = json.loads(design_call(home, payload).stdout)
    plan, phome, before, request = setup_plan(tmp_path, repo, receipt)
    return plan, design_repo, repo, phome, before, request, baseline, candidate


def test_branch_move_after_validation_rejects_join_without_writing(tmp_path, monkeypatch):
    plan, design_repo, repo, home, before, request, baseline, candidate = prepared_join(tmp_path)
    original = plan._finalize_graph
    def finalize(*args):
        original(*args)
        support.git(design_repo, "update-ref", "refs/heads/" + support.DESIGN_BRANCH, baseline, candidate)
    monkeypatch.setattr(plan, "_finalize_graph", finalize)
    with pytest.raises(plan.PlanGraphError, match="Design candidate branch"):
        join(plan, request)
    assert plan.load_workflow(repo, support.TARGET_BRANCH, home) == before


def test_candidate_branch_cannot_move_during_join_save(tmp_path, monkeypatch):
    plan, design_repo, repo, home, _, request, baseline, candidate = prepared_join(tmp_path)
    original = plan._rotate
    attempts = []
    def rotate(*args):
        attempts.append(subprocess.run(
            ["git", "update-ref", "refs/heads/" + support.DESIGN_BRANCH, baseline, candidate],
            cwd=design_repo, capture_output=True, text=True,
        ))
        return original(*args)
    monkeypatch.setattr(plan, "_rotate", rotate)
    assert join(plan, request).state == "ready"
    assert len(attempts) == 1 and attempts[0].returncode != 0
    assert "cannot lock ref" in attempts[0].stderr
    assert plan.load_workflow(repo, support.TARGET_BRANCH, home)["lifecycle"]["derived_state"] == "ready"
    support.git(design_repo, "update-ref", "refs/heads/" + support.DESIGN_BRANCH, candidate, candidate)


def test_failed_join_releases_candidate_lock(tmp_path, monkeypatch):
    plan, design_repo, repo, home, before, request, _, candidate = prepared_join(tmp_path)
    def fail(*args):
        raise OSError("simulated disk write failure")
    monkeypatch.setattr(plan, "_rotate", fail)
    with pytest.raises(OSError, match="disk write failure"):
        join(plan, request)
    assert plan.load_workflow(repo, support.TARGET_BRANCH, home) == before
    support.git(design_repo, "update-ref", "refs/heads/" + support.DESIGN_BRANCH, candidate, candidate)
