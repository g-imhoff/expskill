from __future__ import annotations

import hashlib
import json


TECHNICAL_GATE_NAMES = frozenset(
    {
        "format",
        "lint",
        "type",
        "build",
        "runtime",
        "responsive",
        "accessibility",
        "interaction",
        "reduced_motion",
    }
)


def passing_technical(digest: str, command: str = "design-gates") -> dict[str, object]:
    gates: dict[str, dict[str, object]] = {
        name: {"status": "pass", "evidence_digest": digest, "details": {}}
        for name in TECHNICAL_GATE_NAMES
    }
    gates["responsive"]["details"] = {
        "page_overflow": {"compact": False, "intermediate": False, "wide": False}
    }
    gates["accessibility"]["details"] = {"serious": 0, "critical": 0}
    return {
        "status": "pass",
        "results": [{"command": command, "exit": 0, "output_digest": digest}],
        "gates": gates,
    }


def prepare_delivery_fixture(module, receipt, state_home, workflow_id, records, layers):
    import copy
    import hashlib
    import json
    import sys
    from pathlib import Path

    state = module.load_workflow(workflow_id=workflow_id, state_home=state_home)
    worktree = Path(state["identity"]["worktree"])
    digest_map = {}
    paths = [item["path"] for layer in layers for item in layer["files"]]
    exclude = worktree / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as output:
        output.write("\n" + "\n".join("/" + path for path in paths) + "\n")
    for layer in layers:
        for item in layer["files"]:
            old = item["digest"]
            content = b"fixture-artifact" if old == hashlib.sha256(b"fixture-artifact").hexdigest() else b"fixture-artifact:" + old.encode()
            target = worktree / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            digest_map[old] = hashlib.sha256(content).hexdigest()
    def replace(value):
        if isinstance(value, dict): return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list): return [replace(item) for item in value]
        return digest_map.get(value, value) if isinstance(value, str) else value
    updated = replace(records)
    records.clear()
    records.update(updated)
    for layer in layers:
        updated = replace(copy.deepcopy(layer))
        layer.clear()
        layer.update(updated)
    result = module.record_check(workflow_id=workflow_id, expected_revision=receipt["revision"], argv=[sys.executable, "-c", "print('fixture-check-output')"], candidate_payload=layers[0], state_home=state_home)
    for evidence in records["evidence"].values():
        evidence["technical"]["results"] = [result.copy()]
        for gate in evidence["technical"]["gates"].values(): gate["evidence_digest"] = result["output_digest"]
    for approval in records["approvals"].values():
        approval["provenance"] = {"kind": "trusted-attestation", "actor": "human", "authority_reference": "fixture user authorized Design", "decision_reference": "fixture explicit approval of candidate"}
    if "delivery" in records:
        records["delivery"] = {"classifications": {"candidate": "component", "review": "review", "manifest": "manifest"}}
        for name, layer in zip(("candidate", "review", "manifest"), layers):
            records["delivery"][name] = {"inventory_digest": hashlib.sha256(json.dumps(layer, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "files": layer["files"]}
    return layers


FIXTURE_BRIEF = {"objective": "Fixture component review", "requirements": ["Preserve fixture behavior"], "responsive_expectations": {"compact": "No overflow", "intermediate": "No overflow", "wide": "No overflow"}, "non_goals": ["Integration"], "source": {"kind": "specification", "path": "README.md", "digest": hashlib.sha256(b"fixture\n").hexdigest()}}
FIXTURE_BRIEF_DIGEST = hashlib.sha256(json.dumps({**FIXTURE_BRIEF, "confirmed": True}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def confirm_fixture_workflow(module, receipt, state_home):
    confirmed = module.confirm_brief(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], brief=FIXTURE_BRIEF, confirmed=True, state_home=state_home)
    return {**receipt, "revision": confirmed["revision"]}
