from __future__ import annotations

import ast
import copy
import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
LEGACY_FIXTURES = FIXTURES / "improve-skill"
BUILDER_FIXTURES = FIXTURES / "skill-builder"
SKILL_ROOT = ROOT / "plugins" / "codex-dev-flow" / "skills" / "skill-builder"
PUBLIC_SKILLS_ROOT = ROOT / "plugins" / "codex-dev-flow" / "skills"
EXPECTED_SCENARIOS = {
    "create_non_git_near_neighbour",
    "improve_exact_malformed",
    "create_exact_collision",
    "ambiguous_identity_after_facts",
    "multi_target_single_active",
    "stale_confirmation_after_contract_change",
    "snapshot_change_after_freeze",
    "research_evidence_missing_or_contaminated",
    "unauthorized_production_write_or_delivery",
    "valid_negative_review_repairs",
    "false_category_ten_missing_evidence",
    "pause_resume_validated_state",
    "premature_cleanup_without_authority",
    "goal_change_selective_invalidation",
    "static_validation_is_not_completion",
    "hidden_or_sibling_leakage",
}
RESEARCH_ROLES = {
    "domain-techniques",
    "agent-skill-design",
    "evaluation-methods",
}
TARGET_CATEGORIES = (
    "Triggering",
    "Scope discipline",
    "Workflow quality",
    "Collaboration",
    "Output contract",
    "Safety",
    "Recovery",
    "Composability",
    "Context efficiency",
    "Testability",
)
CATEGORY_CRITERION_IDS = {
    "triggering": frozenset(f"TR{index}" for index in range(1, 11)),
    "scope discipline": frozenset(f"SC{index}" for index in range(1, 11)),
    "workflow quality": frozenset(f"WF{index}" for index in range(1, 11)),
    "collaboration": frozenset(f"CO{index}" for index in range(1, 11)),
    "output contract": frozenset(f"OU{index}" for index in range(1, 11)),
    "safety": frozenset(f"SA{index}" for index in range(1, 11)),
    "recovery": frozenset(f"RE{index}" for index in range(1, 11)),
    "composability": frozenset(f"CP{index}" for index in range(1, 11)),
    "context efficiency": frozenset(f"CE{index}" for index in range(1, 11)),
    "testability": frozenset(f"TE{index}" for index in range(1, 11)),
}
CONFORMANCE_GATE_IDS = frozenset(f"BR{index}" for index in range(1, 11))
WORKFLOW_ID_RE = re.compile(r"[0-9a-f]{32}")
REVIEW_REQUIRED_ARTIFACTS = frozenset(
    {
        "artifact-manifest",
        "candidate-diff",
        "candidate-manifest",
        "confirmed-contract",
        "evaluation-pack",
        "host-rules",
        "preserved-regressions",
        "raw-trial-evidence",
        "rubric",
    }
)
GATE_EVIDENCE_TYPES = frozenset({"raw-trial-evidence", "trial-receipt"})
CATEGORY_EVIDENCE_TYPES = frozenset(
    {"category-evidence", "raw-trial-evidence", "trial-receipt"}
)
CONFORMANCE_GATE_ARTIFACT_TYPES = {
    "BR1": frozenset({"resolution-record"}),
    "BR2": frozenset({"baseline-report"}),
    "BR3": frozenset({"research-pack", "evidence-sieve"}),
    "BR4": frozenset(
        {"design-record", "skill-contract", "user-confirmation-record"}
    ),
    "BR5": frozenset({"evaluation-pack"}),
    "BR6": frozenset({"candidate-record"}),
    "BR7": frozenset(
        {"raw-trial-evidence", "trial-receipt", "trial-pack"}
    ),
    "BR8": frozenset({"independence-ledger"}),
    "BR9": frozenset({"transition-ledger"}),
    "BR10": frozenset({"authority-record"}),
}
PRE_CANDIDATE_ARTIFACT_TYPES = frozenset(
    {
        "resolution-record",
        "baseline-report",
        "research-pack",
        "evidence-sieve",
        "design-record",
        "skill-contract",
        "user-confirmation-record",
        "evaluation-pack",
        "authority-record",
    }
)
CANDIDATE_BOUND_ARTIFACT_TYPES = frozenset(
    {
        "candidate-record",
        "raw-trial-evidence",
        "trial-receipt",
        "trial-pack",
        "independence-ledger",
        "transition-ledger",
        "artifact-manifest",
        "builder-run-conformance-ledger",
        "review-record",
        "category-evidence",
        "target-scorecard",
        "spec-outcome-record",
        "verification-record",
        "release-record",
    }
)
EVALUATION_BOUND_ARTIFACT_TYPES = CANDIDATE_BOUND_ARTIFACT_TYPES | {
    "evaluation-pack"
}
CONTRACT_BOUND_ARTIFACT_TYPES = EVALUATION_BOUND_ARTIFACT_TYPES | {
    "skill-contract",
    "user-confirmation-record",
}
RETAINED_EVENT_ARTIFACT_TYPES = frozenset(
    {
        "authority-record",
        "raw-trial-evidence",
        "trial-receipt",
        "trial-pack",
        "independence-ledger",
        "transition-ledger",
        "artifact-manifest",
        "category-evidence",
    }
)
REVIEW_EVIDENCE_ARTIFACT_TYPES = frozenset(
    {
        "resolution-record",
        "baseline-report",
        "skill-contract",
        "evaluation-pack",
        "candidate-record",
        "raw-trial-evidence",
        "trial-pack",
        "artifact-manifest",
        "builder-run-conformance-ledger",
    }
)
REVIEW_SEVERITIES = frozenset({"High", "Medium", "Low"})
CRITERION_CATEGORY = {
    criterion_id: category
    for category, criterion_ids in CATEGORY_CRITERION_IDS.items()
    for criterion_id in criterion_ids
}
AUTHORING_AND_SCORING_ACTOR_ROLES = frozenset(
    {
        *RESEARCH_ROLES,
        "candidate-implementer",
        "designer",
        "main-agent",
        "researcher",
        "skill-designer",
        "target-scorer",
    }
)
REVIEW_BARRED_ACTOR_ROLES = frozenset(
    {
        *AUTHORING_AND_SCORING_ACTOR_ROLES,
        "independent-spec-judge",
        "independent-verifier",
    }
)
VERIFIER_BARRED_ACTOR_ROLES = frozenset(
    {
        *AUTHORING_AND_SCORING_ACTOR_ROLES,
        "independent-spec-judge",
        "independent-target-reviewer",
    }
)
SPEC_JUDGE_BARRED_ACTOR_ROLES = frozenset(
    {
        *AUTHORING_AND_SCORING_ACTOR_ROLES,
        "independent-target-reviewer",
        "independent-verifier",
    }
)
SCORER_BARRED_ACTOR_ROLES = frozenset(
    {
        *RESEARCH_ROLES,
        "candidate-implementer",
        "designer",
        "main-agent",
        "researcher",
        "skill-designer",
        "independent-spec-judge",
        "independent-target-reviewer",
        "independent-verifier",
    }
)
IMPLEMENTER_BARRED_ACTOR_ROLES = frozenset(
    {
        *RESEARCH_ROLES,
        "designer",
        "independent-spec-judge",
        "independent-target-reviewer",
        "independent-verifier",
        "main-agent",
        "researcher",
        "skill-designer",
        "target-scorer",
    }
)


def load_traces(partition: str) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(
        (BUILDER_FIXTURES / partition / "traces.json").read_text(encoding="utf-8")
    )
    return payload["traces"]


