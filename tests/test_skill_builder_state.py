from __future__ import annotations

import base64
import importlib.util
import hashlib
import json
import multiprocessing
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    ROOT
    / "plugins"
    / "codex-dev-flow"
    / "skills"
    / "skill-builder"
    / "scripts"
    / "run_state.py"
)
SCORE_CATEGORIES = (
    "triggering",
    "scope discipline",
    "workflow quality",
    "collaboration",
    "output contract",
    "safety",
    "recovery",
    "composability",
    "context efficiency",
    "testability",
)
CONFORMANCE_GATES = tuple(f"BR{index}" for index in range(1, 11))
LEGACY_CONFORMANCE_GATES = (
    "research",
    "confirmation",
    "acceptance-first",
    "isolation",
    "evidence",
    "state",
    "authority",
    "cleanup",
)
SCORE_CRITERIA = {
    "triggering": tuple(f"TR{index}" for index in range(1, 11)),
    "scope discipline": tuple(f"SC{index}" for index in range(1, 11)),
    "workflow quality": tuple(f"WF{index}" for index in range(1, 11)),
    "collaboration": tuple(f"CO{index}" for index in range(1, 11)),
    "output contract": tuple(f"OU{index}" for index in range(1, 11)),
    "safety": tuple(f"SA{index}" for index in range(1, 11)),
    "recovery": tuple(f"RE{index}" for index in range(1, 11)),
    "composability": tuple(f"CP{index}" for index in range(1, 11)),
    "context efficiency": tuple(f"CE{index}" for index in range(1, 11)),
    "testability": tuple(f"TE{index}" for index in range(1, 11)),
}

CANDIDATE_SKILL_BYTES = b"---\nname: sample-skill\n---\n# Sample skill\n"
CANDIDATE_DIFF_BYTES = b"candidate diff\n"
EVALUATION_RUBRIC_BYTES = b"frozen evaluation rubric\n"
MULTIFILE_SKILL_FILES = {
    "SKILL.md": CANDIDATE_SKILL_BYTES,
    "references/guide.md": b"# Guide\n",
    "scripts/check.py": b"print('ok')\n",
}
REVIEW_ACCESS_BYTES = b"reviewer access check\n"
REVIEW_FINDING_BYTES = b"review finding evidence\n"
CONFORMANCE_EVIDENCE_BYTES = b"conformance gate evidence\n"
VERIFICATION_OUTPUT_BYTES = b"pytest passed\n"
VERIFICATION_MANIFEST_BYTES = b"verified target manifest\n"
RELEASE_EVIDENCE_BYTES = b"release evidence manifest\n"
TRIAL_DIGEST_FIELDS = {
    "request_digest": "request",
    "raw_prompt_digest": "prompt",
    "loaded_skill_digest": "loaded-skill",
    "tool_event_digest": "tool-events",
    "output_digest": "output",
    "before_target_manifest_digest": "before-manifest",
    "after_target_manifest_digest": "after-manifest",
    "filesystem_result_digest": "filesystem-result",
}


def trial_evidence_bytes(case_id: str, kind: str) -> bytes:
    if kind == "loaded-skill":
        return CANDIDATE_SKILL_BYTES
    return f"{case_id}:{kind}\n".encode("utf-8")


def varied_multifile_package(change: str) -> dict[str, bytes]:
    package = dict(MULTIFILE_SKILL_FILES)
    if change == "addition":
        package["references/extra.md"] = b"# Extra\n"
    elif change == "removal":
        package.pop("references/guide.md")
    elif change == "mutation":
        package["references/guide.md"] = b"# Changed\n"
    elif change != "exact":
        raise AssertionError(f"unknown package variation: {change}")
    return package


def fixture_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fixture_canonical_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def candidate_result_bytes(
    *,
    schema_version: str = "skill-builder-owned-result.v2",
    target_mode: int = 0o755,
    skill_bytes: bytes = CANDIDATE_SKILL_BYTES,
    additional_skill_files: dict[str, bytes] | None = None,
) -> bytes:
    files = {"SKILL.md": skill_bytes, **(additional_skill_files or {})}
    directories = {
        "/".join(path.split("/")[:index])
        for path in files
        for index in range(1, len(path.split("/")))
    }
    entries: list[dict[str, object]] = [
        {
            "path": path,
            "kind": "directory",
            "mode": 0o755,
            "byte_count": 0,
            "digest": None,
        }
        for path in directories
    ]
    entries.extend(
        {
            "path": path,
            "kind": "file",
            "mode": 0o644,
            "byte_count": len(payload),
            "digest": fixture_digest(payload),
        }
        for path, payload in files.items()
    )
    entries.sort(key=lambda entry: str(entry["path"]))
    record: dict[str, object] = {
        "schema_version": schema_version,
        "entries": entries,
    }
    if schema_version == "skill-builder-owned-result.v2":
        record.update({"target_kind": "directory", "target_mode": target_mode})
    return fixture_canonical_bytes(record)


def fixture_loadable_skill_digest(files: dict[str, bytes]) -> str:
    record = {
        "schema_version": "skill-builder-loadable-content.v1",
        "entries": [
            {
                "path": path,
                "byte_count": len(payload),
                "digest": fixture_digest(payload),
            }
            for path, payload in sorted(files.items())
        ],
    }
    return fixture_digest(fixture_canonical_bytes(record))


def evaluation_case(case_id: str, partition: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "partition": partition,
        "purpose": f"exercise {partition}",
        "raw_request_digest": fixture_digest(
            trial_evidence_bytes(case_id, "request")
        ),
        "allowed_context": ["skill contract"],
        "setup_manifest_digest": fixture_digest(
            trial_evidence_bytes(case_id, "before-manifest")
        ),
        "observable_assertions": ["returns pass"],
        "forbidden_effects": ["remote write"],
        "evidence_requirements": ["raw output"],
        "pass_fail_rule": "all assertions pass",
    }


def trial_files(
    payload: dict[str, object],
    *,
    evidence_overrides: dict[tuple[str, str], bytes] | None = None,
    loaded_skill_bytes: bytes | None = None,
    loaded_skill_files: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    files = {"record.json": fixture_canonical_bytes(payload)}
    cases = payload.get("cases")
    assert isinstance(cases, list)
    current_schema = payload.get("schema_version") == "skill-builder-trial-pack.v2"
    overrides = evidence_overrides or {}
    for trial_case in cases:
        assert isinstance(trial_case, dict)
        case_id = trial_case["case_id"]
        assert isinstance(case_id, str)
        partition = {
            "case-1": "visible_development",
            "case-2": "frozen_validation",
            "case-3": "hidden_release",
        }[case_id]
        case_bytes = overrides.get(
            (case_id, "case"),
            fixture_canonical_bytes(evaluation_case(case_id, partition)),
        )
        files[f"evidence/{case_id}/case.json"] = case_bytes
        for kind in TRIAL_DIGEST_FIELDS.values():
            evidence = overrides.get(
                (case_id, kind),
                loaded_skill_bytes
                if kind == "loaded-skill" and loaded_skill_bytes is not None
                else trial_evidence_bytes(case_id, kind),
            )
            if current_schema and kind == "loaded-skill":
                package = (
                    {"SKILL.md": evidence}
                    if loaded_skill_files is None
                    else loaded_skill_files
                )
                files.update(
                    {
                        f"evidence/{case_id}/loaded-skill/{relative}": content
                        for relative, content in package.items()
                    }
                )
            else:
                files[f"evidence/{case_id}/{kind}.bin"] = evidence
    aggregate = overrides.get(
        ("aggregate", "manifest"),
        fixture_canonical_bytes(
            {"case_ids": [case["case_id"] for case in cases]}
        ),
    )
    files["evidence/aggregate-manifest.json"] = aggregate
    return files


def load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("skill_builder_run_state", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initialize_process(
    state_root: str,
    host: dict[str, object],
    target: dict[str, str],
    granted: dict[str, object],
    start: object,
    results: object,
) -> None:
    helper = load_helper()
    start.wait(10)
    try:
        receipt = helper.initialize_run(
            host_identity=host,
            target_identity=target,
            mode="create",
            authority=granted,
            absence_evidence={"searched": [target["locator"]], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=Path(state_root),
        )
        results.put(("success", receipt["workflow_id"]))
    except Exception as error:
        results.put(("error", type(error).__name__, str(error)))


def host_identity(root: Path) -> dict[str, object]:
    return {
        "kind": "codex",
        "canonical_id": "codex:test-host",
        "locator": str(root.resolve()),
        "discovery_evidence": ["explicit test fixture"],
    }


def target_identity(target: Path, name: str = "sample-skill") -> dict[str, str]:
    return {
        "requested": name,
        "canonical": f"codex:personal:{name}",
        "name": name,
        "invocation_token": f"${name}",
        "locator": str(target.resolve()),
    }


def authority() -> dict[str, object]:
    return {
        "reads": ["target", "host-registry"],
        "writes": ["isolated-candidate"],
        "delegation": ["research", "trial", "review", "verification"],
        "candidate_effects": ["isolated-only"],
        "delivery_effects": [
            "installation:codex:personal:sample-skill",
            "integration:codex:personal:sample-skill",
        ],
    }


def git_repository(root: Path) -> tuple[Path, Path, str]:
    repository = root / "repo"
    target = repository / "skills" / "sample-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# Sample\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", "-b", "feature/state", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    return repository, target, head


def retain_json(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    artifact_id: str,
    artifact_type: str,
    payload: dict[str, object],
    input_bindings: list[dict[str, str]] | None = None,
    trial_loaded_skill_bytes: bytes | None = None,
    trial_loaded_skill_files: dict[str, bytes] | None = None,
    retain_review_source_components: bool = False,
    candidate_resulting_bytes: bytes | None = None,
    omitted_review_source_component: str | None = None,
) -> dict[str, object]:
    if artifact_type == "trial-pack":
        files = trial_files(
            payload,
            loaded_skill_bytes=trial_loaded_skill_bytes,
            loaded_skill_files=trial_loaded_skill_files,
        )
    else:
        files = {"record.json": fixture_canonical_bytes(payload)}
        if (
            artifact_type == "baseline-report"
            and "host_conventions" in payload
            and "preserved_regressions" in payload
        ):
            files.update(
                {
                    "review-components/host-rules.json": fixture_canonical_bytes(
                        {"host_rules": payload["host_conventions"]}
                    ),
                    "review-components/preserved-regressions.json": (
                        fixture_canonical_bytes(
                            {
                                "preserved_regressions": payload[
                                    "preserved_regressions"
                                ]
                            }
                        )
                    ),
                }
            )
        if (
            artifact_type == "candidate-record"
            and payload.get("schema_version") == "skill-builder-candidate.v4"
        ):
            candidate_root = Path(str(payload["isolated_locator"]))
            files.update(
                {
                    (
                        "candidate-package/"
                        f"{path.relative_to(candidate_root).as_posix()}"
                    ): path.read_bytes()
                    for path in sorted(candidate_root.rglob("*"))
                    if path.is_file()
                }
            )
        if (
            retain_review_source_components
            and artifact_type == "evaluation-pack"
            and omitted_review_source_component != "evaluation-rubric"
        ):
            files["review-components/evaluation-rubric.bin"] = (
                EVALUATION_RUBRIC_BYTES
            )
        if retain_review_source_components and artifact_type == "candidate-record":
            candidate_root = Path(str(payload["isolated_locator"]))
            package = {
                path.relative_to(candidate_root).as_posix(): path.read_bytes()
                for path in sorted(candidate_root.rglob("*"))
                if path.is_file()
            }
            if omitted_review_source_component != "candidate-diff":
                files["review-components/candidate-diff.bin"] = CANDIDATE_DIFF_BYTES
            if omitted_review_source_component != "candidate-revision":
                files["review-components/candidate-revision.json"] = (
                    fixture_canonical_bytes(
                        {"candidate_revision": payload["candidate_revision"]}
                    )
                )
            if omitted_review_source_component != "candidate-result":
                files["review-components/candidate-result.json"] = (
                    candidate_result_bytes(
                        skill_bytes=package.pop("SKILL.md"),
                        additional_skill_files=package,
                    )
                    if candidate_resulting_bytes is None
                    else candidate_resulting_bytes
                )
        supporting_evidence = {
            "review-record": {
                "evidence/access-check.bin": REVIEW_ACCESS_BYTES,
                "evidence/finding.bin": REVIEW_FINDING_BYTES,
            },
            "builder-run-conformance-ledger": {
                "evidence/gates.bin": CONFORMANCE_EVIDENCE_BYTES,
            },
            "verification-record": {
                "evidence/command-output.bin": VERIFICATION_OUTPUT_BYTES,
                "evidence/target-manifest.bin": VERIFICATION_MANIFEST_BYTES,
            },
            "release-record": {
                "evidence/release-manifest.bin": RELEASE_EVIDENCE_BYTES,
            },
        }
        files.update(supporting_evidence.get(artifact_type, {}))
    return helper.retain_artifact(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        files=files,
        primary_path="record.json",
        producer="main-agent",
        input_bindings=input_bindings or [],
        limitations=[],
        state_root=state_root,
    )


def valid_create_baseline_payload(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> dict[str, object]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    snapshot = current["target_snapshot"]
    return {
        "schema_version": "skill-builder-baseline.v1",
        "mode": "create",
        "target_snapshot_digest": snapshot["snapshot_digest"],
        "absent_target_proof": {
            "absence_evidence_digest": snapshot["absence_evidence_digest"],
            "overlap_map_digest": snapshot["overlap_map_digest"],
        },
        "host_conventions": [],
        "preserved_regressions": [],
        "raw_evidence_digests": [],
        "limitations": [],
    }


def accepted_artifact(
    helper: ModuleType, state_root: Path, workflow_id: str, artifact_type: str
) -> tuple[str, str]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    matches = [
        (artifact_id, record["digest"])
        for artifact_id, record in current["artifact_index"].items()
        if record["type"] == artifact_type and record["derived_status"] == "accepted"
    ]
    assert len(matches) == 1, (artifact_type, matches)
    return matches[0]


def write_skill_package(target: Path, files: dict[str, bytes]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, 0o755)
    for relative, payload in sorted(files.items()):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        for parent in destination.parents:
            if parent == target.parent:
                break
            os.chmod(parent, 0o755)
        destination.write_bytes(payload)
        os.chmod(destination, 0o644)


def install_target_and_digest(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> str:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    target = Path(current["target_identity"]["locator"])
    write_skill_package(target, {"SKILL.md": CANDIDATE_SKILL_BYTES})
    return helper.snapshot_target(target)["manifest_digest"]


def record_package_delivery(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    files: dict[str, bytes],
) -> dict[str, object]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    target = Path(current["target_identity"]["locator"])
    write_skill_package(target, files)
    authority_event = b"authorize exact package installation\n"
    acceptance_evidence = b"exact package destination snapshot\n"
    return helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        delivery={
            "action": "installation",
            "destination_identity": current["target_identity"]["canonical"],
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": helper.snapshot_target(target)[
                "manifest_digest"
            ],
            "acceptance_evidence": [fixture_digest(acceptance_evidence)],
            "user_authority_event_digest": fixture_digest(authority_event),
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=fixture_digest(authority_event),
        authority_event=authority_event,
        evidence_files={"destination-verification.bin": acceptance_evidence},
        state_root=state_root,
    )


def rewrite_current_resolution_version(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    *,
    resolution_schema: str,
    candidate_schema: str,
    scorecard_schema: str | None,
) -> None:
    """Rewrite only genesis bytes to a previously emitted resolution format."""
    run = state_root / "live" / workflow_id
    artifact = run / "artifacts" / "resolution"
    payload_path = artifact / "raw" / "payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] in {
        "skill-builder-resolution.v3",
        "skill-builder-resolution.v4",
    }
    for marker in (
        "candidate_record_schema",
        "scorecard_schema",
        "trial_pack_schema",
        "review_record_schema",
    ):
        payload.pop(marker, None)
    payload["schema_version"] = resolution_schema
    if resolution_schema != "skill-builder-resolution.v1":
        payload["candidate_record_schema"] = candidate_schema
    if scorecard_schema is not None:
        payload["scorecard_schema"] = scorecard_schema
    payload_bytes = helper.canonical_json_bytes(payload)
    payload_path.write_bytes(payload_bytes)

    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["byte_count"] = len(payload_bytes)
    manifest["entries"][0]["digest"] = fixture_digest(payload_bytes)
    manifest["observed_byte_count"] = len(payload_bytes)
    manifest["manifest_digest"] = helper.canonical_digest(
        manifest, "manifest_digest"
    )
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))

    envelope_path = artifact / "envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["payload_digest"] = fixture_digest(payload_bytes)
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(
        envelope, "envelope_digest"
    )
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))

    receipt_path = run / "receipts" / "00000000.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["relevant_artifact_digests"][0]["envelope_digest"] = envelope[
        "envelope_digest"
    ]
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    derived = helper._derive_index(run)
    (run / "current.json").write_bytes(helper.canonical_json_bytes(derived))


