import importlib.util
import inspect
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
