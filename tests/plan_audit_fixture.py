from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def artifact(path: Path, value: object) -> dict:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return {"path": str(path), "digest": hashlib.sha256(path.read_bytes()).hexdigest()}


def result_for(dispatch: dict, output: dict, *, findings: list | None = None,
               resolutions: list | None = None, actor: str | None = None,
               independent: bool = True, constraints: list | None = None) -> dict:
    return {"schema_version": "plan-audit-result.v1",
        **{key: dispatch[key] for key in ("dispatch_id", "workflow_id", "graph_revision", "graph_digest", "baseline_commit", "head_commit")},
        "accepted_input_digest": dispatch["accepted_input"]["digest"],
        "actor_session_id": actor or f"test-only-auditor-{dispatch['dispatch_id']}",
        "independent": independent, "elapsed_seconds": 1, "output": output,
        "evidence": ["E1"], "constraints": constraints or ["Test-only simulated independent auditor fixture"],
        "findings": findings or [], "resolutions": resolutions or []}


def refresh_audit(module, repo: Path, branch: str, graph: dict, audit: dict, state_home: Path):
    if any(audit[key] != graph["audit"][key] for key in ("breadth", "complexity", "high_consequence")):
        module.classify_plan_audit(repo, branch, graph["workflow_id"], graph["graph_revision"],
            audit["breadth"], audit["complexity"], audit["high_consequence"], audit["reason"], state_home)
        graph = module.load_workflow(repo, branch, state_home)
    try:
        history = module.load_plan_audits(repo, branch, graph["workflow_id"], state_home)
    except module.PlanGraphError:
        history = None
    root = state_home.parent
    input_path = root / f"{graph['workflow_id']}-test-accepted-input.json"
    if not input_path.exists():
        artifact(input_path, {"kind": "test-only accepted foundation", "graph": graph})
    accepted = {"path": str(input_path), "digest": hashlib.sha256(input_path.read_bytes()).hexdigest()}
    dispatch_id = f"fixture-{1 if history is None else history['spent_calls'] + 1}"
    reservation = module.reserve_plan_audit(repo, branch, graph["workflow_id"], graph["graph_revision"],
        dispatch_id, accepted, "initial" if history is None or not history["spent_calls"] else "correction",
        "Test-only simulated independent audit", state_home)
    findings = copy.deepcopy(audit["findings"])
    for finding in findings:
        finding.setdefault("description", f"Test-only retained finding {finding['id']}")
    output = artifact(root / f"{graph['workflow_id']}-{dispatch_id}-test-output.json", {"findings": findings})
    result = result_for(reservation["dispatch"], output, findings=findings,
        resolutions=audit["resolutions"], independent=audit["independent"], constraints=audit["constraints"])
    module.record_plan_audit_result(repo, branch, graph["workflow_id"], dispatch_id, result, state_home)
    return module.apply_plan_audit_result(repo, branch, graph["workflow_id"], graph["graph_revision"], dispatch_id, state_home)