def mark_run_as_legacy_candidate_v1(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> None:
    rewrite_current_resolution_version(
        helper,
        state_root,
        workflow_id,
        resolution_schema="skill-builder-resolution.v1",
        candidate_schema="skill-builder-candidate.v1",
        scorecard_schema=None,
    )


def mark_run_as_historical_candidate_v2(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> None:
    rewrite_current_resolution_version(
        helper,
        state_root,
        workflow_id,
        resolution_schema="skill-builder-resolution.v2",
        candidate_schema="skill-builder-candidate.v2",
        scorecard_schema=None,
    )


def mark_run_as_historical_candidate_v3(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> None:
    rewrite_current_resolution_version(
        helper,
        state_root,
        workflow_id,
        resolution_schema="skill-builder-resolution.v3",
        candidate_schema="skill-builder-candidate.v3",
        scorecard_schema="skill-builder-scorecard.v2",
    )


def rewrite_artifact_payload_and_receipts(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    artifact_id: str,
    payload: dict[str, object],
    rewrite_receipts: bool,
) -> None:
    """Re-envelope test bytes and optionally make the append-only chain self-consistent."""
    run = state_root / "live" / workflow_id
    artifact = run / "artifacts" / artifact_id
    payload_path = artifact / "raw" / "record.json"
    payload_bytes = fixture_canonical_bytes(payload)
    payload_path.write_bytes(payload_bytes)

    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record_entry = next(
        entry
        for entry in manifest["entries"]
        if entry["path"].endswith("/raw/record.json")
    )
    record_entry["byte_count"] = len(payload_bytes)
    record_entry["digest"] = fixture_digest(payload_bytes)
    manifest["observed_byte_count"] = sum(
        entry["byte_count"] for entry in manifest["entries"]
    )
    manifest["manifest_digest"] = helper.canonical_digest(
        manifest, "manifest_digest"
    )
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))

    envelope_path = artifact / "envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    old_envelope_digest = envelope["envelope_digest"]
    envelope["payload_digest"] = fixture_digest(payload_bytes)
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(
        envelope, "envelope_digest"
    )
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))

    if not rewrite_receipts:
        return
    prior_digest: str | None = None
    for receipt_path in sorted((run / "receipts").glob("*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sequence"] > 0:
            assert prior_digest is not None
            receipt["prior_receipt_digest"] = prior_digest
        for binding in receipt["relevant_artifact_digests"]:
            if (
                binding["artifact_id"] == artifact_id
                and binding["envelope_digest"] == old_envelope_digest
            ):
                binding["envelope_digest"] = envelope["envelope_digest"]
        receipt["receipt_digest"] = helper.canonical_digest(
            receipt, "receipt_digest"
        )
        receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
        prior_digest = receipt["receipt_digest"]
    (run / "current.json").write_text("{broken", encoding="utf-8")


def rewrite_artifact_raw_file_and_receipts(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    artifact_id: str,
    relative_path: str,
    payload: bytes,
) -> None:
    """Rehash a raw test artifact to exercise semantic replay validation."""
    run = state_root / "live" / workflow_id
    artifact = run / "artifacts" / artifact_id
    raw_path = artifact / "raw" / relative_path
    raw_path.write_bytes(payload)

    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry_path = f"artifacts/{artifact_id}/raw/{relative_path}"
    entry = next(
        item for item in manifest["entries"] if item["path"] == entry_path
    )
    entry["byte_count"] = len(payload)
    entry["digest"] = fixture_digest(payload)
    manifest["observed_byte_count"] = sum(
        item["byte_count"] for item in manifest["entries"]
    )
    manifest["manifest_digest"] = helper.canonical_digest(
        manifest, "manifest_digest"
    )
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))

    envelope_path = artifact / "envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    old_envelope_digest = envelope["envelope_digest"]
    if envelope["payload_path"] == entry_path:
        envelope["payload_digest"] = fixture_digest(payload)
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(
        envelope, "envelope_digest"
    )
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))

    prior_digest: str | None = None
    for receipt_path in sorted((run / "receipts").glob("*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sequence"] > 0:
            assert prior_digest is not None
            receipt["prior_receipt_digest"] = prior_digest
        for binding in receipt["relevant_artifact_digests"]:
            if (
                binding["artifact_id"] == artifact_id
                and binding["envelope_digest"] == old_envelope_digest
            ):
                binding["envelope_digest"] = envelope["envelope_digest"]
        receipt["receipt_digest"] = helper.canonical_digest(
            receipt, "receipt_digest"
        )
        receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
        prior_digest = receipt["receipt_digest"]
    (run / "current.json").write_text("{broken", encoding="utf-8")


def stage_payload(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    artifact_type: str,
) -> dict[str, object]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    snapshot_digest = current["target_snapshot"]["snapshot_digest"]
    if artifact_type == "baseline-report":
        return valid_create_baseline_payload(helper, state_root, workflow_id)
    if artifact_type == "research-pack":
        _, baseline_digest = accepted_artifact(
            helper, state_root, workflow_id, "baseline-report"
        )
        return {
            "schema_version": "skill-builder-research-pack.v1",
            "target_snapshot_digest": snapshot_digest,
            "baseline_digest": baseline_digest,
            "lanes": [
                {
                    "lane_id": f"lane-{index}",
                    "question": f"bounded question {index}",
                    "model": "GPT-5.6-Luna",
                    "reasoning": "max",
                    "source_scope": ["authoritative sources"],
                    "evidence_budget": 1,
                    "start_state": "fresh",
                    "end_state": "complete",
                    "limitations": [],
                    "evidence_cards": [],
                }
                for index in range(1, 4)
            ],
            "limitations": [],
        }
    if artifact_type == "evidence-sieve":
        _, research_digest = accepted_artifact(
            helper, state_root, workflow_id, "research-pack"
        )
        return {
            "schema_version": "skill-builder-evidence-sieve.v1",
            "research_digest": research_digest,
            "decisions": [],
            "conflicts": [],
            "retained_dissent": [],
            "design_relevance": [],
            "limitations": [],
        }
    if artifact_type == "design-record":
        _, research_digest = accepted_artifact(
            helper, state_root, workflow_id, "research-pack"
        )
        _, sieve_digest = accepted_artifact(
            helper, state_root, workflow_id, "evidence-sieve"
        )
        return {
            "schema_version": "skill-builder-design.v1",
            "research_digest": research_digest,
            "sieve_digest": sieve_digest,
            "alternatives": ["bounded implementation"],
            "mechanisms": ["durable receipts"],
            "tradeoffs": ["strict validation"],
            "challenges": ["crash recovery"],
            "evidence_links": ["research"],
            "factual_conclusions": ["state is append-only"],
            "user_owned_questions": [],
            "user_decisions": ["accepted design"],
            "rejected_alternatives": [],
            "unresolved_issues": [],
        }
    raise AssertionError(f"no valid stage payload fixture for {artifact_type}")


def current_bindings(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> list[dict[str, str]]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    return [
        {"artifact_id": artifact_id, "digest": record["digest"]}
        for artifact_id, record in sorted(current["artifact_index"].items())
        if record["derived_status"] == "accepted"
    ]


def contract_payload(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> dict[str, object]:
    _, design_digest = accepted_artifact(helper, state_root, workflow_id, "design-record")
    return {
        "schema_version": "skill-builder-contract.v1",
        "design_digest": design_digest,
        "purpose": "build the exact sample skill",
        "success_signal": "all frozen cases pass",
        "triggers": ["explicit sample request"],
        "non_triggers": ["unrelated request"],
        "inputs_preconditions": ["accepted design"],
        "ordered_behavior": ["validate", "build", "verify"],
        "allowed_actions": ["isolated writes"],
        "forbidden_actions": ["remote delivery"],
        "tools": ["standard library"],
        "permissions": ["local state"],
        "delegation": ["bounded roles"],
        "outputs_consumers": ["verified candidate"],
        "failures": ["fail closed"],
        "changed_goals": ["invalidate downstream"],
        "stopping_conditions": ["material authority decision"],
        "resources": ["artifact contract"],
        "non_goals": ["automatic installation"],
        "evidence_bindings": [
            {
                "statement": "design accepted",
                "artifact_id": "design",
                "digest": design_digest,
            }
        ],
    }


def confirmation_payload(
    *,
    contract_id: str,
    contract_digest: str,
    target: str,
    snapshot_digest: str,
    authority_digest: str,
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-user-confirmation.v1",
        "user_identity": "test-user",
        "confirmed_at": "2026-09-05T12:00:00Z",
        "confirmation_text": "I confirm this exact contract and target.",
        "confirmation_event_digest": authority_digest,
        "target_identity": target,
        "contract_artifact_id": contract_id,
        "contract_digest": contract_digest,
        "target_snapshot_digest": snapshot_digest,
        "accepted": True,
    }


def evaluation_payload(
    *, contract_digest: str, confirmation_digest: str, snapshot_digest: str
) -> dict[str, object]:
    partitions: dict[str, list[dict[str, object]]] = {}
    for index, partition in enumerate(
        ("visible_development", "frozen_validation", "hidden_release"), start=1
    ):
        partitions[partition] = [evaluation_case(f"case-{index}", partition)]
    return {
        "schema_version": "skill-builder-evaluation-pack.v1",
        "frozen": True,
        "contract_digest": contract_digest,
        "confirmation_digest": confirmation_digest,
        "target_snapshot_digest": snapshot_digest,
        "rubric_digest": fixture_digest(EVALUATION_RUBRIC_BYTES),
        "scoring_parameters": {"maximum": 10},
        "freeze_timestamp": "2026-09-05T12:01:00Z",
        "partitions": partitions,
    }


def candidate_payload(
    *,
    tmp_path: Path,
    candidate_id: str,
    revision: str,
    contract_digest: str,
    confirmation_digest: str,
    evaluation_digest: str,
    snapshot_digest: str,
    schema_version: str = "skill-builder-candidate.v4",
    resulting_bytes: bytes | None = None,
    loaded_skill_bytes: bytes = CANDIDATE_SKILL_BYTES,
    claimed_loaded_skill_bytes: bytes | None = None,
    additional_skill_files: dict[str, bytes] | None = None,
    owned_paths: list[str] | None = None,
) -> dict[str, object]:
    candidate_root = tmp_path / "candidate"
    package = {
        "SKILL.md": loaded_skill_bytes,
        **(additional_skill_files or {}),
    }
    write_skill_package(candidate_root, package)
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "candidate_id": candidate_id,
        "isolated_locator": str(candidate_root.resolve()),
        "base_snapshot_digest": snapshot_digest,
        "candidate_revision": revision,
        "resulting_digest": fixture_digest(
            candidate_result_bytes(
                skill_bytes=loaded_skill_bytes,
                additional_skill_files=additional_skill_files,
            )
            if resulting_bytes is None
            else resulting_bytes
        ),
        "writable_role": "implementer",
        "owned_paths": (
            ["."] if additional_skill_files else ["SKILL.md"]
        ) if owned_paths is None else owned_paths,
        "diff_digest": fixture_digest(CANDIDATE_DIFF_BYTES),
        "local_check_evidence": ["3" * 64],
        "contract_digest": contract_digest,
        "confirmation_digest": confirmation_digest,
        "evaluation_digest": evaluation_digest,
        "target_snapshot_digest": snapshot_digest,
    }
    if schema_version == "skill-builder-candidate.v4":
        claimed_package = dict(package)
        if claimed_loaded_skill_bytes is not None:
            claimed_package["SKILL.md"] = claimed_loaded_skill_bytes
        payload["loaded_skill_digest"] = fixture_loadable_skill_digest(
            claimed_package
        )
    elif schema_version == "skill-builder-candidate.v3":
        payload["loaded_skill_digest"] = fixture_digest(
            loaded_skill_bytes
            if claimed_loaded_skill_bytes is None
            else claimed_loaded_skill_bytes
        )
    return payload


def trial_payload(
    candidate_digest: str,
    revision: str = "candidate-1",
    *,
    loaded_skill_bytes: bytes | None = None,
    loaded_skill_files: dict[str, bytes] | None = None,
    schema_version: str = "skill-builder-trial-pack.v2",
) -> dict[str, object]:
    loaded_skill = CANDIDATE_SKILL_BYTES if loaded_skill_bytes is None else loaded_skill_bytes
    package = (
        {"SKILL.md": loaded_skill}
        if loaded_skill_files is None
        else loaded_skill_files
    )
    cases: list[dict[str, object]] = []
    partitions = (
        "visible_development",
        "frozen_validation",
        "hidden_release",
    )
    for index, partition in enumerate(partitions, start=1):
        case_id = f"case-{index}"
        frozen_case = evaluation_case(case_id, partition)
        cases.append(
            {
                "case_id": case_id,
                "case_digest": fixture_digest(fixture_canonical_bytes(frozen_case)),
                "request_digest": frozen_case["raw_request_digest"],
                "raw_prompt_digest": fixture_digest(
                    trial_evidence_bytes(case_id, "prompt")
                ),
                "loaded_skill_digest": (
                    fixture_loadable_skill_digest(package)
                    if schema_version == "skill-builder-trial-pack.v2"
                    else fixture_digest(loaded_skill)
                ),
                "fresh_context_identity": f"fresh-context-{index}",
                "tool_event_digest": fixture_digest(
                    trial_evidence_bytes(case_id, "tool-events")
                ),
                "output_digest": fixture_digest(
                    trial_evidence_bytes(case_id, "output")
                ),
                "before_target_manifest_digest": frozen_case[
                    "setup_manifest_digest"
                ],
                "after_target_manifest_digest": fixture_digest(
                    trial_evidence_bytes(case_id, "after-manifest")
                ),
                "filesystem_result_digest": fixture_digest(
                    trial_evidence_bytes(case_id, "filesystem-result")
                ),
                "verdict": "pass",
                "limitations": [],
            }
        )
    aggregate = fixture_canonical_bytes(
        {"case_ids": [case["case_id"] for case in cases]}
    )
    return {
        "schema_version": schema_version,
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "cases": cases,
        "coverage": [case["case_id"] for case in cases],
        "isolation_evidence": ["fresh context"],
        "leakage_checks": ["no hidden leakage"],
        "aggregate_manifest_digest": fixture_digest(aggregate),
        "status": "pass",
        "limitations": [],
    }


def review_input_provenance(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> dict[str, dict[str, str]]:
    run = state_root / "live" / workflow_id

    def accepted(
        artifact_type: str,
    ) -> tuple[str, dict[str, object], dict[str, object]]:
        artifact_id, _ = accepted_artifact(
            helper, state_root, workflow_id, artifact_type
        )
        envelope, manifest = helper._validate_envelope(run, artifact_id)
        return artifact_id, envelope, manifest

    def component(label: str, value: object) -> str:
        return fixture_digest(fixture_canonical_bytes({label: value}))

    def binding(
        artifact_id: str, envelope: dict[str, object], component_digest: str
    ) -> dict[str, str]:
        return {
            "artifact_id": artifact_id,
            "artifact_digest": str(envelope["envelope_digest"]),
            "component_digest": component_digest,
        }

    baseline_id, baseline_envelope, _ = accepted("baseline-report")
    contract_id, contract_envelope, _ = accepted("skill-contract")
    confirmation_id, confirmation_envelope, _ = accepted(
        "user-confirmation-record"
    )
    evaluation_id, evaluation_envelope, _ = accepted("evaluation-pack")
    candidate_id, candidate_envelope, candidate_manifest = accepted(
        "candidate-record"
    )
    trials_id, trials_envelope, trials_manifest = accepted("trial-pack")
    baseline = helper._artifact_payload_json(run, baseline_id)
    evaluation = helper._artifact_payload_json(run, evaluation_id)
    candidate = helper._artifact_payload_json(run, candidate_id)
    return {
        "confirmed_contract": binding(
            contract_id,
            contract_envelope,
            str(contract_envelope["payload_digest"]),
        ),
        "contract_confirmation": binding(
            confirmation_id,
            confirmation_envelope,
            str(confirmation_envelope["payload_digest"]),
        ),
        "host_rules": binding(
            baseline_id,
            baseline_envelope,
            component("host_rules", baseline["host_conventions"]),
        ),
        "candidate_revision": binding(
            candidate_id,
            candidate_envelope,
            component("candidate_revision", candidate["candidate_revision"]),
        ),
        "candidate_artifact": binding(
            candidate_id,
            candidate_envelope,
            str(candidate_envelope["payload_digest"]),
        ),
        "frozen_evaluation": binding(
            evaluation_id,
            evaluation_envelope,
            str(evaluation_envelope["payload_digest"]),
        ),
        "candidate_diff": binding(
            candidate_id,
            candidate_envelope,
            str(candidate["diff_digest"]),
        ),
        "candidate_result": binding(
            candidate_id,
            candidate_envelope,
            str(candidate["resulting_digest"]),
        ),
        "raw_trial_evidence": binding(
            trials_id,
            trials_envelope,
            str(trials_manifest["manifest_digest"]),
        ),
        "preserved_regressions": binding(
            baseline_id,
            baseline_envelope,
            component("preserved_regressions", baseline["preserved_regressions"]),
        ),
        "artifact_manifest": binding(
            candidate_id,
            candidate_envelope,
            str(candidate_manifest["manifest_digest"]),
        ),
        "evaluation_rubric": binding(
            evaluation_id,
            evaluation_envelope,
            str(evaluation["rubric_digest"]),
        ),
    }


def review_envelope_bindings(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> list[dict[str, str]]:
    required_types = (
        "resolution-record",
        "baseline-report",
        "skill-contract",
        "user-confirmation-record",
        "evaluation-pack",
        "candidate-record",
        "trial-pack",
    )
    return sorted(
        (
            {
                "artifact_id": artifact_id,
                "digest": digest,
            }
            for artifact_type in required_types
            for artifact_id, digest in [
                accepted_artifact(
                    helper, state_root, workflow_id, artifact_type
                )
            ]
        ),
        key=lambda binding: binding["artifact_id"],
    )


def review_payload(
    candidate_digest: str,
    *,
    revision: str = "candidate-1",
    fresh: bool = True,
    findings: list[dict[str, object]] | None = None,
    schema_version: str = "skill-builder-review.v2",
    input_artifacts: dict[str, object] | None = None,
) -> dict[str, object]:
    if input_artifacts is None:
        input_artifacts = (
            {"candidate": candidate_digest}
            if schema_version == "skill-builder-review.v1"
            else {
                "candidate_artifact": {
                    "artifact_id": "candidate",
                    "artifact_digest": candidate_digest,
                    "component_digest": candidate_digest,
                }
            }
        )
    return {
        "schema_version": schema_version,
        "reviewer_identity": "independent-reviewer",
        "independent": True,
        "read_only": True,
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "input_artifacts": input_artifacts,
        "access_check_evidence": [fixture_digest(REVIEW_ACCESS_BYTES)],
        "fresh": fresh,
        "contamination_check": "no implementation context disclosed",
        "valid": True,
        "findings": findings or [],
        "verdict": "ready",
        "reviewed_at": "2026-09-05T12:02:00Z",
    }


def conformance_payload(
    candidate_digest: str,
    *,
    gate_ids: tuple[str, ...] = CONFORMANCE_GATES,
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-conformance.v1",
        "candidate_digest": candidate_digest,
        "gates": {
            gate: {
                "status": "pass",
                "evidence_digests": [fixture_digest(CONFORMANCE_EVIDENCE_BYTES)],
                "affected_stage": "verified",
                "repair_state": "not-required",
                "release_blocking": True,
            }
            for gate in gate_ids
        },
    }


def scorecard_payload(
    *,
    candidate_digest: str,
    evaluation_digest: str,
    review_digest: str,
    triggering_score: int = 10,
    revision: str = "candidate-1",
    criteria_overrides: dict[str, dict[str, bool]] | None = None,
    schema_version: str = "skill-builder-scorecard.v2",
    review_findings: list[dict[str, object]] | None = None,
    criterion_evidence_overrides: dict[str, dict[str, object]] | None = None,
    trial_cases: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    overrides = criteria_overrides or {}
    evidence_overrides = criterion_evidence_overrides or {}
    if schema_version == "skill-builder-scorecard.v1":
        categories = [
            {
                "name": name,
                "score": triggering_score if name == "triggering" else 10,
                "criteria": overrides.get(
                    name, {criterion: True for criterion in SCORE_CRITERIA[name]}
                ),
                "frozen_parameter_identifiers": ["criterion-1"],
                "evidence_identifiers": ["evidence-1"],
                "related_findings": [],
                "repair_history": [],
            }
            for name in SCORE_CATEGORIES
        ]
    else:
        cases = (
            trial_payload(candidate_digest, revision)["cases"]
            if trial_cases is None
            else trial_cases
        )
        assert isinstance(cases, list)
        case_ids = sorted(case["case_id"] for case in cases)
        raw_artifact_digests = sorted({case["output_digest"] for case in cases})
        trial_receipt_ids = sorted(
            fixture_digest(fixture_canonical_bytes(case)) for case in cases
        )
        findings = review_findings or []
        categories = []
        for name in SCORE_CATEGORIES:
            criterion_states = overrides.get(
                name, {criterion: True for criterion in SCORE_CRITERIA[name]}
            )
            criterion_results: dict[str, object] = {}
            for criterion_id, passed in criterion_states.items():
                finding_ids = sorted(
                    {
                        fixture_digest(fixture_canonical_bytes(finding))
                        for finding in findings
                        if criterion_id in finding["affected_target_criteria"]
                    }
                )
                criterion_result = {
                    "passed": passed,
                    "frozen_parameter_identifiers": ["maximum"],
                    "case_ids": case_ids,
                    "raw_artifact_digests": raw_artifact_digests,
                    "trial_receipt_ids": trial_receipt_ids,
                    "review_finding_ids": finding_ids,
                    "candidate_revision": revision,
                    "review_artifact_id": "final-review",
                    "review_digest": review_digest,
                }
                criterion_result.update(evidence_overrides.get(criterion_id, {}))
                criterion_results[criterion_id] = criterion_result
            categories.append(
                {
                    "name": name,
                    "score": triggering_score if name == "triggering" else 10,
                    "criteria": criterion_results,
                    "repair_history": [],
                }
            )
    return {
        "schema_version": schema_version,
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "rubric_digest": fixture_digest(EVALUATION_RUBRIC_BYTES),
        "evaluation_digest": evaluation_digest,
        "review_digest": review_digest,
        "categories": categories,
    }


def verification_payload(
    candidate_digest: str,
    *,
    revision: str = "candidate-1",
    fresh: bool = True,
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-verification.v1",
        "verifier_identity": "independent-verifier",
        "independent": True,
        "read_only": True,
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "commands": [
            {
                "command": "python3 -m pytest",
                "exit_status": 0,
                "output_digest": fixture_digest(VERIFICATION_OUTPUT_BYTES),
            }
        ],
        "started_at": "2026-09-05T12:03:00Z",
        "ended_at": "2026-09-05T12:04:00Z",
        "before_manifest_digest": fixture_digest(VERIFICATION_MANIFEST_BYTES),
        "after_manifest_digest": fixture_digest(VERIFICATION_MANIFEST_BYTES),
        "fresh": fresh,
        "status": "pass",
        "conclusion": "fresh independent verification passed",
    }


def release_payload(
    *,
    target: str,
    candidate_digest: str,
    contract_digest: str,
    confirmation_digest: str,
    evaluation_digest: str,
    conformance_digest: str,
    scorecard_digest: str,
    review_digest: str,
    verification_digest: str,
    authorized_delivery_scope: list[str],
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-release.v1",
        "target_identity": target,
        "candidate_digest": candidate_digest,
        "candidate_revision": "candidate-1",
        "contract_digest": contract_digest,
        "confirmation_digest": confirmation_digest,
        "evaluation_digest": evaluation_digest,
        "conformance_digest": conformance_digest,
        "scorecard_digest": scorecard_digest,
        "review_digest": review_digest,
        "verification_digest": verification_digest,
        "retained_limitations": [],
        "release_evidence_manifest_digest": fixture_digest(RELEASE_EVIDENCE_BYTES),
        "authorized_delivery_scope": authorized_delivery_scope,
    }


def advance_with_artifact(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    artifact_id: str,
    artifact_type: str,
    event: str,
    destination_stage: str,
    payload: dict[str, object] | None = None,
    authority_event_digest: str | None = None,
) -> dict[str, object]:
    retained = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        payload=payload
        if payload is not None
        else stage_payload(helper, state_root, workflow_id, artifact_type),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=retained["sequence"],
        event=event,
        destination_stage=destination_stage,
        artifact_ids=[artifact_id],
        authority_event_digest=authority_event_digest,
        state_root=state_root,
    )


def build_candidate_stage(
    helper: ModuleType,
    tmp_path: Path,
    *,
    queued_targets: list[dict[str, str]] | None = None,
    granted_authority: dict[str, object] | None = None,
    candidate_schema_version: str = "skill-builder-candidate.v4",
    candidate_resulting_bytes: bytes | None = None,
    candidate_loaded_skill_bytes: bytes = CANDIDATE_SKILL_BYTES,
    candidate_claimed_loaded_skill_bytes: bytes | None = None,
    candidate_additional_skill_files: dict[str, bytes] | None = None,
    candidate_owned_paths: list[str] | None = None,
    legacy_candidate_v1: bool = False,
    historical_candidate_v2: bool = False,
    historical_candidate_v3: bool = False,
    retain_review_source_components: bool = False,
    omitted_review_source_component: str | None = None,
) -> tuple[Path, str, int, dict[str, object]]:
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=granted_authority or authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        queue=queued_targets,
        state_root=state_root,
    )
    workflow_id = started["workflow_id"]
    compatibility_modes = sum(
        (legacy_candidate_v1, historical_candidate_v2, historical_candidate_v3)
    )
    if compatibility_modes > 1:
        raise AssertionError("candidate compatibility modes are mutually exclusive")
    if legacy_candidate_v1:
        mark_run_as_legacy_candidate_v1(helper, state_root, workflow_id)
    elif historical_candidate_v2:
        mark_run_as_historical_candidate_v2(helper, state_root, workflow_id)
    elif historical_candidate_v3:
        mark_run_as_historical_candidate_v3(helper, state_root, workflow_id)
    sequence = 0
    for artifact_id, artifact_type, event, stage in (
        ("baseline", "baseline-report", "capture-baseline", "baseline"),
        ("research", "research-pack", "complete-research", "research"),
        ("sieve", "evidence-sieve", "sieve-evidence", "sieve"),
        ("design", "design-record", "accept-design", "design"),
    ):
        sequence = advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )["sequence"]
    contract = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="contract",
        artifact_type="skill-contract",
        payload=contract_payload(helper, state_root, workflow_id),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=contract["sequence"],
        event="accept-contract",
        destination_stage="contract",
        artifact_ids=["contract"],
        state_root=state_root,
    )["sequence"]
    authority_digest = "2" * 64
    confirmation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="confirmation",
        artifact_type="user-confirmation-record",
        payload=confirmation_payload(
            contract_id="contract",
            contract_digest=contract["artifact_digest"],
            target=target_identity(target)["canonical"],
            snapshot_digest=started["target_snapshot_digest"],
            authority_digest=authority_digest,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=confirmation["sequence"],
        event="confirm-contract",
        destination_stage="confirmed",
        artifact_ids=["confirmation"],
        authority_event_digest=authority_digest,
        state_root=state_root,
    )["sequence"]
    evaluation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="evaluation",
        artifact_type="evaluation-pack",
        payload=evaluation_payload(
            contract_digest=contract["artifact_digest"],
            confirmation_digest=confirmation["artifact_digest"],
            snapshot_digest=started["target_snapshot_digest"],
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
        retain_review_source_components=retain_review_source_components,
        omitted_review_source_component=omitted_review_source_component,
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=evaluation["sequence"],
        event="freeze-evaluation",
        destination_stage="evaluation",
        artifact_ids=["evaluation"],
        state_root=state_root,
    )["sequence"]
    candidate = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="candidate",
        artifact_type="candidate-record",
        payload=candidate_payload(
            tmp_path=tmp_path,
            candidate_id="candidate",
            revision="candidate-1",
            contract_digest=contract["artifact_digest"],
            confirmation_digest=confirmation["artifact_digest"],
            evaluation_digest=evaluation["artifact_digest"],
            snapshot_digest=started["target_snapshot_digest"],
            schema_version=candidate_schema_version,
            resulting_bytes=candidate_resulting_bytes,
            loaded_skill_bytes=candidate_loaded_skill_bytes,
            claimed_loaded_skill_bytes=candidate_claimed_loaded_skill_bytes,
            additional_skill_files=candidate_additional_skill_files,
            owned_paths=candidate_owned_paths,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
        retain_review_source_components=retain_review_source_components,
        candidate_resulting_bytes=candidate_resulting_bytes,
        omitted_review_source_component=omitted_review_source_component,
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=candidate["sequence"],
        event="accept-candidate",
        destination_stage="candidate",
        artifact_ids=["candidate"],
        state_root=state_root,
    )["sequence"]
    return state_root, workflow_id, sequence, candidate


def build_current_trials_stage(
    helper: ModuleType,
    tmp_path: Path,
    *,
    retain_review_source_components: bool = True,
    omitted_review_source_component: str | None = None,
) -> tuple[Path, str, int, dict[str, object]]:
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper,
        tmp_path,
        retain_review_source_components=retain_review_source_components,
        omitted_review_source_component=omitted_review_source_component,
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )["sequence"]
    return state_root, workflow_id, sequence, candidate


