from __future__ import annotations

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
) -> dict[str, object]:
    return helper.retain_artifact(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        files={
            "record.json": (
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        },
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
        partitions[partition] = [
            {
                "case_id": f"case-{index}",
                "partition": partition,
                "purpose": f"exercise {partition}",
                "raw_request_digest": f"{index}" * 64,
                "allowed_context": ["skill contract"],
                "setup_manifest_digest": "a" * 64,
                "observable_assertions": ["returns pass"],
                "forbidden_effects": ["remote write"],
                "evidence_requirements": ["raw output"],
                "pass_fail_rule": "all assertions pass",
            }
        ]
    return {
        "schema_version": "skill-builder-evaluation-pack.v1",
        "frozen": True,
        "contract_digest": contract_digest,
        "confirmation_digest": confirmation_digest,
        "target_snapshot_digest": snapshot_digest,
        "rubric_digest": "f" * 64,
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
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-candidate.v1",
        "candidate_id": candidate_id,
        "isolated_locator": str((tmp_path / "candidate").resolve()),
        "base_snapshot_digest": snapshot_digest,
        "candidate_revision": revision,
        "resulting_digest": "1" * 64,
        "writable_role": "implementer",
        "owned_paths": ["SKILL.md"],
        "diff_digest": "2" * 64,
        "local_check_evidence": ["3" * 64],
        "contract_digest": contract_digest,
        "confirmation_digest": confirmation_digest,
        "evaluation_digest": evaluation_digest,
        "target_snapshot_digest": snapshot_digest,
    }


def trial_payload(candidate_digest: str, revision: str = "candidate-1") -> dict[str, object]:
    return {
        "schema_version": "skill-builder-trial-pack.v1",
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "cases": [{"case_id": "case-1", "verdict": "pass"}],
        "coverage": ["visible", "frozen", "hidden"],
        "isolation_evidence": ["fresh context"],
        "leakage_checks": ["no hidden leakage"],
        "aggregate_manifest_digest": "4" * 64,
        "status": "pass",
        "limitations": [],
    }


def review_payload(
    candidate_digest: str,
    *,
    revision: str = "candidate-1",
    fresh: bool = True,
    findings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-review.v1",
        "reviewer_identity": "independent-reviewer",
        "independent": True,
        "read_only": True,
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "input_artifacts": {"candidate": candidate_digest},
        "access_check_evidence": ["5" * 64],
        "fresh": fresh,
        "contamination_check": "no implementation context disclosed",
        "valid": True,
        "findings": findings or [],
        "verdict": "ready",
        "reviewed_at": "2026-09-05T12:02:00Z",
    }


def conformance_payload(candidate_digest: str) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-conformance.v1",
        "candidate_digest": candidate_digest,
        "gates": {
            gate: {
                "status": "pass",
                "evidence_digests": ["a" * 64],
                "affected_stage": "verified",
                "repair_state": "not-required",
                "release_blocking": True,
            }
            for gate in (
                "research",
                "confirmation",
                "acceptance-first",
                "isolation",
                "evidence",
                "state",
                "authority",
                "cleanup",
            )
        },
    }


def scorecard_payload(
    *,
    candidate_digest: str,
    evaluation_digest: str,
    review_digest: str,
    triggering_score: int = 10,
    revision: str = "candidate-1",
) -> dict[str, object]:
    return {
        "schema_version": "skill-builder-scorecard.v1",
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "rubric_digest": "f" * 64,
        "evaluation_digest": evaluation_digest,
        "review_digest": review_digest,
        "categories": [
            {
                "name": name,
                "score": triggering_score if name == "triggering" else 10,
                "criteria": {"criterion-1": True},
                "frozen_parameter_identifiers": ["criterion-1"],
                "evidence_identifiers": ["evidence-1"],
                "related_findings": [],
                "repair_history": [],
            }
            for name in SCORE_CATEGORIES
        ],
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
                "output_digest": "b" * 64,
            }
        ],
        "started_at": "2026-09-05T12:03:00Z",
        "ended_at": "2026-09-05T12:04:00Z",
        "before_manifest_digest": "6" * 64,
        "after_manifest_digest": "6" * 64,
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
        "release_evidence_manifest_digest": "7" * 64,
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
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
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
) -> tuple[Path, str, int, dict[str, object]]:
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper,
        tmp_path,
        queued_targets=queued_targets,
        granted_authority=granted_authority,
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
            fresh=review_fresh,
            findings=review_findings,
        ),
        input_bindings=current_bindings(helper, state_root, workflow_id),
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
        input_bindings=[],
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
    delivery_authority = "3" * 64
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": candidate_revision,
            "resulting_destination_digest": "c" * 64,
            "acceptance_evidence": ["d" * 64],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        state_root=state_root,
    )
    cleanup_authority = "4" * 64
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=cleanup_authority,
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
    authority_digest = "5" * 64
    rejected_delivery = {
        "action": "installation",
        "destination_identity": "codex:personal:sample-skill",
        "finalized_revision": "candidate-1",
        "resulting_destination_digest": "c" * 64,
        "acceptance_evidence": ["d" * 64],
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
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery=accepted_delivery,
        authority_event_digest=authority_digest,
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
    delivery_authority = "6" * 64
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": "c" * 64,
            "acceptance_evidence": ["d" * 64],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        state_root=state_root,
    )
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest="7" * 64,
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
            "resulting_destination_digest": "c" * 64,
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
    delivery_authority = "9" * 64
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": "c" * 64,
            "acceptance_evidence": ["d" * 64],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        state_root=state_root,
    )
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest="a" * 64,
        actor="user",
        state_root=state_root,
    )
    original_remove = helper._remove_owned_tree

    def partially_remove(path: Path) -> None:
        (path / "current.json").unlink(missing_ok=True)
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
    )
    second = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=first["sequence"],
        artifact_id="baseline-two",
        artifact_type="baseline-report",
        payload=payload,
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
    )
    second = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=first["sequence"],
        artifact_id="baseline-two",
        artifact_type="baseline-report",
        payload=payload,
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
                    "evidence": ["a" * 64],
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
    delivery_authority = "b" * 64
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": "c" * 64,
            "acceptance_evidence": ["d" * 64],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        state_root=state_root,
    )
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest="e" * 64,
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

    manifest_path = state_root / "live" / workflow_id / "final-run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["target_identity"] == "codex:personal:sample-skill"
    assert manifest["finalization_receipt_digest"] == finalized["receipt_digest"]
    assert manifest["release_record_digest"] == finalized["release_artifact_digest"]
    assert manifest["accepted_delivery_record_digest"] == delivery[
        "delivery_artifact_digest"
    ]
    assert manifest["head_transition_digest"] == authorized["receipt_digest"]
