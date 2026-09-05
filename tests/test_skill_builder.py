from __future__ import annotations

import ast
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


def accepted_finalization_trace(
    revision: str = "candidate-v1",
) -> list[dict[str, Any]]:
    binding = {
        "revision": revision,
        "contract_digest": "contract-v1",
        "evaluation_digest": "evaluation-v1",
    }
    proof = {
        "event": "artifact_retained",
        "artifact_id": "accepted-proof",
        "artifact_type": "trial-receipt",
        "valid": True,
        **binding,
    }
    scores = [
        {
            "event": "category_scored",
            "category": category,
            "score": 10,
            "criteria": [
                {"id": criterion_id, "passed": True, "evidence": ["accepted-proof"]}
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
            "target_snapshot": "fixture-improve-snapshot-v1",
        },
        {
            "event": "research_pack",
            "lanes": [
                {
                    "role": role,
                    "context_id": f"research-{index}",
                    "blind": True,
                    "contaminated_by": [],
                    "evidence_cards": 1,
                }
                for index, role in enumerate(sorted(RESEARCH_ROLES), start=1)
            ],
        },
        {"event": "contract_written", "contract_digest": "contract-v1"},
        {"event": "user_confirmed", "contract_digest": "contract-v1"},
        {
            "event": "evaluation_frozen",
            "contract_digest": "contract-v1",
            "evaluation_digest": "evaluation-v1",
            "target_snapshot": "fixture-improve-snapshot-v1",
        },
        {"event": "candidate_edit", "candidate_revision": revision},
        proof,
        {
            "event": "builder_conformance_recorded",
            "gates": [
                {"id": gate_id, "passed": True, "evidence": ["accepted-proof"]}
                for gate_id in sorted(CONFORMANCE_GATE_IDS)
            ],
            **binding,
        },
        *scores,
        {
            "event": "review_recorded",
            "phase": "final",
            "valid": True,
            "verdict": "ready",
            "independent": True,
            "read_only": True,
            "findings": [],
            "evidence": ["accepted-proof"],
            **binding,
        },
        {
            "event": "spec_outcome_recorded",
            "valid": True,
            "outcome": "pass",
            "independent": True,
            "read_only": True,
            "evidence": ["accepted-proof"],
            **binding,
        },
        {
            "event": "verification_recorded",
            "behavioral_trials": 1,
            "exit_status": 0,
            "conclusion": "pass",
            "independent": True,
            "read_only": True,
            "evidence": ["accepted-proof"],
            **binding,
        },
        {
            "event": "release_evidence_retained",
            "valid": True,
            "evidence": ["accepted-proof"],
            **binding,
        },
        {"event": "finalized", **binding},
    ]


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
            "event": "run_state_manifest_validated",
            "valid": True,
            "xdg_state_home": state_home,
            "run_directory": run_directory,
            "manifest_digest": "manifest-1",
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
            "event": "delivery_accepted",
            "valid": True,
            "accepted": True,
            "delivery_record_digest": "delivery-1",
            **binding,
        },
        {
            "event": "cleanup_tombstone_validated",
            "valid": True,
            "scope": "helper-owned-parent",
            "tombstone_digest": "tombstone-1",
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
    frozen_evaluation: dict[str, Any] | None = None
    current_snapshot: str | None = None
    research_lanes: list[dict[str, Any]] | None = None
    active_target: str | None = None
    material_findings: dict[str, tuple[set[str], Any, int]] = {}
    verification: dict[str, Any] | None = None
    pause_record: dict[str, Any] | None = None
    identity_ambiguous = False
    current_candidate_revision: str | None = None
    retained_artifacts: dict[str, dict[str, Any]] = {}
    conformance_record: dict[str, Any] | None = None
    final_review: dict[str, Any] | None = None
    spec_outcome: dict[str, Any] | None = None
    category_scores: dict[str, tuple[dict[str, Any], bool]] = {}
    release_evidence: dict[str, Any] | None = None
    cleanup_authority: dict[str, Any] | None = None
    accepted_delivery: dict[str, Any] | None = None
    cleanup_tombstone: dict[str, Any] | None = None
    run_state_manifest: dict[str, Any] | None = None
    snapshot_changed_since_freeze = False
    current_candidate_event_index: int | None = None
    pending_repair: dict[str, Any] | None = None
    record_event_indices: dict[int, int] = {}

    def record_binding(record: dict[str, Any]) -> tuple[Any, Any, Any]:
        return (
            record.get("revision"),
            record.get("contract_digest"),
            record.get("evaluation_digest"),
        )

    def record_postdates_candidate(record: dict[str, Any]) -> bool:
        return (
            current_candidate_event_index is not None
            and record_event_indices.get(id(record), -1)
            > current_candidate_event_index
        )

    def evidence_resolves(
        evidence_ids: object,
        binding: tuple[Any, Any, Any],
        *,
        current_epoch: bool = False,
    ) -> bool:
        return (
            isinstance(evidence_ids, list)
            and bool(evidence_ids)
            and all(
                isinstance(artifact_id, str)
                and artifact_id in retained_artifacts
                and retained_artifacts[artifact_id].get("valid") is True
                and record_binding(retained_artifacts[artifact_id]) == binding
                and (
                    not current_epoch
                    or record_postdates_candidate(retained_artifacts[artifact_id])
                )
                for artifact_id in evidence_ids
            )
        )

    for index, event in enumerate(trace):
        event_name = event["event"]
        record_event_indices[id(event)] = index
        if event_name == "resolve":
            manifest = json.loads(
                (fixture_root / event["target_manifest"]).read_text(encoding="utf-8")
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
            current_contract = event["contract_digest"]
        elif event_name == "target_snapshot_changed":
            current_snapshot = event["target_snapshot"]
            if frozen_evaluation is not None:
                snapshot_changed_since_freeze = True
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
        elif event_name == "artifact_retained":
            retained_artifacts[event["artifact_id"]] = event
        elif event_name == "builder_conformance_recorded":
            conformance_record = event
        elif event_name == "spec_outcome_recorded":
            spec_outcome = event
        elif event_name == "release_evidence_retained":
            release_evidence = event
        elif event_name == "review_recorded":
            if event.get("phase") == "final":
                final_review = event
            if event["valid"]:
                for finding in event["findings"]:
                    if finding["severity"] in {"High", "Medium"}:
                        affected_categories = set(finding["affected_categories"])
                        material_findings[finding["id"]] = (
                            affected_categories,
                            event.get("revision"),
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
                or any(
                    finding_revision != current_candidate_revision
                    or finding_index <= current_candidate_event_index
                    for _, finding_revision, finding_index in finding_records
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
        elif event_name == "category_scored":
            criteria = event["criteria"]
            binding = record_binding(event)
            expected_ids = CATEGORY_CRITERION_IDS.get(event["category"])
            ten_is_proven = (
                len(criteria) == 10
                and {criterion["id"] for criterion in criteria} == expected_ids
                and all(
                    criterion["passed"] is True
                    and evidence_resolves(criterion["evidence"], binding)
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
            verification = event
        elif event_name == "finalized":
            evaluation_digest = (
                frozen_evaluation.get("evaluation_digest")
                if frozen_evaluation is not None
                else None
            )
            expected_binding = (
                current_candidate_revision,
                current_contract,
                evaluation_digest,
            )
            if (
                None in expected_binding
                or record_binding(event) != expected_binding
                or frozen_evaluation is None
                or frozen_evaluation.get("contract_digest") != current_contract
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
                or snapshot_changed_since_freeze
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
                conformance_record is not None
                and record_postdates_candidate(conformance_record)
                and record_binding(conformance_record) == expected_binding
                and {gate.get("id") for gate in conformance_record.get("gates", [])}
                == CONFORMANCE_GATE_IDS
                and all(
                    gate.get("passed") is True
                    and evidence_resolves(
                        gate.get("evidence"),
                        expected_binding,
                        current_epoch=True,
                    )
                    for gate in conformance_record.get("gates", [])
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

            review_valid = (
                final_review is not None
                and record_postdates_candidate(final_review)
                and record_binding(final_review) == expected_binding
                and final_review.get("valid") is True
                and final_review.get("verdict") == "ready"
                and final_review.get("independent") is True
                and final_review.get("read_only") is True
                and not material_findings
                and evidence_resolves(
                    final_review.get("evidence"),
                    expected_binding,
                    current_epoch=True,
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
                and evidence_resolves(
                    spec_outcome.get("evidence"),
                    expected_binding,
                    current_epoch=True,
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

            scores_valid = set(category_scores) == set(CATEGORY_CRITERION_IDS) and all(
                score_event.get("score") == 10
                and score_proven
                and record_postdates_candidate(score_event)
                and record_binding(score_event) == expected_binding
                and all(
                    evidence_resolves(
                        criterion.get("evidence"),
                        expected_binding,
                        current_epoch=True,
                    )
                    for criterion in score_event.get("criteria", [])
                )
                for score_event, score_proven in category_scores.values()
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
                and record_postdates_candidate(verification)
                and record_binding(verification) == expected_binding
                and verification.get("behavioral_trials", 0) >= 1
                and verification.get("exit_status") == 0
                and verification.get("conclusion") == "pass"
                and verification.get("independent") is True
                and verification.get("read_only") is True
                and evidence_resolves(
                    verification.get("evidence"),
                    expected_binding,
                    current_epoch=True,
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
                and evidence_resolves(
                    release_evidence.get("evidence"),
                    expected_binding,
                    current_epoch=True,
                )
            )
            if not release_valid:
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
                        index,
                        "finalization lacked retained release evidence for the exact binding",
                    )
                )
        elif event_name == "user_confirmed":
            confirmed_contract = event["contract_digest"]
        elif event_name == "research_pack":
            research_lanes = event["lanes"]
        elif event_name == "evaluation_frozen":
            frozen_evaluation = event
            snapshot_changed_since_freeze = False
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
            elif current_contract is not None and confirmed_contract != current_contract:
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
                    and frozen_evaluation["contract_digest"] != current_contract
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
            if pending_repair is not None:
                if (
                    current_candidate_revision == pending_repair["prior_revision"]
                    and candidate_revision == pending_repair["candidate_revision"]
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

            retained_artifacts.clear()
            conformance_record = None
            final_review = None
            spec_outcome = None
            category_scores.clear()
            verification = None
            release_evidence = None
            current_candidate_revision = candidate_revision
            current_candidate_event_index = index

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
            {
                "id": "late-safety-finding",
                "severity": "Medium",
                "affected_categories": ["safety"],
            }
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

        self.assertEqual(
            {failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)},
            {
                "FINALIZE_WITHOUT_CONFORMANCE",
                "FINALIZE_WITHOUT_READY_REVIEW",
                "FINALIZE_WITHOUT_SPEC_PASS",
                "FINALIZE_WITHOUT_TEN_SCORES",
                "FINALIZE_WITHOUT_VERIFICATION",
                "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
            },
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
            {
                "id": "pending-safety-finding",
                "severity": "Medium",
                "affected_categories": ["safety"],
            }
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
            {
                "id": "current-safety-finding",
                "severity": "Medium",
                "affected_categories": ["safety"],
            }
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