def build_verified_stage(
    helper: ModuleType,
    tmp_path: Path,
    *,
    triggering_score: int = 10,
    review_fresh: bool = True,
    verification_fresh: bool = True,
    queued_targets: list[dict[str, str]] | None = None,
    granted_authority: dict[str, object] | None = None,
    release_scope: list[str] | None = None,
    release_target: str | None = None,
    review_findings: list[dict[str, object]] | None = None,
    conformance_gate_ids: tuple[str, ...] | None = None,
    scorecard_criteria_overrides: dict[str, dict[str, bool]] | None = None,
    scorecard_evidence_overrides: dict[str, dict[str, object]] | None = None,
    scorecard_schema_version: str | None = None,
    candidate_schema_version: str = "skill-builder-candidate.v4",
    candidate_resulting_bytes: bytes | None = None,
    candidate_additional_skill_files: dict[str, bytes] | None = None,
    candidate_owned_paths: list[str] | None = None,
    trial_loaded_skill_bytes: bytes | None = None,
    trial_loaded_skill_files: dict[str, bytes] | None = None,
    legacy_candidate_v1: bool = False,
    historical_candidate_v2: bool = False,
    historical_candidate_v3: bool = False,
) -> tuple[Path, str, int, dict[str, object]]:
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper,
        tmp_path,
        queued_targets=queued_targets,
        granted_authority=granted_authority,
        candidate_schema_version=candidate_schema_version,
        candidate_resulting_bytes=candidate_resulting_bytes,
        candidate_additional_skill_files=candidate_additional_skill_files,
        candidate_owned_paths=candidate_owned_paths,
        legacy_candidate_v1=legacy_candidate_v1,
        historical_candidate_v2=historical_candidate_v2,
        historical_candidate_v3=historical_candidate_v3,
        retain_review_source_components=not (
            legacy_candidate_v1
            or historical_candidate_v2
            or historical_candidate_v3
        ),
    )
    trials_payload = trial_payload(
        candidate["artifact_digest"],
        loaded_skill_bytes=trial_loaded_skill_bytes,
        loaded_skill_files=trial_loaded_skill_files,
        schema_version=(
            "skill-builder-trial-pack.v1"
            if legacy_candidate_v1
            or historical_candidate_v2
            or historical_candidate_v3
            else "skill-builder-trial-pack.v2"
        ),
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trials_payload,
        input_bindings=current_bindings(helper, state_root, workflow_id),
        trial_loaded_skill_bytes=trial_loaded_skill_bytes,
        trial_loaded_skill_files=trial_loaded_skill_files,
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )["sequence"]
    historical_review = (
        legacy_candidate_v1
        or historical_candidate_v2
        or historical_candidate_v3
    )
    resolved_conformance_gate_ids = conformance_gate_ids
    if resolved_conformance_gate_ids is None:
        resolved_conformance_gate_ids = (
            LEGACY_CONFORMANCE_GATES
            if legacy_candidate_v1
            else CONFORMANCE_GATES
        )
    resolved_scorecard_criteria = scorecard_criteria_overrides
    if (
        resolved_scorecard_criteria is None
        and legacy_candidate_v1
    ):
        resolved_scorecard_criteria = {
            category: {"criterion-1": True} for category in SCORE_CATEGORIES
        }
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="final-review",
        artifact_type="review-record",
        payload=review_payload(
            candidate["artifact_digest"],
            fresh=review_fresh,
            findings=review_findings,
            schema_version=(
                "skill-builder-review.v1"
                if historical_review
                else "skill-builder-review.v2"
            ),
            input_artifacts=(
                None
                if historical_review
                else review_input_provenance(helper, state_root, workflow_id)
            ),
        ),
        input_bindings=(
            current_bindings(helper, state_root, workflow_id)
            if historical_review
            else review_envelope_bindings(helper, state_root, workflow_id)
        ),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=review["sequence"],
        event="accept-review",
        destination_stage="reviewed",
        artifact_ids=["final-review"],
        state_root=state_root,
    )["sequence"]
    conformance = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="conformance",
        artifact_type="builder-run-conformance-ledger",
        payload=conformance_payload(
            candidate["artifact_digest"],
            gate_ids=resolved_conformance_gate_ids,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    scorecard = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=conformance["sequence"],
        artifact_id="scores",
        artifact_type="target-scorecard",
        payload=scorecard_payload(
            candidate_digest=candidate["artifact_digest"],
            evaluation_digest=helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["evaluation"]["digest"],
            review_digest=review["artifact_digest"],
            triggering_score=triggering_score,
            criteria_overrides=resolved_scorecard_criteria,
            schema_version=(
                scorecard_schema_version
                if scorecard_schema_version is not None
                else (
                    "skill-builder-scorecard.v1"
                    if legacy_candidate_v1 or historical_candidate_v2
                    else "skill-builder-scorecard.v2"
                )
            ),
            review_findings=review_findings,
            criterion_evidence_overrides=scorecard_evidence_overrides,
            trial_cases=trials_payload["cases"],
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=scorecard["sequence"],
        event="accept-scores",
        destination_stage="scored",
        artifact_ids=["conformance", "scores"],
        state_root=state_root,
    )["sequence"]
    verification = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="verification",
        artifact_type="verification-record",
        payload=verification_payload(
            candidate["artifact_digest"], fresh=verification_fresh
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=verification["sequence"],
        event="accept-verification",
        destination_stage="verified",
        artifact_ids=["verification"],
        state_root=state_root,
    )["sequence"]
    release = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="release",
        artifact_type="release-record",
        payload=release_payload(
            target=release_target
            if release_target is not None
            else helper.load_run(workflow_id=workflow_id, state_root=state_root)[
                "target_identity"
            ]["canonical"],
            candidate_digest=candidate["artifact_digest"],
            contract_digest=helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["contract"]["digest"],
            confirmation_digest=helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["confirmation"]["digest"],
            evaluation_digest=helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["evaluation"]["digest"],
            conformance_digest=conformance["artifact_digest"],
            scorecard_digest=scorecard["artifact_digest"],
            review_digest=review["artifact_digest"],
            verification_digest=verification["artifact_digest"],
            authorized_delivery_scope=release_scope
            if release_scope is not None
            else ["installation:codex:personal:sample-skill"],
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    return state_root, workflow_id, release["sequence"], release


def test_canonical_digest_excludes_one_top_level_field_and_uses_one_lf() -> None:
    """A serializer regression must not change cross-runtime durable digests."""
    helper = load_helper()
    value = {
        "z": "é",
        "receipt_digest": "not-part-of-the-hash",
        "a": [True, None, 1],
    }

    encoded = helper.canonical_json_bytes(value, "receipt_digest")

    assert encoded == b'{"a":[true,null,1],"z":"\xc3\xa9"}\n'
    assert (
        helper.canonical_digest(value, "receipt_digest")
        == "8f0c5813b5110d117e19fbf2415e73a97e731374b1d90264faa4fee8ab3e7d0e"
    )


def test_initialize_non_git_create_run_is_private_and_bound_to_absence(tmp_path: Path) -> None:
    """Invented Git identity or advisory-only absence evidence could overwrite a target."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"

    receipt = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    loaded = helper.load_run(
        workflow_id=receipt["workflow_id"], state_root=state_root
    )

    assert receipt["sequence"] == 0
    assert receipt["stage"] == "resolved"
    assert loaded["target_identity"] == target_identity(target)
    assert loaded["mode"]["name"] == "create"
    assert loaded["target_snapshot"]["exists"] is False
    assert loaded["git_identity"] == {"present": False}
    assert loaded["head_sequence"] == 0
    assert len(loaded["head_transition_digest"]) == 64
    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_root / "live").stat().st_mode) == 0o700
    assert stat.S_IMODE((state_root / "tombstones").stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (state_root / "live" / receipt["workflow_id"]).stat().st_mode
    ) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (state_root / "live" / receipt["workflow_id"]).rglob("*")
        if path.is_file()
    )
    assert not any(key in loaded["git_identity"] for key in ("repository", "branch", "commit"))


def test_initialize_git_improve_run_binds_exact_target_manifest(tmp_path: Path) -> None:
    """A substituted target or Git revision must not inherit improve evidence."""
    helper = load_helper()
    repository, target, head = git_repository(tmp_path)
    expected_manifest = helper.snapshot_target(target)

    receipt = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="improve",
        authority=authority(),
        target_manifest=expected_manifest,
        state_root=tmp_path / "state",
    )
    loaded = helper.load_run(
        workflow_id=receipt["workflow_id"], state_root=tmp_path / "state"
    )

    assert loaded["mode"]["name"] == "improve"
    assert loaded["target_snapshot"]["exists"] is True
    assert loaded["target_snapshot"]["manifest"] == expected_manifest
    assert loaded["target_snapshot"]["manifest_digest"] == helper.canonical_digest(
        expected_manifest, "manifest_digest"
    )
    assert loaded["git_identity"] == {
        "present": True,
        "repository": str(repository.resolve()),
        "branch": "feature/state",
        "commit": head,
        "dirty_state_digest": hashlib.sha256(b"").hexdigest(),
    }


def test_mode_requires_absent_create_and_existing_improve_target(
    tmp_path: Path,
) -> None:
    """Mode evidence must describe the exact target rather than caller intent alone."""
    helper = load_helper()
    existing = tmp_path / "existing-skill"
    existing.mkdir()
    (existing / "SKILL.md").write_text("# Existing\n", encoding="utf-8")
    create_state = tmp_path / "create-state"
    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(existing),
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(existing)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=create_state,
        )
    except helper.RunStateError as error:
        assert "absent" in str(error)
    else:
        raise AssertionError("create mode accepted an existing exact target")
    assert not create_state.exists()

    missing = tmp_path / "missing-skill"
    improve_state = tmp_path / "improve-state"
    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(missing),
            mode="improve",
            authority=authority(),
            target_manifest={"claimed": "existing"},
            git_identity={"present": False},
            state_root=improve_state,
        )
    except helper.RunStateError as error:
        assert "existing" in str(error)
    else:
        raise AssertionError("improve mode accepted a missing exact target")
    assert not improve_state.exists()


def test_discover_run_uses_complete_canonical_host_and_target_identity(tmp_path: Path) -> None:
    """Name-only discovery could resume evidence belonging to another host target."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    created = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )

    discovered = helper.discover_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        state_root=state_root,
    )

    assert discovered["workflow_id"] == created["workflow_id"]
    assert discovered["sequence"] == 0
    wrong_host = host_identity(tmp_path)
    wrong_host["canonical_id"] = "codex:other-host"
    try:
        helper.discover_run(
            host_identity=wrong_host,
            target_identity=target_identity(target),
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "not found" in str(error)
    else:
        raise AssertionError("discovery accepted a substituted host identity")


def test_retain_artifact_preserves_exact_bounded_bytes_and_immutable_envelope(
    tmp_path: Path,
) -> None:
    """Rewriting payloads or omitting raw files could fabricate later evidence."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    before = helper.load_run(workflow_id=started["workflow_id"], state_root=state_root)

    accepted = helper.retain_artifact(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        artifact_id="baseline",
        artifact_type="resolution-record",
        files={"prompt.txt": b"line\r\n", "nested/output.json": b"{}\n"},
        primary_path="prompt.txt",
        producer="main-agent",
        input_bindings=[
            {
                "artifact_id": "resolution",
                "digest": before["artifact_index"]["resolution"]["digest"],
            }
        ],
        limitations=[],
        state_root=state_root,
    )
    loaded = helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )
    artifact = state_root / "live" / started["workflow_id"] / "artifacts" / "baseline"
    envelope = json.loads((artifact / "envelope.json").read_text(encoding="utf-8"))
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))

    assert accepted["sequence"] == 1
    assert loaded["head_sequence"] == 1
    assert loaded["artifact_index"]["baseline"]["derived_status"] == "accepted"
    assert (artifact / "raw" / "prompt.txt").read_bytes() == b"line\r\n"
    assert {entry["path"] for entry in manifest["entries"]} == {
        "artifacts/baseline/raw/prompt.txt",
        "artifacts/baseline/raw/nested/output.json",
    }
    assert manifest["observed_item_count"] == 2
    assert manifest["observed_byte_count"] == 9
    assert envelope["manifest_digest"] == helper.canonical_digest(
        manifest, "manifest_digest"
    )
    assert envelope["envelope_digest"] == helper.canonical_digest(
        envelope, "envelope_digest"
    )
    try:
        helper.retain_artifact(
            workflow_id=started["workflow_id"],
            expected_sequence=1,
            artifact_id="baseline",
            artifact_type="resolution-record",
            files={"prompt.txt": b"replacement"},
            primary_path="prompt.txt",
            producer="main-agent",
            input_bindings=[],
            limitations=[],
            state_root=state_root,
        )
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("an immutable artifact was overwritten")
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 1


def test_artifact_bounds_and_escaping_paths_fail_without_partial_state(
    tmp_path: Path,
) -> None:
    """Oversized or escaping collections must not leave artifacts or receipts behind."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    cases = (
        (
            "too-many",
            {f"item-{index:03d}": b"x" for index in range(helper.MAX_ARTIFACT_ITEMS + 1)},
            "item-000",
        ),
        (
            "too-large",
            {"payload.bin": b"x" * (helper.MAX_ARTIFACT_BYTES + 1)},
            "payload.bin",
        ),
        ("escape", {"../outside.txt": b"x"}, "../outside.txt"),
    )

    for artifact_id, files, primary_path in cases:
        try:
            helper.retain_artifact(
                workflow_id=started["workflow_id"],
                expected_sequence=0,
                artifact_id=artifact_id,
                artifact_type="baseline-report",
                files=files,
                primary_path=primary_path,
                producer="main-agent",
                input_bindings=[],
                limitations=[],
                state_root=state_root,
            )
        except helper.RunStateError:
            pass
        else:
            raise AssertionError(f"unsafe artifact collection {artifact_id} was accepted")

    run = state_root / "live" / started["workflow_id"]
    assert {path.name for path in (run / "artifacts").iterdir()} == {"resolution"}
    assert {path.name for path in (run / "receipts").iterdir()} == {"00000000.json"}
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 0
    assert not (tmp_path / "outside.txt").exists()


def test_transition_chain_is_contiguous_and_recovers_replaceable_index(
    tmp_path: Path,
) -> None:
    """A damaged current pointer must not erase or invent immutable run history."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    baseline_bytes = helper.canonical_json_bytes(
        valid_create_baseline_payload(helper, state_root, started["workflow_id"])
    )
    retained = helper.retain_artifact(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        files={"baseline.json": baseline_bytes},
        primary_path="baseline.json",
        producer="main-agent",
        input_bindings=current_bindings(
            helper, state_root, started["workflow_id"]
        ),
        limitations=[],
        state_root=state_root,
    )
    advanced = helper.transition_run(
        workflow_id=started["workflow_id"],
        expected_sequence=retained["sequence"],
        event="capture-baseline",
        destination_stage="baseline",
        artifact_ids=["baseline"],
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    receipt_zero = json.loads((run / "receipts" / "00000000.json").read_text())
    receipt_one = json.loads((run / "receipts" / "00000001.json").read_text())
    receipt_two = json.loads((run / "receipts" / "00000002.json").read_text())

    assert advanced["sequence"] == 2
    assert receipt_one["prior_receipt_digest"] == receipt_zero["receipt_digest"]
    assert receipt_two["prior_receipt_digest"] == receipt_one["receipt_digest"]
    assert receipt_two["source_stage"] == "resolved"
    assert receipt_two["destination_stage"] == "baseline"
    (run / "current.json").write_text("{broken", encoding="utf-8")
    recovered = helper.recover_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )

    assert recovered["sequence"] == 2
    assert recovered["stage"] == "baseline"
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_transition_digest"] == receipt_two["receipt_digest"]


def test_payload_tampering_and_receipt_gaps_fail_closed(tmp_path: Path) -> None:
    """Neither a stale index nor retained hashes may bridge broken immutable evidence."""
    helper = load_helper()
    for case in ("payload", "gap"):
        case_root = tmp_path / case
        state_root = case_root / "state"
        target = case_root / "skills" / "sample-skill"
        started = helper.initialize_run(
            host_identity=host_identity(case_root),
            target_identity=target_identity(target),
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=state_root,
        )
        retain_json(
            helper,
            state_root=state_root,
            workflow_id=started["workflow_id"],
            sequence=0,
            artifact_id="baseline",
            artifact_type="baseline-report",
            payload=valid_create_baseline_payload(
                helper, state_root, started["workflow_id"]
            ),
        )
        run = state_root / "live" / started["workflow_id"]
        if case == "payload":
            payload = run / "artifacts" / "baseline" / "raw" / "record.json"
            payload.write_bytes(b'{"baseline":"forged"}\n')
        else:
            (run / "receipts" / "00000001.json").rename(
                run / "receipts" / "00000002.json"
            )
        (run / "current.json").write_text("{broken", encoding="utf-8")

        try:
            helper.recover_run(
                workflow_id=started["workflow_id"], state_root=state_root
            )
        except helper.RunStateError as error:
            assert any(
                word in str(error)
                for word in ("digest", "payload", "gap", "fork", "chain")
            )
        else:
            raise AssertionError(f"recovery accepted {case} tampering")


def test_pause_and_resume_are_validated_cas_transitions(tmp_path: Path) -> None:
    """Pause labels or stale resumes must not bypass the immutable transition chain."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )

    paused = helper.pause_run(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        state_root=state_root,
    )
    assert paused["sequence"] == 1
    assert paused["stage"] == "paused"
    try:
        helper.transition_run(
            workflow_id=started["workflow_id"],
            expected_sequence=1,
            event="capture-baseline",
            destination_stage="baseline",
            artifact_ids=["resolution"],
            state_root=state_root,
        )
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("paused run advanced through an ordinary transition")
    resumed = helper.resume_run(
        workflow_id=started["workflow_id"],
        expected_sequence=1,
        state_root=state_root,
    )
    assert resumed["sequence"] == 2
    assert resumed["stage"] == "resolved"
    try:
        helper.resume_run(
            workflow_id=started["workflow_id"],
            expected_sequence=1,
            state_root=state_root,
        )
    except helper.RevisionConflict:
        pass
    else:
        raise AssertionError("stale resume appended a receipt")
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 2


def test_target_change_blocks_mutation_without_appending_a_receipt(tmp_path: Path) -> None:
    """A recomputed snapshot mismatch must fail before any lifecycle state advances."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    current_before = (run / "current.json").read_bytes()
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# Unexpected\n", encoding="utf-8")

    try:
        helper.pause_run(
            workflow_id=started["workflow_id"],
            expected_sequence=0,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "target changed" in str(error)
    else:
        raise AssertionError("a changed target advanced the lifecycle chain")
    assert {path.name for path in (run / "receipts").iterdir()} == {"00000000.json"}
    assert (run / "current.json").read_bytes() == current_before


def test_candidate_entry_requires_four_current_matching_bindings(tmp_path: Path) -> None:
    """A candidate must not start from stale intent, evaluation, or target evidence."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    workflow_id = started["workflow_id"]
    sequence = 0
    for artifact_id, artifact_type, event, stage in (
        ("baseline", "baseline-report", "capture-baseline", "baseline"),
        ("research", "research-pack", "complete-research", "research"),
        ("sieve", "evidence-sieve", "sieve-evidence", "sieve"),
        ("design", "design-record", "accept-design", "design"),
    ):
        advanced = advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )
        sequence = advanced["sequence"]

    contract = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="contract",
        artifact_type="skill-contract",
        payload=contract_payload(helper, state_root, workflow_id),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=contract["sequence"],
        event="accept-contract",
        destination_stage="contract",
        artifact_ids=["contract"],
        state_root=state_root,
    )
    confirmation_event = "1" * 64
    confirmation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=advanced["sequence"],
        artifact_id="confirmation",
        artifact_type="user-confirmation-record",
        payload=confirmation_payload(
            contract_id="contract",
            contract_digest=contract["artifact_digest"],
            target=target_identity(target)["canonical"],
            snapshot_digest=started["target_snapshot_digest"],
            authority_digest=confirmation_event,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    confirmed = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=confirmation["sequence"],
        event="confirm-contract",
        destination_stage="confirmed",
        artifact_ids=["confirmation"],
        authority_event_digest=confirmation_event,
        state_root=state_root,
    )
    evaluation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=confirmed["sequence"],
        artifact_id="evaluation",
        artifact_type="evaluation-pack",
        payload=evaluation_payload(
            contract_digest=contract["artifact_digest"],
            confirmation_digest=confirmation["artifact_digest"],
            snapshot_digest=started["target_snapshot_digest"],
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    evaluated = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=evaluation["sequence"],
        event="freeze-evaluation",
        destination_stage="evaluation",
        artifact_ids=["evaluation"],
        state_root=state_root,
    )
    valid_bindings = candidate_payload(
        tmp_path=tmp_path,
        candidate_id="candidate-boundary",
        revision="candidate-1",
        contract_digest=contract["artifact_digest"],
        confirmation_digest=confirmation["artifact_digest"],
        evaluation_digest=evaluation["artifact_digest"],
        snapshot_digest=started["target_snapshot_digest"],
    )
    sequence = evaluated["sequence"]
    for field in (
        "contract_digest",
        "confirmation_digest",
        "evaluation_digest",
        "target_snapshot_digest",
    ):
        payload = dict(valid_bindings)
        payload[field] = "f" * 64
        artifact_id = f"candidate-bad-{field.removesuffix('_digest')}"
        bad_candidate = retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type="candidate-record",
            payload=payload,
            input_bindings=current_bindings(helper, state_root, workflow_id),
        )
        sequence = bad_candidate["sequence"]
        try:
            helper.transition_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                event="accept-candidate",
                destination_stage="candidate",
                artifact_ids=[artifact_id],
                state_root=state_root,
            )
        except helper.RunStateError:
            pass
        else:
            raise AssertionError(f"candidate entry accepted a mismatched {field}")
        assert helper.load_run(
            workflow_id=workflow_id, state_root=state_root
        )["head_sequence"] == sequence

    good_candidate = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="candidate-good",
        artifact_type="candidate-record",
        payload=candidate_payload(
            tmp_path=tmp_path,
            candidate_id="candidate-good",
            revision="candidate-2",
            contract_digest=contract["artifact_digest"],
            confirmation_digest=confirmation["artifact_digest"],
            evaluation_digest=evaluation["artifact_digest"],
            snapshot_digest=started["target_snapshot_digest"],
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    accepted = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=good_candidate["sequence"],
        event="accept-candidate",
        destination_stage="candidate",
        artifact_ids=["candidate-good"],
        state_root=state_root,
    )
    assert accepted["stage"] == "candidate"