def load_scenarios(partition: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(
        (BUILDER_FIXTURES / partition / "scenarios.json").read_text(encoding="utf-8")
    )
    return {scenario["id"]: scenario for scenario in payload["scenarios"]}


def parse_frontmatter(contents: str) -> dict[str, str]:
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing frontmatter")
    end = lines.index("---", 1)
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, raw_value = line.partition(":")
        if not separator:
            raise ValueError(f"invalid frontmatter line: {line!r}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
            value = ast.literal_eval(value)
        values[key.strip()] = value
    return values


def parse_two_level_metadata(contents: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    current: dict[str, object] | None = None
    for line in contents.splitlines():
        if not line.strip():
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw_value = line.strip().partition(":")
        if not separator:
            raise ValueError(f"invalid metadata line: {line!r}")
        if indentation == 0:
            if raw_value.strip():
                raise ValueError(f"root metadata value must be a mapping: {line!r}")
            current = {}
            result[key] = current
            continue
        if indentation != 2 or current is None:
            raise ValueError(f"invalid metadata indentation: {line!r}")
        value: object = raw_value.strip()
        if value in {"true", "false"}:
            value = value == "true"
        elif (
            len(str(value)) >= 2
            and str(value)[0] == str(value)[-1]
            and str(value)[0] in {'\"', "'"}
        ):
            value = ast.literal_eval(str(value))
        current[key] = value
    return result


@dataclass(frozen=True)
class OracleFailure:
    code: str
    event_index: int
    message: str
    ledger: str = "builder-run"


def evidence_bound_finding(
    trace: list[dict[str, Any]],
    finding_id: str,
    severity: str,
    criterion_id: str,
) -> dict[str, Any]:
    raw_artifact_id = next(
        event["artifact_id"]
        for event in trace
        if event.get("artifact_type") == "raw-trial-evidence"
    )
    return {
        "id": finding_id,
        "severity": severity,
        "evidence": [raw_artifact_id],
        "impact": f"The candidate fails frozen target criterion {criterion_id}.",
        "correction": f"Repair {criterion_id} and rerun its frozen cases.",
        "affected_criteria": [criterion_id],
        "affected_categories": [CRITERION_CATEGORY[criterion_id]],
    }


def accepted_finalization_trace(
    revision: str = "candidate-v1",
) -> list[dict[str, Any]]:
    workflow_id = "b" * 32
    target_identity = "fixture-terse-summary"
    artifact_suffix = "" if revision == "candidate-v1" else f"-{revision}"
    scoring_review_id = f"scoring-review-v1{artifact_suffix}"
    final_review_id = f"final-review-v1{artifact_suffix}"
    category_proof_ids = {
        category: f"accepted-{category.replace(' ', '-')}-proof{artifact_suffix}"
        for category in CATEGORY_CRITERION_IDS
    }
    resolution_id = "resolution-v1"
    baseline_id = "baseline-v1"
    research_id = "research-pack-v1"
    sieve_id = "evidence-sieve-v1"
    design_id = "design-record-v1"
    contract_id = "skill-contract-v1"
    confirmation_id = "user-confirmation-v1"
    evaluation_id = "evaluation-pack-v1"
    authority_id = "authority-record-v1"
    candidate_id = f"candidate-record-v1{artifact_suffix}"
    raw_trial_id = f"accepted-raw-trial-evidence{artifact_suffix}"
    trial_receipt_id = f"accepted-trial-receipt{artifact_suffix}"
    trial_pack_id = f"accepted-trial-pack{artifact_suffix}"
    independence_id = f"independence-ledger{artifact_suffix}"
    transition_id = f"transition-ledger{artifact_suffix}"
    artifact_manifest_id = f"artifact-manifest{artifact_suffix}"
    conformance_id = f"builder-conformance-v1{artifact_suffix}"
    scorecard_id = f"target-scorecard-v1{artifact_suffix}"
    spec_id = f"spec-outcome-v1{artifact_suffix}"
    verification_id = f"verification-record-v1{artifact_suffix}"
    release_id = f"release-record-v1{artifact_suffix}"
    target_snapshot = "fixture-improve-snapshot-v1"
    base_binding = {
        "workflow_id": workflow_id,
        "target_identity": target_identity,
        "target_snapshot": target_snapshot,
        "identity_generation": 1,
        "snapshot_generation": 1,
    }
    contract_binding = {
        **base_binding,
        "contract_digest": "contract-v1",
    }
    evaluation_binding = {
        **contract_binding,
        "evaluation_digest": "evaluation-v1",
    }
    binding = {
        **evaluation_binding,
        "revision": revision,
    }
    review_evidence = [
        resolution_id,
        baseline_id,
        contract_id,
        evaluation_id,
        candidate_id,
        raw_trial_id,
        trial_pack_id,
        artifact_manifest_id,
        conformance_id,
    ]
    gate_evidence = {
        "BR1": [resolution_id],
        "BR2": [baseline_id],
        "BR3": [research_id, sieve_id],
        "BR4": [design_id, contract_id, confirmation_id],
        "BR5": [evaluation_id],
        "BR6": [candidate_id],
        "BR7": [raw_trial_id, trial_receipt_id, trial_pack_id],
        "BR8": [independence_id],
        "BR9": [transition_id],
        "BR10": [authority_id],
    }
    manifested_artifact_ids = [
        resolution_id,
        baseline_id,
        research_id,
        sieve_id,
        design_id,
        contract_id,
        confirmation_id,
        evaluation_id,
        authority_id,
        candidate_id,
        raw_trial_id,
        trial_receipt_id,
        trial_pack_id,
        independence_id,
        transition_id,
    ]
    category_proofs = [
        {
            "event": "artifact_retained",
            "artifact_id": category_proof_ids[category],
            "artifact_type": "category-evidence",
            "category": category,
            "criterion_ids": sorted(criterion_ids),
            "frozen_parameter_ids": [
                f"parameter-{criterion_id}" for criterion_id in sorted(criterion_ids)
            ],
            "case_ids": ["visible-case-1", "frozen-case-1", "hidden-case-1"],
            "raw_artifact_ids": [raw_trial_id],
            "trial_receipt_ids": [trial_receipt_id],
            "review_finding_ids": [],
            "review_id": scoring_review_id,
            "input_artifact_ids": [
                evaluation_id,
                raw_trial_id,
                trial_receipt_id,
                scoring_review_id,
            ],
            "valid": True,
            **binding,
        }
        for category, criterion_ids in CATEGORY_CRITERION_IDS.items()
    ]
    scorecard_evidence = [
        conformance_id,
        scoring_review_id,
        *category_proof_ids.values(),
    ]
    final_review_evidence = [*review_evidence, scorecard_id]
    spec_evidence = [
        contract_id,
        evaluation_id,
        candidate_id,
        scorecard_id,
        final_review_id,
    ]
    verification_evidence = [
        candidate_id,
        trial_pack_id,
        conformance_id,
        scorecard_id,
        final_review_id,
        spec_id,
    ]
    release_evidence_ids = [
        contract_id,
        confirmation_id,
        evaluation_id,
        candidate_id,
        artifact_manifest_id,
        conformance_id,
        scorecard_id,
        final_review_id,
        spec_id,
        verification_id,
    ]
    scores = [
        {
            "event": "category_scored",
            "category": category,
            "score": 10,
            "review_id": scoring_review_id,
            "scorer_identity": "target-scorer-v1",
            "scorer_role": "target-scorer",
            "criteria": [
                {
                    "id": criterion_id,
                    "passed": True,
                    "evidence": [category_proof_ids[category]],
                }
                for criterion_id in sorted(criterion_ids)
            ],
            **binding,
        }
        for category, criterion_ids in CATEGORY_CRITERION_IDS.items()
    ]
    return [
        {
            "event": "resolve",
            "selected_mode": "improve",
            "target_manifest": "targets/exact-improve/manifest.json",
            "target_snapshot": target_snapshot,
            "actor_identity": "main-agent-v1",
            "actor_role": "main-agent",
            "artifact_id": resolution_id,
            "artifact_type": "resolution-record",
            "input_artifact_ids": [],
            "valid": True,
            **base_binding,
        },
        {
            "event": "baseline_captured",
            "target_snapshot": target_snapshot,
            "artifact_id": baseline_id,
            "artifact_type": "baseline-report",
            "input_artifact_ids": [resolution_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "research_pack",
            "lanes": [
                {
                    "role": role,
                    "context_id": f"research-{index}",
                    "actor_identity": f"research-{index}",
                    "blind": True,
                    "contaminated_by": [],
                    "evidence_cards": 1,
                }
                for index, role in enumerate(sorted(RESEARCH_ROLES), start=1)
            ],
            "artifact_id": research_id,
            "artifact_type": "research-pack",
            "input_artifact_ids": [baseline_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "evidence_sieved",
            "card_count": 3,
            "decisions": ["adopt", "experiment", "reject"],
            "artifact_id": sieve_id,
            "artifact_type": "evidence-sieve",
            "input_artifact_ids": [research_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "designs_challenged",
            "alternative_count": 2,
            "actor_identity": "design-owner-v1",
            "actor_role": "skill-designer",
            "artifact_id": design_id,
            "artifact_type": "design-record",
            "input_artifact_ids": [sieve_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "contract_written",
            "contract_digest": "contract-v1",
            "artifact_id": contract_id,
            "artifact_type": "skill-contract",
            "input_artifact_ids": [baseline_id, sieve_id, design_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "user_confirmed",
            "contract_digest": "contract-v1",
            "accepted": True,
            "artifact_id": confirmation_id,
            "artifact_type": "user-confirmation-record",
            "input_artifact_ids": [contract_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "evaluation_frozen",
            "contract_digest": "contract-v1",
            "evaluation_digest": "evaluation-v1",
            "target_snapshot": target_snapshot,
            "partitions": ["visible", "frozen-validation", "hidden-release"],
            "case_ids": ["visible-case-1", "frozen-case-1", "hidden-case-1"],
            "frozen_parameter_ids": [
                f"parameter-{criterion_id}"
                for criterion_ids in CATEGORY_CRITERION_IDS.values()
                for criterion_id in sorted(criterion_ids)
            ],
            "artifact_id": evaluation_id,
            "artifact_type": "evaluation-pack",
            "input_artifact_ids": [contract_id, confirmation_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": authority_id,
            "artifact_type": "authority-record",
            "authorized_delivery_scope": "none",
            "cleanup_authorized": False,
            "input_artifact_ids": [resolution_id],
            "valid": True,
            **base_binding,
        },
        {
            "event": "candidate_edit",
            "candidate_revision": revision,
            "revision": revision,
            "actor_identity": "candidate-implementer-v1",
            "actor_role": "candidate-implementer",
            "artifact_id": candidate_id,
            "artifact_type": "candidate-record",
            "input_artifact_ids": [contract_id, confirmation_id, evaluation_id],
            "valid": True,
            **evaluation_binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": raw_trial_id,
            "artifact_type": "raw-trial-evidence",
            "case_ids": ["visible-case-1", "frozen-case-1", "hidden-case-1"],
            "raw_artifact_digests": ["prompt-v1", "output-v1", "tools-v1"],
            "input_artifact_ids": [candidate_id, evaluation_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": trial_receipt_id,
            "artifact_type": "trial-receipt",
            "case_ids": ["visible-case-1", "frozen-case-1", "hidden-case-1"],
            "fresh_context_ids": ["trial-context-1", "trial-context-2", "trial-context-3"],
            "input_artifact_ids": [candidate_id, evaluation_id, raw_trial_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": trial_pack_id,
            "artifact_type": "trial-pack",
            "case_ids": ["visible-case-1", "frozen-case-1", "hidden-case-1"],
            "trial_receipt_ids": [trial_receipt_id],
            "raw_artifact_ids": [raw_trial_id],
            "input_artifact_ids": [
                candidate_id,
                evaluation_id,
                raw_trial_id,
                trial_receipt_id,
            ],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": independence_id,
            "artifact_type": "independence-ledger",
            "actors": {
                "main": "main-agent-v1",
                "design": "design-owner-v1",
                "research_domain": "research-2",
                "research_design": "research-1",
                "research_evaluation": "research-3",
                "implementer": "candidate-implementer-v1",
                "scorer": "target-scorer-v1",
                "scoring_reviewer": "scoring-reviewer-v1",
                "final_reviewer": "final-reviewer-v1",
                "spec_judge": "spec-judge-v1",
                "verifier": "target-verifier-v1",
            },
            "input_artifact_ids": [candidate_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": transition_id,
            "artifact_type": "transition-ledger",
            "ordered_stage_artifact_ids": [
                resolution_id,
                baseline_id,
                research_id,
                sieve_id,
                design_id,
                contract_id,
                confirmation_id,
                evaluation_id,
                candidate_id,
                trial_pack_id,
            ],
            "input_artifact_ids": [
                resolution_id,
                baseline_id,
                research_id,
                sieve_id,
                design_id,
                contract_id,
                confirmation_id,
                evaluation_id,
                candidate_id,
                trial_pack_id,
            ],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": artifact_manifest_id,
            "artifact_type": "artifact-manifest",
            "manifested_artifact_ids": list(manifested_artifact_ids),
            "input_artifact_ids": list(manifested_artifact_ids),
            "valid": True,
            **binding,
        },
        {
            "event": "builder_conformance_recorded",
            "gates": [
                {
                    "id": gate_id,
                    "passed": True,
                    "evidence": gate_evidence[gate_id],
                }
                for gate_id in sorted(CONFORMANCE_GATE_IDS)
            ],
            "artifact_id": conformance_id,
            "artifact_type": "builder-run-conformance-ledger",
            "input_artifact_ids": [
                artifact_id
                for gate_id in sorted(CONFORMANCE_GATE_IDS)
                for artifact_id in gate_evidence[gate_id]
            ],
            "valid": True,
            **binding,
        },
        {
            "event": "review_recorded",
            "phase": "scoring",
            "review_id": scoring_review_id,
            "reviewer_identity": "scoring-reviewer-v1",
            "reviewer_role": "independent-target-reviewer",
            "valid": True,
            "verdict": "ready",
            "independent": True,
            "read_only": True,
            "findings": [],
            "supplied_artifacts": sorted(REVIEW_REQUIRED_ARTIFACTS),
            "accessed_artifacts": sorted(REVIEW_REQUIRED_ARTIFACTS),
            "forbidden_artifacts_accessed": [],
            "evidence": list(review_evidence),
            "artifact_id": scoring_review_id,
            "artifact_type": "review-record",
            "input_artifact_ids": list(review_evidence),
            **binding,
        },
        *category_proofs,
        *scores,
        {
            "event": "target_scorecard_recorded",
            "artifact_id": scorecard_id,
            "artifact_type": "target-scorecard",
            "review_id": scoring_review_id,
            "categories": list(CATEGORY_CRITERION_IDS),
            "evidence": list(scorecard_evidence),
            "input_artifact_ids": list(scorecard_evidence),
            "valid": True,
            **binding,
        },
        {
            "event": "review_recorded",
            "phase": "final",
            "review_id": final_review_id,
            "reviewer_identity": "final-reviewer-v1",
            "reviewer_role": "independent-target-reviewer",
            "valid": True,
            "verdict": "ready",
            "independent": True,
            "read_only": True,
            "findings": [],
            "supplied_artifacts": sorted(REVIEW_REQUIRED_ARTIFACTS),
            "accessed_artifacts": sorted(REVIEW_REQUIRED_ARTIFACTS),
            "forbidden_artifacts_accessed": [],
            "evidence": list(final_review_evidence),
            "artifact_id": final_review_id,
            "artifact_type": "review-record",
            "input_artifact_ids": list(final_review_evidence),
            **binding,
        },
        {
            "event": "spec_outcome_recorded",
            "artifact_id": spec_id,
            "artifact_type": "spec-outcome-record",
            "valid": True,
            "outcome": "pass",
            "independent": True,
            "read_only": True,
            "judge_identity": "spec-judge-v1",
            "judge_role": "independent-spec-judge",
            "evidence": list(spec_evidence),
            "input_artifact_ids": list(spec_evidence),
            **binding,
        },
        {
            "event": "verification_recorded",
            "artifact_id": verification_id,
            "artifact_type": "verification-record",
            "valid": True,
            "behavioral_trials": 1,
            "exit_status": 0,
            "conclusion": "pass",
            "independent": True,
            "read_only": True,
            "verifier_identity": "target-verifier-v1",
            "verifier_role": "independent-verifier",
            "evidence": list(verification_evidence),
            "input_artifact_ids": list(verification_evidence),
            **binding,
        },
        {
            "event": "release_evidence_retained",
            "artifact_id": release_id,
            "artifact_type": "release-record",
            "valid": True,
            "evidence": list(release_evidence_ids),
            "input_artifact_ids": list(release_evidence_ids),
            **binding,
        },
        {"event": "finalized", **binding},
    ]


def finalization_trace_after_aba(
    transition: str,
    *,
    stale_prerequisites: bool,
) -> list[dict[str, Any]]:
    """Rebuild a complete trace after an ABA transition with fresh artifact IDs."""

    original = accepted_finalization_trace()
    candidate_index = next(
        index
        for index, event in enumerate(original)
        if event["event"] == "candidate_edit"
    )
    prefix = copy.deepcopy(original[:candidate_index])
    replay = copy.deepcopy(original)
    id_map = {
        event["artifact_id"]: f"{event['artifact_id']}-{transition}-replay"
        for event in replay
        if isinstance(event.get("artifact_id"), str)
    }

    def remap(value: Any, *, field: str | None = None) -> Any:
        if isinstance(value, str):
            return (
                value
                if field
                in {
                    "artifact_type",
                    "supplied_artifacts",
                    "accessed_artifacts",
                    "forbidden_artifacts_accessed",
                }
                else id_map.get(value, value)
            )
        if isinstance(value, list):
            return [remap(item, field=field) for item in value]
        if isinstance(value, dict):
            return {key: remap(item, field=key) for key, item in value.items()}
        return value

    replay = remap(replay)
    if transition in {"workflow", "target"}:
        foreign = copy.deepcopy(original[0])
        foreign.update(
            {
                "artifact_id": f"foreign-{transition}-resolution",
                "workflow_id": "c" * 32,
                "identity_generation": 2,
                "snapshot_generation": 2,
            }
        )
        if transition == "target":
            foreign.update(
                {
                    "selected_mode": "create",
                    "target_manifest": "targets/non-git-create/manifest.json",
                    "target_identity": "fixture-release-notes",
                    "target_snapshot": "fixture-create-snapshot-v1",
                }
            )
        transitions = [foreign]
        current_identity_generation = 3
        current_snapshot_generation = 3
    elif transition == "snapshot":
        transitions = [
            {"event": "target_snapshot_changed", "target_snapshot": "snapshot-v2"},
            {
                "event": "target_snapshot_changed",
                "target_snapshot": "fixture-improve-snapshot-v1",
            },
        ]
        current_identity_generation = 2
        current_snapshot_generation = 4
    else:
        raise ValueError(f"unsupported ABA transition: {transition}")

    for index, event in enumerate(replay):
        if "identity_generation" not in event:
            continue
        is_prerequisite = index < candidate_index
        event["identity_generation"] = (
            1 if stale_prerequisites and is_prerequisite else current_identity_generation
        )
        event["snapshot_generation"] = (
            1 if stale_prerequisites and is_prerequisite else current_snapshot_generation
        )

    return [*prefix, *transitions, *replay]


def cleanup_trace(
    run_directory: str,
    xdg_state_home: str | None = None,
    workflow_id: str = "a" * 32,
) -> list[dict[str, Any]]:
    state_home = xdg_state_home or str(BUILDER_FIXTURES / "private-state")
    binding = {
        "workflow_id": workflow_id,
        "target_identity": "fixture-terse-summary",
        "finalized_revision": "candidate-v1",
        "run_directory_identity": "run-directory-1",
    }
    return [
        {
            "event": "finalization_receipt_validated",
            "valid": True,
            "final_transition_digest": "transition-1",
            **binding,
        },
        {
            "event": "delivery_accepted",
            "valid": True,
            "accepted": True,
            "delivery_record_digest": "delivery-1",
            **binding,
        },
        {
            "event": "cleanup_authority_recorded",
            "valid": True,
            "effect": "cleanup",
            "authority_event_digest": "authority-1",
            **binding,
        },
        {
            "event": "run_state_manifest_validated",
            "valid": True,
            "xdg_state_home": state_home,
            "run_directory": run_directory,
            "manifest_digest": "manifest-1",
            "final_transition_digest": "transition-1",
            **binding,
        },
        {
            "event": "cleanup_tombstone_validated",
            "valid": True,
            "scope": "helper-owned-parent",
            "tombstone_digest": "tombstone-1",
            "tombstone_path": str(
                Path(state_home)
                / "codex-dev-flow"
                / "skill-builder"
                / "tombstones"
                / f"{workflow_id}.json"
            ),
            "final_transition_digest": "transition-1",
            "final_run_manifest_digest": "manifest-1",
            "authority_event_digest": "authority-1",
            "delivery_record_digest": "delivery-1",
            **binding,
        },
        {
            "event": "cleanup_attempt",
            "deletion_scope": "helper-owned-run-directory",
            "path": run_directory,
            **binding,
        },
    ]


def evaluate_trace(
    trace: list[dict[str, Any]],
    fixture_root: Path,
    scenario: dict[str, Any] | None = None,
) -> tuple[OracleFailure, ...]:
    failures: list[OracleFailure] = []
    current_contract: str | None = None
    confirmed_contract: str | None = None
    contract_epoch = 0
    confirmed_contract_epoch: int | None = None
    frozen_contract_epoch: int | None = None
    frozen_evaluation: dict[str, Any] | None = None
    evaluation_epoch = 0
    current_snapshot: str | None = None
    resolution_record: dict[str, Any] | None = None
    baseline_report: dict[str, Any] | None = None
    research_pack_record: dict[str, Any] | None = None
    sieve_record: dict[str, Any] | None = None
    design_record: dict[str, Any] | None = None
    contract_record: dict[str, Any] | None = None
    confirmation_record: dict[str, Any] | None = None
    candidate_record: dict[str, Any] | None = None
    trial_pack_record: dict[str, Any] | None = None
    independence_record: dict[str, Any] | None = None
    transition_record: dict[str, Any] | None = None
    authority_record: dict[str, Any] | None = None
    artifact_manifest_record: dict[str, Any] | None = None
    identity_generation = 0
    snapshot_generation = 0
    candidate_generation = 0
    identity_resolution_seen = False
    prerequisite_generation_invalid = False
    research_lanes: list[dict[str, Any]] | None = None
    active_target: str | None = None
    material_findings: dict[str, tuple[set[str], Any, int]] = {}
    verification: dict[str, Any] | None = None
    pause_record: dict[str, Any] | None = None
    identity_ambiguous = False
    current_workflow_id: str | None = None
    current_target_identity: str | None = None
    current_candidate_revision: str | None = None
    current_candidate_implementer: str | None = None
    current_candidate_implementer_role: str | None = None
    retained_artifacts: dict[str, dict[str, Any]] = {}
    conformance_record: dict[str, Any] | None = None
    scoring_review: dict[str, Any] | None = None
    final_review: dict[str, Any] | None = None
    spec_outcome: dict[str, Any] | None = None
    category_scores: dict[str, tuple[dict[str, Any], bool]] = {}
    target_scorecard: dict[str, Any] | None = None
    release_evidence: dict[str, Any] | None = None
    cleanup_authority: dict[str, Any] | None = None
    accepted_delivery: dict[str, Any] | None = None
    cleanup_tombstone: dict[str, Any] | None = None
    run_state_manifest: dict[str, Any] | None = None
    finalization_receipt: dict[str, Any] | None = None
    current_candidate_event_index: int | None = None
    candidate_contract_epoch: int | None = None
    candidate_evaluation_epoch: int | None = None
    candidate_snapshot_epoch: int | None = None
    frozen_snapshot_epoch: int | None = None
    snapshot_epoch = 0
    finalization_event_index: int | None = None
    pending_repair: dict[str, Any] | None = None
    record_event_indices: dict[int, int] = {}
    seen_finding_ids: set[str] = set()
    seen_artifact_ids: set[str] = set()
    ambiguous_artifact_ids: set[str] = set()
    seen_review_ids: set[str] = set()
    ambiguous_review_ids: set[str] = set()
    actor_roles: dict[str, set[str]] = {}
    artifact_generations: dict[str, tuple[int, int, int, int, int]] = {}
    invalidated_artifact_ids: set[str] = set()
    record_lifecycle_validity: dict[int, bool] = {}
    record_schema_validity: dict[int, bool] = {}

    def register_actor(identity: object, role: object) -> None:
        if (
            isinstance(identity, str)
            and bool(identity)
            and isinstance(role, str)
            and bool(role)
        ):
            actor_roles.setdefault(identity, set()).add(role)

    def actor_has_barred_role(identity: str, barred_roles: frozenset[str]) -> bool:
        return bool(actor_roles.get(identity, set()) & barred_roles)

    def record_binding(record: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any]:
        return (
            record.get("workflow_id"),
            record.get("target_identity"),
            record.get("revision"),
            record.get("contract_digest"),
            record.get("evaluation_digest"),
        )

    def current_binding() -> tuple[Any, Any, Any, Any, Any]:
        return (
            current_workflow_id,
            current_target_identity,
            current_candidate_revision,
            current_contract,
            (
                frozen_evaluation.get("evaluation_digest")
                if frozen_evaluation is not None
                else None
            ),
        )

    def record_postdates_candidate(record: dict[str, Any]) -> bool:
        return (
            current_candidate_event_index is not None
            and record_event_indices.get(id(record), -1)
            > current_candidate_event_index
        )

    def record_identity_binding_is_current(record: dict[str, Any]) -> bool:
        return (
            record.get("workflow_id") == current_workflow_id
            and record.get("target_identity") == current_target_identity
            and record.get("target_snapshot") == current_snapshot
            and record.get("identity_generation") == identity_generation
            and record.get("snapshot_generation") == snapshot_generation
        )

    def artifact_envelope_is_valid(
        record: dict[str, Any], expected_type: str
    ) -> bool:
        return (
            isinstance(record.get("artifact_id"), str)
            and bool(record["artifact_id"])
            and record.get("artifact_type") == expected_type
            and record.get("valid") is True
            and record_identity_binding_is_current(record)
        )

    def register_artifact(record: dict[str, Any], event_index: int) -> bool:
        artifact_id = record.get("artifact_id")
        artifact_type = record.get("artifact_type")
        if not isinstance(artifact_id, str) or not artifact_id:
            failures.append(
                OracleFailure(
                    "INVALID_ARTIFACT_SCHEMA",
                    event_index,
                    "a retained artifact lacked a nonempty immutable identity",
                )
            )
            return False
        if not isinstance(artifact_type, str) or not artifact_type:
            failures.append(
                OracleFailure(
                    "INVALID_ARTIFACT_SCHEMA",
                    event_index,
                    "a retained artifact lacked its contract artifact type",
                )
            )
            return False
        if artifact_id in seen_artifact_ids:
            failures.append(
                OracleFailure(
                    "DUPLICATE_ARTIFACT_ID",
                    event_index,
                    "a retained artifact reused an immutable identity",
                )
            )
            ambiguous_artifact_ids.add(artifact_id)
        else:
            seen_artifact_ids.add(artifact_id)
        retained_artifacts[artifact_id] = record
        artifact_generations[artifact_id] = (
            identity_generation,
            snapshot_generation,
            contract_epoch,
            evaluation_epoch,
            candidate_generation,
        )
        return True

    def artifact_is_current(artifact_id: str) -> bool:
        artifact = retained_artifacts.get(artifact_id)
        stamp = artifact_generations.get(artifact_id)
        if (
            artifact is None
            or stamp is None
            or artifact_id in ambiguous_artifact_ids
            or artifact_id in invalidated_artifact_ids
            or artifact.get("valid") is not True
            or not record_schema_validity.get(id(artifact), False)
        ):
            return False
        artifact_type = artifact.get("artifact_type")
        if stamp[0] != identity_generation or stamp[1] != snapshot_generation:
            return False
        if artifact_type in CONTRACT_BOUND_ARTIFACT_TYPES and stamp[2] != contract_epoch:
            return False
        if (
            artifact_type in EVALUATION_BOUND_ARTIFACT_TYPES
            and stamp[3] != evaluation_epoch
        ):
            return False
        if artifact_type in CANDIDATE_BOUND_ARTIFACT_TYPES and stamp[4] != candidate_generation:
            return False
        if artifact.get("workflow_id") != current_workflow_id:
            return False
        if artifact.get("target_identity") != current_target_identity:
            return False
        if artifact.get("target_snapshot") != current_snapshot:
            return False
        if artifact.get("identity_generation") != identity_generation:
            return False
        if artifact.get("snapshot_generation") != snapshot_generation:
            return False
        if artifact_type in CONTRACT_BOUND_ARTIFACT_TYPES and (
            artifact.get("contract_digest") != current_contract
        ):
            return False
        if artifact_type in EVALUATION_BOUND_ARTIFACT_TYPES and (
            frozen_evaluation is None
            or artifact.get("evaluation_digest")
            != frozen_evaluation.get("evaluation_digest")
        ):
            return False
        if artifact_type in CANDIDATE_BOUND_ARTIFACT_TYPES and (
            artifact.get("revision") != current_candidate_revision
        ):
            return False
        return True

    def resolved_artifacts(
        evidence_ids: object,
        consumer: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or not all(isinstance(artifact_id, str) for artifact_id in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            return None
        consumer_index = record_event_indices.get(id(consumer), -1)
        if not all(
            artifact_is_current(artifact_id)
            and record_event_indices.get(id(retained_artifacts[artifact_id]), consumer_index)
            < consumer_index
            for artifact_id in evidence_ids
        ):
            return None
        return [retained_artifacts[artifact_id] for artifact_id in evidence_ids]

    def exact_evidence_resolves(
        evidence_ids: object,
        required_ids: set[str],
        consumer: dict[str, Any],
    ) -> bool:
        records = resolved_artifacts(evidence_ids, consumer)
        return (
            records is not None
            and isinstance(evidence_ids, list)
            and set(evidence_ids) == required_ids
            and len(evidence_ids) == len(required_ids)
        )

    def consumer_inputs_are_exact(
        record: dict[str, Any], required_ids: set[str]
    ) -> bool:
        return exact_evidence_resolves(record.get("evidence"), required_ids, record) and (
            exact_evidence_resolves(
                record.get("input_artifact_ids"), required_ids, record
            )
        )

    def current_artifacts_of_type(artifact_type: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            artifact
            for artifact_id, artifact in retained_artifacts.items()
            if artifact_is_current(artifact_id)
            and artifact.get("artifact_type") == artifact_type
        )

    def evidence_types_resolve(
        evidence_ids: object,
        required_types: frozenset[str],
        consumer: dict[str, Any],
    ) -> bool:
        records = resolved_artifacts(evidence_ids, consumer)
        return (
            records is not None
            and {record.get("artifact_type") for record in records} == required_types
            and len(records) == len(required_types)
        )

    def evidence_resolves(
        evidence_ids: object,
        binding: tuple[Any, Any, Any, Any, Any],
        consumer: dict[str, Any],
        *,
        acceptable_types: frozenset[str] = GATE_EVIDENCE_TYPES,
        current_epoch: bool = False,
    ) -> bool:
        if current_workflow_id is not None:
            records = resolved_artifacts(evidence_ids, consumer)
            return (
                records is not None
                and all(
                    record.get("artifact_type") in acceptable_types
                    for record in records
                )
                and (
                    not current_epoch
                    or all(
                        record.get("artifact_type") in PRE_CANDIDATE_ARTIFACT_TYPES
                        or record_postdates_candidate(record)
                        for record in records
                    )
                )
            )
        consumer_index = record_event_indices.get(id(consumer), -1)
        return (
            isinstance(evidence_ids, list)
            and bool(evidence_ids)
            and all(
                isinstance(artifact_id, str)
                and artifact_id in retained_artifacts
                and artifact_id not in ambiguous_artifact_ids
                and retained_artifacts[artifact_id].get("valid") is True
                and retained_artifacts[artifact_id].get("artifact_type")
                in acceptable_types
                and record_binding(retained_artifacts[artifact_id]) == binding
                and record_event_indices.get(
                    id(retained_artifacts[artifact_id]), consumer_index
                )
                < consumer_index
                and (
                    not current_epoch
                    or record_postdates_candidate(retained_artifacts[artifact_id])
                )
                for artifact_id in evidence_ids
            )
        )

    def review_access_is_valid(review: dict[str, Any]) -> bool:
        supplied = review.get("supplied_artifacts")
        accessed = review.get("accessed_artifacts")
        forbidden = review.get("forbidden_artifacts_accessed")
        return (
            isinstance(supplied, list)
            and all(isinstance(item, str) for item in supplied)
            and len(supplied) == len(set(supplied))
            and set(supplied) == REVIEW_REQUIRED_ARTIFACTS
            and isinstance(accessed, list)
            and all(isinstance(item, str) for item in accessed)
            and len(accessed) == len(set(accessed))
            and set(accessed) == REVIEW_REQUIRED_ARTIFACTS
            and forbidden == []
        )

    def review_is_valid(
        review: dict[str, Any] | None,
        binding: tuple[Any, Any, Any, Any, Any],
        scorer_identities: set[str],
    ) -> bool:
        if review is None:
            return False
        review_id = review.get("review_id")
        reviewer_identity = review.get("reviewer_identity")
        full_bound_run = current_workflow_id is not None
        required_review_ids = {
            record.get("artifact_id")
            for record in (
                resolution_record,
                baseline_report,
                contract_record,
                frozen_evaluation,
                candidate_record,
                artifact_manifest_record,
                conformance_record,
            )
            if record is not None
        }
        required_review_ids.update(
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact_is_current(artifact_id)
            and artifact.get("artifact_type")
            in {"raw-trial-evidence", "trial-pack"}
        )
        if review.get("phase") == "final" and target_scorecard is not None:
            required_review_ids.add(target_scorecard.get("artifact_id"))
        expected_review_types = set(REVIEW_EVIDENCE_ARTIFACT_TYPES)
        if review.get("phase") == "final":
            expected_review_types.add("target-scorecard")
        review_evidence = review.get("evidence")
        review_evidence_types = {
            retained_artifacts[artifact_id].get("artifact_type")
            for artifact_id in review_evidence
            if isinstance(artifact_id, str)
            and artifact_id in retained_artifacts
        } if isinstance(review_evidence, list) else set()
        return (
            record_postdates_candidate(review)
            and record_binding(review) == binding
            and isinstance(review_id, str)
            and bool(review_id)
            and review_id not in ambiguous_review_ids
            and review.get("valid") is True
            and review.get("verdict") == "ready"
            and review.get("independent") is True
            and review.get("read_only") is True
            and isinstance(reviewer_identity, str)
            and bool(reviewer_identity)
            and review.get("reviewer_role") == "independent-target-reviewer"
            and isinstance(current_candidate_implementer, str)
            and bool(current_candidate_implementer)
            and current_candidate_implementer_role == "candidate-implementer"
            and reviewer_identity != current_candidate_implementer
            and reviewer_identity not in scorer_identities
            and (
                not full_bound_run
                or (
                    independence_record is not None
                    and reviewer_identity
                    == independence_record.get("actors", {}).get(
                        "final_reviewer"
                        if review.get("phase") == "final"
                        else "scoring_reviewer"
                    )
                )
            )
            and not actor_has_barred_role(
                reviewer_identity,
                REVIEW_BARRED_ACTOR_ROLES,
            )
            and (
                scoring_review is None
                or review.get("phase") != "final"
                or reviewer_identity != scoring_review.get("reviewer_identity")
            )
            and review_access_is_valid(review)
            and record_schema_validity.get(id(review), not full_bound_run)
            and record_lifecycle_validity.get(id(review), not full_bound_run)
            and (not full_bound_run or review_evidence_types == expected_review_types)
            and (
                consumer_inputs_are_exact(
                    review,
                    {
                        artifact_id
                        for artifact_id in required_review_ids
                        if isinstance(artifact_id, str)
                    },
                )
                if full_bound_run
                else evidence_resolves(
                    review.get("evidence"),
                    binding,
                    review,
                    current_epoch=True,
                )
            )
        )

    def review_findings_schema_is_valid(review: dict[str, Any]) -> bool:
        findings = review.get("findings")
        review_evidence = review.get("evidence")
        review_inputs = review.get("input_artifact_ids")
        if (
            not isinstance(findings, list)
            or not isinstance(review_evidence, list)
            or not isinstance(review_inputs, list)
            or not all(isinstance(item, str) for item in review_evidence)
            or not all(isinstance(item, str) for item in review_inputs)
        ):
            return False
        for finding in findings:
            if not isinstance(finding, dict):
                return False
            finding_id = finding.get("id")
            severity = finding.get("severity")
            evidence = finding.get("evidence")
            impact = finding.get("impact")
            correction = finding.get("correction")
            criteria = finding.get("affected_criteria")
            categories = finding.get("affected_categories")
            if (
                not isinstance(finding_id, str)
                or not finding_id
                or not isinstance(severity, str)
                or severity not in REVIEW_SEVERITIES
                or not isinstance(impact, str)
                or not impact.strip()
                or not isinstance(correction, str)
                or not correction.strip()
                or not isinstance(criteria, list)
                or not criteria
                or not all(
                    isinstance(criterion_id, str)
                    and criterion_id in CRITERION_CATEGORY
                    for criterion_id in criteria
                )
                or len(criteria) != len(set(criteria))
                or not isinstance(categories, list)
                or not categories
                or not all(
                    isinstance(category, str)
                    and category in CATEGORY_CRITERION_IDS
                    for category in categories
                )
                or len(categories) != len(set(categories))
                or set(categories)
                != {CRITERION_CATEGORY[criterion_id] for criterion_id in criteria}
                or not isinstance(evidence, list)
                or not evidence
                or not all(isinstance(artifact_id, str) for artifact_id in evidence)
                or len(evidence) != len(set(evidence))
                or not set(evidence).issubset(review_evidence)
                or not set(evidence).issubset(review_inputs)
                or not evidence_resolves(
                    evidence,
                    record_binding(review),
                    review,
                    acceptable_types=frozenset(
                        {"raw-trial-evidence", "trial-receipt"}
                    ),
                    current_epoch=True,
                )
            ):
                return False
        return True

    def review_record_schema_is_valid(review: dict[str, Any]) -> bool:
        evidence = review.get("evidence")
        inputs = review.get("input_artifact_ids")
        reviewer_identity = review.get("reviewer_identity")
        review_id = review.get("review_id")
        phase = review.get("phase")
        verdict = review.get("verdict")
        return (
            artifact_envelope_is_valid(review, "review-record")
            and record_binding(review) == current_binding()
            and isinstance(review_id, str)
            and bool(review_id)
            and isinstance(phase, str)
            and phase in {"scoring", "final", "repair"}
            and isinstance(reviewer_identity, str)
            and bool(reviewer_identity)
            and review.get("reviewer_role") == "independent-target-reviewer"
            and isinstance(verdict, str)
            and verdict in {"ready", "not ready"}
            and review.get("independent") is True
            and review.get("read_only") is True
            and review_access_is_valid(review)
            and isinstance(evidence, list)
            and bool(evidence)
            and all(isinstance(item, str) for item in evidence)
            and len(evidence) == len(set(evidence))
            and isinstance(inputs, list)
            and bool(inputs)
            and all(isinstance(item, str) for item in inputs)
            and len(inputs) == len(set(inputs))
            and set(evidence) == set(inputs)
            and review_findings_schema_is_valid(review)
        )

    def category_evidence_resolves(
        evidence_ids: object,
        binding: tuple[Any, Any, Any, Any, Any],
        category: str,
        criterion_id: str,
        consumer: dict[str, Any],
        *,
        current_epoch: bool = False,
    ) -> bool:
        if current_workflow_id is not None:
            records = resolved_artifacts(evidence_ids, consumer)
            expected_parameter_ids = {
                f"parameter-{expected_id}"
                for expected_id in CATEGORY_CRITERION_IDS.get(category, ())
            }
            evaluation_case_ids = set(
                frozen_evaluation.get("case_ids", [])
                if frozen_evaluation is not None
                else []
            )
            related_review_finding_ids = {
                finding.get("id")
                for finding in (scoring_review or {}).get("findings", [])
                if isinstance(finding, dict)
                and category in finding.get("affected_categories", [])
            }

            def category_record_is_valid(record: dict[str, Any]) -> bool:
                raw_ids = record.get("raw_artifact_ids")
                receipt_ids = record.get("trial_receipt_ids")
                review_artifact_id = (scoring_review or {}).get("artifact_id")
                evaluation_artifact_id = (frozen_evaluation or {}).get("artifact_id")
                required_input_ids = {
                    artifact_id
                    for artifact_id in [
                        evaluation_artifact_id,
                        review_artifact_id,
                        *(raw_ids if isinstance(raw_ids, list) else []),
                        *(receipt_ids if isinstance(receipt_ids, list) else []),
                    ]
                    if isinstance(artifact_id, str)
                }
                return (
                    record.get("category") == category
                    and isinstance(record.get("criterion_ids"), list)
                    and set(record["criterion_ids"])
                    == set(CATEGORY_CRITERION_IDS.get(category, ()))
                    and criterion_id in record["criterion_ids"]
                    and isinstance(record.get("frozen_parameter_ids"), list)
                    and set(record["frozen_parameter_ids"])
                    == expected_parameter_ids
                    and isinstance(record.get("case_ids"), list)
                    and bool(record["case_ids"])
                    and set(record["case_ids"]).issubset(evaluation_case_ids)
                    and isinstance(raw_ids, list)
                    and evidence_types_resolve(
                        raw_ids,
                        frozenset({"raw-trial-evidence"}),
                        record,
                    )
                    and isinstance(receipt_ids, list)
                    and evidence_types_resolve(
                        receipt_ids,
                        frozenset({"trial-receipt"}),
                        record,
                    )
                    and isinstance(record.get("review_finding_ids"), list)
                    and set(record["review_finding_ids"])
                    == related_review_finding_ids
                    and record.get("review_id")
                    == (scoring_review or {}).get("review_id")
                    and isinstance(evaluation_artifact_id, str)
                    and isinstance(review_artifact_id, str)
                    and exact_evidence_resolves(
                        record.get("input_artifact_ids"),
                        required_input_ids,
                        record,
                    )
                    and record_binding(record) == binding
                )

            return (
                records is not None
                and all(
                    record.get("artifact_type") == "category-evidence"
                    for record in records
                )
                and all(
                    category_record_is_valid(record) for record in records
                )
            )
        return (
            evidence_resolves(
                evidence_ids,
                binding,
                consumer,
                acceptable_types=CATEGORY_EVIDENCE_TYPES,
                current_epoch=current_epoch,
            )
            and isinstance(evidence_ids, list)
            and all(
                retained_artifacts[artifact_id].get("category") == category
                and isinstance(
                    retained_artifacts[artifact_id].get("criterion_ids"), list
                )
                and criterion_id
                in retained_artifacts[artifact_id].get("criterion_ids", [])
                for artifact_id in evidence_ids
            )
        )

    def clear_revision_dependent_records() -> None:
        nonlocal conformance_record
        nonlocal scoring_review
        nonlocal final_review
        nonlocal spec_outcome
        nonlocal verification
        nonlocal release_evidence
        nonlocal target_scorecard
        nonlocal independence_record
        nonlocal trial_pack_record
        nonlocal transition_record
        nonlocal artifact_manifest_record
        invalidated_artifact_ids.update(
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact.get("artifact_type") in CANDIDATE_BOUND_ARTIFACT_TYPES
        )
        category_scores.clear()
        conformance_record = None
        scoring_review = None
        final_review = None
        spec_outcome = None
        verification = None
        release_evidence = None
        target_scorecard = None
        independence_record = None
        trial_pack_record = None
        transition_record = None
        artifact_manifest_record = None

    def invalidate_candidate_epoch() -> None:
        nonlocal current_candidate_revision
        nonlocal current_candidate_implementer
        nonlocal current_candidate_implementer_role
        nonlocal current_candidate_event_index
        nonlocal candidate_contract_epoch
        nonlocal candidate_evaluation_epoch
        nonlocal candidate_snapshot_epoch
        nonlocal pending_repair
        nonlocal candidate_record
        clear_revision_dependent_records()
        candidate_record = None
        current_candidate_revision = None
        current_candidate_implementer = None
        current_candidate_implementer_role = None
        current_candidate_event_index = None
        candidate_contract_epoch = None
        candidate_evaluation_epoch = None
        candidate_snapshot_epoch = None
        pending_repair = None
        material_findings.clear()

    def invalidate_contract_and_downstream() -> None:
        nonlocal current_contract
        nonlocal confirmed_contract
        nonlocal confirmed_contract_epoch
        nonlocal frozen_evaluation
        nonlocal frozen_contract_epoch
        nonlocal frozen_snapshot_epoch
        nonlocal contract_record
        nonlocal confirmation_record
        invalidated_artifact_ids.update(
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact.get("artifact_type") in CONTRACT_BOUND_ARTIFACT_TYPES
        )
        invalidate_candidate_epoch()
        current_contract = None
        confirmed_contract = None
        confirmed_contract_epoch = None
        frozen_evaluation = None
        frozen_contract_epoch = None
        frozen_snapshot_epoch = None
        contract_record = None
        confirmation_record = None

    def begin_pre_candidate_stage(stage: str, event_index: int) -> None:
        nonlocal baseline_report
        nonlocal research_lanes
        nonlocal research_pack_record
        nonlocal sieve_record
        nonlocal design_record
        stage_types = {
            "baseline": frozenset(
                {"baseline-report", "research-pack", "evidence-sieve", "design-record"}
            ),
            "research": frozenset(
                {"research-pack", "evidence-sieve", "design-record"}
            ),
            "sieve": frozenset({"evidence-sieve", "design-record"}),
            "design": frozenset({"design-record"}),
        }
        if current_workflow_id is None:
            return
        after_candidate = current_candidate_event_index is not None
        invalidated_artifact_ids.update(
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact.get("artifact_type") in stage_types[stage]
        )
        invalidate_contract_and_downstream()
        if stage == "baseline":
            baseline_report = None
            research_lanes = None
            research_pack_record = None
            sieve_record = None
            design_record = None
        elif stage == "research":
            research_lanes = None
            research_pack_record = None
            sieve_record = None
            design_record = None
        elif stage == "sieve":
            sieve_record = None
            design_record = None
        else:
            design_record = None
        if after_candidate:
            failures.append(
                OracleFailure(
                    "PRE_CANDIDATE_STAGE_AFTER_CANDIDATE",
                    event_index,
                    "a pre-candidate stage replay invalidated its candidate and terminal dependents",
                )
            )

    def invalidate_evaluation_and_downstream() -> None:
        nonlocal frozen_evaluation
        nonlocal frozen_contract_epoch
        nonlocal frozen_snapshot_epoch
        invalidated_artifact_ids.update(
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact.get("artifact_type") in EVALUATION_BOUND_ARTIFACT_TYPES
        )
        invalidate_candidate_epoch()
        frozen_evaluation = None
        frozen_contract_epoch = None
        frozen_snapshot_epoch = None

    def invalidate_identity_or_snapshot_chain() -> None:
        nonlocal current_contract
        nonlocal confirmed_contract
        nonlocal frozen_evaluation
        nonlocal research_lanes
        nonlocal resolution_record
        nonlocal baseline_report
        nonlocal research_pack_record
        nonlocal sieve_record
        nonlocal design_record
        nonlocal contract_record
        nonlocal confirmation_record
        nonlocal authority_record
        nonlocal confirmed_contract_epoch
        nonlocal frozen_contract_epoch
        nonlocal frozen_snapshot_epoch
        nonlocal contract_epoch
        nonlocal evaluation_epoch
        nonlocal candidate_generation
        nonlocal prerequisite_generation_invalid
        invalidated_artifact_ids.update(retained_artifacts)
        invalidate_candidate_epoch()
        contract_epoch += 1
        evaluation_epoch += 1
        candidate_generation += 1
        current_contract = None
        confirmed_contract = None
        frozen_evaluation = None
        research_lanes = None
        resolution_record = None
        baseline_report = None
        research_pack_record = None
        sieve_record = None
        design_record = None
        contract_record = None
        confirmation_record = None
        authority_record = None
        confirmed_contract_epoch = None
        frozen_contract_epoch = None
        frozen_snapshot_epoch = None
        prerequisite_generation_invalid = True

    def artifact_inputs_match(
        record: dict[str, Any],
        required_records: tuple[dict[str, Any] | None, ...],
    ) -> bool:
        required_ids = {
            required.get("artifact_id")
            for required in required_records
            if required is not None
            and isinstance(required.get("artifact_id"), str)
            and artifact_is_current(required["artifact_id"])
        }
        return (
            len(required_ids) == len(required_records)
            and exact_evidence_resolves(
                record.get("input_artifact_ids"),
                {artifact_id for artifact_id in required_ids if isinstance(artifact_id, str)},
                record,
            )
        )

    def artifact_manifest_covers_preconformance(
        conformance: dict[str, Any],
    ) -> bool:
        if artifact_manifest_record is None:
            return False
        manifest_id = artifact_manifest_record.get("artifact_id")
        conformance_id = conformance.get("artifact_id")
        conformance_index = record_event_indices.get(id(conformance), -1)
        expected_ids = {
            artifact_id
            for artifact_id in retained_artifacts
            if artifact_id not in {manifest_id, conformance_id}
            and artifact_is_current(artifact_id)
            and record_event_indices.get(id(retained_artifacts[artifact_id]), -1)
            < conformance_index
        }
        manifested_ids = artifact_manifest_record.get("manifested_artifact_ids")
        input_ids = artifact_manifest_record.get("input_artifact_ids")
        manifest_index = record_event_indices.get(id(artifact_manifest_record), -1)
        return (
            isinstance(manifest_id, str)
            and artifact_is_current(manifest_id)
            and isinstance(manifested_ids, list)
            and all(isinstance(artifact_id, str) for artifact_id in manifested_ids)
            and len(manifested_ids) == len(set(manifested_ids))
            and set(manifested_ids) == expected_ids
            and isinstance(input_ids, list)
            and all(isinstance(artifact_id, str) for artifact_id in input_ids)
            and len(input_ids) == len(set(input_ids))
            and set(input_ids) == expected_ids
            and all(
                record_event_indices.get(id(retained_artifacts[artifact_id]), -1)
                < manifest_index
                for artifact_id in expected_ids
            )
        )

    def conformance_is_valid(record: dict[str, Any] | None) -> bool:
        if record is None:
            return False
        gates = record.get("gates")
        if (
            not isinstance(gates, list)
            or len(gates) != len(CONFORMANCE_GATE_IDS)
            or not all(
                isinstance(gate, dict) and isinstance(gate.get("id"), str)
                for gate in gates
            )
            or {gate.get("id") for gate in gates if isinstance(gate, dict)}
            != CONFORMANCE_GATE_IDS
            or record_binding(record) != current_binding()
            or not record_postdates_candidate(record)
            or record.get("artifact_type") != "builder-run-conformance-ledger"
            or record.get("valid") is not True
            or not record_lifecycle_validity.get(id(record), False)
            or not artifact_manifest_covers_preconformance(record)
        ):
            return False
        canonical_records = {
            "BR1": (resolution_record,),
            "BR2": (baseline_report,),
            "BR3": (research_pack_record, sieve_record),
            "BR4": (design_record, contract_record, confirmation_record),
            "BR5": (frozen_evaluation,),
            "BR6": (candidate_record,),
            "BR7": (
                *current_artifacts_of_type("raw-trial-evidence"),
                *current_artifacts_of_type("trial-receipt"),
                trial_pack_record,
            ),
            "BR8": (independence_record,),
            "BR9": (transition_record,),
            "BR10": (authority_record,),
        }
        claimed_ids: set[str] = set()
        for gate in gates:
            if not isinstance(gate, dict):
                return False
            gate_id = gate.get("id")
            evidence = gate.get("evidence")
            required_types = CONFORMANCE_GATE_ARTIFACT_TYPES.get(gate_id)
            required_records = canonical_records.get(gate_id, ())
            required_ids = {
                required.get("artifact_id")
                for required in required_records
                if required is not None
                and isinstance(required.get("artifact_id"), str)
            }
            if (
                gate.get("passed") is not True
                or required_types is None
                or not required_records
                or len(required_ids) != len(required_records)
                or any(
                    len(current_artifacts_of_type(artifact_type)) != 1
                    for artifact_type in required_types
                )
                or not evidence_types_resolve(evidence, required_types, record)
                or not exact_evidence_resolves(
                    evidence,
                    {artifact_id for artifact_id in required_ids if artifact_id},
                    record,
                )
                or not isinstance(evidence, list)
                or claimed_ids.intersection(evidence)
            ):
                return False
            claimed_ids.update(evidence)
        return exact_evidence_resolves(
            record.get("input_artifact_ids"), claimed_ids, record
        )

    for index, event in enumerate(trace):
        event_name = event["event"]
        record_event_indices[id(event)] = index
        if finalization_event_index is not None and event_name in {
            "actor_role_recorded",
            "artifact_retained",
            "baseline_captured",
            "builder_conformance_recorded",
            "candidate_edit",
            "category_scored",
            "contract_changed",
            "contract_written",
            "evaluation_frozen",
            "evidence_sieved",
            "finalized",
            "release_evidence_retained",
            "repair_completed",
            "research_pack",
            "resolve",
            "review_recorded",
            "spec_outcome_recorded",
            "target_scorecard_recorded",
            "target_snapshot_changed",
            "user_confirmed",
            "verification_recorded",
        }:
            failures.append(
                OracleFailure(
                    "EVENT_AFTER_FINALIZATION",
                    index,
                    "revision-dependent work occurred after the finalized terminal event",
                )
            )
            continue
        if event_name == "actor_role_recorded":
            register_actor(event.get("actor_identity"), event.get("actor_role"))
        elif event_name == "resolve":
            manifest = json.loads(
                (fixture_root / event["target_manifest"]).read_text(encoding="utf-8")
            )
            new_workflow_id = event.get("workflow_id")
            new_target_identity = manifest.get("canonical_target")
            full_bound_resolution = isinstance(new_workflow_id, str)
            if identity_resolution_seen and (
                current_workflow_id is not None or full_bound_resolution
            ):
                identity_generation += 1
                snapshot_generation += 1
                invalidate_identity_or_snapshot_chain()
            elif full_bound_resolution:
                identity_generation = 1
                snapshot_generation = 1
            current_workflow_id = new_workflow_id
            current_target_identity = new_target_identity
            identity_resolution_seen = True
            if (
                event.get("target_identity") is not None
                and event.get("target_identity") != current_target_identity
            ):
                failures.append(
                    OracleFailure(
                        "TARGET_BINDING_MISMATCH",
                        index,
                        "resolved target identity disagreed with the exact target manifest",
                    )
                )
            identity_ambiguous = manifest.get("identity_ambiguous", False)
            if event["selected_mode"] == "create" and manifest["exact_target_exists"]:
                failures.append(
                    OracleFailure(
                        "CREATE_TARGET_EXISTS",
                        index,
                        f"create mode would overwrite exact target {manifest['canonical_target']}",
                    )
                )
            current_snapshot = event["target_snapshot"]
            register_actor(event.get("actor_identity"), event.get("actor_role"))
            if full_bound_resolution:
                register_artifact(event, index)
                resolution_record = event
                record_schema_validity[id(event)] = (
                    artifact_envelope_is_valid(event, "resolution-record")
                    and event.get("input_artifact_ids") == []
                    and event.get("actor_role") == "main-agent"
                    and event.get("target_identity") == current_target_identity
                )
                if not record_schema_validity[id(event)]:
                    resolution_record = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "resolution did not retain the required typed identity record",
                        )
                    )
        elif event_name == "target_activated":
            if active_target is not None:
                failures.append(
                    OracleFailure(
                        "MULTIPLE_ACTIVE_TARGETS",
                        index,
                        f"target {event['target_identity']} activated while {active_target} still owns the lock",
                    )
                )
            else:
                active_target = event["target_identity"]
        elif event_name in {"target_finalized", "target_abandoned"}:
            if event["target_identity"] == active_target:
                active_target = None
        elif event_name in {"contract_written", "contract_changed"}:
            if current_workflow_id is not None:
                invalidate_contract_and_downstream()
            else:
                invalidate_candidate_epoch()
            contract_epoch += 1
            current_contract = event["contract_digest"]
            if current_workflow_id is not None:
                register_artifact(event, index)
                contract_record = event
                record_schema_validity[id(event)] = (
                    artifact_envelope_is_valid(event, "skill-contract")
                    and artifact_inputs_match(
                        event,
                        (baseline_report, sieve_record, design_record),
                    )
                )
                if not record_schema_validity[id(event)]:
                    contract_record = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "skill contract lacked its baseline, sieve, or design bindings",
                        )
                    )
        elif event_name == "target_snapshot_changed":
            current_snapshot = event["target_snapshot"]
            snapshot_epoch += 1
            if current_workflow_id is not None:
                snapshot_generation += 1
                invalidate_identity_or_snapshot_chain()
            else:
                invalidate_candidate_epoch()
        elif event_name == "paused":
            pause_record = event
        elif event_name == "resumed":
            revalidation_fields = (
                "chain_revalidated",
                "identity_revalidated",
                "snapshot_revalidated",
                "evidence_revalidated",
            )
            if (
                pause_record is None
                or not pause_record["state_validated"]
                or pause_record["target_identity"] != event["target_identity"]
                or not all(event[field] for field in revalidation_fields)
            ):
                failures.append(
                    OracleFailure(
                        "RESUME_WITHOUT_REVALIDATION",
                        index,
                        "resume did not revalidate the paused chain, identity, snapshot, and evidence",
                    )
                )
        elif event_name == "finalization_receipt_validated":
            finalization_receipt = event
        elif event_name == "run_state_manifest_validated":
            run_state_manifest = event
        elif event_name == "cleanup_authority_recorded":
            cleanup_authority = event
        elif event_name == "delivery_accepted":
            accepted_delivery = event
        elif event_name == "cleanup_tombstone_validated":
            cleanup_tombstone = event
        elif event_name == "cleanup_attempt":
            cleanup_binding = (
                event.get("workflow_id"),
                event.get("target_identity"),
                event.get("finalized_revision"),
                event.get("run_directory_identity"),
            )
            state_home = (
                run_state_manifest.get("xdg_state_home")
                if run_state_manifest is not None
                else None
            )
            run_directory = (
                run_state_manifest.get("run_directory")
                if run_state_manifest is not None
                else None
            )
            trusted_state_home = fixture_root / "private-state"
            workflow_identity = event.get("workflow_id")
            workflow_identity_valid = (
                isinstance(workflow_identity, str)
                and WORKFLOW_ID_RE.fullmatch(workflow_identity) is not None
            )
            expected_directory = (
                trusted_state_home
                / "codex-dev-flow"
                / "skill-builder"
                / "runs"
                / workflow_identity
                if workflow_identity_valid
                else None
            )
            ownership_valid = (
                run_state_manifest is not None
                and workflow_identity_valid
                and run_state_manifest.get("valid") is True
                and bool(run_state_manifest.get("manifest_digest"))
                and isinstance(state_home, str)
                and Path(state_home) == trusted_state_home
                and (
                    run_state_manifest.get("workflow_id"),
                    run_state_manifest.get("target_identity"),
                    run_state_manifest.get("finalized_revision"),
                    run_state_manifest.get("run_directory_identity"),
                )
                == cleanup_binding
                and isinstance(run_directory, str)
                and Path(run_directory).is_absolute()
                and Path(run_directory) == expected_directory
                and event.get("path") == run_directory
            )
            authority_valid = (
                cleanup_authority is not None
                and cleanup_authority.get("valid") is True
                and cleanup_authority.get("effect") == "cleanup"
                and bool(cleanup_authority.get("authority_event_digest"))
                and (
                    cleanup_authority.get("workflow_id"),
                    cleanup_authority.get("target_identity"),
                    cleanup_authority.get("finalized_revision"),
                    cleanup_authority.get("run_directory_identity"),
                )
                == cleanup_binding
            )
            delivery_valid = (
                accepted_delivery is not None
                and accepted_delivery.get("valid") is True
                and accepted_delivery.get("accepted") is True
                and bool(accepted_delivery.get("delivery_record_digest"))
                and (
                    accepted_delivery.get("workflow_id"),
                    accepted_delivery.get("target_identity"),
                    accepted_delivery.get("finalized_revision"),
                    accepted_delivery.get("run_directory_identity"),
                )
                == cleanup_binding
            )
            tombstone_valid = (
                cleanup_tombstone is not None
                and cleanup_tombstone.get("valid") is True
                and cleanup_tombstone.get("scope") == "helper-owned-parent"
                and bool(cleanup_tombstone.get("tombstone_digest"))
                and (
                    cleanup_tombstone.get("workflow_id"),
                    cleanup_tombstone.get("target_identity"),
                    cleanup_tombstone.get("finalized_revision"),
                    cleanup_tombstone.get("run_directory_identity"),
                )
                == cleanup_binding
                and authority_valid
                and delivery_valid
                and cleanup_tombstone.get("authority_event_digest")
                == cleanup_authority.get("authority_event_digest")
                and cleanup_tombstone.get("delivery_record_digest")
                == accepted_delivery.get("delivery_record_digest")
            )
            expected_tombstone_path = (
                trusted_state_home
                / "codex-dev-flow"
                / "skill-builder"
                / "tombstones"
                / f"{workflow_identity}.json"
                if workflow_identity_valid
                else None
            )
            finalization_valid = (
                finalization_receipt is not None
                and finalization_receipt.get("valid") is True
                and bool(finalization_receipt.get("final_transition_digest"))
                and (
                    finalization_receipt.get("workflow_id"),
                    finalization_receipt.get("target_identity"),
                    finalization_receipt.get("finalized_revision"),
                    finalization_receipt.get("run_directory_identity"),
                )
                == cleanup_binding
            )
            provenance_valid = (
                finalization_valid
                and ownership_valid
                and tombstone_valid
                and run_state_manifest.get("final_transition_digest")
                == finalization_receipt.get("final_transition_digest")
                and cleanup_tombstone.get("final_transition_digest")
                == finalization_receipt.get("final_transition_digest")
                and cleanup_tombstone.get("final_run_manifest_digest")
                == run_state_manifest.get("manifest_digest")
                and isinstance(cleanup_tombstone.get("tombstone_path"), str)
                and Path(cleanup_tombstone["tombstone_path"])
                == expected_tombstone_path
                and Path(cleanup_tombstone["tombstone_path"]).parent
                != Path(run_directory)
                and record_event_indices[id(finalization_receipt)]
                < record_event_indices[id(accepted_delivery)]
                < record_event_indices[id(cleanup_authority)]
                < record_event_indices[id(run_state_manifest)]
                < record_event_indices[id(cleanup_tombstone)]
                < index
            )
            if not authority_valid:
                failures.append(
                    OracleFailure(
                        "CLEANUP_WITHOUT_AUTHORITY",
                        index,
                        "cleanup was attempted without explicit user authority",
                    )
                )
            if not delivery_valid:
                failures.append(
                    OracleFailure(
                        "CLEANUP_WITHOUT_ACCEPTED_DELIVERY",
                        index,
                        "cleanup was attempted before accepted delivery or installation",
                    )
                )
            if not tombstone_valid:
                failures.append(
                    OracleFailure(
                        "CLEANUP_WITHOUT_TOMBSTONE",
                        index,
                        "cleanup was attempted before its durable tombstone was validated",
                    )
                )
            if event.get("deletion_scope") != "helper-owned-run-directory":
                failures.append(
                    OracleFailure(
                        "FORBIDDEN_CLEANUP_SCOPE",
                        index,
                        "cleanup attempted to delete production or an unowned path",
                    )
                )
            if not ownership_valid:
                failures.append(
                    OracleFailure(
                        "INVALID_CLEANUP_OWNERSHIP",
                        index,
                        "cleanup target was not derived from a validated helper-owned XDG run manifest",
                    )
                )
            if authority_valid and delivery_valid and tombstone_valid and not provenance_valid:
                failures.append(
                    OracleFailure(
                        "INVALID_CLEANUP_PROVENANCE",
                        index,
                        "cleanup lacked ordered finalization, manifest, authority, delivery, or parent tombstone provenance",
                    )
                )
        elif event_name == "goal_changed" and scenario is not None:
            required = set(scenario["required_invalidations"])
            observed = set(event["invalidated"])
            if missing := required - observed:
                failures.append(
                    OracleFailure(
                        "DEPENDENT_INVALIDATION_MISSING",
                        index,
                        f"goal change left dependent artifacts valid: {sorted(missing)}",
                    )
                )
            if unrelated := observed - required:
                failures.append(
                    OracleFailure(
                        "UNRELATED_ARTIFACT_INVALIDATED",
                        index,
                        f"goal change invalidated unrelated artifacts: {sorted(unrelated)}",
                    )
                )
        elif event_name == "write":
            if event["destination_scope"] == "production-target" and (
                not event["authority"] or event["actor"].startswith("candidate-")
            ):
                failures.append(
                    OracleFailure(
                        "UNAUTHORIZED_PRODUCTION_WRITE",
                        index,
                        f"{event['actor']} wrote outside the isolated disposable candidate",
                    )
                )
        elif event_name == "delivery_attempt":
            if not event["authority"]:
                failures.append(
                    OracleFailure(
                        "UNAUTHORIZED_DELIVERY",
                        index,
                        f"{event['effect']} was attempted without explicit user authority",
                    )
                )
        elif event_name == "context_read":
            path_parts = Path(event["path"]).parts
            if (
                event["actor_role"] in {"candidate", "trial"}
                and "hidden-release" in path_parts
            ):
                failures.append(
                    OracleFailure(
                        "HIDDEN_ORACLE_LEAK",
                        index,
                        f"{event['actor']} read withheld hidden-release material",
                    )
                )
            elif (
                event["actor_role"] == "research"
                and event["source_role"].startswith("research-")
                and event["source_role"] != event["actor"]
            ):
                failures.append(
                    OracleFailure(
                        "SIBLING_OUTPUT_LEAK",
                        index,
                        f"{event['actor']} read sibling output owned by {event['source_role']}",
                    )
                )
        elif event_name == "baseline_captured":
            begin_pre_candidate_stage("baseline", index)
            if current_workflow_id is not None:
                register_artifact(event, index)
                baseline_report = event
                record_schema_validity[id(event)] = (
                    artifact_envelope_is_valid(event, "baseline-report")
                    and artifact_inputs_match(event, (resolution_record,))
                )
                if not record_schema_validity[id(event)]:
                    baseline_report = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "baseline did not bind the current resolution and snapshot",
                        )
                    )
        elif event_name == "evidence_sieved":
            begin_pre_candidate_stage("sieve", index)
            if current_workflow_id is not None:
                register_artifact(event, index)
                sieve_record = event
                record_schema_validity[id(event)] = (
                    artifact_envelope_is_valid(event, "evidence-sieve")
                    and event.get("card_count") == sum(
                        lane.get("evidence_cards", 0) for lane in research_lanes or []
                    )
                    and isinstance(event.get("decisions"), list)
                    and len(event["decisions"]) == event.get("card_count")
                    and all(
                        isinstance(decision, str)
                        and decision in {"adopt", "experiment", "reject"}
                        for decision in event["decisions"]
                    )
                    and artifact_inputs_match(event, (research_pack_record,))
                )
                if not record_schema_validity[id(event)]:
                    sieve_record = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "evidence sieve did not account for the current research pack",
                        )
                    )
        elif event_name == "artifact_retained":
            register_artifact(event, index)
            if current_workflow_id is not None:
                artifact_type = event.get("artifact_type")
                current_raw_records = tuple(
                    artifact
                    for artifact_id, artifact in retained_artifacts.items()
                    if artifact_id != event.get("artifact_id")
                    and artifact_is_current(artifact_id)
                    and artifact.get("artifact_type") == "raw-trial-evidence"
                )
                current_receipts = tuple(
                    artifact
                    for artifact_id, artifact in retained_artifacts.items()
                    if artifact_id != event.get("artifact_id")
                    and artifact_is_current(artifact_id)
                    and artifact.get("artifact_type") == "trial-receipt"
                )
                schema_valid = (
                    isinstance(artifact_type, str)
                    and artifact_type in RETAINED_EVENT_ARTIFACT_TYPES
                    and artifact_envelope_is_valid(event, artifact_type)
                )
                if artifact_type == "authority-record":
                    schema_valid = (
                        schema_valid
                        and event.get("authorized_delivery_scope") == "none"
                        and event.get("cleanup_authorized") is False
                        and artifact_inputs_match(event, (resolution_record,))
                    )
                    if schema_valid:
                        authority_record = event
                elif artifact_type == "raw-trial-evidence":
                    case_ids = event.get("case_ids")
                    raw_digests = event.get("raw_artifact_digests")
                    schema_valid = (
                        schema_valid
                        and isinstance(case_ids, list)
                        and all(isinstance(case_id, str) for case_id in case_ids)
                        and set(case_ids)
                        == set((frozen_evaluation or {}).get("case_ids", []))
                        and isinstance(raw_digests, list)
                        and len(raw_digests) >= 3
                        and all(isinstance(digest, str) and digest for digest in raw_digests)
                        and artifact_inputs_match(
                            event, (candidate_record, frozen_evaluation)
                        )
                    )
                elif artifact_type == "trial-receipt":
                    context_ids = event.get("fresh_context_ids")
                    case_ids = event.get("case_ids")
                    schema_valid = (
                        schema_valid
                        and len(current_raw_records) == 1
                        and isinstance(case_ids, list)
                        and all(isinstance(case_id, str) for case_id in case_ids)
                        and set(case_ids)
                        == set((frozen_evaluation or {}).get("case_ids", []))
                        and isinstance(context_ids, list)
                        and len(context_ids)
                        == len((frozen_evaluation or {}).get("case_ids", []))
                        and all(isinstance(context_id, str) for context_id in context_ids)
                        and len(set(context_ids)) == len(context_ids)
                        and artifact_inputs_match(
                            event,
                            (candidate_record, frozen_evaluation, *current_raw_records),
                        )
                    )
                elif artifact_type == "trial-pack":
                    case_ids = event.get("case_ids")
                    raw_ids = event.get("raw_artifact_ids")
                    receipt_ids = event.get("trial_receipt_ids")
                    schema_valid = (
                        schema_valid
                        and len(current_raw_records) == 1
                        and len(current_receipts) == 1
                        and isinstance(case_ids, list)
                        and all(isinstance(case_id, str) for case_id in case_ids)
                        and set(case_ids)
                        == set((frozen_evaluation or {}).get("case_ids", []))
                        and isinstance(raw_ids, list)
                        and all(isinstance(artifact_id, str) for artifact_id in raw_ids)
                        and set(raw_ids)
                        == {
                            record.get("artifact_id")
                            for record in current_raw_records
                        }
                        and isinstance(receipt_ids, list)
                        and all(
                            isinstance(artifact_id, str) for artifact_id in receipt_ids
                        )
                        and set(receipt_ids)
                        == {
                            record.get("artifact_id") for record in current_receipts
                        }
                        and artifact_inputs_match(
                            event,
                            (
                                candidate_record,
                                frozen_evaluation,
                                *current_raw_records,
                                *current_receipts,
                            ),
                        )
                    )
                    if schema_valid:
                        trial_pack_record = event
                elif artifact_type == "independence-ledger":
                    actors = event.get("actors")
                    research_identities_by_role = {
                        lane.get("role"): lane.get(
                            "actor_identity", lane.get("context_id")
                        )
                        for lane in research_lanes or []
                    }
                    schema_valid = (
                        schema_valid
                        and artifact_inputs_match(event, (candidate_record,))
                        and isinstance(actors, dict)
                        and set(actors)
                        == {
                            "main",
                            "design",
                            "research_domain",
                            "research_design",
                            "research_evaluation",
                            "implementer",
                            "scorer",
                            "scoring_reviewer",
                            "final_reviewer",
                            "spec_judge",
                            "verifier",
                        }
                        and all(
                            isinstance(actor, str) and bool(actor)
                            for actor in actors.values()
                        )
                        and len(set(actors.values())) == len(actors)
                        and actors.get("main")
                        == (resolution_record or {}).get("actor_identity")
                        and actors.get("design")
                        == (design_record or {}).get("actor_identity")
                        and actors.get("research_domain")
                        == research_identities_by_role.get("domain-techniques")
                        and actors.get("research_design")
                        == research_identities_by_role.get("agent-skill-design")
                        and actors.get("research_evaluation")
                        == research_identities_by_role.get("evaluation-methods")
                        and actors.get("implementer")
                        == current_candidate_implementer
                    )
                    if schema_valid:
                        independence_record = event
                elif artifact_type == "transition-ledger":
                    required_transition_records = (
                        resolution_record,
                        baseline_report,
                        research_pack_record,
                        sieve_record,
                        design_record,
                        contract_record,
                        confirmation_record,
                        frozen_evaluation,
                        candidate_record,
                        trial_pack_record,
                    )
                    expected_stage_ids = [
                        record.get("artifact_id")
                        for record in required_transition_records
                        if record is not None
                    ]
                    schema_valid = (
                        schema_valid
                        and len(expected_stage_ids)
                        == len(required_transition_records)
                        and isinstance(
                            event.get("ordered_stage_artifact_ids"), list
                        )
                        and event.get("ordered_stage_artifact_ids")
                        == expected_stage_ids
                        and artifact_inputs_match(
                            event, required_transition_records
                        )
                    )
                    if schema_valid:
                        transition_record = event
                elif artifact_type == "artifact-manifest":
                    manifested_ids = event.get("manifested_artifact_ids")
                    prior_current_ids = {
                        artifact_id
                        for artifact_id in retained_artifacts
                        if artifact_id != event.get("artifact_id")
                        and artifact_is_current(artifact_id)
                    }
                    schema_valid = (
                        schema_valid
                        and isinstance(manifested_ids, list)
                        and all(
                            isinstance(artifact_id, str)
                            for artifact_id in manifested_ids
                        )
                        and len(manifested_ids) == len(set(manifested_ids))
                        and set(manifested_ids) == prior_current_ids
                        and exact_evidence_resolves(
                            event.get("input_artifact_ids"),
                            prior_current_ids,
                            event,
                        )
                    )
                    if schema_valid:
                        artifact_manifest_record = event
                elif artifact_type == "category-evidence":
                    schema_valid = (
                        schema_valid and record_binding(event) == current_binding()
                    )
                record_schema_validity[id(event)] = schema_valid
                if not schema_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            f"{artifact_type!r} lacked required current input bindings",
                        )
                    )
        elif event_name == "builder_conformance_recorded":
            if current_workflow_id is not None:
                register_artifact(event, index)
                lifecycle_valid = (
                    current_candidate_event_index is not None
                    and trial_pack_record is not None
                    and artifact_manifest_record is not None
                    and scoring_review is None
                    and final_review is None
                    and not category_scores
                    and record_event_indices[id(trial_pack_record)] < index
                    and record_event_indices[id(artifact_manifest_record)] < index
                )
                record_lifecycle_validity[id(event)] = lifecycle_valid
                if not lifecycle_valid:
                    failures.append(
                        OracleFailure(
                            "CONFORMANCE_AFTER_REVIEW_OR_SCORE",
                            index,
                            "builder conformance must precede every dependent review and score",
                        )
                    )
                schema_valid = (
                    artifact_envelope_is_valid(
                        event, "builder-run-conformance-ledger"
                    )
                    and conformance_is_valid(event)
                )
                record_schema_validity[id(event)] = schema_valid
                if not schema_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "builder conformance lacked exact typed gate evidence",
                        )
                    )
            conformance_record = event
        elif event_name == "spec_outcome_recorded":
            if current_workflow_id is not None:
                register_actor(event.get("judge_identity"), event.get("judge_role"))
                register_artifact(event, index)
                scorer_identities = {
                    score_event.get("scorer_identity")
                    for score_event, _ in category_scores.values()
                    if isinstance(score_event.get("scorer_identity"), str)
                }
                judge_identity = event.get("judge_identity")
                required_ids = {
                    record.get("artifact_id")
                    for record in (
                        contract_record,
                        frozen_evaluation,
                        candidate_record,
                        target_scorecard,
                        final_review,
                    )
                    if record is not None
                    and isinstance(record.get("artifact_id"), str)
                }
                schema_valid = (
                    artifact_envelope_is_valid(event, "spec-outcome-record")
                    and record_binding(event) == current_binding()
                    and event.get("outcome") in {"pass", "fail"}
                    and event.get("independent") is True
                    and event.get("read_only") is True
                    and event.get("judge_role") == "independent-spec-judge"
                    and isinstance(judge_identity, str)
                    and bool(judge_identity)
                )
                lifecycle_valid = (
                    schema_valid
                    and final_review is not None
                    and record_event_indices[id(final_review)] < index
                    and review_is_valid(
                        final_review,
                        current_binding(),
                        {identity for identity in scorer_identities if identity},
                    )
                    and len(required_ids) == 5
                    and consumer_inputs_are_exact(event, required_ids)
                    and independence_record is not None
                    and judge_identity
                    == independence_record.get("actors", {}).get("spec_judge")
                    and not actor_has_barred_role(
                        judge_identity, SPEC_JUDGE_BARRED_ACTOR_ROLES
                    )
                )
                record_schema_validity[id(event)] = schema_valid
                record_lifecycle_validity[id(event)] = lifecycle_valid
                if not schema_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "specification outcome lacked its exact actor and target binding",
                        )
                    )
                if not lifecycle_valid:
                    failures.append(
                        OracleFailure(
                            "SPEC_BEFORE_FINAL_REVIEW",
                            index,
                            "specification judgment lacked a prior ready final review or independent judge",
                        )
                    )
            spec_outcome = event
        elif event_name == "release_evidence_retained":
            if current_workflow_id is not None:
                register_artifact(event, index)
                required_records = (
                    contract_record,
                    confirmation_record,
                    frozen_evaluation,
                    candidate_record,
                    artifact_manifest_record,
                    conformance_record,
                    target_scorecard,
                    final_review,
                    spec_outcome,
                    verification,
                )
                required_ids = {
                    record.get("artifact_id")
                    for record in required_records
                    if record is not None
                    and isinstance(record.get("artifact_id"), str)
                }
                schema_valid = (
                    artifact_envelope_is_valid(event, "release-record")
                    and record_binding(event) == current_binding()
                )
                lifecycle_valid = (
                    schema_valid
                    and len(required_ids) == len(required_records)
                    and all(
                        record_event_indices[id(record)] < index
                        for record in required_records
                        if record is not None
                    )
                    and record_lifecycle_validity.get(id(spec_outcome), False)
                    and record_lifecycle_validity.get(id(verification), False)
                    and consumer_inputs_are_exact(event, required_ids)
                )
                record_schema_validity[id(event)] = schema_valid
                record_lifecycle_validity[id(event)] = lifecycle_valid
                if not schema_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "release record lacked its immutable exact-run binding",
                        )
                    )
                if not lifecycle_valid:
                    failures.append(
                        OracleFailure(
                            "RELEASE_BEFORE_TERMINAL_GATES",
                            index,
                            "release evidence preceded conformance, scorecard, final review, spec, or verification",
                        )
                    )
            release_evidence = event
        elif event_name == "review_recorded":
            register_actor(event.get("reviewer_identity"), event.get("reviewer_role"))
            review_id = event.get("review_id")
            if isinstance(review_id, str) and bool(review_id):
                if review_id in seen_review_ids:
                    failures.append(
                        OracleFailure(
                            "DUPLICATE_REVIEW_ID",
                            index,
                            "a review record reused an immutable identity",
                        )
                    )
                    ambiguous_review_ids.add(review_id)
                else:
                    seen_review_ids.add(review_id)
            if current_workflow_id is not None:
                register_artifact(event, index)
                schema_valid = review_record_schema_is_valid(event)
                record_schema_validity[id(event)] = schema_valid
                if event.get("phase") == "scoring":
                    lifecycle_valid = (
                        conformance_record is not None
                        and record_event_indices[id(conformance_record)] < index
                        and conformance_is_valid(conformance_record)
                        and not category_scores
                    )
                elif event.get("phase") == "final":
                    lifecycle_valid = (
                        target_scorecard is not None
                        and record_event_indices[id(target_scorecard)] < index
                        and set(category_scores) == set(CATEGORY_CRITERION_IDS)
                        and all(
                            score_event.get("score") == 10 and score_proven
                            for score_event, score_proven in category_scores.values()
                        )
                        and conformance_is_valid(conformance_record)
                    )
                else:
                    lifecycle_valid = record_postdates_candidate(event)
                record_lifecycle_validity[id(event)] = lifecycle_valid
                if not schema_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_REVIEW_SCHEMA",
                            index,
                            "review findings lacked a supported severity, evidence, impact, correction, or target mapping",
                        )
                    )
                if not lifecycle_valid and event.get("phase") == "scoring":
                    failures.append(
                        OracleFailure(
                            "REVIEW_BEFORE_CONFORMANCE",
                            index,
                            "scoring review preceded valid builder conformance",
                        )
                    )
            if event.get("phase") == "scoring":
                scoring_review = event
            if event.get("phase") == "final":
                final_review = event
            trusted_review = event.get("valid") is True and (
                current_workflow_id is None
                or (
                    record_schema_validity.get(id(event), False)
                    and record_lifecycle_validity.get(id(event), False)
                )
            )
            if trusted_review:
                for finding in event["findings"]:
                    finding_id = finding["id"]
                    if finding_id in seen_finding_ids:
                        failures.append(
                            OracleFailure(
                                "DUPLICATE_MATERIAL_FINDING_ID",
                                index,
                                "a review reused an immutable finding identity",
                            )
                        )
                        continue
                    seen_finding_ids.add(finding_id)
                    if finding["severity"] in {"High", "Medium"}:
                        affected_categories = set(finding["affected_categories"])
                        material_findings[finding_id] = (
                            affected_categories,
                            record_binding(event),
                            index,
                        )
                        for category in affected_categories:
                            prior_score = category_scores.get(category)
                            if prior_score is not None and prior_score[0].get("score") == 10:
                                category_scores.pop(category, None)
        elif event_name == "repair_completed":
            repaired_ids = set(event["finding_ids"])
            finding_records = [
                material_findings[finding_id]
                for finding_id in repaired_ids
                if finding_id in material_findings
            ]
            if (
                current_candidate_revision is None
                or current_candidate_event_index is None
                or pending_repair is not None
                or not repaired_ids
                or not repaired_ids.issubset(material_findings)
                or event["prior_revision"] != current_candidate_revision
                or event["candidate_revision"] == current_candidate_revision
                or (
                    current_workflow_id is not None
                    and (
                        event.get("workflow_id") != current_workflow_id
                        or event.get("target_identity") != current_target_identity
                    )
                )
                or any(
                    finding_binding[2] != current_candidate_revision
                    or finding_index <= current_candidate_event_index
                    or (
                        current_workflow_id is not None
                        and finding_binding != current_binding()
                    )
                    for _, finding_binding, finding_index in finding_records
                )
            ):
                failures.append(
                    OracleFailure(
                        "INVALID_REPAIR_BINDING",
                        index,
                        "repair did not bind known material findings to a new candidate revision",
                    )
                )
            else:
                pending_repair = {
                    "finding_ids": repaired_ids,
                    "prior_revision": current_candidate_revision,
                    "candidate_revision": event["candidate_revision"],
                }
        elif event_name == "target_scorecard_recorded":
            if current_workflow_id is not None:
                register_artifact(event, index)
                category_evidence_ids = {
                    artifact_id
                    for artifact_id, artifact in retained_artifacts.items()
                    if artifact_is_current(artifact_id)
                    and artifact.get("artifact_type") == "category-evidence"
                }
                required_ids = {
                    record.get("artifact_id")
                    for record in (conformance_record, scoring_review)
                    if record is not None
                    and isinstance(record.get("artifact_id"), str)
                } | category_evidence_ids
                scorecard_valid = (
                    artifact_envelope_is_valid(event, "target-scorecard")
                    and record_binding(event) == current_binding()
                    and event.get("review_id")
                    == (scoring_review or {}).get("review_id")
                    and event.get("categories") == list(CATEGORY_CRITERION_IDS)
                    and set(category_scores) == set(CATEGORY_CRITERION_IDS)
                    and all(
                        score_event.get("score") == 10 and score_proven
                        for score_event, score_proven in category_scores.values()
                    )
                    and len(category_evidence_ids) == len(CATEGORY_CRITERION_IDS)
                    and len(required_ids) == len(CATEGORY_CRITERION_IDS) + 2
                    and consumer_inputs_are_exact(event, required_ids)
                )
                record_lifecycle_validity[id(event)] = scorecard_valid
                record_schema_validity[id(event)] = scorecard_valid
                if scorecard_valid:
                    target_scorecard = event
                else:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_TARGET_SCORECARD",
                            index,
                            "target scorecard lacked ten current category results and exact evidence bindings",
                            "target-quality",
                        )
                    )
        elif event_name == "category_scored":
            criteria = event["criteria"]
            binding = record_binding(event)
            expected_ids = CATEGORY_CRITERION_IDS.get(event["category"])
            scorer_identity = event.get("scorer_identity")
            scorer_role = event.get("scorer_role")
            register_actor(scorer_identity, scorer_role)
            full_bound_run = current_workflow_id is not None
            scoring_review_valid = (
                not full_bound_run
                or (
                    event.get("review_id") == scoring_review.get("review_id")
                    and isinstance(scorer_identity, str)
                    and bool(scorer_identity)
                    and scorer_role == "target-scorer"
                    and scorer_identity != current_candidate_implementer
                    and independence_record is not None
                    and scorer_identity
                    == independence_record.get("actors", {}).get("scorer")
                    and not actor_has_barred_role(
                        scorer_identity, SCORER_BARRED_ACTOR_ROLES
                    )
                    and conformance_is_valid(conformance_record)
                    and record_event_indices[id(conformance_record)] < index
                    and record_event_indices[id(scoring_review)] < index
                    and review_is_valid(
                        scoring_review,
                        current_binding(),
                        {scorer_identity},
                    )
                )
            ) if scoring_review is not None else not full_bound_run
            ten_is_proven = (
                len(criteria) == 10
                and {criterion["id"] for criterion in criteria} == expected_ids
                and scoring_review_valid
                and (not full_bound_run or binding == current_binding())
                and (
                    not full_bound_run
                    or record_identity_binding_is_current(event)
                )
                and all(
                    criterion["passed"] is True
                    and (
                        category_evidence_resolves(
                            criterion["evidence"],
                            binding,
                            event["category"],
                            criterion["id"],
                            event,
                        )
                        if full_bound_run
                        else evidence_resolves(
                            criterion["evidence"],
                            binding,
                            event,
                            acceptable_types=CATEGORY_EVIDENCE_TYPES,
                        )
                    )
                    for criterion in criteria
                )
            )
            category_scores[event["category"]] = (event, ten_is_proven)
            if event["score"] == 10 and not ten_is_proven:
                failures.append(
                    OracleFailure(
                        "FALSE_CATEGORY_TEN",
                        index,
                        f"{event['category']} scored 10 without ten passing evidence-bound criteria",
                        "target-quality",
                    )
                )
            elif (
                event["score"] == 10
                and event["category"]
                in {
                    category
                    for categories, _, _ in material_findings.values()
                    for category in categories
                }
            ):
                failures.append(
                    OracleFailure(
                        "MATERIAL_FINDING_AT_TEN",
                        index,
                        f"{event['category']} scored 10 while a High or Medium finding remains",
                        "target-quality",
                    )
                )
        elif event_name == "verification_recorded":
            register_actor(event.get("verifier_identity"), event.get("verifier_role"))
            if current_workflow_id is not None:
                register_artifact(event, index)
                verifier_identity = event.get("verifier_identity")
                required_records = (
                    candidate_record,
                    trial_pack_record,
                    conformance_record,
                    target_scorecard,
                    final_review,
                    spec_outcome,
                )
                required_ids = {
                    record.get("artifact_id")
                    for record in required_records
                    if record is not None
                    and isinstance(record.get("artifact_id"), str)
                }
                schema_valid = (
                    artifact_envelope_is_valid(event, "verification-record")
                    and record_binding(event) == current_binding()
                    and isinstance(verifier_identity, str)
                    and bool(verifier_identity)
                    and event.get("verifier_role") == "independent-verifier"
                    and event.get("behavioral_trials", 0) >= 1
                    and event.get("exit_status") == 0
                    and event.get("conclusion") == "pass"
                    and event.get("independent") is True
                    and event.get("read_only") is True
                )
                lifecycle_valid = (
                    schema_valid
                    and len(required_ids) == len(required_records)
                    and final_review is not None
                    and spec_outcome is not None
                    and record_event_indices[id(final_review)] < index
                    and record_event_indices[id(spec_outcome)] < index
                    and record_lifecycle_validity.get(id(final_review), False)
                    and record_lifecycle_validity.get(id(spec_outcome), False)
                    and independence_record is not None
                    and verifier_identity
                    == independence_record.get("actors", {}).get("verifier")
                    and not actor_has_barred_role(
                        verifier_identity, VERIFIER_BARRED_ACTOR_ROLES
                    )
                    and consumer_inputs_are_exact(event, required_ids)
                )
                record_schema_validity[id(event)] = schema_valid
                record_lifecycle_validity[id(event)] = lifecycle_valid
                if not schema_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "verification record lacked behavioral or exact actor binding",
                        )
                    )
                if not lifecycle_valid:
                    failures.append(
                        OracleFailure(
                            "VERIFICATION_BEFORE_FINAL_GATES",
                            index,
                            "verification lacked prior ready final review and independent spec judgment",
                        )
                    )
            verification = event
        elif event_name == "finalized":
            expected_binding = current_binding()
            if (
                None in expected_binding
                or not isinstance(current_workflow_id, str)
                or WORKFLOW_ID_RE.fullmatch(current_workflow_id) is None
                or not isinstance(current_target_identity, str)
                or not current_target_identity
                or record_binding(event) != expected_binding
                or (
                    current_workflow_id is not None
                    and not record_identity_binding_is_current(event)
                )
                or frozen_evaluation is None
                or frozen_evaluation.get("contract_digest") != current_contract
                or candidate_contract_epoch != contract_epoch
                or candidate_evaluation_epoch != evaluation_epoch
                or (
                    current_workflow_id is not None
                    and (
                        prerequisite_generation_invalid
                        or candidate_record is None
                        or not artifact_is_current(
                            str(candidate_record.get("artifact_id"))
                        )
                    )
                )
            ):
                failures.append(
                    OracleFailure(
                        "FINALIZATION_BINDING_MISMATCH",
                        index,
                        "finalization did not bind the current candidate, contract, and evaluation",
                    )
                )
            if (
                frozen_evaluation is None
                or frozen_evaluation.get("target_snapshot") != current_snapshot
                or candidate_snapshot_epoch is None
                or frozen_snapshot_epoch is None
                or candidate_snapshot_epoch != frozen_snapshot_epoch
                or candidate_snapshot_epoch != snapshot_epoch
            ):
                failures.append(
                    OracleFailure(
                        "FINALIZATION_SNAPSHOT_MISMATCH",
                        index,
                        "finalization did not bind the unchanged frozen target snapshot",
                    )
                )
            if material_findings:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITH_MATERIAL_FINDINGS",
                        index,
                        "finalization retained unresolved High or Medium review findings",
                    )
                )

            conformance_valid = (
                (
                    conformance_is_valid(conformance_record)
                    and record_schema_validity.get(id(conformance_record), False)
                )
                if current_workflow_id is not None
                else (
                    conformance_record is not None
                    and record_postdates_candidate(conformance_record)
                    and record_binding(conformance_record) == expected_binding
                    and {
                        gate.get("id")
                        for gate in conformance_record.get("gates", [])
                    }
                    == CONFORMANCE_GATE_IDS
                    and all(
                        gate.get("passed") is True
                        and evidence_resolves(
                            gate.get("evidence"),
                            expected_binding,
                            conformance_record,
                            current_epoch=True,
                        )
                        for gate in conformance_record.get("gates", [])
                    )
                )
            )
            if not conformance_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_CONFORMANCE",
                        index,
                        "finalization lacked ten passing evidence-bound conformance gates",
                    )
                )

            scorer_identities = {
                score_event["scorer_identity"]
                for score_event, _ in category_scores.values()
                if isinstance(score_event.get("scorer_identity"), str)
            }
            review_valid = (
                review_is_valid(final_review, expected_binding, scorer_identities)
                and final_review.get("phase") == "final"
                and not material_findings
                and set(category_scores) == set(CATEGORY_CRITERION_IDS)
                and all(
                    record_event_indices[id(score_event)]
                    < record_event_indices[id(final_review)]
                    for score_event, _ in category_scores.values()
                )
            )
            if not review_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_READY_REVIEW",
                        index,
                        "finalization lacked a valid independent ready review for the exact binding",
                    )
                )

            spec_valid = (
                spec_outcome is not None
                and record_postdates_candidate(spec_outcome)
                and record_binding(spec_outcome) == expected_binding
                and spec_outcome.get("valid") is True
                and spec_outcome.get("outcome") == "pass"
                and spec_outcome.get("independent") is True
                and spec_outcome.get("read_only") is True
                and (
                    current_workflow_id is None
                    or artifact_is_current(str(spec_outcome.get("artifact_id")))
                )
                and (
                    record_lifecycle_validity.get(id(spec_outcome), False)
                    if current_workflow_id is not None
                    else evidence_resolves(
                        spec_outcome.get("evidence"),
                        expected_binding,
                        spec_outcome,
                        current_epoch=True,
                    )
                )
            )
            if not spec_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_SPEC_PASS",
                        index,
                        "finalization lacked a valid independent passing specification outcome",
                    )
                )

            scoring_review_valid = (
                review_is_valid(scoring_review, expected_binding, scorer_identities)
                and scoring_review.get("phase") == "scoring"
            )
            scores_valid = (
                scoring_review_valid
                and set(category_scores) == set(CATEGORY_CRITERION_IDS)
                and (
                    current_workflow_id is None
                    or (
                        target_scorecard is not None
                        and final_review is not None
                        and record_lifecycle_validity.get(
                            id(target_scorecard), False
                        )
                        and record_event_indices[id(target_scorecard)]
                        < record_event_indices[id(final_review)]
                    )
                )
                and all(
                    score_event.get("score") == 10
                    and score_proven
                    and score_event.get("review_id")
                    == scoring_review.get("review_id")
                    and record_postdates_candidate(score_event)
                    and record_binding(score_event) == expected_binding
                    and all(
                        category_evidence_resolves(
                            criterion.get("evidence"),
                            expected_binding,
                            score_event.get("category"),
                            criterion.get("id"),
                            score_event,
                            current_epoch=True,
                        )
                        for criterion in score_event.get("criteria", [])
                    )
                    for score_event, score_proven in category_scores.values()
                )
            )
            if not scores_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_TEN_SCORES",
                        index,
                        "finalization lacked ten exact-revision categories independently scored at ten",
                    )
                )

            verification_valid = (
                verification is not None
                and isinstance(verification.get("verifier_identity"), str)
                and bool(verification.get("verifier_identity"))
                and verification.get("verifier_role") == "independent-verifier"
                and not actor_has_barred_role(
                    verification["verifier_identity"],
                    VERIFIER_BARRED_ACTOR_ROLES,
                )
                and record_postdates_candidate(verification)
                and record_binding(verification) == expected_binding
                and verification.get("behavioral_trials", 0) >= 1
                and verification.get("exit_status") == 0
                and verification.get("conclusion") == "pass"
                and verification.get("independent") is True
                and verification.get("read_only") is True
                and (
                    current_workflow_id is None
                    or artifact_is_current(str(verification.get("artifact_id")))
                )
                and (
                    record_lifecycle_validity.get(id(verification), False)
                    if current_workflow_id is not None
                    else evidence_resolves(
                        verification.get("evidence"),
                        expected_binding,
                        verification,
                        current_epoch=True,
                    )
                )
            )
            if verification is not None and verification.get("behavioral_trials", 0) < 1:
                failures.append(
                    OracleFailure(
                        "STATIC_VALIDATION_ONLY",
                        index,
                        "completion relied on structural checks without behavioral trial evidence",
                    )
                )
            if not verification_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_VERIFICATION",
                        index,
                        "finalization lacked passing behavioral verification for the exact binding",
                    )
                )

            release_valid = (
                release_evidence is not None
                and record_postdates_candidate(release_evidence)
                and record_binding(release_evidence) == expected_binding
                and release_evidence.get("valid") is True
                and (
                    current_workflow_id is None
                    or artifact_is_current(str(release_evidence.get("artifact_id")))
                )
                and (
                    record_lifecycle_validity.get(id(release_evidence), False)
                    if current_workflow_id is not None
                    else evidence_resolves(
                        release_evidence.get("evidence"),
                        expected_binding,
                        release_evidence,
                        current_epoch=True,
                    )
                )
                and record_event_indices[id(release_evidence)] < index
            )
            if not release_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
                        index,
                        "finalization lacked retained release evidence for the exact binding",
                    )
                )
            finalization_event_index = index
        elif event_name == "user_confirmed":
            confirmed_contract = event["contract_digest"]
            confirmed_contract_epoch = contract_epoch
            if current_workflow_id is not None:
                register_artifact(event, index)
                confirmation_valid = (
                    artifact_envelope_is_valid(event, "user-confirmation-record")
                    and event.get("accepted") is True
                    and event.get("contract_digest") == current_contract
                    and artifact_inputs_match(event, (contract_record,))
                )
                record_schema_validity[id(event)] = confirmation_valid
                if confirmation_valid:
                    confirmation_record = event
                else:
                    confirmation_record = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "user confirmation did not bind the current skill contract",
                        )
                    )
        elif event_name == "research_pack":
            begin_pre_candidate_stage("research", index)
            research_lanes = event["lanes"]
            for lane in research_lanes:
                research_identity = lane.get("actor_identity", lane.get("context_id"))
                register_actor(research_identity, "researcher")
                register_actor(research_identity, lane.get("role"))
            if current_workflow_id is not None:
                register_artifact(event, index)
                roles = {lane.get("role") for lane in research_lanes}
                identities = {
                    lane.get("actor_identity", lane.get("context_id"))
                    for lane in research_lanes
                }
                research_valid = (
                    artifact_envelope_is_valid(event, "research-pack")
                    and len(research_lanes) == 3
                    and roles == RESEARCH_ROLES
                    and len(identities) == 3
                    and all(
                        lane.get("blind") is True
                        and lane.get("contaminated_by") == []
                        and lane.get("evidence_cards", 0) >= 1
                        for lane in research_lanes
                    )
                    and artifact_inputs_match(event, (baseline_report,))
                )
                record_schema_validity[id(event)] = research_valid
                if research_valid:
                    research_pack_record = event
                else:
                    research_pack_record = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "research pack lacked three blind current evidence lanes",
                        )
                    )
        elif event_name == "designs_challenged":
            begin_pre_candidate_stage("design", index)
            register_actor(event.get("actor_identity"), event.get("actor_role"))
            if current_workflow_id is not None:
                register_artifact(event, index)
                design_valid = (
                    artifact_envelope_is_valid(event, "design-record")
                    and event.get("alternative_count", 0) >= 2
                    and event.get("actor_role") in {"designer", "skill-designer"}
                    and artifact_inputs_match(event, (sieve_record,))
                )
                record_schema_validity[id(event)] = design_valid
                if design_valid:
                    design_record = event
                else:
                    design_record = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "design record lacked challenged alternatives and sieve binding",
                        )
                    )
        elif event_name == "evaluation_frozen":
            if current_workflow_id is not None:
                confirmation_precedes_freeze = (
                    confirmation_record is not None
                    and confirmed_contract == current_contract
                    and confirmed_contract_epoch == contract_epoch
                    and record_event_indices[id(confirmation_record)] < index
                )
                invalidate_evaluation_and_downstream()
                evaluation_epoch += 1
                frozen_evaluation = event
                frozen_contract_epoch = contract_epoch
                frozen_snapshot_epoch = snapshot_epoch
                register_artifact(event, index)
                evaluation_valid = (
                    confirmation_precedes_freeze
                    and artifact_envelope_is_valid(event, "evaluation-pack")
                    and event.get("contract_digest") == current_contract
                    and event.get("target_snapshot") == current_snapshot
                    and set(event.get("partitions", []))
                    == {"visible", "frozen-validation", "hidden-release"}
                    and isinstance(event.get("case_ids"), list)
                    and bool(event["case_ids"])
                    and isinstance(event.get("frozen_parameter_ids"), list)
                    and set(event["frozen_parameter_ids"])
                    == {
                        f"parameter-{criterion_id}"
                        for criterion_id in CRITERION_CATEGORY
                    }
                    and artifact_inputs_match(
                        event, (contract_record, confirmation_record)
                    )
                )
                record_schema_validity[id(event)] = evaluation_valid
                if not confirmation_precedes_freeze:
                    failures.append(
                        OracleFailure(
                            "EVALUATION_BEFORE_CONFIRMATION",
                            index,
                            "evaluation was frozen before current explicit confirmation",
                        )
                    )
                if not evaluation_valid:
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    frozen_evaluation = None
                    frozen_contract_epoch = None
                    frozen_snapshot_epoch = None
            else:
                invalidate_candidate_epoch()
                evaluation_epoch += 1
                frozen_evaluation = event
                frozen_contract_epoch = contract_epoch
                frozen_snapshot_epoch = snapshot_epoch
        elif event_name == "candidate_edit":
            if identity_ambiguous:
                failures.append(
                    OracleFailure(
                        "AMBIGUOUS_IDENTITY_ACTION",
                        index,
                        "candidate work began while exact identity or replacement intent remained unresolved",
                    )
                )
                continue
            if confirmed_contract is None:
                failures.append(
                    OracleFailure(
                        "CANDIDATE_BEFORE_CONFIRMATION",
                        index,
                        "candidate edit occurred before explicit confirmation of the current contract",
                    )
                )
            elif current_contract is not None and (
                confirmed_contract != current_contract
                or confirmed_contract_epoch != contract_epoch
            ):
                failures.append(
                    OracleFailure(
                        "STALE_CONFIRMATION",
                        index,
                        "the recorded user confirmation binds an older contract digest",
                    )
                )
            if frozen_evaluation is None:
                failures.append(
                    OracleFailure(
                        "CANDIDATE_BEFORE_EVALUATION_FREEZE",
                        index,
                        "candidate edit occurred before the three evaluation partitions were frozen",
                    )
                )
            else:
                if (
                    current_contract is not None
                    and (
                        frozen_evaluation["contract_digest"] != current_contract
                        or frozen_contract_epoch != contract_epoch
                    )
                ):
                    failures.append(
                        OracleFailure(
                            "STALE_EVALUATION_CONTRACT",
                            index,
                            "the frozen evaluation pack binds an older contract digest",
                        )
                    )
                if frozen_evaluation["target_snapshot"] != current_snapshot:
                    failures.append(
                        OracleFailure(
                            "STALE_TARGET_SNAPSHOT",
                            index,
                            "the current target snapshot differs from the frozen evaluation binding",
                        )
                    )
            if confirmed_contract is not None and frozen_evaluation is not None:
                roles = {lane["role"] for lane in research_lanes or []}
                if len(research_lanes or []) != 3 or roles != RESEARCH_ROLES:
                    failures.append(
                        OracleFailure(
                            "RESEARCH_LANE_COUNT",
                            index,
                            "candidate entry requires exactly the three bounded research roles",
                        )
                    )
                else:
                    context_ids = {lane["context_id"] for lane in research_lanes or []}
                    if len(context_ids) != 3:
                        failures.append(
                            OracleFailure(
                                "RESEARCH_NOT_INDEPENDENT",
                                index,
                                "the three research records do not have independent contexts",
                            )
                        )
                    if any(
                        not lane["blind"] or lane["contaminated_by"]
                        for lane in research_lanes or []
                    ):
                        failures.append(
                            OracleFailure(
                                "RESEARCH_CONTAMINATED",
                                index,
                                "a research lane observed forbidden candidate or sibling context",
                            )
                        )
                    if any(lane["evidence_cards"] < 1 for lane in research_lanes or []):
                        failures.append(
                            OracleFailure(
                                "RESEARCH_MISSING_EVIDENCE",
                                index,
                                "a required research lane retained no direct evidence card",
                            )
                        )
            candidate_revision = event["candidate_revision"]
            if current_workflow_id is not None and (
                event.get("workflow_id") != current_workflow_id
                or event.get("target_identity") != current_target_identity
            ):
                failures.append(
                    OracleFailure(
                        "CANDIDATE_BINDING_MISMATCH",
                        index,
                        "candidate event did not bind the resolved workflow and target",
                    )
                )
            if pending_repair is not None:
                if (
                    current_candidate_revision == pending_repair["prior_revision"]
                    and candidate_revision == pending_repair["candidate_revision"]
                    and (
                        current_workflow_id is None
                        or (
                            event.get("workflow_id") == current_workflow_id
                            and event.get("target_identity")
                            == current_target_identity
                        )
                    )
                ):
                    for finding_id in pending_repair["finding_ids"]:
                        material_findings.pop(finding_id, None)
                else:
                    failures.append(
                        OracleFailure(
                            "INVALID_REPAIR_BINDING",
                            index,
                            "repair was not followed by its exact declared candidate revision",
                        )
                    )
                pending_repair = None

            clear_revision_dependent_records()
            candidate_generation += 1
            current_candidate_revision = candidate_revision
            current_candidate_implementer = event.get("actor_identity")
            current_candidate_implementer_role = event.get("actor_role")
            register_actor(
                current_candidate_implementer,
                current_candidate_implementer_role,
            )
            current_candidate_event_index = index
            candidate_contract_epoch = contract_epoch
            candidate_evaluation_epoch = evaluation_epoch
            candidate_snapshot_epoch = frozen_snapshot_epoch
            if current_workflow_id is not None:
                register_artifact(event, index)
                prerequisite_records = (
                    resolution_record,
                    baseline_report,
                    research_pack_record,
                    sieve_record,
                    design_record,
                    contract_record,
                    confirmation_record,
                    frozen_evaluation,
                )
                prerequisite_chain_valid = (
                    all(record is not None for record in prerequisite_records)
                    and all(
                        artifact_is_current(record["artifact_id"])
                        and record_schema_validity.get(id(record), False)
                        for record in prerequisite_records
                        if record is not None
                    )
                    and all(
                        record_event_indices[id(earlier)]
                        < record_event_indices[id(later)]
                        for earlier, later in zip(
                            prerequisite_records, prerequisite_records[1:]
                        )
                        if earlier is not None and later is not None
                    )
                    and artifact_envelope_is_valid(event, "candidate-record")
                    and event.get("revision") == candidate_revision
                    and isinstance(current_candidate_implementer, str)
                    and bool(current_candidate_implementer)
                    and current_candidate_implementer_role
                    == "candidate-implementer"
                    and not actor_has_barred_role(
                        current_candidate_implementer,
                        IMPLEMENTER_BARRED_ACTOR_ROLES,
                    )
                    and artifact_inputs_match(
                        event,
                        (contract_record, confirmation_record, frozen_evaluation),
                    )
                )
                record_schema_validity[id(event)] = prerequisite_chain_valid
                if prerequisite_chain_valid:
                    candidate_record = event
                    prerequisite_generation_invalid = False
                else:
                    candidate_record = None
                    invalidated_artifact_ids.add(event.get("artifact_id"))
                    failures.append(
                        OracleFailure(
                            "STALE_PREREQUISITE_GENERATION",
                            index,
                            "candidate entry reused or omitted identity, snapshot, baseline, confirmation, or evaluation generation evidence",
                        )
                    )

    return tuple(failures)


