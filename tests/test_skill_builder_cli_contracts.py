import importlib.util
import inspect
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins/expskill/content/skills/skill-builder/scripts/run_state.py"


def cli(cwd, state_root, *arguments, payload="not JSON"):
    return subprocess.run(
        [sys.executable, str(HELPER), "--state-root", str(state_root), *arguments],
        input=payload,
        text=True,
        capture_output=True,
        cwd=cwd,
        env={**os.environ, "XDG_STATE_HOME": str(cwd / "unused-xdg"), "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
    )


def footprint(root):
    return {
        path.relative_to(root).as_posix(): (
            path.is_dir(), path.stat().st_mode, path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
    }


def contract(cwd, state_root, operation):
    result = cli(cwd, state_root, "describe", operation)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["schema_version"] == "skill-builder-cli-contract.v1"
    assert value["operation"] == operation
    return value


def test_descriptions_and_operation_help_do_not_open_state_or_change_external_files(tmp_path):
    cwd = tmp_path / "untrusted-non-git-cwd"
    cwd.mkdir()
    target = tmp_path / "untouched-product"
    target.mkdir()
    (target / "source.py").write_bytes(b"print('untouched')\n")
    state_root = tmp_path / "does-not-exist"
    before = footprint(tmp_path)

    result = cli(cwd, state_root, "describe")
    assert result.returncode == 0, result.stderr
    catalog = json.loads(result.stdout)
    assert catalog["state_access"] == "none"
    assert len(catalog["operations"]) == 15
    for operation, description in catalog["operations"].items():
        assert contract(cwd, state_root, operation) == description
        help_result = cli(cwd, state_root, operation, "--help")
        assert help_result.returncode == 0, help_result.stderr
        assert json.loads(help_result.stdout) == description
        assert description["status_codes"] == {"success": 0, "domain_error": 1, "usage_error": 2}
        assert "one strict JSON object" in description["input"]
        assert "before or after" in description["option_order"]
    assert not state_root.exists()
    assert not (cwd / "unused-xdg").exists()
    assert footprint(tmp_path) == before


@pytest.mark.parametrize("mode", ["create", "improve"])
def test_described_fields_construct_valid_initialize_load_and_discover_requests(tmp_path, mode):
    cwd = tmp_path / "outside-repository"
    cwd.mkdir()
    state_root = tmp_path / "isolated-state"
    target = tmp_path / "skills" / "described-skill"
    if mode == "improve":
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"---\nname: described-skill\n---\nCount lines.\n")
    target_before = footprint(target) if target.exists() else None
    host = {"kind": "codex", "canonical_id": "codex:contract-test", "locator": str(cwd), "discovery_evidence": ["explicit isolated fixture"]}
    identity = {"requested": "described-skill", "canonical": str(target), "name": "described-skill", "invocation_token": "$described-skill", "locator": str(target)}
    initialize = contract(cwd, state_root, "initialize")
    assert set(initialize["fields"]["host_identity"]["required_properties"]) == set(host)
    assert set(initialize["fields"]["target_identity"]["required_properties"]) == set(identity)
    granted = {field: [] for field in initialize["fields"]["authority"]["required_properties"]}
    values = {"host_identity": host, "target_identity": identity, "mode": mode, "authority": granted,
              "absence_evidence": {"searched": [str(target)], "exists": False}, "overlap_map": {"exact": [], "near_neighbours": []}}
    if mode == "improve":
        snapshot = contract(cwd, state_root, "snapshot")
        result = cli(cwd, state_root, "snapshot", payload=json.dumps({snapshot["required_fields"][0]: str(target)}))
        assert result.returncode == 0, result.stderr
        values["target_manifest"] = json.loads(result.stdout)
    payload = {field: values[field] for field in initialize["required_fields"]}
    for field in initialize["mode_requirements"][mode]:
        payload[field] = values[field]
    result = cli(cwd, state_root, "initialize", payload=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    before = footprint(state_root)

    load = contract(cwd, state_root, "load")
    assert footprint(state_root) == before
    assert load["required_fields"] == ["workflow_id"]
    assert load["optional_fields"] == []
    result = cli(cwd, state_root, "load", payload=json.dumps({field: receipt[field] for field in load["required_fields"]}))
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    discover = contract(cwd, state_root, "discover")
    assert footprint(state_root) == before
    assert discover["required_fields"] == ["host_identity", "target_identity"]
    result = cli(cwd, state_root, "discover", payload=json.dumps({field: loaded[field] for field in discover["required_fields"]}))
    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    assert found["workflow_id"] == receipt["workflow_id"]
    assert found["stage"] == loaded["stage"]
    after = footprint(state_root)
    assert {name: (item[0], item[1], item[3]) for name, item in after.items()} == {name: (item[0], item[1], item[3]) for name, item in before.items()}
    assert found["sequence"] == loaded["head_sequence"] == receipt["sequence"]
    assert found["receipt_digest"] == loaded["head_transition_digest"] == receipt["receipt_digest"]
    assert (footprint(target) if target.exists() else None) == target_before
    assert list(cwd.iterdir()) == []


def test_command_contracts_match_dispatch_signatures_and_actual_wire_adapters(tmp_path):
    spec = importlib.util.spec_from_file_location("described_builder_helper", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state_root = tmp_path / "no-state"
    hidden = {"state_root", "acknowledge_cleanup"}
    aliases = {"files": "files_base64", "authority_event": "authority_event_base64", "evidence_files": "evidence_files_base64"}
    for operation, function in helper.CLI_OPERATIONS.items():
        description = contract(cwd, state_root, operation)
        parameters = inspect.signature(function).parameters
        expected = {aliases.get(name, name) for name in parameters if name not in hidden and not name.startswith("_")}
        assert set(description["fields"]) == expected
        assert set(description["required_fields"]) | set(description["optional_fields"]) == expected
        for name, parameter in parameters.items():
            if name in hidden or name.startswith("_"):
                continue
            field = aliases.get(name, name)
            required = parameter.default is inspect.Parameter.empty or name in aliases
            assert (field in description["required_fields"]) == required
        assert "state_root" not in description["fields"]
    retain = contract(cwd, state_root, "retain")
    assert retain["fields"]["files_base64"]["encoding"] == "strict base64 values keyed by run-relative path"
    cleanup = contract(cwd, state_root, "cleanup")
    assert cleanup["required_flags"] == ["--yes"]
    transition = contract(cwd, state_root, "transition")
    assert transition["transitions"]["capture-baseline"] == {"source": "resolved", "destination": "baseline", "artifact_types": ["baseline-report"]}
    assert transition["transitions"]["accept-final-review"]["destination"] == "final-reviewed"


def test_unknown_described_operation_and_unknown_request_fields_fail_without_writes(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state_root = tmp_path / "no-state"
    before = footprint(tmp_path)
    result = cli(cwd, state_root, "describe", "not-an-operation")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr and "not-an-operation" in result.stderr
    result = cli(cwd, state_root, "load", payload=json.dumps({"workflow_id": "0" * 32, "state_root": str(state_root)}))
    assert result.returncode == 1
    assert "unsupported request fields: state_root" in result.stderr
    assert footprint(tmp_path) == before


@pytest.mark.parametrize("operation,field,invalid", [
    ("retain", "files_base64", {"record.json": "%%%"}),
    ("retain", "files_base64", {"record.json": 1}),
    ("deliver", "authority_event_base64", "%%%"),
    ("deliver", "evidence_files_base64", {"delivery.json": "%%%"}),
    ("cleanup-authority", "authority_event_base64", "%%%"),
])
def test_declared_wire_adapter_rejects_invalid_base64_before_state_access(tmp_path, operation, field, invalid):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    state_root = tmp_path / "no-state"
    description = contract(cwd, state_root, operation)
    values = {"workflow_id": "0" * 32, "expected_sequence": 0, "artifact_id": "artifact", "artifact_type": "baseline-report",
              "files_base64": {"record.json": "e30K"}, "primary_path": "record.json", "producer": "actor",
              "input_bindings": [], "limitations": [], "delivery": {}, "authority_event_digest": "0" * 64,
              "authority_event_base64": "e30K", "evidence_files_base64": {"delivery.json": "e30K"}, "actor": "actor"}
    payload = {name: values[name] for name in description["required_fields"]}
    payload[field] = invalid
    result = cli(cwd, state_root, operation, payload=json.dumps(payload))
    assert result.returncode == 1
    assert field in result.stderr and "base64" in result.stderr
    assert not state_root.exists()


def artifact_contract(cwd, state_root, artifact_type):
    result = cli(cwd, state_root, "describe-artifact", artifact_type)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["schema_version"] == "skill-builder-artifact-contract.v1"
    assert value["artifact_type"] == artifact_type
    return value


def test_artifact_catalog_and_focused_predicates_are_read_only_and_share_enforcement(tmp_path):
    cwd = tmp_path / "external"
    cwd.mkdir()
    state_root = tmp_path / "no-state"
    before = footprint(tmp_path)
    result = cli(cwd, state_root, "describe-artifact")
    assert result.returncode == 0, result.stderr
    catalog = json.loads(result.stdout)
    assert catalog["state_access"] == "none"
    assert len(result.stdout.encode()) < 6000
    spec = importlib.util.spec_from_file_location("artifact_description_helper", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    assert set(catalog["artifact_types"]) == set(helper._PAYLOAD_REQUIRED_FIELDS)
    for kind, versions in catalog["artifact_types"].items():
        description = artifact_contract(cwd, state_root, kind)
        assert description["schema_versions"] == versions
        assert description["optional_fields"] == []
        for version in versions:
            assert set(description["required_fields_by_version"][version]) == helper._versioned_payload_fields(kind, version)
        assert description["validator_source"]["function"] == "_validate_artifact_payload"
        assert description["validator_source"]["sha256"] == helper.raw_digest(description["validator_source"]["source"].encode())
        assert description["validator_source"]["source"] in inspect.getsource(helper._validate_artifact_payload)
    research = artifact_contract(cwd, state_root, "research-pack")
    assert '"evidence_budget"' in research["validator_source"]["source"]
    assert '_exact_integer(lane["evidence_budget"]' in research["validator_source"]["source"]
    assert '"direct_source"' in research["validator_source"]["source"]
    assert "_exact_integer" in research["type_predicates"]
    assert any("_validate_research_binding" in source["function"] for source in research["binding_predicates"])
    assert footprint(tmp_path) == before
    assert not state_root.exists()


def test_described_early_artifacts_retain_and_advance_through_real_public_gates(tmp_path):
    spec = importlib.util.spec_from_file_location("artifact_contract_state_fixtures", ROOT / "tests/test_skill_builder_state.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    helper = fixtures.load_helper()
    cwd = tmp_path / "external"
    cwd.mkdir()
    state_root = tmp_path / "state"
    target = tmp_path / "target"
    payload = {"host_identity": fixtures.host_identity(cwd), "target_identity": fixtures.target_identity(target), "mode": "create", "authority": fixtures.authority(),
               "absence_evidence": {"searched": [str(target)], "exists": False}, "overlap_map": {"exact": [], "near_neighbours": []}}
    initialized = cli(cwd, state_root, "initialize", payload=json.dumps(payload))
    assert initialized.returncode == 0, initialized.stderr
    workflow_id = json.loads(initialized.stdout)["workflow_id"]
    for kind, event, stage in (("baseline-report", "capture-baseline", "baseline"), ("research-pack", "complete-research", "research"),
                               ("evidence-sieve", "sieve-evidence", "sieve"), ("design-record", "accept-design", "design")):
        description = artifact_contract(cwd, state_root, kind)
        value = fixtures.stage_payload(helper, state_root, workflow_id, kind)
        assert set(value) == set(description["required_fields_by_version"][value["schema_version"]])
        current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        files = {"record.json": helper.canonical_json_bytes(value)}
        if kind == "research-pack":
            source_bytes = b"offline synthetic source, not live research\n"
            value["lanes"][0]["evidence_cards"] = [{"card_id": "offline-card", "claim": "offline claim", "technique": "offline technique", "direct_source": "offline source",
                         "locator": "offline:source", "applicable_situation": "offline", "limitation": "synthetic fixture", "experiment": "offline experiment",
                         "lane_id": value["lanes"][0]["lane_id"], "raw_source_digest": helper.raw_digest(source_bytes)}]
            files["record.json"] = helper.canonical_json_bytes(value)
            files["raw-source.txt"] = source_bytes
            for invalid in ("extra-field", "wrong-budget", "wrong-model", "wrong-card-source"):
                broken = json.loads(json.dumps(value))
                lane = broken["lanes"][0]
                if invalid == "extra-field":
                    lane["unknown"] = "invalid"
                elif invalid == "wrong-budget":
                    lane["evidence_budget"] = {"maximum_primary_sources": 3}
                elif invalid == "wrong-model":
                    lane["model"] = "not-the-declared-model"
                else:
                    lane["evidence_cards"][0]["direct_source"] = {"title": "object is invalid"}
                request = {"workflow_id": workflow_id, "expected_sequence": current["head_sequence"], "artifact_id": "invalid-research", "artifact_type": kind,
                           "files_base64": {"record.json": base64.b64encode(helper.canonical_json_bytes(broken)).decode()}, "primary_path": "record.json",
                           "producer": "offline-fixture", "input_bindings": fixtures.current_bindings(helper, state_root, workflow_id), "limitations": []}
                rejected = cli(cwd, state_root, "retain", payload=json.dumps(request))
                assert rejected.returncode == 1
                assert "research" in rejected.stderr or "evidence" in rejected.stderr
                assert helper.load_run(workflow_id=workflow_id, state_root=state_root) == current
        if kind == "evidence-sieve":
            value["decisions"] = [{"card_id": "offline-card", "decision": "experiment", "reason": "offline fixture", "deduplication_links": []}]
            files["record.json"] = helper.canonical_json_bytes(value)
        request = {"workflow_id": workflow_id, "expected_sequence": current["head_sequence"], "artifact_id": kind, "artifact_type": kind,
                   "files_base64": {name: base64.b64encode(data).decode() for name, data in files.items()}, "primary_path": "record.json",
                   "producer": "offline-fixture", "input_bindings": fixtures.current_bindings(helper, state_root, workflow_id), "limitations": []}
        retained = cli(cwd, state_root, "retain", payload=json.dumps(request))
        assert retained.returncode == 0, retained.stderr
        receipt = json.loads(retained.stdout)
        transitioned = cli(cwd, state_root, "transition", payload=json.dumps({"workflow_id": workflow_id, "expected_sequence": receipt["sequence"],
                           "event": event, "destination_stage": stage, "artifact_ids": [kind]}))
        assert transitioned.returncode == 0, transitioned.stderr
        assert json.loads(transitioned.stdout)["stage"] == stage


def test_unknown_artifact_description_fails_before_state_access(tmp_path):
    cwd = tmp_path / "external"
    cwd.mkdir()
    state_root = tmp_path / "no-state"
    before = footprint(tmp_path)
    result = cli(cwd, state_root, "describe-artifact", "unknown-artifact")
    assert result.returncode == 2
    assert "unknown-artifact" in result.stderr
    assert footprint(tmp_path) == before