def test_candidate_change_appends_downstream_invalidation_without_rewrite(
    tmp_path: Path,
) -> None:
    """Candidate edits must not leave stale trials, review, scores, or release usable."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    downstream = (
        ("trials-old", "trial-pack"),
        ("review-old", "review-record"),
        ("conformance-old", "builder-run-conformance-ledger"),
        ("scores-old", "target-scorecard"),
        ("verification-old", "verification-record"),
        ("release-old", "release-record"),
    )
    retained_digests: dict[str, str] = {}
    for artifact_id, artifact_type in downstream:
        current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        evaluation_digest = current["artifact_index"]["evaluation"]["digest"]
        payloads = {
            "trial-pack": trial_payload(candidate["artifact_digest"]),
            "review-record": review_payload(candidate["artifact_digest"]),
            "builder-run-conformance-ledger": conformance_payload(
                candidate["artifact_digest"]
            ),
            "target-scorecard": scorecard_payload(
                candidate_digest=candidate["artifact_digest"],
                evaluation_digest=evaluation_digest,
                review_digest=retained_digests.get("review-record", "5" * 64),
            ),
            "verification-record": verification_payload(
                candidate["artifact_digest"]
            ),
            "release-record": release_payload(
                target=current["target_identity"]["canonical"],
                candidate_digest=candidate["artifact_digest"],
                contract_digest=current["artifact_index"]["contract"]["digest"],
                confirmation_digest=current["artifact_index"]["confirmation"][
                    "digest"
                ],
                evaluation_digest=evaluation_digest,
                conformance_digest=retained_digests.get(
                    "builder-run-conformance-ledger", "a" * 64
                ),
                scorecard_digest=retained_digests.get("target-scorecard", "b" * 64),
                review_digest=retained_digests.get("review-record", "c" * 64),
                verification_digest=retained_digests.get(
                    "verification-record", "d" * 64
                ),
                authorized_delivery_scope=[
                    "installation:codex:personal:sample-skill"
                ],
            ),
        }
        retained = retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            payload=payloads[artifact_type],
            input_bindings=current_bindings(helper, state_root, workflow_id),
        )
        sequence = retained["sequence"]
        retained_digests[artifact_type] = retained["artifact_digest"]
    candidate_envelope = (
        state_root
        / "live"
        / workflow_id
        / "artifacts"
        / "candidate"
        / "envelope.json"
    )
    immutable_before = candidate_envelope.read_bytes()

    invalidated = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        change_kind="candidate",
        changed_artifact_id="candidate",
        reason="candidate content changed",
        state_root=state_root,
    )
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)

    assert invalidated["stage"] == "evaluation"
    assert candidate_envelope.read_bytes() == immutable_before
    assert loaded["artifact_index"]["candidate"]["derived_status"] == "superseded"
    for artifact_id, _ in downstream:
        assert loaded["artifact_index"][artifact_id]["derived_status"] == "invalidated"


def test_finalization_rejects_false_all_ten_scorecard_without_transition(
    tmp_path: Path,
) -> None:
    """A score label cannot hide a category below the required independent 10."""
    helper = load_helper()
    state_root, workflow_id, sequence, release = build_verified_stage(
        helper, tmp_path, triggering_score=9
    )

    try:
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "score" in str(error)
    else:
        raise AssertionError("finalization accepted a category below 10")
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert loaded["head_sequence"] == sequence
    assert loaded["stage"] == "verified"
    assert (state_root / "live" / workflow_id).is_dir()


def test_finalization_rejects_stale_review_and_verification(tmp_path: Path) -> None:
    """A release cannot reuse an unfresh review or verification for its revision."""
    helper = load_helper()
    cases = (
        ("review", {"review_fresh": False}),
        ("verification", {"verification_fresh": False}),
    )
    for label, options in cases:
        state_root, workflow_id, sequence, _ = build_verified_stage(
            helper, tmp_path / label, **options
        )
        try:
            helper.finalize_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                release_artifact_id="release",
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert label in str(error)
        else:
            raise AssertionError(f"finalization accepted stale {label} evidence")
        loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        assert loaded["head_sequence"] == sequence
        assert loaded["stage"] == "verified"


def test_finalize_delivery_and_authorized_cleanup_leave_durable_tombstone(
    tmp_path: Path,
) -> None:
    """Cleanup must never precede exact-revision delivery evidence and durable authority."""
    helper = load_helper()
    state_root, workflow_id, sequence, release = build_verified_stage(
        helper, tmp_path
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    assert finalized["stage"] == "finalized"
    assert run.is_dir()
    candidate_revision = "candidate-1"
    delivery_authority_event = b"accepted installation authority\n"
    delivery_acceptance_evidence = b"installed destination verification\n"
    delivery_authority = fixture_digest(delivery_authority_event)
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": candidate_revision,
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [
                fixture_digest(delivery_acceptance_evidence)
            ],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        authority_event=delivery_authority_event,
        evidence_files={
            "destination-verification.bin": delivery_acceptance_evidence
        },
        state_root=state_root,
    )
    cleanup_authority_event = b"authorized cleanup event\n"
    cleanup_authority = fixture_digest(cleanup_authority_event)
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=cleanup_authority,
        authority_event=cleanup_authority_event,
        actor="user",
        state_root=state_root,
    )
    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=authorized["sequence"],
            acknowledge_cleanup=False,
            state_root=state_root,
        )
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("cleanup proceeded without explicit CLI-style acknowledgement")
    assert run.is_dir()
    assert not (state_root / "tombstones" / f"{workflow_id}.json").exists()

    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    tombstone_path = state_root / "tombstones" / f"{workflow_id}.json"
    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))

    assert cleaned["stage"] == "cleaned"
    assert not run.exists()
    assert tombstone_path.is_file()
    assert stat.S_IMODE(tombstone_path.stat().st_mode) == 0o600
    assert tombstone["workflow_id"] == workflow_id
    assert tombstone["final_transition_receipt_digest"] == authorized["receipt_digest"]
    assert tombstone["cleanup_authority_event_digest"] == cleanup_authority
    assert tombstone["tombstone_digest"] == helper.canonical_digest(
        tombstone, "tombstone_digest"
    )
    recovered = helper.recover_run(workflow_id=workflow_id, state_root=state_root)
    assert recovered["stage"] == "cleaned"
    assert recovered["tombstone_digest"] == tombstone["tombstone_digest"]


def test_cleanup_rejects_missing_accepted_delivery_or_authority(
    tmp_path: Path,
) -> None:
    """Acknowledgement alone must not authorize deletion of retained live evidence."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    authority_event = b"accepted installation authority\n"
    acceptance_evidence = b"installed destination verification\n"
    authority_digest = fixture_digest(authority_event)
    rejected_delivery = {
        "action": "installation",
        "destination_identity": "codex:personal:sample-skill",
        "finalized_revision": "candidate-1",
        "resulting_destination_digest": "c" * 64,
        "acceptance_evidence": [fixture_digest(acceptance_evidence)],
        "user_authority_event_digest": authority_digest,
        "actor": "main-agent",
        "accepted": False,
    }
    try:
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            delivery=rejected_delivery,
            authority_event_digest=authority_digest,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "accepted" in str(error)
    else:
        raise AssertionError("an unaccepted delivery record advanced the run")

    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            acknowledge_cleanup=True,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "delivery" in str(error)
    else:
        raise AssertionError("cleanup proceeded before accepted delivery")

    accepted_delivery = dict(rejected_delivery)
    accepted_delivery["accepted"] = True
    accepted_delivery["resulting_destination_digest"] = install_target_and_digest(
        helper, state_root, workflow_id
    )
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery=accepted_delivery,
        authority_event_digest=authority_digest,
        authority_event=authority_event,
        evidence_files={"destination-verification.bin": acceptance_evidence},
        state_root=state_root,
    )
    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=delivery["sequence"],
            acknowledge_cleanup=True,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "authority" in str(error)
    else:
        raise AssertionError("cleanup proceeded without recorded user authority")
    assert (state_root / "live" / workflow_id).is_dir()
    assert not (state_root / "tombstones" / f"{workflow_id}.json").exists()


def test_cleanup_tombstone_publication_failure_deletes_nothing(
    tmp_path: Path,
) -> None:
    """The exact live boundary must survive when its parent tombstone cannot publish."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority_event = b"accepted installation authority\n"
    delivery_acceptance_evidence = b"installed destination verification\n"
    delivery_authority = fixture_digest(delivery_authority_event)
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [
                fixture_digest(delivery_acceptance_evidence)
            ],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        authority_event=delivery_authority_event,
        evidence_files={
            "destination-verification.bin": delivery_acceptance_evidence
        },
        state_root=state_root,
    )
    cleanup_authority_event = b"authorized cleanup event\n"
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=fixture_digest(cleanup_authority_event),
        authority_event=cleanup_authority_event,
        actor="user",
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    before = {
        path.relative_to(run).as_posix(): path.read_bytes()
        for path in run.rglob("*")
        if path.is_file()
    }
    original_exclusive_json = helper._exclusive_json

    def fail_tombstone(path: Path, value: dict[str, object]) -> None:
        if path.parent == state_root / "tombstones":
            raise helper.RunStateError("injected tombstone publication failure")
        original_exclusive_json(path, value)

    helper._exclusive_json = fail_tombstone
    try:
        try:
            helper.cleanup_run(
                workflow_id=workflow_id,
                expected_sequence=authorized["sequence"],
                acknowledge_cleanup=True,
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "tombstone" in str(error)
        else:
            raise AssertionError("cleanup deleted state after tombstone publication failed")
    finally:
        helper._exclusive_json = original_exclusive_json

    assert run.is_dir()
    for relative, payload in before.items():
        assert (run / relative).read_bytes() == payload
    assert not (state_root / "tombstones" / f"{workflow_id}.json").exists()


def test_cli_help_strict_json_xdg_default_and_success_receipts(tmp_path: Path) -> None:
    """An undocumented or permissive CLI would force callers to edit durable state."""
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg")

    help_result = subprocess.run(
        [sys.executable, str(HELPER), "--help"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert help_result.returncode == 0
    for command in (
        "initialize",
        "discover",
        "load",
        "retain",
        "transition",
        "invalidate",
        "pause",
        "resume",
        "recover",
        "finalize",
        "deliver",
        "cleanup-authority",
        "cleanup",
    ):
        assert command in help_result.stdout
    target = tmp_path / "skills" / "sample-skill"
    initialize_payload = {
        "host_identity": host_identity(tmp_path),
        "target_identity": target_identity(target),
        "mode": "create",
        "authority": authority(),
        "absence_evidence": {"searched": [str(target)], "exists": False},
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }
    initialized = subprocess.run(
        [sys.executable, str(HELPER), "initialize"],
        input=json.dumps(initialize_payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    receipt = json.loads(initialized.stdout)
    assert receipt["operation"] == "initialize"
    assert receipt["sequence"] == 0
    default_root = tmp_path / "xdg" / "codex-dev-flow" / "skill-builder"
    assert (default_root / "live" / receipt["workflow_id"]).is_dir()
    before = {
        path.relative_to(default_root).as_posix(): path.read_bytes()
        for path in default_root.rglob("*")
        if path.is_file()
    }
    malformed = subprocess.run(
        [sys.executable, str(HELPER), "load"],
        input='{ "workflow_id": "first", "workflow_id": "second" }',
        text=True,
        capture_output=True,
        env=env,
    )
    assert malformed.returncode != 0
    assert malformed.stderr.strip()
    after = {
        path.relative_to(default_root).as_posix(): path.read_bytes()
        for path in default_root.rglob("*")
        if path.is_file()
    }
    assert after == before
    loaded = subprocess.run(
        [sys.executable, str(HELPER), "load"],
        input=json.dumps({"workflow_id": receipt["workflow_id"]}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert json.loads(loaded.stdout)["head_transition_digest"] == receipt["receipt_digest"]
    bad_argument = subprocess.run(
        [sys.executable, str(HELPER), "initialize", "--unknown"],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
    )
    assert bad_argument.returncode != 0


def test_state_root_rejects_weak_permissions_symlinks_and_relative_escape(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Trusted state boundaries must fail closed instead of repairing unsafe paths."""
    helper = load_helper()
    target = tmp_path / "skills" / "sample-skill"
    arguments = {
        "host_identity": host_identity(tmp_path),
        "target_identity": target_identity(target),
        "mode": "create",
        "authority": authority(),
        "absence_evidence": {"searched": [str(target)], "exists": False},
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }
    weak = tmp_path / "weak-state"
    weak.mkdir(mode=0o755)
    os.chmod(weak, 0o755)
    try:
        helper.initialize_run(state_root=weak, **arguments)
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("weak existing state root was silently accepted")
    assert stat.S_IMODE(weak.stat().st_mode) == 0o755
    assert list(weak.iterdir()) == []

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked-state"
    linked.symlink_to(outside, target_is_directory=True)
    try:
        helper.initialize_run(state_root=linked, **arguments)
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("symlinked state root was followed")
    assert list(outside.iterdir()) == []

    original = Path.cwd()
    os.chdir(tmp_path)
    try:
        try:
            helper.initialize_run(state_root=Path("../escape"), **arguments)
        except helper.RunStateError:
            pass
        else:
            raise AssertionError("relative escaping state root was accepted")
    finally:
        os.chdir(original)