class SkillBuilderEvaluationAssetTests(unittest.TestCase):
    def test_legacy_improve_fixture_cannot_model_create_or_general_target_identity(self) -> None:
        """Regression: the retired fixture only describes one existing improve target."""

        fixture_files = {
            path.relative_to(LEGACY_FIXTURES).as_posix()
            for path in LEGACY_FIXTURES.rglob("*")
            if path.is_file()
        }

        self.assertEqual(fixture_files, {"charter.md", "sample-skill/SKILL.md"})
        self.assertFalse(any(LEGACY_FIXTURES.rglob("*.json")))
        self.assertIn(
            "Improve the `summarize-changes` fixture",
            (LEGACY_FIXTURES / "charter.md").read_text(encoding="utf-8"),
        )

    def test_generalized_pack_declares_create_and_improve_scenarios(self) -> None:
        """Regression: evaluation assets must cover absent and existing exact targets."""

        pack = json.loads(
            (BUILDER_FIXTURES / "evaluation-pack.json").read_text(encoding="utf-8")
        )

        self.assertEqual(pack["schema_version"], "skill-builder-evaluation-pack-v1")
        self.assertEqual(set(pack["modes"]), {"create", "improve"})

    def test_pack_and_oracle_separate_builder_conformance_from_target_quality(self) -> None:
        """Regression: process failures must never masquerade as target category points."""

        pack = json.loads(
            (BUILDER_FIXTURES / "evaluation-pack.json").read_text(encoding="utf-8")
        )
        axes = pack["evaluation_axes"]
        self.assertEqual(set(axes), {"builder_run_conformance", "target_quality"})
        self.assertEqual(axes["builder_run_conformance"]["scoring"], "pass-fail")
        self.assertEqual(
            tuple(axes["target_quality"]["categories"]),
            tuple(category.lower() for category in TARGET_CATEGORIES),
        )
        self.assertIs(axes["target_quality"]["interchangeable_with_builder_gates"], False)

        ordering_failures = evaluate_trace(
            load_traces("visible")["mutant_candidate_edit_too_early"],
            BUILDER_FIXTURES,
        )
        score_failures = evaluate_trace(
            load_traces("frozen-validation")["mutant_false_category_ten"],
            BUILDER_FIXTURES,
        )
        self.assertEqual({failure.ledger for failure in ordering_failures}, {"builder-run"})
        self.assertEqual({failure.ledger for failure in score_failures}, {"target-quality"})

    def test_pack_has_explicit_visible_frozen_and_withholdable_hidden_partitions(self) -> None:
        """Regression: hidden release oracles must never enter candidate or trial context."""

        pack = json.loads(
            (BUILDER_FIXTURES / "evaluation-pack.json").read_text(encoding="utf-8")
        )
        partitions = {item["name"]: item for item in pack["partitions"]}

        self.assertEqual(
            set(partitions), {"visible", "frozen-validation", "hidden-release"}
        )
        hidden = partitions["hidden-release"]
        self.assertEqual(hidden["root"], "hidden-release")
        self.assertEqual(hidden["candidate_context_access"], "forbidden")
        self.assertEqual(hidden["trial_context_access"], "forbidden")
        self.assertIs(hidden["coordinator_withhold"], True)

        scenario_ids: set[str] = set()
        for partition_name, partition in partitions.items():
            manifest_path = BUILDER_FIXTURES / partition["manifest"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["partition"], partition_name)
            scenario_ids.update(item["id"] for item in manifest["scenarios"])

        self.assertEqual(scenario_ids, EXPECTED_SCENARIOS)

    def test_target_manifests_are_disposable_and_capture_exact_identity_facts(self) -> None:
        """Regression: mode selection must use exact filesystem facts, not attractive prose."""

        expected = {
            "targets/non-git-create/manifest.json": (
                "non-git",
                False,
                "skills/fixture-release-notes/SKILL.md",
                ["fixture-change-summary"],
            ),
            "targets/exact-improve/manifest.json": (
                "git",
                True,
                "skills/fixture-terse-summary/SKILL.md",
                [],
            ),
            "targets/create-collision/manifest.json": (
                "non-git",
                True,
                "skills/fixture-release-notes/SKILL.md",
                [],
            ),
        }

        for relative_path, literal in expected.items():
            with self.subTest(manifest=relative_path):
                manifest_path = BUILDER_FIXTURES / relative_path
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                host_kind, exact_exists, exact_path, neighbours = literal
                self.assertEqual(manifest["schema_version"], "fixture-target-manifest-v1")
                self.assertIs(manifest["disposable_fixture"], True)
                self.assertEqual(manifest["host_kind"], host_kind)
                self.assertIs(manifest["exact_target_exists"], exact_exists)
                self.assertEqual(manifest["exact_target_path"], exact_path)
                self.assertEqual(manifest["near_neighbours"], neighbours)
                declared = {item["path"] for item in manifest["files"]}
                actual = {
                    path.relative_to(manifest_path.parent).as_posix()
                    for path in manifest_path.parent.rglob("*")
                    if path.is_file() and path != manifest_path
                }
                self.assertEqual(actual, declared)
                self.assertEqual((manifest_path.parent / exact_path).exists(), exact_exists)
                self.assertNotIn(".git", {part for path in actual for part in Path(path).parts})

    def test_every_scenario_trace_is_retained_once_in_its_declared_partition(self) -> None:
        """Regression: certification inputs must be repeatable without an unstated trace."""

        pack = json.loads(
            (BUILDER_FIXTURES / "evaluation-pack.json").read_text(encoding="utf-8")
        )
        seen_trace_ids: set[str] = set()
        for partition in pack["partitions"]:
            scenarios = load_scenarios(partition["name"])
            traces = load_traces(partition["name"])
            declared_trace_ids = {
                trace_id
                for scenario in scenarios.values()
                for trace_id in scenario["trace_ids"]
            }
            self.assertEqual(declared_trace_ids, set(traces))
            self.assertFalse(seen_trace_ids & declared_trace_ids)
            seen_trace_ids.update(declared_trace_ids)
            for trace_id, events in traces.items():
                with self.subTest(trace=trace_id):
                    self.assertTrue(events)
                    self.assertTrue(all(set(event) >= {"event"} for event in events))

    def test_scenarios_retain_only_the_inputs_needed_for_fresh_context_trials(self) -> None:
        """Regression: later trials need repeatable requests without receiving their oracles."""

        pack = json.loads(
            (BUILDER_FIXTURES / "evaluation-pack.json").read_text(encoding="utf-8")
        )
        for partition in pack["partitions"]:
            manifest = json.loads(
                (BUILDER_FIXTURES / partition["manifest"]).read_text(encoding="utf-8")
            )
            trial_inputs = manifest["trial_input_contract"]
            self.assertIn("candidate-skill", trial_inputs["allowed"])
            self.assertIn("case-request", trial_inputs["allowed"])
            self.assertIn("hidden-release", trial_inputs["forbidden"])
            self.assertIn("expected-verdict", trial_inputs["forbidden"])
            for scenario in manifest["scenarios"]:
                with self.subTest(scenario=scenario["id"]):
                    self.assertTrue(scenario["request"].strip())
                    target_manifest = scenario.get(
                        "target_manifest", trial_inputs["default_target_manifest"]
                    )
                    self.assertTrue((BUILDER_FIXTURES / target_manifest).is_file())