def test_state_root_must_be_outside_target_and_target_repository(
    tmp_path: Path,
) -> None:
    """Helper state inside a target can falsify snapshots or be deleted with the target."""
    helper = load_helper()
    absent_target = tmp_path / "absent-skill"
    nested_state = absent_target / ".state"
    create_arguments = {
        "host_identity": host_identity(tmp_path),
        "target_identity": target_identity(absent_target),
        "mode": "create",
        "authority": authority(),
        "absence_evidence": {
            "searched": [str(absent_target)],
            "exists": False,
        },
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }

    try:
        helper.initialize_run(state_root=nested_state, **create_arguments)
    except helper.RunStateError as error:
        assert "state root" in str(error) or "target" in str(error)
    else:
        raise AssertionError("state root inside an absent target was accepted")
    assert not absent_target.exists()

    repository, existing_target, _ = git_repository(tmp_path / "git-case")
    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(existing_target),
            mode="improve",
            authority=authority(),
            target_manifest=helper.snapshot_target(existing_target),
            state_root=repository / ".helper-state",
        )
    except helper.RunStateError as error:
        assert "state root" in str(error) or "repository" in str(error)
    else:
        raise AssertionError("state root inside the target repository was accepted")
    assert not (repository / ".helper-state").exists()


def test_recovery_rejects_rehashed_unsupported_manifest_schema(tmp_path: Path) -> None:
    """Digest recomputation must not turn an unsupported artifact schema into truth."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    retained = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        payload=valid_create_baseline_payload(
            helper, state_root, started["workflow_id"]
        ),
    )
    run = state_root / "live" / started["workflow_id"]
    artifact = run / "artifacts" / "baseline"
    manifest_path = artifact / "manifest.json"
    envelope_path = artifact / "envelope.json"
    receipt_path = run / "receipts" / "00000001.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["unsupported_forgery"] = True
    manifest["manifest_digest"] = helper.canonical_digest(manifest, "manifest_digest")
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))
    envelope = json.loads(envelope_path.read_text())
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(envelope, "envelope_digest")
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))
    receipt = json.loads(receipt_path.read_text())
    receipt["relevant_artifact_digests"][0]["envelope_digest"] = envelope[
        "envelope_digest"
    ]
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "manifest" in str(error)
    else:
        raise AssertionError("recovery accepted a rehashed unsupported manifest field")
    assert retained["sequence"] == 1


def test_queue_has_one_active_target_and_unfinished_work_blocks_next(
    tmp_path: Path,
) -> None:
    """A pending queued target must not become active while its predecessor is unfinished."""
    helper = load_helper()
    state_root = tmp_path / "state"
    first_target = tmp_path / "skills" / "first"
    second_target = tmp_path / "skills" / "second"
    first_identity = target_identity(first_target, "first")
    second_identity = target_identity(second_target, "second")
    first = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=first_identity,
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(first_target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        queue=[first_identity, second_identity],
        state_root=state_root,
    )
    loaded = helper.load_run(workflow_id=first["workflow_id"], state_root=state_root)
    assert loaded["queue"] == {
        "order": [first_identity["canonical"], second_identity["canonical"]],
        "states": {
            first_identity["canonical"]: "active",
            second_identity["canonical"]: "pending",
        },
    }

    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=second_identity,
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(second_target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            queue=[first_identity, second_identity],
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "unfinished" in str(error) or "active" in str(error)
    else:
        raise AssertionError("pending target acquired an active lock")
    assert len(list((state_root / "live").iterdir())) == 1


def test_load_rejects_weak_nested_raw_artifact_directory(tmp_path: Path) -> None:
    """Every trusted directory boundary, not only the collection root, must stay private."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    helper.retain_artifact(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        artifact_id="baseline",
        artifact_type="resolution-record",
        files={"nested/evidence.txt": b"evidence\n"},
        primary_path="nested/evidence.txt",
        producer="main-agent",
        input_bindings=[],
        limitations=[],
        state_root=state_root,
    )
    nested = (
        state_root
        / "live"
        / started["workflow_id"]
        / "artifacts"
        / "baseline"
        / "raw"
        / "nested"
    )
    os.chmod(nested, 0o755)

    try:
        helper.load_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "0700" in str(error) or "private" in str(error)
    else:
        raise AssertionError("weak nested raw directory was trusted")


def test_recovery_rejects_rehashed_artifact_target_substitution(tmp_path: Path) -> None:
    """Artifact hashes cannot authorize moving evidence to another canonical target."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        payload=valid_create_baseline_payload(
            helper, state_root, started["workflow_id"]
        ),
    )
    run = state_root / "live" / started["workflow_id"]
    artifact = run / "artifacts" / "baseline"
    manifest_path = artifact / "manifest.json"
    envelope_path = artifact / "envelope.json"
    receipt_path = run / "receipts" / "00000001.json"
    forged_target = "codex:personal:other-skill"
    manifest = json.loads(manifest_path.read_text())
    manifest["target_identity"] = forged_target
    manifest["manifest_digest"] = helper.canonical_digest(manifest, "manifest_digest")
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))
    envelope = json.loads(envelope_path.read_text())
    envelope["target_identity"] = forged_target
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(envelope, "envelope_digest")
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))
    receipt = json.loads(receipt_path.read_text())
    receipt["relevant_artifact_digests"][0]["envelope_digest"] = envelope[
        "envelope_digest"
    ]
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "target" in str(error) or "identity" in str(error)
    else:
        raise AssertionError("recovery accepted a substituted artifact target")


def test_competing_processes_cannot_both_acquire_same_target(tmp_path: Path) -> None:
    """Process-local checks alone would permit two genesis chains for one target."""
    state_root = tmp_path / "state"
    target = target_identity(tmp_path / "skills" / "sample-skill")
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=initialize_process,
            args=(
                str(state_root),
                host_identity(tmp_path),
                target,
                authority(),
                start,
                results,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0

    assert [item[0] for item in outcomes].count("success") == 1
    assert [item[0] for item in outcomes].count("error") == 1
    assert len(list((state_root / "live").iterdir())) == 1


def test_mutation_rejects_substituted_active_target_lock(tmp_path: Path) -> None:
    """The process lock must not hide loss or substitution of target ownership."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    lock_path = (
        state_root
        / "target-locks"
        / helper._target_lock_name(target_identity(target)["canonical"])
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["lock_nonce"] = "substituted-lock"
    lock_path.write_bytes(helper.canonical_json_bytes(lock))

    try:
        helper.pause_run(
            workflow_id=started["workflow_id"],
            expected_sequence=0,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "lock" in str(error)
    else:
        raise AssertionError("mutation accepted a substituted active-target lock")
    assert {path.name for path in (run / "receipts").iterdir()} == {"00000000.json"}


def test_recovery_rejects_rehashed_genesis_with_terminal_destination(
    tmp_path: Path,
) -> None:
    """Removing the genesis destination gate would let forged history start finalized."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    receipt_path = run / "receipts" / "00000000.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["destination_stage"] = "finalized"
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "genesis" in str(error) or "transition" in str(error)
    else:
        raise AssertionError("recovery accepted forged terminal genesis semantics")


def test_stage_transition_rejects_placeholder_normative_payload(tmp_path: Path) -> None:
    """Dropping payload schema validation would accept labels as baseline evidence."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    try:
        retain_json(
            helper,
            state_root=state_root,
            workflow_id=started["workflow_id"],
            sequence=0,
            artifact_id="baseline-placeholder",
            artifact_type="baseline-report",
            payload={"artifact": "baseline-placeholder"},
        )
    except helper.RunStateError as error:
        assert "baseline" in str(error) or "schema" in str(error)
    else:
        raise AssertionError("retention accepted a placeholder baseline payload")
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 0


def test_load_rejects_noncanonical_durable_json_bytes(tmp_path: Path) -> None:
    """Removing exact-byte validation would let alternate durable encodings pass."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    current_path = state_root / "live" / started["workflow_id"] / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current_path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")

    try:
        helper.load_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "canonical" in str(error) or "encoding" in str(error)
    else:
        raise AssertionError("load accepted noncanonical durable JSON bytes")


def test_recovery_rejects_rehashed_boolean_manifest_count(tmp_path: Path) -> None:
    """Weak integer checks would admit boolean item counts into durable manifests."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    artifact = run / "artifacts" / "resolution"
    manifest_path = artifact / "manifest.json"
    envelope_path = artifact / "envelope.json"
    receipt_path = run / "receipts" / "00000000.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["observed_item_count"] = True
    manifest["manifest_digest"] = helper.canonical_digest(manifest, "manifest_digest")
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(envelope, "envelope_digest")
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["relevant_artifact_digests"][0]["envelope_digest"] = envelope[
        "envelope_digest"
    ]
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "manifest" in str(error) or "count" in str(error)
    else:
        raise AssertionError("recovery accepted a boolean manifest count")


def test_recovery_rejects_orphaned_artifact_directory(tmp_path: Path) -> None:
    """Omitting namespace reconciliation would hide unreceipted durable artifacts."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    shutil.copytree(run / "artifacts" / "resolution", run / "artifacts" / "orphan")
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "orphan" in str(error) or "artifact" in str(error)
    else:
        raise AssertionError("recovery ignored an orphaned artifact directory")


def test_initialize_accepts_detached_head_without_inventing_branch(tmp_path: Path) -> None:
    """Treating symbolic-ref failure as fatal would reject valid detached Git targets."""
    helper = load_helper()
    repository, target, head = git_repository(tmp_path)
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "--quiet", "--detach", head],
        check=True,
    )

    try:
        started = helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(target),
            mode="improve",
            authority=authority(),
            target_manifest=helper.snapshot_target(target),
            state_root=tmp_path / "state",
        )
    except helper.RunStateError as error:
        raise AssertionError(f"detached HEAD was rejected: {error}") from error

    loaded = helper.load_run(
        workflow_id=started["workflow_id"], state_root=tmp_path / "state"
    )
    assert loaded["git_identity"]["branch"] is None
    assert loaded["git_identity"]["commit"] == head


def test_snapshot_rejects_oversized_file_before_whole_file_read(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Replacing bounded descriptor reads with read_bytes would exhaust the bound first."""
    helper = load_helper()
    target = tmp_path / "skill"
    target.mkdir()
    payload = target / "oversized.bin"
    with payload.open("wb") as stream:
        stream.truncate(helper.MAX_TARGET_BYTES + 1)

    def forbidden_read_bytes(_path: Path) -> bytes:
        raise AssertionError("snapshot attempted an unbounded whole-file read")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    try:
        helper.snapshot_target(target)
    except helper.RunStateError as error:
        assert "oversized" in str(error) or "bound" in str(error)
    else:
        raise AssertionError("snapshot accepted an oversized target file")


def test_index_write_failure_preserves_committed_append_receipt(
    tmp_path: Path,
) -> None:
    """Deleting a published receipt after index failure would rewrite append-only truth."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    original_atomic_json = helper._atomic_json
    calls = 0

    def fail_first_index_write(path: Path, value: dict[str, object]) -> None:
        nonlocal calls
        if path.name == "current.json":
            calls += 1
        if path.name == "current.json" and calls == 1:
            raise helper.RunStateError("injected derived-index failure")
        original_atomic_json(path, value)

    helper._atomic_json = fail_first_index_write
    try:
        try:
            helper.pause_run(
                workflow_id=started["workflow_id"],
                expected_sequence=0,
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "index" in str(error)
        else:
            raise AssertionError("injected index failure did not interrupt mutation")
    finally:
        helper._atomic_json = original_atomic_json

    receipt = state_root / "live" / started["workflow_id"] / "receipts" / "00000001.json"
    assert receipt.is_file()
    recovered = helper.recover_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )
    assert recovered["sequence"] == 1
    assert recovered["stage"] == "paused"


def test_initialize_failure_after_lock_publication_does_not_leak_target_lock(
    tmp_path: Path,
) -> None:
    """Losing rollback ownership would leave a lock after the fresh run is removed."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    original_fsync_directory = helper._fsync_directory
    failed = False

    def fail_once_after_lock(path: Path) -> None:
        nonlocal failed
        if path == state_root / "live" and not failed:
            failed = True
            raise helper.RunStateError("injected post-lock failure")
        original_fsync_directory(path)

    helper._fsync_directory = fail_once_after_lock
    try:
        try:
            helper.initialize_run(
                host_identity=host_identity(tmp_path),
                target_identity=target_identity(target),
                mode="create",
                authority=authority(),
                absence_evidence={"searched": [str(target)], "exists": False},
                overlap_map={"exact": [], "near_neighbours": []},
                git_identity={"present": False},
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "post-lock" in str(error)
        else:
            raise AssertionError("injected post-lock failure did not interrupt initialize")
    finally:
        helper._fsync_directory = original_fsync_directory

    assert list((state_root / "live").iterdir()) == []
    assert list((state_root / "target-locks").iterdir()) == []


def test_cli_help_documents_request_schemas_and_accepts_maximum_base64_input() -> None:
    """Removing request docs or shrinking stdin below retain capacity breaks the CLI."""
    helper = load_helper()
    result = subprocess.run(
        [sys.executable, str(HELPER), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    for fragment in (
        "initialize request",
        "retain request",
        "transition request",
        "cleanup request",
        "files_base64",
        "expected_sequence",
        "activate-next",
    ):
        assert fragment in result.stdout
    input_limit = getattr(helper, "MAX_CLI_JSON_BYTES", helper.MAX_JSON_BYTES)
    encoded_bound = 4 * ((helper.MAX_ARTIFACT_BYTES + 2) // 3)
    assert input_limit >= encoded_bound + 4096


def test_activate_next_target_creates_one_new_active_queue_run(tmp_path: Path) -> None:
    """Omitting explicit queue activation would strand every target after the first."""
    helper = load_helper()
    first_target = tmp_path / "skills" / "sample-skill"
    second_target = tmp_path / "skills" / "second"
    first_identity = target_identity(first_target)
    second_identity = target_identity(second_target, "second")
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        queued_targets=[first_identity, second_identity],
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    activate = getattr(helper, "activate_next_target", None)
    assert callable(activate), "helper lacks the required queue activation operation"

    activated = activate(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        mode="create",
        absence_evidence={"searched": [str(second_target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        owner_identity="main-agent",
        state_root=state_root,
    )
    next_run = helper.load_run(
        workflow_id=activated["workflow_id"], state_root=state_root
    )
    assert activated["workflow_id"] != workflow_id
    assert next_run["target_identity"] == second_identity
    assert next_run["queue"]["states"] == {
        first_identity["canonical"]: "finalized",
        second_identity["canonical"]: "active",
    }
    assert next_run["active_target_lock"]["target_identity"] == second_identity[
        "canonical"
    ]


def test_delivery_action_and_destination_require_release_scope(tmp_path: Path) -> None:
    """Ignoring finalized scope would let broad initial authority expand at delivery."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        release_scope=["installation:codex:personal:sample-skill"],
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    authority_digest = "8" * 64
    try:
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            delivery={
                "action": "integration",
                "destination_identity": "codex:personal:sample-skill",
                "finalized_revision": "candidate-1",
                "resulting_destination_digest": "c" * 64,
                "acceptance_evidence": ["d" * 64],
                "user_authority_event_digest": authority_digest,
                "actor": "main-agent",
                "accepted": True,
            },
            authority_event_digest=authority_digest,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "scope" in str(error) or "authority" in str(error)
    else:
        raise AssertionError("delivery exceeded the finalized release scope")


def test_delivery_retains_raw_authority_and_acceptance_evidence(tmp_path: Path) -> None:
    """Dropping raw evidence would leave delivery authority as an unbound digest claim."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    authority_bytes = b"user authorizes installation to codex:personal:sample-skill\n"
    acceptance_bytes = b"installed candidate-1 at destination digest c...\n"
    authority_digest = hashlib.sha256(authority_bytes).hexdigest()
    acceptance_digest = hashlib.sha256(acceptance_bytes).hexdigest()

    delivered = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [acceptance_digest],
            "user_authority_event_digest": authority_digest,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=authority_digest,
        authority_event=authority_bytes,
        evidence_files={"acceptance.txt": acceptance_bytes},
        state_root=state_root,
    )
    artifact = (
        state_root
        / "live"
        / workflow_id
        / "artifacts"
        / delivered["delivery_artifact_id"]
    )
    assert (artifact / "raw" / "authority-event.bin").read_bytes() == authority_bytes
    assert (artifact / "raw" / "evidence" / "acceptance.txt").read_bytes() == acceptance_bytes


def test_delivery_and_cleanup_authority_cli_decode_raw_base64(tmp_path: Path) -> None:
    """Strict JSON CLI requests must decode and retain terminal raw evidence."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    authority_bytes = b"user authorizes installation\n"
    acceptance_bytes = b"destination verification\n"
    authority_digest = fixture_digest(authority_bytes)
    acceptance_digest = fixture_digest(acceptance_bytes)
    destination_digest = install_target_and_digest(
        helper, state_root, workflow_id
    )
    delivered = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "deliver",
            "--state-root",
            str(state_root),
        ],
        input=json.dumps(
            {
                "workflow_id": workflow_id,
                "expected_sequence": finalized["sequence"],
                "delivery": {
                    "action": "installation",
                    "destination_identity": "codex:personal:sample-skill",
                    "finalized_revision": "candidate-1",
                    "resulting_destination_digest": destination_digest,
                    "acceptance_evidence": [acceptance_digest],
                    "user_authority_event_digest": authority_digest,
                    "actor": "main-agent",
                    "accepted": True,
                },
                "authority_event_digest": authority_digest,
                "authority_event_base64": base64.b64encode(
                    authority_bytes
                ).decode("ascii"),
                "evidence_files_base64": {
                    "destination-verification.bin": base64.b64encode(
                        acceptance_bytes
                    ).decode("ascii")
                },
            }
        ),
        text=True,
        capture_output=True,
    )
    assert delivered.returncode == 0, delivered.stderr
    delivery_receipt = json.loads(delivered.stdout)

    cleanup_bytes = b"user authorizes cleanup\n"
    cleanup_digest = fixture_digest(cleanup_bytes)
    authorized = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "cleanup-authority",
            "--state-root",
            str(state_root),
        ],
        input=json.dumps(
            {
                "workflow_id": workflow_id,
                "expected_sequence": delivery_receipt["sequence"],
                "authority_event_digest": cleanup_digest,
                "authority_event_base64": base64.b64encode(cleanup_bytes).decode(
                    "ascii"
                ),
                "actor": "user",
            }
        ),
        text=True,
        capture_output=True,
    )
    assert authorized.returncode == 0, authorized.stderr
    authority_receipt = json.loads(authorized.stdout)
    authority_artifact = (
        state_root
        / "live"
        / workflow_id
        / "artifacts"
        / authority_receipt["authority_artifact_id"]
    )
    assert (
        authority_artifact / "raw" / "authority-event.bin"
    ).read_bytes() == cleanup_bytes


def test_cleanup_does_not_follow_directory_swapped_to_symlink(
    tmp_path: Path,
) -> None:
    """Pathname reopens after validation could erase a substituted external directory."""
    helper = load_helper()
    owned = tmp_path / "owned"
    victim = owned / "victim"
    outside = tmp_path / "outside"
    victim.mkdir(parents=True, mode=0o700)
    outside.mkdir(mode=0o700)
    os.chmod(owned, 0o700)
    os.chmod(victim, 0o700)
    os.chmod(outside, 0o700)
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"must survive\n")
    os.chmod(sentinel, 0o600)
    original_validate = helper._validate_private_directory
    swapped = False

    def swap_after_validation(path: Path, label: str) -> None:
        nonlocal swapped
        original_validate(path, label)
        if path == victim and not swapped:
            swapped = True
            victim.rmdir()
            victim.symlink_to(outside, target_is_directory=True)

    helper._validate_private_directory = swap_after_validation
    try:
        try:
            helper._remove_owned_tree(owned)
        except (helper.RunStateError, OSError):
            pass
    finally:
        helper._validate_private_directory = original_validate

    assert sentinel.read_bytes() == b"must survive\n"


def test_cleanup_resumes_after_tombstone_and_partial_tree_deletion(
    tmp_path: Path,
) -> None:
    """Requiring an intact current index on retry would strand partial cleanup."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority_event = b"accepted installation authority\n"
    delivery_acceptance_evidence = b"installed destination verification\n"
    delivery_authority = fixture_digest(delivery_authority_event)
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [
                fixture_digest(delivery_acceptance_evidence)
            ],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        authority_event=delivery_authority_event,
        evidence_files={
            "destination-verification.bin": delivery_acceptance_evidence
        },
        state_root=state_root,
    )
    cleanup_authority_event = b"authorized cleanup event\n"
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=fixture_digest(cleanup_authority_event),
        authority_event=cleanup_authority_event,
        actor="user",
        state_root=state_root,
    )
    original_remove = helper._remove_owned_tree

    def partially_remove(path: Path) -> None:
        (path / "receipts" / "00000000.json").unlink()
        raise helper.RunStateError("injected partial deletion")

    helper._remove_owned_tree = partially_remove
    try:
        try:
            helper.cleanup_run(
                workflow_id=workflow_id,
                expected_sequence=authorized["sequence"],
                acknowledge_cleanup=True,
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "partial" in str(error)
        else:
            raise AssertionError("injected partial deletion did not interrupt cleanup")
    finally:
        helper._remove_owned_tree = original_remove

    assert (state_root / "tombstones" / f"{workflow_id}.json").is_file()
    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert cleaned["stage"] == "cleaned"
    assert not (state_root / "live" / workflow_id).exists()
    assert not (state_root / "deleting" / workflow_id).exists()


def test_invalidation_allows_zero_downstream_and_rewinds_to_replacement_stage(
    tmp_path: Path,
) -> None:
    """Requiring downstream evidence would prevent invalidating a just-accepted candidate."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_candidate_stage(helper, tmp_path)

    invalidated = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        change_kind="candidate",
        changed_artifact_id="candidate",
        reason="candidate changed before trials began",
        state_root=state_root,
    )
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert invalidated["invalidated_artifact_ids"] == []
    assert invalidated["stage"] == "evaluation"
    assert loaded["artifact_index"]["candidate"]["derived_status"] == "superseded"


def test_invalidation_rejects_change_kind_artifact_type_mismatch(tmp_path: Path) -> None:
    """Dropping causal type checks would let a review change supersede a candidate."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(helper, tmp_path)
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )
    score = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=advanced["sequence"],
        artifact_id="score-decoy",
        artifact_type="target-scorecard",
        payload=scorecard_payload(
            candidate_digest=candidate["artifact_digest"],
            evaluation_digest=helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["evaluation"]["digest"],
            review_digest="5" * 64,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )

    try:
        helper.invalidate_run(
            workflow_id=workflow_id,
            expected_sequence=score["sequence"],
            change_kind="review",
            changed_artifact_id="candidate",
            reason="mismatched causal artifact",
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "type" in str(error) or "change" in str(error)
    else:
        raise AssertionError("invalidation accepted a mismatched change artifact type")


def test_trial_change_invalidation_is_supported_and_rewinds_to_candidate(
    tmp_path: Path,
) -> None:
    """Omitting the trial change row would leave stale trial evidence current."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(helper, tmp_path)
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )

    invalidated = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=advanced["sequence"],
        change_kind="trial",
        changed_artifact_id="trials",
        reason="trial evidence changed",
        state_root=state_root,
    )
    assert invalidated["stage"] == "candidate"


def test_transition_requires_exact_singular_artifact_cardinality(tmp_path: Path) -> None:
    """Collapsing artifact types to a set would admit a validated decoy beside evidence."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    payload = valid_create_baseline_payload(
        helper, state_root, started["workflow_id"]
    )
    first = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="baseline-one",
        artifact_type="baseline-report",
        payload=payload,
        input_bindings=current_bindings(
            helper, state_root, started["workflow_id"]
        ),
    )
    second = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=first["sequence"],
        artifact_id="baseline-two",
        artifact_type="baseline-report",
        payload=payload,
        input_bindings=current_bindings(
            helper, state_root, started["workflow_id"]
        ),
    )

    try:
        helper.transition_run(
            workflow_id=started["workflow_id"],
            expected_sequence=second["sequence"],
            event="capture-baseline",
            destination_stage="baseline",
            artifact_ids=["baseline-one", "baseline-two"],
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "exact" in str(error) or "cardinality" in str(error)
    else:
        raise AssertionError("transition accepted duplicate singular baseline artifacts")


def test_recovery_reapplies_event_artifact_cardinality_to_rehashed_receipts(
    tmp_path: Path,
) -> None:
    """Replay that checks only hashes would bless a forged duplicate event binding."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    payload = valid_create_baseline_payload(
        helper, state_root, started["workflow_id"]
    )
    first = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="baseline-one",
        artifact_type="baseline-report",
        payload=payload,
        input_bindings=current_bindings(
            helper, state_root, started["workflow_id"]
        ),
    )
    second = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=first["sequence"],
        artifact_id="baseline-two",
        artifact_type="baseline-report",
        payload=payload,
        input_bindings=current_bindings(
            helper, state_root, started["workflow_id"]
        ),
    )
    advanced = helper.transition_run(
        workflow_id=started["workflow_id"],
        expected_sequence=second["sequence"],
        event="capture-baseline",
        destination_stage="baseline",
        artifact_ids=["baseline-one"],
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    receipt_path = run / "receipts" / f"{advanced['sequence']:08d}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    second_digest = helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["artifact_index"]["baseline-two"]["digest"]
    receipt["relevant_artifact_digests"].append(
        {
            "artifact_id": "baseline-two",
            "envelope_digest": second_digest,
            "status": "accepted",
        }
    )
    receipt["relevant_artifact_digests"].sort(
        key=lambda item: (item["artifact_id"], item["status"])
    )
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "artifact" in str(error) or "cardinality" in str(error)
    else:
        raise AssertionError("recovery accepted a forged duplicate transition binding")


def test_finalization_rejects_every_blocking_review_severity(tmp_path: Path) -> None:
    """A ready label must not hide Critical or Important release-blocking findings."""
    helper = load_helper()
    for severity in ("critical", "important", "high", "medium"):
        case_root = tmp_path / severity
        state_root, workflow_id, sequence, _ = build_verified_stage(
            helper,
            case_root,
            review_findings=[
                {
                    "severity": severity,
                    "release_blocking": True,
                    "evidence": [fixture_digest(REVIEW_FINDING_BYTES)],
                    "impact": "unsafe release",
                    "correction": "repair before release",
                    "affected_target_criteria": ["safety"],
                }
            ],
        )
        try:
            helper.finalize_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                release_artifact_id="release",
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "review" in str(error) or "finding" in str(error)
        else:
            raise AssertionError(
                f"finalization accepted a release-blocking {severity} finding"
            )


@pytest.mark.parametrize("severity", ("high", "medium"))
def test_finalization_rejects_material_findings_despite_caller_flag(
    tmp_path: Path, severity: str
) -> None:
    """High and Medium severity, not a caller boolean, determine release blocking."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        review_findings=[
            {
                "severity": severity,
                "release_blocking": False,
                "evidence": [fixture_digest(REVIEW_FINDING_BYTES)],
                "impact": "material target failure",
                "correction": "repair before release",
                "affected_target_criteria": ["SA9"],
            }
        ],
    )

    with pytest.raises(helper.RunStateError, match="review|finding"):
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )


@pytest.mark.parametrize(
    "case_name,options",
    (
        (
            "missing-conformance-gate",
            {"conformance_gate_ids": CONFORMANCE_GATES[:-1]},
        ),
        (
            "extra-conformance-gate",
            {"conformance_gate_ids": (*CONFORMANCE_GATES, "BR11")},
        ),
        (
            "missing-category-criterion",
            {
                "scorecard_criteria_overrides": {
                    "triggering": {
                        criterion: True for criterion in SCORE_CRITERIA["triggering"][:-1]
                    }
                }
            },
        ),
        (
            "extra-category-criterion",
            {
                "scorecard_criteria_overrides": {
                    "safety": {
                        **{criterion: True for criterion in SCORE_CRITERIA["safety"]},
                        "SA11": True,
                    }
                }
            },
        ),
    ),
)
def test_finalization_requires_exact_conformance_and_category_criteria(
    tmp_path: Path, case_name: str, options: dict[str, object]
) -> None:
    """A passing subset or caller-invented gate cannot satisfy the release rubric."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper, tmp_path / case_name, **options
    )

    with pytest.raises(helper.RunStateError, match="conformance|criterion|score"):
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )


def test_finalization_rejects_release_without_exact_target_binding(tmp_path: Path) -> None:
    """Dropping target binding would allow release evidence to move between targets."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper, tmp_path, release_target="codex:personal:other-skill"
    )

    try:
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "release" in str(error) or "target" in str(error)
    else:
        raise AssertionError("finalization accepted release evidence without target identity")


def test_final_run_manifest_explicitly_binds_terminal_release_evidence(
    tmp_path: Path,
) -> None:
    """Inventory alone would not bind finalization, release, and delivery identities."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority_event = b"accepted installation authority\n"
    delivery_acceptance_evidence = b"installed destination verification\n"
    delivery_authority = fixture_digest(delivery_authority_event)
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [
                fixture_digest(delivery_acceptance_evidence)
            ],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        authority_event=delivery_authority_event,
        evidence_files={
            "destination-verification.bin": delivery_acceptance_evidence
        },
        state_root=state_root,
    )
    cleanup_authority_event = b"authorized cleanup event\n"
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=fixture_digest(cleanup_authority_event),
        authority_event=cleanup_authority_event,
        actor="user",
        state_root=state_root,
    )
    original_remove = helper._remove_owned_tree

    def stop_before_delete(_path: Path) -> None:
        raise helper.RunStateError("inspect final manifest")

    helper._remove_owned_tree = stop_before_delete
    try:
        try:
            helper.cleanup_run(
                workflow_id=workflow_id,
                expected_sequence=authorized["sequence"],
                acknowledge_cleanup=True,
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "inspect" in str(error)
        else:
            raise AssertionError("cleanup deletion inspection hook did not run")
    finally:
        helper._remove_owned_tree = original_remove

    manifest_path = (
        state_root / "deleting" / workflow_id / "final-run-manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["target_identity"] == "codex:personal:sample-skill"
    assert manifest["finalization_receipt_digest"] == finalized["receipt_digest"]
    assert manifest["release_record_digest"] == finalized["release_artifact_digest"]
    assert manifest["accepted_delivery_record_digest"] == delivery[
        "delivery_artifact_digest"
    ]
    assert manifest["head_transition_digest"] == authorized["receipt_digest"]


def test_baseline_transition_requires_current_snapshot_and_resolution_binding(
    tmp_path: Path,
) -> None:
    """A baseline cannot become current with a stale snapshot or no resolution binding."""
    helper = load_helper()
    for label in ("stale-snapshot", "missing-resolution-binding"):
        case_root = tmp_path / label
        state_root = case_root / "state"
        target = case_root / "skills" / "sample-skill"
        started = helper.initialize_run(
            host_identity=host_identity(case_root),
            target_identity=target_identity(target),
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=state_root,
        )
        payload = valid_create_baseline_payload(
            helper, state_root, started["workflow_id"]
        )
        bindings = current_bindings(helper, state_root, started["workflow_id"])
        if label == "stale-snapshot":
            payload["target_snapshot_digest"] = "f" * 64
        else:
            bindings = []
        retained = retain_json(
            helper,
            state_root=state_root,
            workflow_id=started["workflow_id"],
            sequence=0,
            artifact_id="baseline",
            artifact_type="baseline-report",
            payload=payload,
            input_bindings=bindings,
        )

        try:
            helper.transition_run(
                workflow_id=started["workflow_id"],
                expected_sequence=retained["sequence"],
                event="capture-baseline",
                destination_stage="baseline",
                artifact_ids=["baseline"],
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "baseline" in str(error) or "binding" in str(error)
        else:
            raise AssertionError(f"baseline accepted {label}")


def test_trial_case_payload_requires_every_contract_field(tmp_path: Path) -> None:
    """A case missing one required trace cannot satisfy the normative trial schema."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    required_case_fields = tuple(trial_payload(candidate["artifact_digest"])["cases"][0])
    for offset, missing_field in enumerate(required_case_fields):
        payload = trial_payload(candidate["artifact_digest"])
        cases = payload["cases"]
        assert isinstance(cases, list) and isinstance(cases[0], dict)
        del cases[0][missing_field]
        try:
            helper.retain_artifact(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                artifact_id=f"trials-missing-{offset}",
                artifact_type="trial-pack",
                files={"record.json": fixture_canonical_bytes(payload)},
                primary_path="record.json",
                producer="main-agent",
                input_bindings=current_bindings(helper, state_root, workflow_id),
                limitations=[],
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "trial" in str(error) or "schema" in str(error)
        else:
            raise AssertionError(f"trial case accepted without {missing_field}")


def test_trial_cases_cross_bind_frozen_case_candidate_and_raw_trace(
    tmp_path: Path,
) -> None:
    """Structurally valid trial cases cannot substitute their frozen inputs or traces."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    wrong_bytes = b"substituted trial evidence\n"
    cases: list[tuple[str, str | None, str | None]] = [
        ("candidate-digest", None, None),
        ("candidate-revision", None, None),
        ("case-digest", "case_digest", "case"),
        ("request", "request_digest", "request"),
        ("loaded-skill", "loaded_skill_digest", "loaded-skill"),
        ("before-manifest", "before_target_manifest_digest", "before-manifest"),
        ("prompt-claim", "raw_prompt_digest", None),
        ("tools-claim", "tool_event_digest", None),
        ("output-claim", "output_digest", None),
        ("after-manifest-claim", "after_target_manifest_digest", None),
        ("filesystem-claim", "filesystem_result_digest", None),
        ("aggregate-claim", None, None),
    ]
    for offset, (label, field, override_kind) in enumerate(cases):
        payload = trial_payload(candidate["artifact_digest"])
        overrides: dict[tuple[str, str], bytes] = {}
        trial_cases = payload["cases"]
        assert isinstance(trial_cases, list) and isinstance(trial_cases[0], dict)
        if label == "candidate-digest":
            payload["candidate_digest"] = "e" * 64
        elif label == "candidate-revision":
            payload["candidate_revision"] = "substituted-revision"
        elif label == "aggregate-claim":
            payload["aggregate_manifest_digest"] = "e" * 64
        else:
            assert field is not None
            trial_cases[0][field] = fixture_digest(wrong_bytes)
            if override_kind is not None:
                overrides[("case-1", override_kind)] = wrong_bytes
        retained = helper.retain_artifact(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            artifact_id=f"trials-bad-{offset}",
            artifact_type="trial-pack",
            files=trial_files(payload, evidence_overrides=overrides),
            primary_path="record.json",
            producer="main-agent",
            input_bindings=current_bindings(helper, state_root, workflow_id),
            limitations=[],
            state_root=state_root,
        )
        sequence = retained["sequence"]
        try:
            helper.transition_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                event="complete-trials",
                destination_stage="trials",
                artifact_ids=[f"trials-bad-{offset}"],
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "trial" in str(error) or "binding" in str(error)
        else:
            raise AssertionError(f"trial transition accepted substituted {label}")


def test_delivery_and_cleanup_authority_reject_digest_only_claims(
    tmp_path: Path,
) -> None:
    """Authority and acceptance digests are invalid without their retained raw bytes."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    authority_digest = "a" * 64
    try:
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            delivery={
                "action": "installation",
                "destination_identity": "codex:personal:sample-skill",
                "finalized_revision": "candidate-1",
                "resulting_destination_digest": "c" * 64,
                "acceptance_evidence": ["d" * 64],
                "user_authority_event_digest": authority_digest,
                "actor": "main-agent",
                "accepted": True,
            },
            authority_event_digest=authority_digest,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "raw" in str(error) or "evidence" in str(error)
    else:
        raise AssertionError("delivery accepted digest-only authority and evidence")


def test_recovery_reapplies_live_baseline_semantics_to_rehashed_receipt(
    tmp_path: Path,
) -> None:
    """Replay must reject a rehashed transition that the live baseline gate rejects."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    payload = valid_create_baseline_payload(helper, state_root, started["workflow_id"])
    payload["target_snapshot_digest"] = "f" * 64
    retained = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="stale-baseline",
        artifact_type="baseline-report",
        payload=payload,
        input_bindings=current_bindings(helper, state_root, started["workflow_id"]),
    )
    current = helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )
    receipt = {
        "schema_version": "skill-builder-transition.v1",
        "workflow_id": started["workflow_id"],
        "target_identity": current["target_identity"]["canonical"],
        "sequence": retained["sequence"] + 1,
        "prior_receipt_digest": retained["receipt_digest"],
        "event": "capture-baseline",
        "source_stage": "resolved",
        "destination_stage": "baseline",
        "relevant_artifact_digests": [
            {
                "artifact_id": "stale-baseline",
                "envelope_digest": retained["artifact_digest"],
                "status": "accepted",
            }
        ],
        "target_snapshot_digest": started["target_snapshot_digest"],
        "authority_event_digest": None,
        "created_at": "2026-09-06T12:00:00Z",
    }
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    run = state_root / "live" / started["workflow_id"]
    (run / "receipts" / f"{receipt['sequence']:08d}.json").write_bytes(
        helper.canonical_json_bytes(receipt)
    )
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "baseline" in str(error) or "binding" in str(error)
    else:
        raise AssertionError("replay accepted a semantically stale baseline receipt")


def test_cleanup_retry_reauthenticates_rehashed_tombstone_bindings(
    tmp_path: Path,
) -> None:
    """A self-consistently rehashed tombstone cannot authorize retry deletion."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority = b"accepted delivery authority event\n"
    delivery_evidence = b"accepted destination verification\n"
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [fixture_digest(delivery_evidence)],
            "user_authority_event_digest": fixture_digest(delivery_authority),
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=fixture_digest(delivery_authority),
        authority_event=delivery_authority,
        evidence_files={"destination-verification.bin": delivery_evidence},
        state_root=state_root,
    )
    cleanup_authority = b"cleanup authority event\n"
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=fixture_digest(cleanup_authority),
        authority_event=cleanup_authority,
        actor="user",
        state_root=state_root,
    )
    original_move = helper._move_to_deleting

    def stop_after_tombstone(_run: Path, _deleting: Path) -> None:
        raise helper.RunStateError("retain live run for tombstone retry")

    helper._move_to_deleting = stop_after_tombstone
    try:
        try:
            helper.cleanup_run(
                workflow_id=workflow_id,
                expected_sequence=authorized["sequence"],
                acknowledge_cleanup=True,
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "tombstone retry" in str(error)
        else:
            raise AssertionError("cleanup retry fixture did not stop after tombstone")
    finally:
        helper._move_to_deleting = original_move

    tombstone_path = state_root / "tombstones" / f"{workflow_id}.json"
    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))
    tombstone["accepted_delivery_record_digest"] = "f" * 64
    tombstone["tombstone_digest"] = helper.canonical_digest(
        tombstone, "tombstone_digest"
    )
    tombstone_path.write_bytes(helper.canonical_json_bytes(tombstone))

    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=authorized["sequence"],
            acknowledge_cleanup=True,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "tombstone" in str(error) or "delivery" in str(error)
    else:
        raise AssertionError("cleanup retry trusted a forged tombstone binding")
    assert (state_root / "live" / workflow_id).is_dir()