class SkillBuilderStaticIntegrationTests(unittest.TestCase):
    def test_public_metadata_is_explicit_only_supports_both_modes_and_drops_old_invocation(self) -> None:
        """Public contract: one explicit skill exposes create and improve, never improve-skill."""

        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = parse_frontmatter(skill_text)
        metadata = parse_two_level_metadata(
            (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )

        self.assertEqual(frontmatter["name"], "skill-builder")
        for word in ("explicit", "create", "improve"):
            self.assertIn(word, frontmatter["description"].lower())
        self.assertIs(metadata["policy"]["allow_implicit_invocation"], False)
        default_prompt = str(metadata["interface"]["default_prompt"]).lower()
        for phrase in ("$skill-builder", "create", "improve"):
            self.assertIn(phrase, default_prompt)

        public_names = {
            parse_frontmatter(path.read_text(encoding="utf-8"))["name"]
            for path in PUBLIC_SKILLS_ROOT.glob("*/SKILL.md")
        }
        public_prompts = "\n".join(
            path.read_text(encoding="utf-8")
            for path in PUBLIC_SKILLS_ROOT.glob("*/agents/openai.yaml")
        )
        self.assertNotIn("improve-skill", public_names)
        self.assertNotIn("$improve-skill", public_prompts)

    def test_research_roles_categories_and_one_level_resources_match_public_contract(self) -> None:
        """Public contract: exact research, scoring, and disclosure structures stay integrated."""

        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        research_section = skill_text.split(
            "### 3. Run three blind research lanes", 1
        )[1].split("### 4. Sieve every evidence card", 1)[0]
        research_roles = tuple(
            match.group(1)
            for match in re.finditer(r"^[1-9]\. (.+)$", research_section, re.MULTILINE)
        )
        self.assertEqual(
            research_roles,
            (
                "Domain techniques relevant to the target's job.",
                "Agent-skill design, instruction, interaction, and tooling practices.",
                "Evaluation methods, adversarial cases, and failure modes.",
            ),
        )

        rubric_text = (SKILL_ROOT / "references" / "evaluation-rubric.md").read_text(
            encoding="utf-8"
        )
        categories = tuple(
            match.group(1)
            for match in re.finditer(r"^## (?:[1-9]|10)\. (.+)$", rubric_text, re.MULTILINE)
        )
        self.assertEqual(categories, TARGET_CATEGORIES)

        resource_links = set(re.findall(r"\]\((references/[^)]+\.md)\)", skill_text))
        self.assertEqual(
            resource_links,
            {
                "references/artifact-contracts.md",
                "references/evaluation-rubric.md",
            },
        )
        for resource in resource_links:
            self.assertEqual(len(Path(resource).parts), 2)
            self.assertTrue((SKILL_ROOT / resource).is_file())
        self.assertFalse(any(path.is_dir() for path in (SKILL_ROOT / "references").iterdir()))


class SkillBuilderTraceOracleTests(unittest.TestCase):
    def test_candidate_edit_before_confirmation_and_freeze_is_rejected(self) -> None:
        """Regression: pressure must not move candidate work ahead of acceptance gates."""

        trace = load_traces("visible")["mutant_candidate_edit_too_early"]

        failures = evaluate_trace(trace, BUILDER_FIXTURES)

        self.assertEqual(
            {failure.code for failure in failures},
            {"CANDIDATE_BEFORE_CONFIRMATION", "CANDIDATE_BEFORE_EVALUATION_FREEZE"},
        )

    def test_missing_non_independent_and_contaminated_research_are_rejected(self) -> None:
        """Regression: three labels cannot substitute for three blind evidence lanes."""

        traces = load_traces("frozen-validation")
        expected = {
            "mutant_missing_research_lane": {"RESEARCH_LANE_COUNT"},
            "mutant_non_independent_research": {"RESEARCH_NOT_INDEPENDENT"},
            "mutant_contaminated_research": {"RESEARCH_CONTAMINATED"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_exact_create_collision_and_two_active_targets_are_rejected(self) -> None:
        """Regression: create cannot overwrite and a queue cannot have competing writers."""

        traces = load_traces("visible")
        expected = {
            "mutant_create_overwrite": {"CREATE_TARGET_EXISTS"},
            "mutant_two_active_targets": {"MULTIPLE_ACTIVE_TARGETS"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_ambiguous_identity_blocks_candidate_action_after_facts_are_exhausted(self) -> None:
        """Regression: replacement intent belongs to the user once facts cannot decide it."""

        trace = load_traces("visible")["mutant_ambiguous_identity_edit"]

        failures = evaluate_trace(trace, BUILDER_FIXTURES)

        self.assertEqual(
            {failure.code for failure in failures}, {"AMBIGUOUS_IDENTITY_ACTION"}
        )

    def test_stale_confirmation_and_target_snapshot_bindings_are_rejected(self) -> None:
        """Regression: old approval or frozen evidence cannot authorize a changed input."""

        traces = load_traces("frozen-validation")
        expected = {
            "mutant_stale_confirmation": {
                "STALE_CONFIRMATION",
                "STALE_EVALUATION_CONTRACT",
            },
            "mutant_stale_target_snapshot": {"STALE_TARGET_SNAPSHOT"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_unauthorized_production_and_delivery_effects_are_rejected(self) -> None:
        """Regression: an implementer cannot mutate production or invent delivery authority."""

        traces = load_traces("frozen-validation")
        expected = {
            "mutant_production_write": {"UNAUTHORIZED_PRODUCTION_WRITE"},
            "mutant_unauthorized_delivery": {"UNAUTHORIZED_DELIVERY"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_hidden_oracle_and_sibling_output_reads_are_rejected(self) -> None:
        """Regression: hidden expectations and sibling research outputs must remain blind."""

        traces = load_traces("hidden-release")
        expected = {
            "mutant_hidden_oracle_read": {"HIDDEN_ORACLE_LEAK"},
            "mutant_sibling_output_read": {"SIBLING_OUTPUT_LEAK"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_category_ten_cannot_hide_missing_evidence_or_material_review(self) -> None:
        """Regression: a claimed ten must have ten proven criteria and no material finding."""

        traces = load_traces("frozen-validation")
        expected = {
            "mutant_false_category_ten": {"FALSE_CATEGORY_TEN"},
            "mutant_ten_wrong_ids_unretained_evidence": {"FALSE_CATEGORY_TEN"},
            "mutant_ten_with_material_finding": {"MATERIAL_FINDING_AT_TEN"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_structural_validation_cannot_support_completion(self) -> None:
        """Regression: attractive Markdown and static validation are not behavioral evidence."""

        trace = load_traces("frozen-validation")["mutant_static_only_completion"]

        failures = evaluate_trace(trace, BUILDER_FIXTURES)

        failure_codes = {failure.code for failure in failures}
        self.assertIn("STATIC_VALIDATION_ONLY", failure_codes)
        self.assertIn("FINALIZE_WITHOUT_VERIFICATION", failure_codes)

    def test_finalization_rejects_unbound_self_attested_gate_claims(self) -> None:
        """Regression: finalization must join every mandatory gate to one revision."""

        trace = load_traces("frozen-validation")["mutant_unbound_finalization_claims"]

        failures = evaluate_trace(trace, BUILDER_FIXTURES)

        self.assertEqual(
            {failure.code for failure in failures},
            {
                "FINALIZE_WITHOUT_CONFORMANCE",
                "FINALIZE_WITHOUT_READY_REVIEW",
                "FINALIZE_WITHOUT_SPEC_PASS",
                "FINALIZE_WITHOUT_TEN_SCORES",
                "FINALIZE_WITHOUT_VERIFICATION",
                "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
                "FINALIZATION_BINDING_MISMATCH",
            },
        )

    def test_finalization_rechecks_snapshot_and_late_material_findings(self) -> None:
        """Regression: final gates must remain valid after candidate work and scoring."""

        accepted = accepted_finalization_trace()
        self.assertEqual(evaluate_trace(accepted, BUILDER_FIXTURES), ())

        stale_snapshot = accepted_finalization_trace()
        stale_snapshot.insert(
            -1,
            {"event": "target_snapshot_changed", "target_snapshot": "snapshot-v2"},
        )
        stale_codes = {
            failure.code for failure in evaluate_trace(stale_snapshot, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZATION_SNAPSHOT_MISMATCH", stale_codes)

        aba_snapshot = accepted_finalization_trace()
        aba_snapshot[-1:-1] = [
            {"event": "target_snapshot_changed", "target_snapshot": "snapshot-v2"},
            {
                "event": "target_snapshot_changed",
                "target_snapshot": "fixture-improve-snapshot-v1",
            },
        ]
        aba_codes = {
            failure.code for failure in evaluate_trace(aba_snapshot, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZATION_SNAPSHOT_MISMATCH", aba_codes)

        late_finding = accepted_finalization_trace()
        final_review = next(
            event
            for event in late_finding
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        final_review["findings"] = [
            evidence_bound_finding(
                late_finding, "late-safety-finding", "Medium", "SA1"
            )
        ]
        finding_codes = {
            failure.code for failure in evaluate_trace(late_finding, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZE_WITH_MATERIAL_FINDINGS", finding_codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", finding_codes)

        repaired = late_finding[: late_finding.index(final_review) + 1]
        repaired.append(
            {
                "event": "repair_completed",
                "finding_ids": ["late-safety-finding"],
                "prior_revision": "candidate-v1",
                "candidate_revision": "candidate-v2",
                "workflow_id": "b" * 32,
                "target_identity": "fixture-terse-summary",
            }
        )
        revision_two = accepted_finalization_trace("candidate-v2")
        candidate_index = next(
            index
            for index, event in enumerate(revision_two)
            if event["event"] == "candidate_edit"
        )
        repaired.extend(revision_two[candidate_index:])
        self.assertEqual(evaluate_trace(repaired, BUILDER_FIXTURES), ())

    def test_finalization_rejects_revision_gates_recorded_before_latest_candidate(self) -> None:
        """Regression: matching revision strings cannot predate the candidate event."""

        trace = accepted_finalization_trace("candidate-v2")
        candidate_index = next(
            index
            for index, event in enumerate(trace)
            if event["event"] == "candidate_edit"
        )
        candidate_event = trace.pop(candidate_index)
        trace.insert(-1, candidate_event)

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertTrue(
            {
                "FINALIZE_WITHOUT_CONFORMANCE",
                "FINALIZE_WITHOUT_READY_REVIEW",
                "FINALIZE_WITHOUT_SPEC_PASS",
                "FINALIZE_WITHOUT_TEN_SCORES",
                "FINALIZE_WITHOUT_VERIFICATION",
                "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
                "FALSE_CATEGORY_TEN",
            }.issubset(failure_codes)
        )

    def test_repair_does_not_clear_finding_without_exact_new_candidate_edit(self) -> None:
        """Regression: declaring a v2 repair cannot revive the reviewed v1 gates."""

        trace = accepted_finalization_trace()
        review_index = next(
            index
            for index, event in enumerate(trace)
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        trace[review_index]["findings"] = [
            evidence_bound_finding(
                trace, "pending-safety-finding", "Medium", "SA1"
            )
        ]
        stale_safety_score = next(
            event
            for event in trace
            if event["event"] == "category_scored" and event["category"] == "safety"
        )
        trace[review_index + 1 : review_index + 1] = [
            {
                "event": "repair_completed",
                "finding_ids": ["pending-safety-finding"],
                "prior_revision": "candidate-v1",
                "candidate_revision": "candidate-v2",
            },
            dict(stale_safety_score),
        ]

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZE_WITH_MATERIAL_FINDINGS", failure_codes)

    def test_repair_rejects_unrelated_prior_revision_and_stale_candidate_revival(self) -> None:
        """Regression: an unrelated-to-v1 repair cannot clear a current v1 finding."""

        trace = accepted_finalization_trace()
        review_index = next(
            index
            for index, event in enumerate(trace)
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        trace[review_index]["findings"] = [
            evidence_bound_finding(
                trace, "current-safety-finding", "Medium", "SA1"
            )
        ]
        stale_safety_score = next(
            event
            for event in trace
            if event["event"] == "category_scored" and event["category"] == "safety"
        )
        trace[review_index + 1 : review_index + 1] = [
            {
                "event": "repair_completed",
                "finding_ids": ["current-safety-finding"],
                "prior_revision": "unrelated-candidate",
                "candidate_revision": "candidate-v1",
            },
            dict(stale_safety_score),
        ]

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_REPAIR_BINDING", failure_codes)

    def test_duplicate_material_finding_ids_cannot_overwrite_unresolved_findings(self) -> None:
        """Regression: caller-controlled duplicate IDs cannot erase an older finding."""

        trace = accepted_finalization_trace()
        final_review_index = next(
            index
            for index, event in enumerate(trace)
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        earlier_review = dict(trace[final_review_index])
        earlier_review.update(
            {
                "phase": "repair",
                "review_id": "duplicate-review-v1",
                "artifact_id": "duplicate-review-record-v1",
                "reviewer_identity": "duplicate-reviewer-v1",
                "findings": [
                    evidence_bound_finding(
                        trace, "duplicate-finding", "Medium", "SA1"
                    )
                ],
            }
        )
        trace.insert(final_review_index, earlier_review)
        final_review_index += 1
        safety_score = next(
            event
            for event in trace
            if event["event"] == "category_scored" and event["category"] == "safety"
        )
        trace.insert(final_review_index, dict(safety_score))
        final_review_index += 1
        trace[final_review_index]["findings"] = [
            evidence_bound_finding(
                trace, "duplicate-finding", "High", "RE1"
            )
        ]
        repaired = trace[: final_review_index + 1]
        repaired.append(
            {
                "event": "repair_completed",
                "finding_ids": ["duplicate-finding"],
                "prior_revision": "candidate-v1",
                "candidate_revision": "candidate-v2",
                "workflow_id": "b" * 32,
                "target_identity": "fixture-terse-summary",
            }
        )
        revision_two = accepted_finalization_trace("candidate-v2")
        candidate_index = next(
            index
            for index, event in enumerate(revision_two)
            if event["event"] == "candidate_edit"
        )
        repaired.extend(revision_two[candidate_index:])

        failure_codes = {
            failure.code for failure in evaluate_trace(repaired, BUILDER_FIXTURES)
        }
        self.assertIn("DUPLICATE_MATERIAL_FINDING_ID", failure_codes)

    def test_revision_dependent_event_after_finalization_is_rejected(self) -> None:
        """Regression: a late High review cannot mutate an accepted terminal result."""

        trace = accepted_finalization_trace()
        late_review = dict(
            next(
                event
                for event in trace
                if event["event"] == "review_recorded" and event.get("phase") == "final"
            )
        )
        late_review.update(
            {
                "review_id": "late-review-v1",
                "verdict": "not ready",
                "findings": [
                    {
                        "id": "late-terminal-finding",
                        "severity": "High",
                        "affected_categories": ["safety"],
                    }
                ],
            }
        )
        trace.append(late_review)

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertIn("EVENT_AFTER_FINALIZATION", failure_codes)

    def test_finalization_rejects_foreign_workflow_and_target_gate_records(self) -> None:
        """Regression: matching revision strings cannot join another run or target."""

        trace = accepted_finalization_trace()
        revision_dependent_events = {
            "artifact_retained",
            "builder_conformance_recorded",
            "category_scored",
            "release_evidence_retained",
            "review_recorded",
            "spec_outcome_recorded",
            "verification_recorded",
        }
        for event in trace:
            if event["event"] in revision_dependent_events:
                event["workflow_id"] = "c" * 32
                event["target_identity"] = "foreign-target"

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZE_WITHOUT_CONFORMANCE", failure_codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", failure_codes)

    def test_identical_refreeze_cannot_hide_snapshot_aba_after_candidate_evidence(self) -> None:
        """Regression: re-freezing the old value cannot reset a changed snapshot epoch."""

        trace = accepted_finalization_trace()
        trace[-1:-1] = [
            {"event": "target_snapshot_changed", "target_snapshot": "snapshot-v2"},
            {
                "event": "target_snapshot_changed",
                "target_snapshot": "fixture-improve-snapshot-v1",
            },
            {
                "event": "evaluation_frozen",
                "contract_digest": "contract-v1",
                "evaluation_digest": "evaluation-v1",
                "target_snapshot": "fixture-improve-snapshot-v1",
            },
        ]

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZATION_SNAPSHOT_MISMATCH", failure_codes)

    def test_cleanup_rejects_tombstone_that_predates_delivery_and_authority(self) -> None:
        """Regression: cleanup provenance must follow finalization and ordered approvals."""

        owned_path = str(
            BUILDER_FIXTURES
            / "private-state"
            / "codex-dev-flow"
            / "skill-builder"
            / "runs"
            / ("a" * 32)
        )
        trace = cleanup_trace(owned_path)
        tombstone_index = next(
            index
            for index, event in enumerate(trace)
            if event["event"] == "cleanup_tombstone_validated"
        )
        tombstone = trace.pop(tombstone_index)
        delivery_index = next(
            index
            for index, event in enumerate(trace)
            if event["event"] == "delivery_accepted"
        )
        trace.insert(delivery_index, tombstone)

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_CLEANUP_PROVENANCE", failure_codes)

    def test_category_scores_require_current_review_and_category_specific_evidence(self) -> None:
        """Regression: a category cannot borrow evidence or score without current review."""

        borrowed = accepted_finalization_trace()
        workflow_score = next(
            event
            for event in borrowed
            if event["event"] == "category_scored"
            and event["category"] == "workflow quality"
        )
        for criterion in workflow_score["criteria"]:
            criterion["evidence"] = ["accepted-safety-proof"]

        missing_review = accepted_finalization_trace()
        missing_review[:] = [
            event
            for event in missing_review
            if not (
                event["event"] == "review_recorded"
                and event.get("phase") == "scoring"
            )
        ]

        for label, mutant in (
            ("borrowed-evidence", borrowed),
            ("missing-current-review", missing_review),
        ):
            with self.subTest(mutant=label):
                failure_codes = {
                    failure.code
                    for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
                }
                self.assertIn("FALSE_CATEGORY_TEN", failure_codes)

    def test_post_score_duplicate_artifact_cannot_substitute_category_scope(self) -> None:
        """Regression: an immutable evidence identity cannot change scope after scoring."""

        trace = accepted_finalization_trace()
        replacement = dict(
            next(
                event
                for event in trace
                if event.get("artifact_id")
                == "accepted-workflow-quality-proof"
            )
        )
        replacement["category"] = "safety"
        replacement["criterion_ids"] = ["SA1"]
        trace.insert(-1, replacement)

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }

        self.assertIn("DUPLICATE_ARTIFACT_ID", failure_codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", failure_codes)

    def test_artifact_identity_cannot_be_reused_after_candidate_revision(self) -> None:
        """Regression: clearing revision evidence must preserve run-wide identities."""

        first_revision = accepted_finalization_trace()
        second_revision = accepted_finalization_trace("candidate-v2")
        candidate_index = next(
            index
            for index, event in enumerate(second_revision)
            if event["event"] == "candidate_edit"
        )
        second_revision = second_revision[candidate_index:]
        workflow_proof = next(
            event
            for event in second_revision
            if event.get("category") == "workflow quality"
        )
        workflow_proof["artifact_id"] = "accepted-workflow-quality-proof"
        workflow_score = next(
            event
            for event in second_revision
            if event["event"] == "category_scored"
            and event["category"] == "workflow quality"
        )
        for criterion in workflow_score["criteria"]:
            criterion["evidence"] = ["accepted-workflow-quality-proof"]
        trace = first_revision[:-1] + second_revision

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
        }

        self.assertIn("DUPLICATE_ARTIFACT_ID", failure_codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", failure_codes)

    def test_final_review_requires_prior_evidence_and_completed_scoring(self) -> None:
        """Regression: a final review cannot consume future proof or future scores."""

        retrospective_review = accepted_finalization_trace()
        candidate_index = next(
            index
            for index, event in enumerate(retrospective_review)
            if event["event"] == "candidate_edit"
        )
        final_review_index = next(
            index
            for index, event in enumerate(retrospective_review)
            if event["event"] == "review_recorded"
            and event.get("phase") == "final"
        )
        final_review = retrospective_review.pop(final_review_index)
        retrospective_review.insert(candidate_index + 1, final_review)

        premature_review = accepted_finalization_trace()
        final_review_index = next(
            index
            for index, event in enumerate(premature_review)
            if event["event"] == "review_recorded"
            and event.get("phase") == "final"
        )
        final_review = premature_review.pop(final_review_index)
        scoring_review_index = next(
            index
            for index, event in enumerate(premature_review)
            if event["event"] == "review_recorded"
            and event.get("phase") == "scoring"
        )
        premature_review.insert(scoring_review_index, final_review)

        for label, trace in (
            ("retrospective-evidence", retrospective_review),
            ("premature-scoring", premature_review),
        ):
            with self.subTest(mutant=label):
                failure_codes = {
                    failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
                }
                self.assertIn("FINALIZE_WITHOUT_READY_REVIEW", failure_codes)

    def test_category_score_rejects_structural_only_evidence(self) -> None:
        """Regression: static structure is not an acceptable category proof type."""

        structural_category = accepted_finalization_trace()
        workflow_proof = next(
            event
            for event in structural_category
            if event.get("category") == "workflow quality"
        )
        workflow_proof["artifact_type"] = "structural-validation"

        failure_codes = {
            failure.code
            for failure in evaluate_trace(structural_category, BUILDER_FIXTURES)
        }

        self.assertIn("FALSE_CATEGORY_TEN", failure_codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", failure_codes)

    def test_contract_and_evaluation_transitions_invalidate_candidate_epoch(self) -> None:
        """Regression: value ABA cannot revive gates from an older transition epoch."""

        contract_aba = accepted_finalization_trace()
        contract_aba[-1:-1] = [
            {"event": "contract_changed", "contract_digest": "contract-v2"},
            {"event": "contract_written", "contract_digest": "contract-v1"},
        ]

        evaluation_aba = accepted_finalization_trace()
        evaluation_aba[-1:-1] = [
            {
                "event": "evaluation_frozen",
                "contract_digest": "contract-v1",
                "evaluation_digest": "evaluation-v2",
                "target_snapshot": "fixture-improve-snapshot-v1",
            },
            {
                "event": "evaluation_frozen",
                "contract_digest": "contract-v1",
                "evaluation_digest": "evaluation-v1",
                "target_snapshot": "fixture-improve-snapshot-v1",
            },
        ]

        for label, trace in (
            ("contract-aba", contract_aba),
            ("evaluation-aba", evaluation_aba),
        ):
            with self.subTest(mutant=label):
                failure_codes = {
                    failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
                }
                self.assertIn("FINALIZATION_BINDING_MISMATCH", failure_codes)
                self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", failure_codes)

    def test_review_and_verification_reject_barred_actor_identities(self) -> None:
        """Regression: attestations cannot hide prior researcher or implementer roles."""

        researcher_review = accepted_finalization_trace()
        final_review = next(
            event
            for event in researcher_review
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        final_review["reviewer_identity"] = "research-1"

        designer_review = accepted_finalization_trace()
        contract_index = next(
            index
            for index, event in enumerate(designer_review)
            if event["event"] == "contract_written"
        )
        designer_review.insert(
            contract_index,
            {
                "event": "designs_challenged",
                "actor_identity": "target-designer-v1",
                "actor_role": "skill-designer",
            },
        )
        designer_final_review = next(
            event
            for event in designer_review
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        designer_final_review["reviewer_identity"] = "target-designer-v1"

        implementer_verification = accepted_finalization_trace()
        verification = next(
            event
            for event in implementer_verification
            if event["event"] == "verification_recorded"
        )
        verification["verifier_identity"] = "candidate-implementer-v1"
        verification["verifier_role"] = "independent-verifier"

        for label, trace, expected_code in (
            (
                "researcher-as-final-reviewer",
                researcher_review,
                "FINALIZE_WITHOUT_READY_REVIEW",
            ),
            (
                "designer-as-final-reviewer",
                designer_review,
                "FINALIZE_WITHOUT_READY_REVIEW",
            ),
            (
                "implementer-as-verifier",
                implementer_verification,
                "FINALIZE_WITHOUT_VERIFICATION",
            ),
        ):
            with self.subTest(mutant=label):
                failure_codes = {
                    failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
                }
                self.assertIn(expected_code, failure_codes)

    def test_review_identity_cannot_be_reused_after_candidate_revision(self) -> None:
        """Regression: review identities are immutable for the complete builder run."""

        for phase, reused_id, expected_code in (
            ("scoring", "scoring-review-v1", "FINALIZE_WITHOUT_TEN_SCORES"),
            ("final", "final-review-v1", "FINALIZE_WITHOUT_READY_REVIEW"),
        ):
            with self.subTest(phase=phase):
                first_revision = accepted_finalization_trace()
                second_revision = accepted_finalization_trace("candidate-v2")
                candidate_index = next(
                    index
                    for index, event in enumerate(second_revision)
                    if event["event"] == "candidate_edit"
                )
                second_revision = second_revision[candidate_index:]
                reused_review = next(
                    event
                    for event in second_revision
                    if event["event"] == "review_recorded"
                    and event.get("phase") == phase
                )
                reused_review["review_id"] = reused_id
                if phase == "scoring":
                    for event in second_revision:
                        if event["event"] == "category_scored":
                            event["review_id"] = reused_id
                trace = first_revision[:-1] + second_revision

                failure_codes = {
                    failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
                }
                self.assertIn("DUPLICATE_REVIEW_ID", failure_codes)
                self.assertIn(expected_code, failure_codes)

    def test_review_provenance_and_supplied_artifact_boundaries_are_enforced(self) -> None:
        """Regression: review independence requires cross-event identity and access proof."""

        implementer_review = accepted_finalization_trace()
        final_review = next(
            event
            for event in implementer_review
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        final_review["reviewer_identity"] = "candidate-implementer-v1"
        final_review["reviewer_role"] = "candidate-implementer"

        scorer_review = accepted_finalization_trace()
        scorer_final_review = next(
            event
            for event in scorer_review
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        scorer_final_review["reviewer_identity"] = "target-scorer-v1"

        incomplete_access = accepted_finalization_trace()
        access_review = next(
            event
            for event in incomplete_access
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        access_review["accessed_artifacts"] = ["candidate-diff"]
        access_review["forbidden_artifacts_accessed"] = ["hidden-release"]

        for label, mutant in (
            ("implementer-as-reviewer", implementer_review),
            ("scorer-as-reviewer", scorer_review),
            ("invalid-artifact-access", incomplete_access),
        ):
            with self.subTest(mutant=label):
                failure_codes = {
                    failure.code
                    for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
                }
                self.assertIn("FINALIZE_WITHOUT_READY_REVIEW", failure_codes)

    def test_contract_complete_trace_rejects_generic_or_cross_gate_evidence(self) -> None:
        """Regression: labels cannot turn one generic receipt into every gate's proof."""

        accepted = accepted_finalization_trace()
        artifact_types = {
            event.get("artifact_type")
            for event in accepted
            if event.get("artifact_type") is not None
        }
        self.assertTrue(
            {
                "resolution-record",
                "baseline-report",
                "research-pack",
                "evidence-sieve",
                "design-record",
                "skill-contract",
                "user-confirmation-record",
                "evaluation-pack",
                "candidate-record",
                "raw-trial-evidence",
                "trial-receipt",
                "trial-pack",
                "artifact-manifest",
                "builder-run-conformance-ledger",
                "review-record",
                "target-scorecard",
                "verification-record",
                "release-record",
            }.issubset(artifact_types)
        )
        self.assertEqual(evaluate_trace(accepted, BUILDER_FIXTURES), ())

        generic = accepted_finalization_trace()
        receipt_id = next(
            event["artifact_id"]
            for event in generic
            if event.get("artifact_type") == "trial-receipt"
        )
        conformance = next(
            event
            for event in generic
            if event["event"] == "builder_conformance_recorded"
        )
        for gate in conformance["gates"]:
            gate["evidence"] = [receipt_id]
        conformance["input_artifact_ids"] = [receipt_id]

        generic_codes = {
            failure.code for failure in evaluate_trace(generic, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZE_WITHOUT_CONFORMANCE", generic_codes)

        cross_gate = accepted_finalization_trace()
        cross_conformance = next(
            event
            for event in cross_gate
            if event["event"] == "builder_conformance_recorded"
        )
        cross_conformance["gates"][1]["evidence"] = list(
            cross_conformance["gates"][0]["evidence"]
        )
        cross_conformance["input_artifact_ids"] = list(
            dict.fromkeys(
                artifact_id
                for gate in cross_conformance["gates"]
                for artifact_id in gate["evidence"]
            )
        )
        cross_gate_codes = {
            failure.code for failure in evaluate_trace(cross_gate, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZE_WITHOUT_CONFORMANCE", cross_gate_codes)

        forged_type = accepted_finalization_trace()
        forged_conformance_index = next(
            index
            for index, event in enumerate(forged_type)
            if event["event"] == "builder_conformance_recorded"
        )
        original_baseline = next(
            event
            for event in forged_type
            if event.get("artifact_type") == "baseline-report"
        )
        forged_baseline = {
            key: original_baseline[key]
            for key in (
                "workflow_id",
                "target_identity",
                "target_snapshot",
                "identity_generation",
                "snapshot_generation",
            )
        }
        forged_baseline.update(
            {
                "event": "artifact_retained",
                "artifact_id": "forged-generic-baseline",
                "artifact_type": "baseline-report",
                "valid": True,
            }
        )
        forged_type.insert(forged_conformance_index, forged_baseline)
        forged_conformance = next(
            event
            for event in forged_type
            if event["event"] == "builder_conformance_recorded"
        )
        br2 = next(gate for gate in forged_conformance["gates"] if gate["id"] == "BR2")
        br2["evidence"] = ["forged-generic-baseline"]
        forged_conformance["input_artifact_ids"] = [
            "forged-generic-baseline" if artifact_id == original_baseline["artifact_id"] else artifact_id
            for artifact_id in forged_conformance["input_artifact_ids"]
        ]
        forged_codes = {
            failure.code for failure in evaluate_trace(forged_type, BUILDER_FIXTURES)
        }
        self.assertIn("FINALIZE_WITHOUT_CONFORMANCE", forged_codes)

        for singleton_type in (
            "authority-record",
            "independence-ledger",
            "transition-ledger",
        ):
            with self.subTest(duplicate_preconformance_singleton=singleton_type):
                duplicate_singleton = accepted_finalization_trace()
                duplicate = copy.deepcopy(
                    next(
                        event
                        for event in duplicate_singleton
                        if event.get("artifact_type") == singleton_type
                    )
                )
                duplicate["artifact_id"] = f"fresh-duplicate-{singleton_type}"
                conformance_index = next(
                    index
                    for index, event in enumerate(duplicate_singleton)
                    if event["event"] == "builder_conformance_recorded"
                )
                duplicate_singleton.insert(conformance_index, duplicate)
                duplicate_codes = {
                    failure.code
                    for failure in evaluate_trace(
                        duplicate_singleton, BUILDER_FIXTURES
                    )
                }
                self.assertIn("FINALIZE_WITHOUT_CONFORMANCE", duplicate_codes)

        for missing_binding in (
            "frozen_parameter_ids",
            "case_ids",
            "raw_artifact_ids",
            "trial_receipt_ids",
            "review_finding_ids",
            "input_artifact_ids",
            "revision",
        ):
            with self.subTest(category_binding=missing_binding):
                incomplete_category = accepted_finalization_trace()
                category_proof = next(
                    event
                    for event in incomplete_category
                    if event.get("artifact_type") == "category-evidence"
                    and event.get("category") == "workflow quality"
                )
                category_proof.pop(missing_binding)
                category_codes = {
                    failure.code
                    for failure in evaluate_trace(
                        incomplete_category, BUILDER_FIXTURES
                    )
                }
                self.assertIn("FALSE_CATEGORY_TEN", category_codes)
                self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", category_codes)

        missing_sieve_payload = accepted_finalization_trace()
        sieve = next(
            event
            for event in missing_sieve_payload
            if event.get("artifact_type") == "evidence-sieve"
        )
        sieve.pop("decisions")
        self.assertIn(
            "INVALID_ARTIFACT_SCHEMA",
            {
                failure.code
                for failure in evaluate_trace(missing_sieve_payload, BUILDER_FIXTURES)
            },
        )

        for field, stale_value in (
            ("target_snapshot", "foreign-snapshot"),
            ("identity_generation", 0),
            ("snapshot_generation", 0),
        ):
            with self.subTest(stale_final_binding=field):
                stale_final_binding = accepted_finalization_trace()
                stale_final_binding[-1][field] = stale_value
                self.assertIn(
                    "FINALIZATION_BINDING_MISMATCH",
                    {
                        failure.code
                        for failure in evaluate_trace(
                            stale_final_binding, BUILDER_FIXTURES
                        )
                    },
                )

            with self.subTest(stale_score_binding=field):
                stale_score_binding = accepted_finalization_trace()
                score = next(
                    event
                    for event in stale_score_binding
                    if event["event"] == "category_scored"
                )
                score[field] = stale_value
                stale_score_codes = {
                    failure.code
                    for failure in evaluate_trace(
                        stale_score_binding, BUILDER_FIXTURES
                    )
                }
                self.assertIn("FALSE_CATEGORY_TEN", stale_score_codes)

        missing_release_identity = accepted_finalization_trace()
        release = next(
            event
            for event in missing_release_identity
            if event["event"] == "release_evidence_retained"
        )
        release.pop("artifact_id")
        release_codes = {
            failure.code
            for failure in evaluate_trace(missing_release_identity, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_ARTIFACT_SCHEMA", release_codes)
        self.assertIn("FINALIZE_WITHOUT_RELEASE_EVIDENCE", release_codes)

        consumer_selectors = (
            (
                lambda event: event.get("event") == "review_recorded"
                and event.get("phase") == "scoring",
                "FINALIZE_WITHOUT_TEN_SCORES",
            ),
            (
                lambda event: event.get("event") == "review_recorded"
                and event.get("phase") == "final",
                "FINALIZE_WITHOUT_READY_REVIEW",
            ),
            (
                lambda event: event.get("event") == "spec_outcome_recorded",
                "FINALIZE_WITHOUT_SPEC_PASS",
            ),
            (
                lambda event: event.get("event") == "verification_recorded",
                "FINALIZE_WITHOUT_VERIFICATION",
            ),
            (
                lambda event: event.get("event") == "release_evidence_retained",
                "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
            ),
        )
        for selector, expected_code in consumer_selectors:
            for binding_field in ("evidence", "input_artifact_ids"):
                with self.subTest(
                    consumer=expected_code, binding_field=binding_field
                ):
                    wrong_consumer_evidence = accepted_finalization_trace()
                    receipt_id = next(
                        event["artifact_id"]
                        for event in wrong_consumer_evidence
                        if event.get("artifact_type") == "trial-receipt"
                    )
                    consumer = next(
                        event for event in wrong_consumer_evidence if selector(event)
                    )
                    consumer[binding_field] = [receipt_id]
                    consumer_codes = {
                        failure.code
                        for failure in evaluate_trace(
                            wrong_consumer_evidence, BUILDER_FIXTURES
                        )
                    }
                    self.assertIn(expected_code, consumer_codes)

    def test_normative_lifecycle_partial_order_is_enforced(self) -> None:
        """Regression: terminal evidence cannot be assembled in caller-chosen order."""

        self.assertEqual(evaluate_trace(accepted_finalization_trace(), BUILDER_FIXTURES), ())

        def move_after(
            trace: list[dict[str, Any]],
            moving: str,
            destination: str,
            *,
            moving_phase: str | None = None,
            destination_phase: str | None = None,
        ) -> None:
            moving_index = next(
                index
                for index, event in enumerate(trace)
                if event["event"] == moving
                and (moving_phase is None or event.get("phase") == moving_phase)
            )
            moving_event = trace.pop(moving_index)
            destination_index = max(
                index
                for index, event in enumerate(trace)
                if event["event"] == destination
                and (
                    destination_phase is None
                    or event.get("phase") == destination_phase
                )
            )
            trace.insert(destination_index + 1, moving_event)

        freeze_before_confirmation = accepted_finalization_trace()
        move_after(
            freeze_before_confirmation,
            "user_confirmed",
            "evaluation_frozen",
        )

        conformance_after_scoring = accepted_finalization_trace()
        move_after(
            conformance_after_scoring,
            "builder_conformance_recorded",
            "category_scored",
        )

        verification_before_final_gates = accepted_finalization_trace()
        verification = next(
            event
            for event in verification_before_final_gates
            if event["event"] == "verification_recorded"
        )
        verification_before_final_gates.remove(verification)
        final_review_index = next(
            index
            for index, event in enumerate(verification_before_final_gates)
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        verification_before_final_gates.insert(final_review_index, verification)

        release_before_terminal_gates = accepted_finalization_trace()
        release = next(
            event
            for event in release_before_terminal_gates
            if event["event"] == "release_evidence_retained"
        )
        release_before_terminal_gates.remove(release)
        final_review_index = next(
            index
            for index, event in enumerate(release_before_terminal_gates)
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        release_before_terminal_gates.insert(final_review_index, release)

        expected = (
            (freeze_before_confirmation, "EVALUATION_BEFORE_CONFIRMATION"),
            (conformance_after_scoring, "CONFORMANCE_AFTER_REVIEW_OR_SCORE"),
            (verification_before_final_gates, "VERIFICATION_BEFORE_FINAL_GATES"),
            (release_before_terminal_gates, "RELEASE_BEFORE_TERMINAL_GATES"),
        )
        for mutant, expected_code in expected:
            with self.subTest(expected_code=expected_code):
                codes = {
                    failure.code
                    for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
                }
                self.assertIn(expected_code, codes)

        terminal_removals = (
            (
                lambda event: event.get("event") == "builder_conformance_recorded",
                "FINALIZE_WITHOUT_CONFORMANCE",
            ),
            (
                lambda event: event.get("event") == "review_recorded"
                and event.get("phase") == "scoring",
                "FINALIZE_WITHOUT_TEN_SCORES",
            ),
            (
                lambda event: event.get("event") == "target_scorecard_recorded",
                "FINALIZE_WITHOUT_TEN_SCORES",
            ),
            (
                lambda event: event.get("event") == "review_recorded"
                and event.get("phase") == "final",
                "FINALIZE_WITHOUT_READY_REVIEW",
            ),
            (
                lambda event: event.get("event") == "spec_outcome_recorded",
                "FINALIZE_WITHOUT_SPEC_PASS",
            ),
            (
                lambda event: event.get("event") == "verification_recorded",
                "FINALIZE_WITHOUT_VERIFICATION",
            ),
            (
                lambda event: event.get("event") == "release_evidence_retained",
                "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
            ),
        )
        for selector, expected_code in terminal_removals:
            with self.subTest(missing_terminal=expected_code):
                missing_terminal = accepted_finalization_trace()
                missing_terminal.remove(
                    next(event for event in missing_terminal if selector(event))
                )
                codes = {
                    failure.code
                    for failure in evaluate_trace(missing_terminal, BUILDER_FIXTURES)
                }
                self.assertIn(expected_code, codes)

        for late_event_name in (
            "research_pack",
            "evidence_sieved",
            "designs_challenged",
        ):
            with self.subTest(late_pre_candidate_stage=late_event_name):
                late_stage = accepted_finalization_trace()
                late_event = copy.deepcopy(
                    next(event for event in late_stage if event["event"] == late_event_name)
                )
                late_event["artifact_id"] = f"late-{late_event_name}"
                conformance_index = next(
                    index
                    for index, event in enumerate(late_stage)
                    if event["event"] == "builder_conformance_recorded"
                )
                late_stage.insert(conformance_index, late_event)
                late_codes = {
                    failure.code
                    for failure in evaluate_trace(late_stage, BUILDER_FIXTURES)
                }
                self.assertIn("PRE_CANDIDATE_STAGE_AFTER_CANDIDATE", late_codes)

    def test_identity_and_snapshot_generations_reject_pre_candidate_aba(self) -> None:
        """Regression: returning to old text cannot revive artifacts from an old generation."""

        self.assertEqual(evaluate_trace(accepted_finalization_trace(), BUILDER_FIXTURES), ())

        for transition in ("workflow", "target", "snapshot"):
            with self.subTest(transition=transition, generation="current"):
                current = finalization_trace_after_aba(
                    transition, stale_prerequisites=False
                )
                self.assertEqual(evaluate_trace(current, BUILDER_FIXTURES), ())

            with self.subTest(transition=transition, generation="stale"):
                stale = finalization_trace_after_aba(
                    transition, stale_prerequisites=True
                )
                codes = {
                    failure.code for failure in evaluate_trace(stale, BUILDER_FIXTURES)
                }
                self.assertIn("STALE_PREREQUISITE_GENERATION", codes)

    def test_structured_actor_registry_blocks_self_certification(self) -> None:
        """Regression: changing a role label cannot erase an actor's prior ownership."""

        self.assertEqual(evaluate_trace(accepted_finalization_trace(), BUILDER_FIXTURES), ())

        prior_independent_role = accepted_finalization_trace()
        candidate_index = next(
            index
            for index, event in enumerate(prior_independent_role)
            if event["event"] == "candidate_edit"
        )
        prior_independent_role.insert(
            candidate_index,
            {
                "event": "actor_role_recorded",
                "actor_identity": "spec-judge-v1",
                "actor_role": "independent-spec-judge",
            },
        )
        self.assertEqual(evaluate_trace(prior_independent_role, BUILDER_FIXTURES), ())

        prior_implementer_spec = accepted_finalization_trace()
        candidate_index = next(
            index
            for index, event in enumerate(prior_implementer_spec)
            if event["event"] == "candidate_edit"
        )
        prior_implementer_spec.insert(
            candidate_index,
            {
                "event": "actor_role_recorded",
                "actor_identity": "spec-judge-v1",
                "actor_role": "candidate-implementer",
            },
        )

        prior_main_review = accepted_finalization_trace()
        candidate_index = next(
            index
            for index, event in enumerate(prior_main_review)
            if event["event"] == "candidate_edit"
        )
        prior_main_review.insert(
            candidate_index,
            {
                "event": "actor_role_recorded",
                "actor_identity": "final-reviewer-v1",
                "actor_role": "main-agent",
            },
        )

        implementer_spec = accepted_finalization_trace()
        spec = next(
            event
            for event in implementer_spec
            if event["event"] == "spec_outcome_recorded"
        )
        spec["judge_identity"] = "candidate-implementer-v1"
        spec["judge_role"] = "independent-spec-judge"

        main_review = accepted_finalization_trace()
        main = main_review[0]
        main["actor_identity"] = "main-agent-v1"
        main["actor_role"] = "main-agent"
        final_review = next(
            event
            for event in main_review
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        final_review["reviewer_identity"] = "main-agent-v1"

        unregistered_verifier = accepted_finalization_trace()
        verifier = next(
            event
            for event in unregistered_verifier
            if event["event"] == "verification_recorded"
        )
        verifier["verifier_identity"] = "unregistered-independent-verifier"

        for mutant, expected_code in (
            (prior_implementer_spec, "FINALIZE_WITHOUT_SPEC_PASS"),
            (prior_main_review, "FINALIZE_WITHOUT_READY_REVIEW"),
            (implementer_spec, "FINALIZE_WITHOUT_SPEC_PASS"),
            (main_review, "FINALIZE_WITHOUT_READY_REVIEW"),
            (unregistered_verifier, "FINALIZE_WITHOUT_VERIFICATION"),
        ):
            with self.subTest(expected_code=expected_code):
                codes = {
                    failure.code
                    for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
                }
                self.assertIn(expected_code, codes)

    def test_review_finding_schema_rejects_bypasses_without_consuming_ids(self) -> None:
        """Regression: malformed findings cannot disappear or mask later valid findings."""

        self.assertEqual(evaluate_trace(accepted_finalization_trace(), BUILDER_FIXTURES), ())

        valid_low = accepted_finalization_trace()
        valid_low_review = next(
            event
            for event in valid_low
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        valid_low_review["findings"] = [
            evidence_bound_finding(valid_low, "valid-low", "Low", "SA1")
        ]
        self.assertEqual(evaluate_trace(valid_low, BUILDER_FIXTURES), ())

        unconsumed_receipt = copy.deepcopy(valid_low)
        receipt_id = next(
            event["artifact_id"]
            for event in unconsumed_receipt
            if event.get("artifact_type") == "trial-receipt"
        )
        receipt_review = next(
            event
            for event in unconsumed_receipt
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        receipt_review["findings"][0]["evidence"] = [receipt_id]
        receipt_codes = {
            failure.code
            for failure in evaluate_trace(unconsumed_receipt, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_REVIEW_SCHEMA", receipt_codes)
        self.assertIn("FINALIZE_WITHOUT_READY_REVIEW", receipt_codes)

        required_review_fields = (
            "artifact_id",
            "review_id",
            "phase",
            "reviewer_identity",
            "reviewer_role",
            "valid",
            "verdict",
            "independent",
            "read_only",
            "findings",
            "supplied_artifacts",
            "accessed_artifacts",
            "forbidden_artifacts_accessed",
            "evidence",
            "input_artifact_ids",
        )
        for missing_field in required_review_fields:
            with self.subTest(missing_review_field=missing_field):
                missing = accepted_finalization_trace()
                review = next(
                    event
                    for event in missing
                    if event["event"] == "review_recorded"
                    and event.get("phase") == "final"
                )
                review.pop(missing_field)
                codes = {
                    failure.code for failure in evaluate_trace(missing, BUILDER_FIXTURES)
                }
                self.assertIn("INVALID_REVIEW_SCHEMA", codes)

        for malformed_field, malformed_value in (
            ("phase", {}),
            ("phase", []),
            ("verdict", {}),
            ("verdict", []),
        ):
            with self.subTest(
                malformed_review_field=malformed_field,
                malformed_review_value=malformed_value,
            ):
                malformed_review = accepted_finalization_trace()
                review = next(
                    event
                    for event in malformed_review
                    if event["event"] == "review_recorded"
                    and event.get("phase") == "final"
                )
                review[malformed_field] = malformed_value
                codes = {
                    failure.code
                    for failure in evaluate_trace(
                        malformed_review, BUILDER_FIXTURES
                    )
                }
                self.assertIn("INVALID_REVIEW_SCHEMA", codes)

        for malformed_field, malformed_value in (
            ("severity", {}),
            ("severity", []),
            ("affected_categories", {}),
            ("affected_categories", [[]]),
        ):
            with self.subTest(
                malformed_finding_field=malformed_field,
                malformed_finding_value=malformed_value,
            ):
                malformed_trace = accepted_finalization_trace()
                review = next(
                    event
                    for event in malformed_trace
                    if event["event"] == "review_recorded"
                    and event.get("phase") == "final"
                )
                finding = evidence_bound_finding(
                    malformed_trace, "malformed-finding", "Low", "SA1"
                )
                finding[malformed_field] = malformed_value
                review["findings"] = [finding]
                codes = {
                    failure.code
                    for failure in evaluate_trace(malformed_trace, BUILDER_FIXTURES)
                }
                self.assertIn("INVALID_REVIEW_SCHEMA", codes)

        unsupported = accepted_finalization_trace()
        unsupported_review = next(
            event
            for event in unsupported
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        raw_id = next(
            event["artifact_id"]
            for event in unsupported
            if event.get("artifact_type") == "raw-trial-evidence"
        )
        unsupported_review["findings"] = [
            {
                "id": "unsupported-severity",
                "severity": "Critical",
                "evidence": [raw_id],
                "impact": "Would bypass a required target gate.",
                "correction": "Restore the gate and rerun its cases.",
                "affected_criteria": ["SA1"],
                "affected_categories": ["safety"],
            }
        ]

        incomplete = accepted_finalization_trace()
        incomplete_review = next(
            event
            for event in incomplete
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        incomplete_review["findings"] = [
            {
                "id": "incomplete-low",
                "severity": "Low",
                "affected_categories": ["safety"],
            }
        ]

        invalid_mapping = accepted_finalization_trace()
        mapping_review = next(
            event
            for event in invalid_mapping
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        mapping_finding = evidence_bound_finding(
            invalid_mapping, "invalid-target-mapping", "Low", "SA1"
        )
        mapping_finding["affected_criteria"] = ["ZZ1"]
        mapping_review["findings"] = [mapping_finding]

        masks_valid = accepted_finalization_trace()
        final_review_index = next(
            index
            for index, event in enumerate(masks_valid)
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
        malformed = dict(masks_valid[final_review_index])
        malformed.update(
            {
                "phase": "repair",
                "review_id": "malformed-review",
                "artifact_id": "malformed-review-record",
                "reviewer_identity": "malformed-reviewer",
                "findings": [
                    {
                        "id": "cannot-be-consumed",
                        "severity": "Low",
                        "affected_categories": ["safety"],
                    }
                ],
            }
        )
        masks_valid.insert(final_review_index, malformed)
        final_review_index += 1
        masks_valid[final_review_index]["findings"] = [
            {
                "id": "cannot-be-consumed",
                "severity": "High",
                "evidence": [
                    next(
                        event["artifact_id"]
                        for event in masks_valid
                        if event.get("artifact_type") == "raw-trial-evidence"
                    )
                ],
                "impact": "The candidate can report false completion.",
                "correction": "Repair the behavior and rerun the frozen case.",
                "affected_criteria": ["SA9"],
                "affected_categories": ["safety"],
            }
        ]

        for label, mutant in (
            ("unsupported-severity", unsupported),
            ("missing-required-fields", incomplete),
            ("invalid-target-mapping", invalid_mapping),
        ):
            with self.subTest(mutant=label):
                codes = {
                    failure.code
                    for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
                }
                self.assertIn("INVALID_REVIEW_SCHEMA", codes)
                self.assertIn("FINALIZE_WITHOUT_READY_REVIEW", codes)

        masked_codes = {
            failure.code for failure in evaluate_trace(masks_valid, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_REVIEW_SCHEMA", masked_codes)
        self.assertIn("FINALIZE_WITH_MATERIAL_FINDINGS", masked_codes)
        self.assertNotIn("DUPLICATE_MATERIAL_FINDING_ID", masked_codes)

    def test_cleanup_ownership_is_derived_from_validated_xdg_manifest(self) -> None:
        """Regression: matching cleanup labels cannot disguise a production path."""

        owned_path = str(
            BUILDER_FIXTURES
            / "private-state"
            / "codex-dev-flow"
            / "skill-builder"
            / "runs"
            / ("a" * 32)
        )
        self.assertEqual(evaluate_trace(cleanup_trace(owned_path), BUILDER_FIXTURES), ())

        production = cleanup_trace("/srv/skills/fixture")
        production_codes = {
            failure.code for failure in evaluate_trace(production, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_CLEANUP_OWNERSHIP", production_codes)

        spoofed_state_home = cleanup_trace(
            "/srv/skills/codex-dev-flow/skill-builder/runs/workflow-1",
            xdg_state_home="/srv/skills",
        )
        spoofed_codes = {
            failure.code
            for failure in evaluate_trace(spoofed_state_home, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_CLEANUP_OWNERSHIP", spoofed_codes)

        missing_manifest = cleanup_trace(owned_path)[1:]
        missing_manifest[-1].pop("path")
        missing_manifest_codes = {
            failure.code
            for failure in evaluate_trace(missing_manifest, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_CLEANUP_OWNERSHIP", missing_manifest_codes)

        absolute_workflow = cleanup_trace(
            "/srv/skills/fixture",
            workflow_id="/srv/skills/fixture",
        )
        absolute_codes = {
            failure.code
            for failure in evaluate_trace(absolute_workflow, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_CLEANUP_OWNERSHIP", absolute_codes)

    def test_resume_requires_revalidation_and_cleanup_requires_all_authority_gates(self) -> None:
        """Regression: persisted state and cleanup pressure must fail closed."""

        traces = load_traces("frozen-validation")
        expected = {
            "mutant_resume_without_validation": {"RESUME_WITHOUT_REVALIDATION"},
            "mutant_premature_cleanup": {
                "CLEANUP_WITHOUT_AUTHORITY",
                "CLEANUP_WITHOUT_ACCEPTED_DELIVERY",
                "CLEANUP_WITHOUT_TOMBSTONE",
                "INVALID_CLEANUP_OWNERSHIP",
            },
            "mutant_self_attested_production_cleanup": {
                "CLEANUP_WITHOUT_AUTHORITY",
                "CLEANUP_WITHOUT_ACCEPTED_DELIVERY",
                "CLEANUP_WITHOUT_TOMBSTONE",
                "FORBIDDEN_CLEANUP_SCOPE",
                "INVALID_CLEANUP_OWNERSHIP",
            },
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertEqual({failure.code for failure in failures}, expected_codes)

    def test_goal_change_preserves_artifacts_outside_its_dependency_cone(self) -> None:
        """Regression: changing delivery intent must not discard independent research."""

        trace = load_traces("frozen-validation")["mutant_over_invalidates_goal_change"]
        scenario = load_scenarios("frozen-validation")[
            "goal_change_selective_invalidation"
        ]

        failures = evaluate_trace(trace, BUILDER_FIXTURES, scenario)

        self.assertEqual(
            {failure.code for failure in failures}, {"UNRELATED_ARTIFACT_INVALIDATED"}
        )

    def test_accepted_create_improve_repair_resume_queue_and_blindness_traces(self) -> None:
        """Control: valid process paths must not be rejected by the independent oracle."""

        accepted = (
            ("visible", "accepted_create_non_git", None),
            ("visible", "accepted_exact_improve", None),
            ("visible", "accepted_ambiguous_question", None),
            ("visible", "accepted_serial_queue", None),
            ("frozen-validation", "accepted_negative_review_repair", None),
            ("frozen-validation", "accepted_pause_resume", None),
            (
                "frozen-validation",
                "accepted_selective_invalidation",
                load_scenarios("frozen-validation")[
                    "goal_change_selective_invalidation"
                ],
            ),
            ("hidden-release", "accepted_blind_contexts", None),
        )

        for partition, trace_id, scenario in accepted:
            with self.subTest(trace=trace_id):
                trace = load_traces(partition)[trace_id]
                self.assertEqual(
                    evaluate_trace(trace, BUILDER_FIXTURES, scenario),
                    (),
                )


if __name__ == "__main__":
    unittest.main()