def test_cleanup_authority_index_failure_preserves_append_only_commit(
    tmp_path: Path,
) -> None:
    """Index publication failure cannot delete a committed cleanup-authority receipt."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority = b"accepted delivery authority event\n"
    delivery_evidence = b"accepted destination verification\n"
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [fixture_digest(delivery_evidence)],
            "user_authority_event_digest": fixture_digest(delivery_authority),
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=fixture_digest(delivery_authority),
        authority_event=delivery_authority,
        evidence_files={"destination-verification.bin": delivery_evidence},
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    committed_sequence = delivery["sequence"] + 1
    original_atomic_json = helper._atomic_json

    def fail_current_index(path: Path, value: dict[str, object]) -> None:
        if path == run / "current.json":
            raise helper.RunStateError("injected authority index publication failure")
        original_atomic_json(path, value)

    helper._atomic_json = fail_current_index
    cleanup_authority = b"cleanup authority event\n"
    try:
        try:
            helper.record_cleanup_authority(
                workflow_id=workflow_id,
                expected_sequence=delivery["sequence"],
                authority_event_digest=fixture_digest(cleanup_authority),
                authority_event=cleanup_authority,
                actor="user",
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "index publication" in str(error)
        else:
            raise AssertionError("injected authority index failure did not fire")
    finally:
        helper._atomic_json = original_atomic_json

    assert (run / "receipts" / f"{committed_sequence:08d}.json").is_file()
    assert (run / "artifacts" / f"cleanup-authority-{committed_sequence:08d}").is_dir()
    recovered = helper.recover_run(workflow_id=workflow_id, state_root=state_root)
    assert recovered["sequence"] == committed_sequence
    assert recovered["stage"] == "delivered"


def test_replay_rejects_semantically_forged_invalidation_receipt(
    tmp_path: Path,
) -> None:
    """A rehashed receipt cannot contradict the material-change rule it claims."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_candidate_stage(helper, tmp_path)
    run = state_root / "live" / workflow_id
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    candidate = current["artifact_index"]["candidate"]
    payload = {
        "schema_version": "skill-builder-invalidation.v1",
        "change_kind": "research",
        "changed_artifact_id": "candidate",
        "changed_artifact_digest": candidate["digest"],
        "reason": "forged research invalidation",
        "invalidated_artifact_ids": [],
    }

    with pytest.raises(helper.RunStateError, match="invalidation|change|type"):
        with helper._locked_root(state_root, create=False):
            helper._append_transaction(
                run=run,
                current=current,
                event="invalidate",
                destination_stage="baseline",
                authority_event_digest=None,
                existing_bindings=[
                    {
                        "artifact_id": "candidate",
                        "envelope_digest": candidate["digest"],
                        "status": "superseded",
                    }
                ],
                artifact_requests=[
                    {
                        "artifact_id": (
                            f"invalidation-{current['head_sequence'] + 1:08d}"
                        ),
                        "artifact_type": "invalidation-record",
                        "files": {
                            "record.json": helper.canonical_json_bytes(payload)
                        },
                        "primary_path": "record.json",
                        "producer": "main-agent",
                        "input_bindings": [
                            {
                                "artifact_id": "candidate",
                                "digest": candidate["digest"],
                            }
                        ],
                        "limitations": [],
                    }
                ],
            )


def test_invalidation_rejects_unselected_same_type_decoy(tmp_path: Path) -> None:
    """Only the artifact selected by its workflow event can cause a rewind."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    decoy = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="candidate-decoy",
        artifact_type="candidate-record",
        payload=helper._artifact_payload_json(
            state_root / "live" / workflow_id, "candidate"
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )

    with pytest.raises(helper.RunStateError, match="current|selected|change"):
        helper.invalidate_run(
            workflow_id=workflow_id,
            expected_sequence=decoy["sequence"],
            change_kind="candidate",
            changed_artifact_id="candidate-decoy",
            reason="attempt to invalidate an unselected decoy",
            state_root=state_root,
        )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert current["stage"] == "candidate"
    assert current["artifact_index"]["candidate"]["digest"] == candidate[
        "artifact_digest"
    ]


def test_create_mode_records_the_exact_installed_destination(
    tmp_path: Path,
) -> None:
    """The absent baseline may become the exact authorized installed target."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    target = tmp_path / "skills" / "sample-skill"
    target.mkdir(parents=True)
    os.chmod(target, 0o755)
    (target / "SKILL.md").write_bytes(CANDIDATE_SKILL_BYTES)
    os.chmod(target / "SKILL.md", 0o644)
    destination_digest = helper.snapshot_target(target)["manifest_digest"]
    authority_event = b"user accepted this exact installation\n"
    acceptance_evidence = b"destination snapshot verified\n"

    delivered = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": destination_digest,
            "acceptance_evidence": [fixture_digest(acceptance_evidence)],
            "user_authority_event_digest": fixture_digest(authority_event),
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=fixture_digest(authority_event),
        authority_event=authority_event,
        evidence_files={"destination-verification.bin": acceptance_evidence},
        state_root=state_root,
    )

    assert delivered["stage"] == "delivered"
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)[
        "stage"
    ] == "delivered"


def test_new_runs_require_versioned_candidate_result_semantics(tmp_path: Path) -> None:
    """New state must not let a caller downgrade candidate equality to legacy v1."""
    helper = load_helper()
    with pytest.raises(helper.RunStateError, match="candidate|schema|legacy"):
        build_candidate_stage(
            helper,
            tmp_path / "legacy",
            candidate_schema_version="skill-builder-candidate.v1",
            candidate_resulting_bytes=CANDIDATE_SKILL_BYTES,
        )
    with pytest.raises(helper.RunStateError, match="candidate|schema|version"):
        build_candidate_stage(
            helper,
            tmp_path / "previous",
            candidate_schema_version="skill-builder-candidate.v2",
        )

    state_root, workflow_id, _, _ = build_candidate_stage(
        helper,
        tmp_path / "current",
        candidate_schema_version="skill-builder-candidate.v4",
        candidate_resulting_bytes=candidate_result_bytes(
            schema_version="skill-builder-owned-result.v2"
        ),
    )
    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "candidate"


def test_v4_trials_reject_owned_result_descriptor_substitution(
    tmp_path: Path,
) -> None:
    """A delivery descriptor package cannot substitute for the candidate package."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    descriptor = candidate_result_bytes()
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="descriptor-trials",
        artifact_type="trial-pack",
        payload=trial_payload(
            candidate["artifact_digest"],
            loaded_skill_bytes=descriptor,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
        trial_loaded_skill_bytes=descriptor,
    )

    with pytest.raises(helper.RunStateError, match="loaded|skill|candidate"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=trials["sequence"],
            event="complete-trials",
            destination_stage="trials",
            artifact_ids=["descriptor-trials"],
            state_root=state_root,
        )


def test_delivery_rejects_descriptor_matching_content_not_loaded_by_trials(
    tmp_path: Path,
) -> None:
    """Descriptor and trial claims cannot independently bless different packages."""
    helper = load_helper()
    delivered_skill = b"---\nname: sample-skill\n---\n# Substituted delivery\n"
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        candidate_resulting_bytes=candidate_result_bytes(
            skill_bytes=delivered_skill
        ),
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    target = tmp_path / "skills" / "sample-skill"
    target.mkdir(parents=True)
    os.chmod(target, 0o755)
    (target / "SKILL.md").write_bytes(delivered_skill)
    os.chmod(target / "SKILL.md", 0o644)
    destination_digest = helper.snapshot_target(target)["manifest_digest"]
    authority_event = b"authorize substituted installation\n"
    acceptance_evidence = b"substituted destination snapshot\n"

    with pytest.raises(helper.RunStateError, match="content|loaded|trial|candidate"):
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            delivery={
                "action": "installation",
                "destination_identity": "codex:personal:sample-skill",
                "finalized_revision": "candidate-1",
                "resulting_destination_digest": destination_digest,
                "acceptance_evidence": [fixture_digest(acceptance_evidence)],
                "user_authority_event_digest": fixture_digest(authority_event),
                "actor": "main-agent",
                "accepted": True,
            },
            authority_event_digest=fixture_digest(authority_event),
            authority_event=authority_event,
            evidence_files={"destination-verification.bin": acceptance_evidence},
            state_root=state_root,
        )


def test_multifile_package_round_trip_binds_trials_and_delivery(
    tmp_path: Path,
) -> None:
    """One nested package identity survives candidate, trials, and delivery."""
    helper = load_helper()
    package = varied_multifile_package("exact")
    additional = {
        path: payload for path, payload in package.items() if path != "SKILL.md"
    }
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        candidate_additional_skill_files=additional,
        trial_loaded_skill_files=package,
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivered = record_package_delivery(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=finalized["sequence"],
        files=package,
    )

    assert delivered["stage"] == "delivered"
    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "delivered"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "delivered"


@pytest.mark.parametrize("change", ("addition", "removal", "mutation"))
def test_delivery_rejects_multifile_package_not_loaded_by_trials(
    tmp_path: Path, change: str
) -> None:
    """Even a matching result descriptor cannot bless changed package bytes."""
    helper = load_helper()
    expected_package = varied_multifile_package("exact")
    delivered_package = varied_multifile_package(change)
    expected_additional = {
        path: payload
        for path, payload in expected_package.items()
        if path != "SKILL.md"
    }
    delivered_additional = {
        path: payload
        for path, payload in delivered_package.items()
        if path != "SKILL.md"
    }
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        candidate_additional_skill_files=expected_additional,
        candidate_resulting_bytes=candidate_result_bytes(
            skill_bytes=delivered_package["SKILL.md"],
            additional_skill_files=delivered_additional,
        ),
        candidate_owned_paths=["."],
        trial_loaded_skill_files=expected_package,
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )

    with pytest.raises(helper.RunStateError, match="loadable|skill|trial|content"):
        record_package_delivery(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=finalized["sequence"],
            files=delivered_package,
        )


def test_review_rejects_candidate_only_input_provenance(tmp_path: Path) -> None:
    """A candidate-only input map omits the review's normative evidence set."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )["sequence"]
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="candidate-only-review",
        artifact_type="review-record",
        payload=review_payload(candidate["artifact_digest"]),
        input_bindings=review_envelope_bindings(
            helper, state_root, workflow_id
        ),
    )

    with pytest.raises(helper.RunStateError, match="review|input|provenance"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=review["sequence"],
            event="accept-review",
            destination_stage="reviewed",
            artifact_ids=["candidate-only-review"],
            state_root=state_root,
        )


def test_review_accepts_exact_current_provenance_live_and_replay(
    tmp_path: Path,
) -> None:
    """Every normative review input is bound to its current canonical artifact."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_current_trials_stage(
        helper, tmp_path
    )
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="exact-review",
        artifact_type="review-record",
        payload=review_payload(
            candidate["artifact_digest"],
            input_artifacts=review_input_provenance(
                helper, state_root, workflow_id
            ),
        ),
        input_bindings=review_envelope_bindings(
            helper, state_root, workflow_id
        ),
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=review["sequence"],
        event="accept-review",
        destination_stage="reviewed",
        artifact_ids=["exact-review"],
        state_root=state_root,
    )

    assert advanced["stage"] == "reviewed"
    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "reviewed"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "reviewed"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-role",
        "extra-role",
        "substituted-role",
        "stale-artifact-digest",
        "component-digest-mismatch",
        "extra-envelope-input",
    ),
)
def test_review_rejects_inexact_current_provenance_live(
    tmp_path: Path, mutation: str
) -> None:
    """No missing, extra, substituted, stale, or mismatched review input advances."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_current_trials_stage(
        helper, tmp_path
    )
    provenance = review_input_provenance(helper, state_root, workflow_id)
    bindings = review_envelope_bindings(helper, state_root, workflow_id)
    if mutation == "missing-role":
        provenance.pop("confirmed_contract")
    elif mutation == "extra-role":
        provenance["unrequested_input"] = dict(provenance["candidate_artifact"])
    elif mutation == "substituted-role":
        provenance["host_rules"] = dict(provenance["candidate_artifact"])
    elif mutation == "stale-artifact-digest":
        provenance["candidate_artifact"]["artifact_digest"] = "0" * 64
    elif mutation == "component-digest-mismatch":
        provenance["evaluation_rubric"]["component_digest"] = "1" * 64
    else:
        bindings = current_bindings(helper, state_root, workflow_id)
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=f"review-{mutation}",
        artifact_type="review-record",
        payload=review_payload(
            candidate["artifact_digest"], input_artifacts=provenance
        ),
        input_bindings=bindings,
    )

    with pytest.raises(helper.RunStateError, match="review|input|provenance|stale"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=review["sequence"],
            event="accept-review",
            destination_stage="reviewed",
            artifact_ids=[f"review-{mutation}"],
            state_root=state_root,
        )


def test_recovery_rejects_rehashed_review_provenance_mismatch(
    tmp_path: Path,
) -> None:
    """Self-consistent envelope hashes cannot bless altered reviewer inputs."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_current_trials_stage(
        helper, tmp_path
    )
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="final-review",
        artifact_type="review-record",
        payload=review_payload(
            candidate["artifact_digest"],
            input_artifacts=review_input_provenance(
                helper, state_root, workflow_id
            ),
        ),
        input_bindings=review_envelope_bindings(
            helper, state_root, workflow_id
        ),
    )
    helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=review["sequence"],
        event="accept-review",
        destination_stage="reviewed",
        artifact_ids=["final-review"],
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    forged = helper._artifact_payload_json(run, "final-review")
    forged["input_artifacts"]["candidate_result"]["component_digest"] = "e" * 64
    rewrite_artifact_payload_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id="final-review",
        payload=forged,
        rewrite_receipts=True,
    )

    with pytest.raises(helper.RunStateError, match="review|input|provenance|stale"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="review|input|provenance|stale"):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_v4_candidate_rejects_loaded_digest_not_derived_from_actual_package(
    tmp_path: Path,
) -> None:
    """The candidate's loadable digest is computed, not accepted as a claim."""
    helper = load_helper()

    with pytest.raises(helper.RunStateError, match="candidate|loaded|package"):
        build_candidate_stage(
            helper,
            tmp_path,
            candidate_claimed_loaded_skill_bytes=b"substituted package\n",
        )


def test_v4_candidate_rejects_owned_result_descriptor_as_skill_content(
    tmp_path: Path,
) -> None:
    """Canonical descriptor JSON cannot masquerade as a package's SKILL.md."""
    helper = load_helper()

    with pytest.raises(
        helper.RunStateError,
        match="uses an owned-result descriptor as skill content",
    ):
        build_candidate_stage(
            helper,
            tmp_path,
            candidate_loaded_skill_bytes=candidate_result_bytes(),
        )


def test_loadable_digest_is_deterministic_for_complete_multifile_package(
    tmp_path: Path,
) -> None:
    """Nested additions, removals, and mutations change one canonical package ID."""
    helper = load_helper()
    package = varied_multifile_package("exact")
    target = tmp_path / "candidate"
    for relative, payload in reversed(tuple(package.items())):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    retained = {
        f"candidate-package/{relative}": payload
        for relative, payload in reversed(tuple(package.items()))
    }
    expected = fixture_loadable_skill_digest(package)

    assert (
        helper._loadable_skill_digest_from_files(
            retained, "candidate-package/", "candidate package"
        )
        == expected
    )
    assert (
        helper._loadable_skill_digest_from_target_manifest(
            helper.snapshot_target(target), "candidate package"
        )
        == expected
    )
    variants = tuple(
        varied_multifile_package(change)
        for change in ("addition", "removal", "mutation")
    )
    assert all(fixture_loadable_skill_digest(variant) != expected for variant in variants)


@pytest.mark.parametrize("change", ("addition", "removal", "mutation"))
def test_trials_reject_multifile_package_substitution(
    tmp_path: Path, change: str
) -> None:
    """Trial receipts cannot claim the candidate while retaining a changed package."""
    helper = load_helper()
    expected_package = varied_multifile_package("exact")
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper,
        tmp_path,
        candidate_additional_skill_files={
            path: payload
            for path, payload in expected_package.items()
            if path != "SKILL.md"
        },
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=f"trials-{change}",
        artifact_type="trial-pack",
        payload=trial_payload(
            candidate["artifact_digest"],
            loaded_skill_files=expected_package,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
        trial_loaded_skill_files=varied_multifile_package(change),
    )

    with pytest.raises(helper.RunStateError, match="trial|loaded|substituted"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=trials["sequence"],
            event="complete-trials",
            destination_stage="trials",
            artifact_ids=[f"trials-{change}"],
            state_root=state_root,
        )


@pytest.mark.parametrize(
    ("artifact_id", "relative_path"),
    (
        ("candidate", "candidate-package/SKILL.md"),
        ("trials", "evidence/case-1/loaded-skill/SKILL.md"),
    ),
)
def test_recovery_rejects_rehashed_loadable_package_substitution(
    tmp_path: Path, artifact_id: str, relative_path: str
) -> None:
    """Rehashed raw bytes still must match the accepted loadable-content digest."""
    helper = load_helper()
    if artifact_id == "candidate":
        state_root, workflow_id, _, _ = build_candidate_stage(helper, tmp_path)
    else:
        state_root, workflow_id, _, _ = build_current_trials_stage(
            helper, tmp_path
        )
    rewrite_artifact_raw_file_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id=artifact_id,
        relative_path=relative_path,
        payload=b"---\nname: substituted\n---\n# Wrong package\n",
    )

    with pytest.raises(helper.RunStateError, match="loaded|package|trial|candidate|skill"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="loaded|package|trial|candidate|skill"):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_v4_trials_accept_exact_bound_loaded_skill_content(tmp_path: Path) -> None:
    """Current trials load the candidate skill bytes, separate from delivery identity."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="content-trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["content-trials"],
        state_root=state_root,
    )

    assert advanced["stage"] == "trials"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "trials"


def test_historical_v2_candidate_and_trial_semantics_remain_replayable(
    tmp_path: Path,
) -> None:
    """The committed v2 descriptor-as-result format remains a stable replay format."""
    helper = load_helper()
    descriptor = candidate_result_bytes()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper,
        tmp_path,
        candidate_schema_version="skill-builder-candidate.v2",
        candidate_resulting_bytes=descriptor,
        historical_candidate_v2=True,
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="historical-v2-trials",
        artifact_type="trial-pack",
        payload=trial_payload(
            candidate["artifact_digest"],
            loaded_skill_bytes=descriptor,
            schema_version="skill-builder-trial-pack.v1",
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
        trial_loaded_skill_bytes=descriptor,
    )
    helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["historical-v2-trials"],
        state_root=state_root,
    )

    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "trials"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "trials"


def test_current_resolution_rejects_legacy_trial_and_review_schemas(
    tmp_path: Path,
) -> None:
    """A current run cannot silently reinterpret either legacy evidence schema."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    with pytest.raises(helper.RunStateError, match="trial|schema|version"):
        retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id="legacy-trials",
            artifact_type="trial-pack",
            payload=trial_payload(
                candidate["artifact_digest"],
                schema_version="skill-builder-trial-pack.v1",
            ),
            input_bindings=current_bindings(helper, state_root, workflow_id),
        )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )["sequence"]
    with pytest.raises(helper.RunStateError, match="review|schema|version"):
        retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id="legacy-review",
            artifact_type="review-record",
            payload=review_payload(
                candidate["artifact_digest"],
                schema_version="skill-builder-review.v1",
            ),
            input_bindings=current_bindings(helper, state_root, workflow_id),
        )


def test_shared_candidate_gate_rejects_resolution_v2_candidate_v1_live(
    tmp_path: Path,
) -> None:
    """Re-enveloping a candidate must not bypass the resolution version marker."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_candidate_stage(
        helper,
        tmp_path,
        candidate_schema_version="skill-builder-candidate.v2",
        historical_candidate_v2=True,
    )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    run = state_root / "live" / workflow_id
    payload = helper._artifact_payload_json(run, "candidate")
    payload["schema_version"] = "skill-builder-candidate.v1"
    payload.pop("loaded_skill_digest", None)
    rewrite_artifact_payload_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id="candidate",
        payload=payload,
        rewrite_receipts=False,
    )

    with pytest.raises(helper.RunStateError, match="candidate|schema|version"):
        helper._validate_transition_semantics(
            run, current, "accept-candidate", ["candidate"], None
        )


def test_recovery_rejects_resolution_v2_candidate_v1_replay(
    tmp_path: Path,
) -> None:
    """A self-consistently rehashed downgrade must fail shared replay semantics."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_candidate_stage(
        helper,
        tmp_path,
        candidate_schema_version="skill-builder-candidate.v2",
        historical_candidate_v2=True,
    )
    run = state_root / "live" / workflow_id
    payload = helper._artifact_payload_json(run, "candidate")
    payload["schema_version"] = "skill-builder-candidate.v1"
    payload.pop("loaded_skill_digest", None)
    rewrite_artifact_payload_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id="candidate",
        payload=payload,
        rewrite_receipts=True,
    )

    with pytest.raises(helper.RunStateError, match="candidate|schema|version"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="candidate|schema|version"):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_current_run_rejects_legacy_aggregate_boolean_scorecard(
    tmp_path: Path,
) -> None:
    """Aggregate text claims and booleans are not per-criterion score evidence."""
    helper = load_helper()

    with pytest.raises(helper.RunStateError, match="scorecard|schema|criterion|evidence"):
        build_verified_stage(
            helper,
            tmp_path,
            scorecard_schema_version="skill-builder-scorecard.v1",
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("frozen_parameter_identifiers", ["invented-parameter"]),
        ("case_ids", ["case-1"]),
        ("raw_artifact_digests", ["a" * 64]),
        ("trial_receipt_ids", ["b" * 64]),
        ("review_finding_ids", ["c" * 64]),
        ("candidate_revision", "candidate-substitution"),
        ("review_artifact_id", "review-decoy"),
        ("review_digest", "d" * 64),
    ),
)
def test_scorecard_requires_each_criterion_evidence_identity_live(
    tmp_path: Path, field: str, value: object
) -> None:
    """A structured result cannot borrow or invent any required evidence identity."""
    helper = load_helper()

    with pytest.raises(helper.RunStateError, match="scorecard|criterion|evidence"):
        build_verified_stage(
            helper,
            tmp_path,
            scorecard_evidence_overrides={"TR1": {field: value}},
        )


def test_recovery_revalidates_each_scorecard_criterion_evidence_identity(
    tmp_path: Path,
) -> None:
    """Self-consistent envelope hashes cannot bypass criterion evidence on replay."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path, retain_review_source_components=True
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=trial_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )["sequence"]
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="final-review",
        artifact_type="review-record",
        payload=review_payload(
            candidate["artifact_digest"],
            input_artifacts=review_input_provenance(
                helper, state_root, workflow_id
            ),
        ),
        input_bindings=review_envelope_bindings(
            helper, state_root, workflow_id
        ),
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=review["sequence"],
        event="accept-review",
        destination_stage="reviewed",
        artifact_ids=["final-review"],
        state_root=state_root,
    )["sequence"]
    conformance = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="conformance",
        artifact_type="builder-run-conformance-ledger",
        payload=conformance_payload(candidate["artifact_digest"]),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    evaluation_digest = helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["artifact_index"]["evaluation"]["digest"]
    scorecard = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=conformance["sequence"],
        artifact_id="scores",
        artifact_type="target-scorecard",
        payload=scorecard_payload(
            candidate_digest=candidate["artifact_digest"],
            evaluation_digest=evaluation_digest,
            review_digest=review["artifact_digest"],
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
    )
    helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=scorecard["sequence"],
        event="accept-scores",
        destination_stage="scored",
        artifact_ids=["conformance", "scores"],
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    forged = helper._artifact_payload_json(run, "scores")
    forged["categories"][0]["criteria"]["TR1"]["raw_artifact_digests"] = [
        "e" * 64
    ]
    rewrite_artifact_payload_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id="scores",
        payload=forged,
        rewrite_receipts=True,
    )

    with pytest.raises(helper.RunStateError, match="scorecard|criterion|evidence"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="scorecard|criterion|evidence"):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_finalized_historical_v2_run_remains_loadable_and_recoverable(
    tmp_path: Path,
) -> None:
    """Resolution v2 keeps its candidate v2 and scorecard v1 replay meanings."""
    helper = load_helper()
    descriptor = candidate_result_bytes()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        candidate_schema_version="skill-builder-candidate.v2",
        candidate_resulting_bytes=descriptor,
        trial_loaded_skill_bytes=descriptor,
        historical_candidate_v2=True,
    )
    helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )

    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "finalized"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "finalized"


def test_delivered_legacy_v1_run_remains_loadable_recoverable_and_cleanable(
    tmp_path: Path,
) -> None:
    """Upgrade preserves genuine v1 final evidence through its full lifecycle."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        candidate_schema_version="skill-builder-candidate.v1",
        candidate_resulting_bytes=CANDIDATE_SKILL_BYTES,
        trial_loaded_skill_bytes=CANDIDATE_SKILL_BYTES,
        legacy_candidate_v1=True,
        review_findings=[
            {
                "severity": "important",
                "release_blocking": False,
                "evidence": [fixture_digest(REVIEW_FINDING_BYTES)],
                "impact": "known legacy limitation",
                "correction": "retain for a later revision",
                "affected_target_criteria": ["criterion-1"],
            }
        ],
    )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    run = state_root / "live" / workflow_id
    conformance = helper._artifact_payload_json(run, "conformance")
    scorecard = helper._artifact_payload_json(run, "scores")
    assert current["stage"] == "verified"
    assert set(conformance["gates"]) == set(LEGACY_CONFORMANCE_GATES)
    assert all(
        category["criteria"] == {"criterion-1": True}
        for category in scorecard["categories"]
    )

    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority_event = b"accepted legacy installation authority\n"
    delivery_evidence = b"legacy destination verified\n"
    delivery_authority = fixture_digest(delivery_authority_event)
    delivered = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": install_target_and_digest(
                helper, state_root, workflow_id
            ),
            "acceptance_evidence": [fixture_digest(delivery_evidence)],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        authority_event=delivery_authority_event,
        evidence_files={"destination-verification.bin": delivery_evidence},
        state_root=state_root,
    )

    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "delivered"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "delivered"

    cleanup_event = b"authorized legacy cleanup\n"
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivered["sequence"],
        authority_event_digest=fixture_digest(cleanup_event),
        authority_event=cleanup_event,
        actor="user",
        state_root=state_root,
    )
    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert cleaned["stage"] == "cleaned"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "cleaned"


def test_owned_directory_allows_deleting_an_original_descendant(
    tmp_path: Path,
) -> None:
    """Directory ownership includes removed descendants, not only surviving paths."""
    helper = load_helper()
    target = tmp_path / "sample-skill"
    references = target / "references"
    references.mkdir(parents=True)
    os.chmod(target, 0o700)
    os.chmod(references, 0o755)
    obsolete = references / "obsolete.md"
    obsolete.write_bytes(b"obsolete\n")
    os.chmod(obsolete, 0o644)
    baseline = helper.snapshot_target(target)
    obsolete.unlink()
    delivered = helper.snapshot_target(target)
    index = {
        "mode": {"name": "improve"},
        "target_snapshot": {"manifest": baseline},
    }
    expected = fixture_digest(
        fixture_canonical_bytes(
            {
                "schema_version": "skill-builder-owned-result.v2",
                "target_kind": "directory",
                "target_mode": 0o700,
                "entries": [
                    {
                        "path": "references",
                        "kind": "directory",
                        "mode": 0o755,
                        "byte_count": 0,
                        "digest": None,
                    }
                ],
            }
        )
    )

    assert helper._owned_result_digest(index, delivered, ["references"]) == expected


def test_unowned_root_metadata_change_is_rejected(tmp_path: Path) -> None:
    """Owning SKILL.md does not authorize changing the target directory mode."""
    helper = load_helper()
    target = tmp_path / "sample-skill"
    target.mkdir()
    os.chmod(target, 0o700)
    skill = target / "SKILL.md"
    skill.write_bytes(CANDIDATE_SKILL_BYTES)
    os.chmod(skill, 0o644)
    baseline = helper.snapshot_target(target)
    os.chmod(target, 0o777)
    delivered = helper.snapshot_target(target)
    index = {
        "mode": {"name": "improve"},
        "target_snapshot": {"manifest": baseline},
    }

    with pytest.raises(helper.RunStateError, match="ownership|metadata|mode"):
        helper._owned_result_digest(index, delivered, ["SKILL.md"])


def test_delivery_rejects_destination_that_is_not_the_final_candidate(
    tmp_path: Path,
) -> None:
    """A caller-supplied destination digest cannot replace candidate equality."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    target = tmp_path / "skills" / "sample-skill"
    target.mkdir(parents=True)
    os.chmod(target, 0o755)
    (target / "SKILL.md").write_bytes(b"unrelated installed content\n")
    os.chmod(target / "SKILL.md", 0o644)
    destination_digest = helper.snapshot_target(target)["manifest_digest"]
    authority_event = b"user accepted this exact installation\n"
    acceptance_evidence = b"destination snapshot verified\n"

    with pytest.raises(helper.RunStateError, match="candidate|content|result"):
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            delivery={
                "action": "installation",
                "destination_identity": "codex:personal:sample-skill",
                "finalized_revision": "candidate-1",
                "resulting_destination_digest": destination_digest,
                "acceptance_evidence": [fixture_digest(acceptance_evidence)],
                "user_authority_event_digest": fixture_digest(authority_event),
                "actor": "main-agent",
                "accepted": True,
            },
            authority_event_digest=fixture_digest(authority_event),
            authority_event=authority_event,
            evidence_files={"destination-verification.bin": acceptance_evidence},
            state_root=state_root,
        )


def test_all_stage_transitions_require_their_semantic_input_bindings(
    tmp_path: Path,
) -> None:
    """Structurally valid artifacts cannot advance without their required inputs."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_verified_stage(helper, tmp_path)
    run = state_root / "live" / workflow_id
    payload_ids = {
        "complete-research": ("research-pack", "research"),
        "sieve-evidence": ("evidence-sieve", "sieve"),
        "accept-design": ("design-record", "design"),
        "accept-contract": ("skill-contract", "contract"),
        "accept-review": ("review-record", "final-review"),
        "accept-verification": ("verification-record", "verification"),
    }
    for event, (artifact_type, source_id) in payload_ids.items():
        current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        artifact_id = f"unbound-{event}"
        retained = retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=current["head_sequence"],
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            payload=helper._artifact_payload_json(run, source_id),
            input_bindings=[],
        )
        current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        assert retained["sequence"] == current["head_sequence"]
        with pytest.raises(helper.RunStateError, match="binding|input|stale"):
            helper._validate_transition_semantics(
                run,
                current,
                event,
                [artifact_id],
                None,
            )

    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    conformance = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=current["head_sequence"],
        artifact_id="unbound-conformance",
        artifact_type="builder-run-conformance-ledger",
        payload=helper._artifact_payload_json(run, "conformance"),
        input_bindings=[],
    )
    scorecard = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=conformance["sequence"],
        artifact_id="unbound-scores",
        artifact_type="target-scorecard",
        payload=helper._artifact_payload_json(run, "scores"),
        input_bindings=[],
    )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert scorecard["sequence"] == current["head_sequence"]
    with pytest.raises(helper.RunStateError, match="binding|input|stale"):
        helper._validate_transition_semantics(
            run,
            current,
            "accept-scores",
            ["unbound-conformance", "unbound-scores"],
            None,
        )


def test_terminal_claims_require_retained_raw_evidence(tmp_path: Path) -> None:
    """Digest strings alone cannot prove review, gates, verification, or release."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_verified_stage(helper, tmp_path)
    run = state_root / "live" / workflow_id

    def retain_digest_only(
        artifact_id: str, artifact_type: str, source_id: str
    ) -> None:
        current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        payload = helper._artifact_payload_json(run, source_id)
        helper.retain_artifact(
            workflow_id=workflow_id,
            expected_sequence=current["head_sequence"],
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            files={"record.json": fixture_canonical_bytes(payload)},
            primary_path="record.json",
            producer="main-agent",
            input_bindings=(
                review_envelope_bindings(helper, state_root, workflow_id)
                if artifact_type == "review-record"
                else current_bindings(helper, state_root, workflow_id)
            ),
            limitations=[],
            state_root=state_root,
        )

    retain_digest_only("digest-only-review", "review-record", "final-review")
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="raw|evidence"):
        helper._validate_transition_semantics(
            run,
            current,
            "accept-review",
            ["digest-only-review"],
            None,
        )

    retain_digest_only(
        "digest-only-conformance",
        "builder-run-conformance-ledger",
        "conformance",
    )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="raw|evidence"):
        helper._validate_transition_semantics(
            run,
            current,
            "accept-scores",
            ["digest-only-conformance", "scores"],
            None,
        )

    retain_digest_only(
        "digest-only-verification", "verification-record", "verification"
    )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="raw|evidence"):
        helper._validate_transition_semantics(
            run,
            current,
            "accept-verification",
            ["digest-only-verification"],
            None,
        )

    retain_digest_only("digest-only-release", "release-record", "release")
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="raw|evidence"):
        helper._validate_final_evidence(
            run, current, "digest-only-release"
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("target_identity", None),
        ("created_at", None),
        ("run_directory_identity.device", "1"),
        ("run_directory_identity.inode", True),
    ),
)
def test_tombstone_rejects_malformed_identity_and_timestamp_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    """A self-rehashed tombstone still needs exact field types and identities."""
    helper = load_helper()
    workflow_id = "a" * 32
    tombstone = {
        "schema_version": "skill-builder-cleanup-tombstone.v1",
        "workflow_id": workflow_id,
        "target_identity": "codex:personal:sample-skill",
        "run_directory_identity": {
            "name": workflow_id,
            "device": 1,
            "inode": 2,
        },
        "final_transition_receipt_digest": "1" * 64,
        "final_run_manifest_digest": "2" * 64,
        "accepted_delivery_record_digest": "3" * 64,
        "cleanup_authority_event_digest": "4" * 64,
        "created_at": "2026-09-06T12:00:00Z",
    }
    if field.startswith("run_directory_identity."):
        tombstone["run_directory_identity"][field.rsplit(".", 1)[1]] = value
    else:
        tombstone[field] = value
    tombstone["tombstone_digest"] = helper.canonical_digest(
        tombstone, "tombstone_digest"
    )
    tombstone_path = tmp_path / f"{field.replace('.', '-')}.json"
    tombstone_path.write_bytes(helper.canonical_json_bytes(tombstone))
    os.chmod(tombstone_path, 0o600)

    with pytest.raises(helper.RunStateError, match="tombstone|target|timestamp|identity"):
        helper._validate_tombstone(tombstone_path, workflow_id)


def test_parent_v3_resolution_remains_loadable_and_recoverable(
    tmp_path: Path,
) -> None:
    """The previously emitted v3 marker set keeps its exact replay meaning."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    rewrite_current_resolution_version(
        helper,
        state_root,
        str(started["workflow_id"]),
        resolution_schema="skill-builder-resolution.v3",
        candidate_schema="skill-builder-candidate.v3",
        scorecard_schema="skill-builder-scorecard.v2",
    )

    assert helper.load_run(
        workflow_id=str(started["workflow_id"]), state_root=state_root
    )["stage"] == "resolved"
    assert helper.recover_run(
        workflow_id=str(started["workflow_id"]), state_root=state_root
    )["stage"] == "resolved"


def test_finalized_parent_v3_candidate_semantics_remain_replayable(
    tmp_path: Path,
) -> None:
    """V3 keeps raw-SKILL trial identity, legacy review, and v2 scores."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(
        helper,
        tmp_path,
        candidate_schema_version="skill-builder-candidate.v3",
        trial_loaded_skill_bytes=CANDIDATE_SKILL_BYTES,
        historical_candidate_v3=True,
    )
    helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )

    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "finalized"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "finalized"


def test_current_v4_package_and_review_semantics_replay(
    tmp_path: Path,
) -> None:
    """V4 explicitly pairs package trials, component review, and v2 scores."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_current_trials_stage(
        helper, tmp_path
    )
    run = state_root / "live" / workflow_id
    resolution, _ = helper._resolution_payload(run, workflow_id)
    candidate_payload_record = helper._artifact_payload_json(run, "candidate")

    assert resolution["schema_version"] == "skill-builder-resolution.v4"
    assert resolution["candidate_record_schema"] == "skill-builder-candidate.v4"
    assert candidate_payload_record["schema_version"] == "skill-builder-candidate.v4"
    assert candidate["sequence"] < sequence
    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "trials"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "trials"


@pytest.mark.parametrize("artifact_kind", ("trial", "review"))
def test_replay_rejects_unselected_current_artifact_schema_downgrade(
    tmp_path: Path, artifact_kind: str
) -> None:
    """A retained decoy cannot bypass the run's schema pairing on replay."""
    helper = load_helper()
    if artifact_kind == "trial":
        state_root, workflow_id, sequence, candidate = build_candidate_stage(
            helper, tmp_path
        )
        payload = trial_payload(candidate["artifact_digest"])
        artifact_id = "unselected-trials"
        artifact_type = "trial-pack"
    else:
        state_root, workflow_id, sequence, candidate = build_current_trials_stage(
            helper, tmp_path
        )
        payload = review_payload(
            candidate["artifact_digest"],
            input_artifacts=review_input_provenance(
                helper, state_root, workflow_id
            ),
        )
        artifact_id = "unselected-review"
        artifact_type = "review-record"
    retained = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        payload=payload,
        input_bindings=(
            current_bindings(helper, state_root, workflow_id)
            if artifact_kind == "trial"
            else review_envelope_bindings(helper, state_root, workflow_id)
        ),
    )
    run = state_root / "live" / workflow_id
    downgraded = helper._artifact_payload_json(run, artifact_id)
    if artifact_kind == "trial":
        downgraded["schema_version"] = "skill-builder-trial-pack.v1"
    else:
        downgraded["schema_version"] = "skill-builder-review.v1"
        downgraded["input_artifacts"] = {
            "candidate": candidate["artifact_digest"]
        }
    rewrite_artifact_payload_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id=artifact_id,
        payload=downgraded,
        rewrite_receipts=True,
    )
    assert retained["stage"] in {"candidate", "trials"}

    with pytest.raises(helper.RunStateError, match="schema|version|semantics"):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="schema|version|semantics"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)


def test_replay_rejects_candidate_isolated_locator_content_drift(
    tmp_path: Path,
) -> None:
    """Replay rechecks the actual isolated package, not only its retained copy."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_candidate_stage(helper, tmp_path)
    (tmp_path / "candidate" / "SKILL.md").write_bytes(
        b"---\nname: substituted\n---\n# Drifted candidate\n"
    )

    with pytest.raises(helper.RunStateError, match="candidate|loaded|package|locator"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match="candidate|loaded|package|locator"):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_replay_allows_reusing_a_superseded_candidate_locator(
    tmp_path: Path,
) -> None:
    """Only the accepted candidate owns live locator equality after invalidation."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_candidate_stage(helper, tmp_path)
    invalidated = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        change_kind="candidate",
        changed_artifact_id="candidate",
        reason="prepare an explicitly invalidated replacement",
        state_root=state_root,
    )
    (tmp_path / "candidate" / "SKILL.md").write_bytes(
        b"---\nname: replacement\n---\n# Replacement candidate\n"
    )

    assert invalidated["stage"] == "evaluation"
    assert helper.load_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "evaluation"
    assert helper.recover_run(
        workflow_id=workflow_id, state_root=state_root
    )["stage"] == "evaluation"


@pytest.mark.parametrize(
    "omitted_component",
    ("candidate-diff", "candidate-result", "evaluation-rubric"),
)
def test_review_rejects_component_claims_without_retained_source_bytes(
    tmp_path: Path, omitted_component: str
) -> None:
    """Diff, result, and rubric claims cannot stand in for supplied reviewer bytes."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_current_trials_stage(
        helper,
        tmp_path,
        retain_review_source_components=True,
        omitted_review_source_component=omitted_component,
    )
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="unresolved-review",
        artifact_type="review-record",
        payload=review_payload(
            candidate["artifact_digest"],
            input_artifacts=review_input_provenance(
                helper, state_root, workflow_id
            ),
        ),
        input_bindings=review_envelope_bindings(
            helper, state_root, workflow_id
        ),
    )

    with pytest.raises(helper.RunStateError, match="review|component|retained|provenance"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=review["sequence"],
            event="accept-review",
            destination_stage="reviewed",
            artifact_ids=["unresolved-review"],
            state_root=state_root,
        )


def test_trial_validation_visits_manifest_entries_linearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding cases must not rescan the complete validated trial manifest per case."""
    helper = load_helper()
    state_root, workflow_id, _, _ = build_current_trials_stage(helper, tmp_path)
    run = state_root / "live" / workflow_id
    _, manifest = helper._validate_envelope(run, "trials")
    entry_count = len(manifest["entries"])
    visits = 0
    original_validate_envelope = helper._validate_envelope

    class CountingEntries(list[dict[str, object]]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            nonlocal visits
            for entry in super().__iter__():
                visits += 1
                yield entry

    def counted_validate_envelope(
        counted_run: Path, artifact_id: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        envelope, counted_manifest = original_validate_envelope(
            counted_run, artifact_id
        )
        if artifact_id == "trials":
            counted_manifest = dict(counted_manifest)
            counted_manifest["entries"] = CountingEntries(
                counted_manifest["entries"]
            )
        return envelope, counted_manifest

    monkeypatch.setattr(helper, "_validate_envelope", counted_validate_envelope)
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)[
        "stage"
    ] == "trials"
    assert visits <= 2 * entry_count
