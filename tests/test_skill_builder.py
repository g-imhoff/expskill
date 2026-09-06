from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
SHA256_RE = re.compile(r"[0-9a-f]{64}")
RFC3339_UTC_RE = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z"
)
TRANSITION_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "workflow_id",
        "target_identity",
        "sequence",
        "prior_receipt_digest",
        "event",
        "source_stage",
        "destination_stage",
        "relevant_artifact_digests",
        "target_snapshot_digest",
        "authority_event_digest",
        "created_at",
        "receipt_digest",
    }
)
ARTIFACT_ENVELOPE_FIELDS = frozenset(
    {
        "artifact_id",
        "artifact_type",
        "workflow_id",
        "target_identity",
        "mode",
        "created_stage",
        "created_sequence",
        "producer",
        "created_at",
        "input_bindings",
        "payload_path",
        "payload_digest",
        "limitations",
        "envelope_digest",
    }
)
MANIFEST_ARTIFACT_TYPES = frozenset(
    {"artifact-manifest", "candidate-manifest", "terminal-manifest"}
)
KNOWN_EVENTS = frozenset(
    {
        "actor_role_recorded",
        "artifact_retained",
        "baseline_captured",
        "builder_conformance_recorded",
        "candidate_edit",
        "category_scored",
        "cleanup_attempt",
        "cleanup_authority_recorded",
        "cleanup_tombstone_validated",
        "context_read",
        "contract_changed",
        "contract_written",
        "delivery_accepted",
        "delivery_attempt",
        "designs_challenged",
        "evaluation_frozen",
        "evidence_sieved",
        "finalization_receipt_validated",
        "finalized",
        "goal_changed",
        "paused",
        "question_asked",
        "release_evidence_retained",
        "repair_completed",
        "research_pack",
        "resolve",
        "resumed",
        "review_recorded",
        "run_state_manifest_validated",
        "spec_outcome_recorded",
        "stopped",
        "target_abandoned",
        "target_activated",
        "target_finalized",
        "target_scorecard_recorded",
        "target_snapshot_changed",
        "user_confirmed",
        "verification_recorded",
        "write",
        "queue_created",
    }
)
EVENT_REQUIRED_FIELDS = {
    "resolve": frozenset(
        {"target_manifest", "selected_mode", "target_snapshot", "authority"}
    ),
    "target_activated": frozenset({"target_identity"}),
    "target_finalized": frozenset({"target_identity"}),
    "target_abandoned": frozenset({"target_identity"}),
    "contract_written": frozenset({"contract_digest"}),
    "contract_changed": frozenset({"contract_digest"}),
    "target_snapshot_changed": frozenset({"target_snapshot"}),
    "paused": frozenset({"state_validated", "target_identity"}),
    "resumed": frozenset(
        {
            "target_identity",
            "chain_revalidated",
            "identity_revalidated",
            "snapshot_revalidated",
            "evidence_revalidated",
        }
    ),
    "goal_changed": frozenset({"invalidated"}),
    "write": frozenset({"destination_scope", "effect", "actor", "path"}),
    "delivery_attempt": frozenset({"destination_scope", "effect", "actor"}),
    "context_read": frozenset(
        {"path", "actor_role", "source_role", "actor"}
    ),
    "research_pack": frozenset({"lanes"}),
    "repair_completed": frozenset(
        {"finding_ids", "prior_revision", "candidate_revision"}
    ),
    "category_scored": frozenset({"category", "score", "criteria"}),
    "candidate_edit": frozenset(
        {
            "candidate_revision",
            "write_scope",
            "isolated_locator",
            "owned_paths",
            "path",
        }
    ),
    "user_confirmed": frozenset({"contract_digest"}),
    "evaluation_frozen": frozenset(
        {
            "contract_digest",
            "evaluation_digest",
            "target_snapshot",
            "partitions",
            "case_ids",
            "cases",
            "rubric_artifact_id",
            "rubric_digest",
            "frozen_parameter_ids",
        }
    ),
}
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
        "host-rules",
        "baseline-report",
        "preserved-regressions",
        "research-pack",
        "evidence-sieve",
        "design-record",
        "skill-contract",
        "user-confirmation-record",
        "confirmed-contract",
        "rubric",
        "evaluation-pack",
        "authority-record",
    }
)
CANDIDATE_BOUND_ARTIFACT_TYPES = frozenset(
    {
        "candidate-record",
        "candidate-diff",
        "candidate-manifest",
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
        "terminal-manifest",
    }
)
EVALUATION_BOUND_ARTIFACT_TYPES = CANDIDATE_BOUND_ARTIFACT_TYPES | {
    "evaluation-pack"
}
CONTRACT_BOUND_ARTIFACT_TYPES = EVALUATION_BOUND_ARTIFACT_TYPES | {
    "skill-contract",
    "user-confirmation-record",
    "confirmed-contract",
    "rubric",
}
RETAINED_EVENT_ARTIFACT_TYPES = frozenset(
    {
        "authority-record",
        "host-rules",
        "preserved-regressions",
        "confirmed-contract",
        "rubric",
        "candidate-diff",
        "candidate-manifest",
        "raw-trial-evidence",
        "trial-receipt",
        "trial-pack",
        "independence-ledger",
        "transition-ledger",
        "artifact-manifest",
        "category-evidence",
        "terminal-manifest",
    }
)
REVIEW_EVIDENCE_ARTIFACT_TYPES = frozenset(
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

MANIFEST_FILE_FIELDS = frozenset(
    {
        "artifact_id",
        "path",
        "media_kind",
        "byte_count",
        "payload_digest",
        "envelope_digest",
        "source_role",
        "retention_class",
    }
)
MANIFEST_HEADER_FIELDS = frozenset(
    {
        "schema_version",
        "workflow_id",
        "target_identity",
        "collection_type",
        "declared_item_count",
        "observed_item_count",
        "observed_byte_count",
        "files",
        "overflow",
    }
)
CANDIDATE_FILE_FIELDS = frozenset(
    {"path", "media_kind", "byte_count", "digest"}
)
CASE_EVIDENCE_FIELDS = frozenset(
    {"case_id", "raw_request_digest", "raw_evidence_digest"}
)
CASE_RECEIPT_FIELDS = frozenset(
    {
        "case_id",
        "raw_evidence_digest",
        "receipt_digest",
        "fresh_context_id",
    }
)
EVALUATION_CASE_FIELDS = frozenset(
    {
        "id",
        "partition",
        "scenario_id",
        "purpose",
        "raw_request_digest",
        "allowed_context",
        "setup_manifest",
        "observable_assertions",
        "forbidden_effects",
        "evidence_requirements",
        "pass_rule",
    }
)
CASE_SETUP_MANIFEST_FIELDS = frozenset({"path", "digest"})
CASE_EVIDENCE_REQUIREMENT_FIELDS = frozenset(
    {
        "criterion_id",
        "frozen_parameter_id",
        "contract_digest",
        "rubric_artifact_id",
        "rubric_digest",
        "scenario_manifest_digest",
        "required_artifact_types",
    }
)
MEDIA_KIND_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*"
)


def load_traces(partition: str) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(
        (BUILDER_FIXTURES / partition / "traces.json").read_text(encoding="utf-8")
    )
    return {
        trace_id: build_fixture_trace(partition, trace_id, events)
        for trace_id, events in payload["traces"].items()
    }


def load_scenarios(partition: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(
        (BUILDER_FIXTURES / partition / "scenarios.json").read_text(encoding="utf-8")
    )
    return {scenario["id"]: scenario for scenario in payload["scenarios"]}


def fixture_case_sources(
    fixture_root: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Load the retained normative scenario and setup bytes for frozen cases."""

    sources: dict[str, list[dict[str, Any]]] = {}
    try:
        for partition in ("visible", "frozen-validation", "hidden-release"):
            scenario_path = fixture_root / partition / "scenarios.json"
            scenario_bytes = scenario_path.read_bytes()
            payload = json.loads(scenario_bytes.decode("utf-8"))
            trial_contract = payload["trial_input_contract"]
            allowed_context = trial_contract["allowed"]
            forbidden_effects = trial_contract["forbidden"]
            default_manifest = trial_contract["default_target_manifest"]
            if (
                payload.get("partition") != partition
                or not unique_nonempty_strings(allowed_context)
                or not unique_nonempty_strings(forbidden_effects)
                or not isinstance(default_manifest, str)
            ):
                return {}
            partition_sources: list[dict[str, Any]] = []
            for scenario in payload["scenarios"]:
                setup_path = scenario.get("target_manifest", default_manifest)
                normalized_setup = normalized_run_relative_path(setup_path)
                if normalized_setup is None:
                    return {}
                setup_bytes = (fixture_root / normalized_setup).read_bytes()
                if not isinstance(scenario.get("request"), str):
                    return {}
                partition_sources.append(
                    {
                        "scenario_id": scenario["id"],
                        "purpose": scenario["regression"],
                        "request": scenario["request"],
                        "allowed_context": copy.deepcopy(allowed_context),
                        "forbidden_effects": [
                            "production-write",
                            *copy.deepcopy(forbidden_effects),
                        ],
                        "setup_manifest": {
                            "path": normalized_setup,
                            "digest": hashlib.sha256(setup_bytes).hexdigest(),
                        },
                        "scenario_manifest_digest": hashlib.sha256(
                            scenario_bytes
                        ).hexdigest(),
                    }
                )
            if not partition_sources:
                return {}
            sources[partition] = partition_sources
    except (KeyError, OSError, TypeError, UnicodeError, ValueError):
        return {}
    return sources


def expected_evaluation_cases(
    fixture_root: Path,
    criterion_ids: list[str],
    contract_digest: str,
    rubric_artifact_id: str,
    rubric_digest: str,
) -> list[dict[str, Any]]:
    """Derive exact case contracts from retained scenario and setup bytes."""

    sources = fixture_case_sources(fixture_root)
    positions = {partition: 0 for partition in sources}
    cases: list[dict[str, Any]] = []
    partitions = ("visible", "frozen-validation", "hidden-release")
    if set(positions) != set(partitions):
        return []
    for index, criterion_id in enumerate(criterion_ids):
        partition = partitions[index % len(partitions)]
        partition_sources = sources[partition]
        source = partition_sources[positions[partition] % len(partition_sources)]
        positions[partition] += 1
        parameter_id = f"parameter-{criterion_id}"
        purpose = source["purpose"]
        cases.append(
            {
                "id": f"case-{criterion_id.lower()}",
                "partition": partition,
                "scenario_id": source["scenario_id"],
                "purpose": purpose,
                "raw_request_digest": hashlib.sha256(
                    source["request"].encode("utf-8")
                ).hexdigest(),
                "allowed_context": copy.deepcopy(source["allowed_context"]),
                "setup_manifest": copy.deepcopy(source["setup_manifest"]),
                "observable_assertions": [
                    purpose,
                    f"{criterion_id} satisfies {parameter_id}",
                ],
                "forbidden_effects": copy.deepcopy(
                    source["forbidden_effects"]
                ),
                "evidence_requirements": {
                    "criterion_id": criterion_id,
                    "frozen_parameter_id": parameter_id,
                    "contract_digest": contract_digest,
                    "rubric_artifact_id": rubric_artifact_id,
                    "rubric_digest": rubric_digest,
                    "scenario_manifest_digest": source[
                        "scenario_manifest_digest"
                    ],
                    "required_artifact_types": [
                        "raw-trial-evidence",
                        "trial-receipt",
                    ],
                },
                "pass_rule": (
                    f"Pass {criterion_id} only if {purpose}; all observable "
                    "assertions pass and no forbidden effect occurs."
                ),
            }
        )
    return cases


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


def canonical_bytes(value: Any, *, digest_field: str | None = None) -> bytes:
    """Return the one canonical JSON byte representation used by every digest."""

    if digest_field is not None:
        if not isinstance(value, dict):
            raise TypeError("digest-field exclusion requires an object")
        value = {key: item for key, item in value.items() if key != digest_field}

    def reject_floats(item: Any) -> None:
        if isinstance(item, float):
            raise TypeError("floating-point values are outside the durable schema")
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise TypeError("durable object keys must be strings")
                reject_floats(nested)
        elif isinstance(item, list):
            for nested in item:
                reject_floats(nested)
        elif not isinstance(item, (str, int, bool, type(None))):
            raise TypeError(f"unsupported durable value: {type(item).__name__}")

    reject_floats(value)
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_digest(value: Any, *, digest_field: str | None = None) -> str:
    return hashlib.sha256(canonical_bytes(value, digest_field=digest_field)).hexdigest()


def digest_value(value: Any) -> str:
    """Turn a fixture label into a real, deterministic digest claim."""

    if isinstance(value, str) and SHA256_RE.fullmatch(value):
        return value
    return canonical_digest({"fixture_value": value})


def digest_is_valid(value: object) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def unique_nonempty_strings(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item) for item in value)
        and len(value) == len(set(value))
    )


def normalized_run_relative_path(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        return None
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        return None
    return value


def trusted_candidate_locator(
    workflow_id: object, candidate_revision: object
) -> str | None:
    if (
        not isinstance(workflow_id, str)
        or WORKFLOW_ID_RE.fullmatch(workflow_id) is None
        or not isinstance(candidate_revision, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", candidate_revision)
        is None
    ):
        return None
    return f"isolated-candidates/{workflow_id}/{candidate_revision}"


def path_is_within_candidate_locator(path: object, locator: object) -> bool:
    normalized_path = normalized_run_relative_path(path)
    normalized_locator = normalized_run_relative_path(locator)
    if normalized_path is None or normalized_locator is None:
        return False
    candidate_path = PurePosixPath(normalized_path)
    candidate_locator = PurePosixPath(normalized_locator)
    return candidate_path != candidate_locator and candidate_locator in candidate_path.parents


def candidate_owned_path(workflow_id: str, candidate_revision: str) -> str:
    locator = trusted_candidate_locator(workflow_id, candidate_revision)
    if locator is None:
        raise ValueError("candidate identity cannot form an isolated locator")
    return f"{locator}/SKILL.md"


def candidate_path_contract_is_valid(record: dict[str, Any]) -> bool:
    revision = record.get("candidate_revision")
    locator = record.get("isolated_locator")
    owned_paths = record.get("owned_paths")
    expected_locator = trusted_candidate_locator(
        record.get("workflow_id"), revision
    )
    return (
        isinstance(revision, str)
        and bool(revision)
        and record.get("write_scope") == "isolated-candidate"
        and locator == expected_locator
        and unique_nonempty_strings(owned_paths)
        and all(
            path_is_within_candidate_locator(path, locator)
            for path in owned_paths
        )
        and record.get("path") in owned_paths
    )


def bind_fixture_candidate_paths(
    events: list[dict[str, Any]], workflow_id: str
) -> None:
    """Populate trusted isolated path contracts in generated fixture events."""

    owned_by_revision: dict[str, str] = {}
    for event in events:
        revision = event.get("candidate_revision")
        if event.get("event") == "candidate_edit" and isinstance(revision, str):
            locator = trusted_candidate_locator(workflow_id, revision)
            if locator is None:
                continue
            owned_path = candidate_owned_path(workflow_id, revision)
            owned_by_revision[revision] = owned_path
            source_event = event.get("fixture_source_event")
            declared_source_fields = (
                set(source_event) if isinstance(source_event, dict) else set()
            )
            expected_fields = {
                "write_scope": "isolated-candidate",
                "isolated_locator": locator,
                "owned_paths": [owned_path],
                "path": owned_path,
            }
            for field, value in expected_fields.items():
                if field not in declared_source_fields:
                    event[field] = value
        elif (
            event.get("artifact_type") == "candidate-manifest"
            and isinstance(revision, str)
            and revision in owned_by_revision
            and isinstance(event.get("files"), list)
            and len(event["files"]) == 1
            and isinstance(event["files"][0], dict)
        ):
            source_event = event.get("fixture_source_event")
            if not isinstance(source_event, dict) or "files" not in source_event:
                event["files"][0]["path"] = owned_by_revision[revision]
        elif event.get("event") == "write" and owned_by_revision:
            source_event = event.get("fixture_source_event")
            if not isinstance(source_event, dict) or "path" not in source_event:
                event["path"] = next(reversed(owned_by_revision.values()))


def media_kind_is_valid(value: object) -> bool:
    return isinstance(value, str) and MEDIA_KIND_RE.fullmatch(value) is not None


def candidate_file_is_valid(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and set(entry) == CANDIDATE_FILE_FIELDS
        and normalized_run_relative_path(entry.get("path")) is not None
        and media_kind_is_valid(entry.get("media_kind"))
        and type(entry.get("byte_count")) is int
        and entry["byte_count"] >= 0
        and digest_is_valid(entry.get("digest"))
    )


def evaluation_case_is_valid(case: object) -> bool:
    if not isinstance(case, dict) or set(case) != EVALUATION_CASE_FIELDS:
        return False
    setup_manifest = case.get("setup_manifest")
    requirements = case.get("evidence_requirements")
    return (
        isinstance(case.get("id"), str)
        and bool(case["id"])
        and case.get("partition")
        in {"visible", "frozen-validation", "hidden-release"}
        and isinstance(case.get("scenario_id"), str)
        and bool(case["scenario_id"])
        and isinstance(case.get("purpose"), str)
        and bool(case["purpose"])
        and digest_is_valid(case.get("raw_request_digest"))
        and unique_nonempty_strings(case.get("allowed_context"))
        and isinstance(setup_manifest, dict)
        and set(setup_manifest) == CASE_SETUP_MANIFEST_FIELDS
        and normalized_run_relative_path(setup_manifest.get("path")) is not None
        and digest_is_valid(setup_manifest.get("digest"))
        and unique_nonempty_strings(case.get("observable_assertions"))
        and bool(case["observable_assertions"])
        and unique_nonempty_strings(case.get("forbidden_effects"))
        and bool(case["forbidden_effects"])
        and isinstance(requirements, dict)
        and set(requirements) == CASE_EVIDENCE_REQUIREMENT_FIELDS
        and isinstance(requirements.get("criterion_id"), str)
        and bool(requirements["criterion_id"])
        and isinstance(requirements.get("frozen_parameter_id"), str)
        and bool(requirements["frozen_parameter_id"])
        and digest_is_valid(requirements.get("contract_digest"))
        and isinstance(requirements.get("rubric_artifact_id"), str)
        and bool(requirements["rubric_artifact_id"])
        and digest_is_valid(requirements.get("rubric_digest"))
        and digest_is_valid(requirements.get("scenario_manifest_digest"))
        and unique_nonempty_strings(
            requirements.get("required_artifact_types")
        )
        and isinstance(case.get("pass_rule"), str)
        and bool(case["pass_rule"])
    )


def evaluation_event_shape_is_valid(event: dict[str, Any]) -> bool:
    partitions = event.get("partitions")
    case_ids = event.get("case_ids")
    cases = event.get("cases")
    parameter_ids = event.get("frozen_parameter_ids")
    return (
        isinstance(event.get("contract_digest"), str)
        and isinstance(event.get("evaluation_digest"), str)
        and digest_is_valid(event.get("target_snapshot"))
        and unique_nonempty_strings(partitions)
        and set(partitions)
        == {"visible", "frozen-validation", "hidden-release"}
        and unique_nonempty_strings(case_ids)
        and isinstance(cases, list)
        and bool(cases)
        and all(evaluation_case_is_valid(case) for case in cases)
        and [case["id"] for case in cases] == case_ids
        and len(case_ids) == len(set(case_ids))
        and {case["partition"] for case in cases}
        == {"visible", "frozen-validation", "hidden-release"}
        and unique_nonempty_strings(parameter_ids)
        and isinstance(event.get("rubric_artifact_id"), str)
        and bool(event["rubric_artifact_id"])
        and digest_is_valid(event.get("rubric_digest"))
    )


def case_evidence_is_valid(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and set(entry) == CASE_EVIDENCE_FIELDS
        and isinstance(entry.get("case_id"), str)
        and bool(entry["case_id"])
        and digest_is_valid(entry.get("raw_request_digest"))
        and digest_is_valid(entry.get("raw_evidence_digest"))
    )


def case_receipt_is_valid(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and set(entry) == CASE_RECEIPT_FIELDS
        and isinstance(entry.get("case_id"), str)
        and bool(entry["case_id"])
        and digest_is_valid(entry.get("raw_evidence_digest"))
        and digest_is_valid(entry.get("receipt_digest"))
        and isinstance(entry.get("fresh_context_id"), str)
        and bool(entry["fresh_context_id"])
    )


def fixture_target_manifest_is_valid(manifest: object) -> bool:
    if not isinstance(manifest, dict):
        return False
    canonical_target = manifest.get("canonical_target")
    identity_ambiguous = manifest.get("identity_ambiguous", False)
    exact_target_path = manifest.get("exact_target_path")
    files = manifest.get("files")
    near_neighbours = manifest.get("near_neighbours")
    return (
        manifest.get("schema_version") == "fixture-target-manifest-v1"
        and manifest.get("disposable_fixture") is True
        and manifest.get("host_kind") in {"git", "non-git"}
        and (
            manifest.get("git_identity") is None
            or isinstance(manifest.get("git_identity"), dict)
        )
        and type(manifest.get("exact_target_exists")) is bool
        and type(identity_ambiguous) is bool
        and (
            isinstance(canonical_target, str)
            and bool(canonical_target)
            or identity_ambiguous is True
            and canonical_target is None
        )
        and (
            exact_target_path is None
            or normalized_run_relative_path(exact_target_path) is not None
        )
        and unique_nonempty_strings(near_neighbours)
        and isinstance(manifest.get("snapshot_digest"), str)
        and bool(manifest["snapshot_digest"])
        and isinstance(files, list)
        and all(
            isinstance(entry, dict)
            and set(entry) == {"path", "kind"}
            and normalized_run_relative_path(entry.get("path")) is not None
            and isinstance(entry.get("kind"), str)
            and bool(entry["kind"])
            for entry in files
        )
    )


def retained_manifest_entry_is_valid(
    entry: object,
    retained_artifacts: dict[str, dict[str, Any]],
) -> bool:
    if not isinstance(entry, dict) or set(entry) != MANIFEST_FILE_FIELDS:
        return False
    artifact_id = entry.get("artifact_id")
    artifact = (
        retained_artifacts.get(artifact_id)
        if isinstance(artifact_id, str)
        else None
    )
    envelope = (
        artifact.get("artifact_envelope")
        if isinstance(artifact, dict)
        else None
    )
    if not isinstance(envelope, dict):
        return False
    try:
        expected_byte_count = len(canonical_bytes(_artifact_payload(artifact)))
    except (TypeError, ValueError):
        return False
    return (
        normalized_run_relative_path(entry.get("path")) is not None
        and entry.get("path") == envelope.get("payload_path")
        and media_kind_is_valid(entry.get("media_kind"))
        and type(entry.get("byte_count")) is int
        and entry["byte_count"] >= 0
        and entry["byte_count"] == expected_byte_count
        and digest_is_valid(entry.get("payload_digest"))
        and entry.get("payload_digest") == envelope.get("payload_digest")
        and digest_is_valid(entry.get("envelope_digest"))
        and entry.get("envelope_digest") == envelope.get("envelope_digest")
        and isinstance(entry.get("source_role"), str)
        and bool(entry["source_role"])
        and entry.get("source_role") == envelope.get("producer")
        and isinstance(entry.get("retention_class"), str)
        and bool(entry["retention_class"])
    )


def manifest_header_is_valid(
    record: dict[str, Any], files: object
) -> bool:
    return (
        MANIFEST_HEADER_FIELDS.issubset(record)
        and isinstance(files, list)
        and all(
            isinstance(entry, dict)
            and type(entry.get("byte_count")) is int
            and entry["byte_count"] >= 0
            for entry in files
        )
        and record.get("schema_version") == "skill-builder-raw-manifest-v1"
        and record.get("collection_type") == record.get("artifact_type")
        and type(record.get("declared_item_count")) is int
        and record.get("declared_item_count") == len(files)
        and type(record.get("observed_item_count")) is int
        and record.get("observed_item_count") == len(files)
        and type(record.get("observed_byte_count")) is int
        and record.get("observed_byte_count")
        == sum(entry["byte_count"] for entry in files)
        and record.get("overflow") is None
    )


def retained_manifest_is_valid(
    record: dict[str, Any],
    expected_ids: set[str],
    retained_artifacts: dict[str, dict[str, Any]],
) -> bool:
    files = record.get("files")
    return (
        manifest_header_is_valid(record, files)
        and all(
            retained_manifest_entry_is_valid(entry, retained_artifacts)
            for entry in files
        )
        and [entry["artifact_id"] for entry in files]
        == list(dict.fromkeys(entry["artifact_id"] for entry in files))
        and len({entry["path"] for entry in files}) == len(files)
        and {entry["artifact_id"] for entry in files} == expected_ids
    )


def _normalize_digest_claims(value: Any, field: str | None = None) -> Any:
    if field == "fixture_source_event":
        return copy.deepcopy(value)
    if field in {"transition_receipt", "artifact_envelope"}:
        return None
    if field in {"target_snapshot", "digest", "artifact_digest"} or (
        isinstance(field, str)
        and (field.endswith("_digest") or field.endswith("_digests"))
    ):
        if isinstance(value, list):
            return [digest_value(item) for item in value]
        if value is None:
            return None
        return digest_value(value)
    if isinstance(value, dict):
        return {
            key: _normalize_digest_claims(item, key)
            for key, item in value.items()
            if key not in {"transition_receipt", "artifact_envelope"}
        }
    if isinstance(value, list):
        return [_normalize_digest_claims(item) for item in value]
    return value


def _timestamp(sequence: int) -> str:
    day, remainder = divmod(sequence, 86_400)
    hour, remainder = divmod(remainder, 3_600)
    minute, second = divmod(remainder, 60)
    return f"2026-01-{day + 1:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"


def _target_from_manifest(locator: object) -> tuple[str, str]:
    if locator == "targets/non-git-create/manifest.json":
        return "fixture-release-notes", "create"
    if locator == "targets/create-collision/manifest.json":
        return "fixture-release-notes", "create"
    return "fixture-terse-summary", "improve"


def _resolution_authority() -> dict[str, Any]:
    """Return the canonical candidate-only authority used by fixture runs."""

    return {
        "allowed_reads": ["target", "host-rules", "evaluation-fixtures"],
        "allowed_writes": ["isolated-candidate"],
        "delegation": {
            "candidate-implementer": ["isolated-candidate-write"],
        },
        "candidate_effects": ["isolated-candidate-write"],
        "delivery_effects": [],
    }


def _event_destination_stage(event_name: str, source_stage: str | None) -> str:
    stages = {
        "resolve": "resolved",
        "baseline_captured": "baseline",
        "research_pack": "research",
        "evidence_sieved": "sieved",
        "designs_challenged": "designed",
        "contract_written": "contracted",
        "contract_changed": "contracted",
        "user_confirmed": "confirmed",
        "evaluation_frozen": "frozen",
        "candidate_edit": "candidate",
        "builder_conformance_recorded": "conformance",
        "review_recorded": "review",
        "category_scored": "scored",
        "target_scorecard_recorded": "scorecard",
        "spec_outcome_recorded": "specification",
        "verification_recorded": "verified",
        "release_evidence_retained": "release",
        "finalized": "finalized",
        "cleanup_attempt": "cleaned",
    }
    return stages.get(event_name, source_stage or "observed")


def _artifact_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in event.items()
        if key not in {"artifact_envelope", "transition_receipt"}
    }


def _manifest_files(
    artifacts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": artifact_id,
            "path": envelope["payload_path"],
            "media_kind": "application/json",
            "byte_count": envelope["payload_byte_count"],
            "payload_digest": envelope["payload_digest"],
            "envelope_digest": envelope["envelope_digest"],
            "source_role": envelope["producer"],
            "retention_class": "terminal-audit",
        }
        for artifact_id, envelope in artifacts.items()
    ]


def seal_trace(
    trace: list[dict[str, Any]],
    *,
    workflow_id: str | None = None,
    target_identity: str | None = None,
    mode: str | None = None,
) -> list[dict[str, Any]]:
    """Build immutable envelopes and a canonical transition chain for a trace."""

    try:
        canonical_bytes(trace)
    except (TypeError, UnicodeError, ValueError):
        return [
            {
                "event": None,
                "construction_error": "invalid-durable-input",
            }
        ]

    events = [
        _normalize_digest_claims(copy.deepcopy(event))
        for event in trace
    ]
    first = next((event for event in events if isinstance(event, dict)), {})
    current_workflow = workflow_id or first.get("workflow_id")
    if not isinstance(current_workflow, str) or not WORKFLOW_ID_RE.fullmatch(
        current_workflow
    ):
        current_workflow = "d" * 32
    inferred_target, inferred_mode = _target_from_manifest(first.get("target_manifest"))
    current_target = target_identity or first.get("target_identity") or inferred_target
    current_mode = mode or first.get("selected_mode") or first.get("mode") or inferred_mode
    if current_mode not in {"create", "improve"}:
        current_mode = "improve"
    current_snapshot = digest_value(
        first.get("target_snapshot", f"{current_target}-snapshot")
    )
    identity_generation = 1
    snapshot_generation = 1
    source_stage: str | None = None
    prior_receipt_digest: str | None = None
    artifact_envelopes: dict[str, dict[str, Any]] = {}
    prior_receipts: list[dict[str, Any]] = []
    authority_envelope: dict[str, Any] | None = None
    candidate_seen = False

    for sequence, event in enumerate(events):
        if not isinstance(event, dict):
            events[sequence] = {"event": None, "malformed_value": repr(event)}
            event = events[sequence]
        event_name = event.get("event")
        if event_name == "resolve":
            resolved_target, resolved_mode = _target_from_manifest(
                event.get("target_manifest")
            )
            next_workflow = workflow_id or event.get(
                "workflow_id", current_workflow
            )
            next_target = target_identity or event.get(
                "target_identity", resolved_target
            )
            if sequence and (
                next_workflow != current_workflow or next_target != current_target
            ):
                identity_generation += 1
                snapshot_generation += 1
            current_workflow = next_workflow
            current_target = next_target
            selected_mode = event.get("selected_mode", resolved_mode)
            if mode in {"create", "improve"}:
                current_mode = mode
            elif selected_mode in {"create", "improve"}:
                current_mode = selected_mode
            current_snapshot = digest_value(
                event.get("target_snapshot", f"{current_target}-snapshot")
            )
        elif event_name == "target_snapshot_changed":
            current_snapshot = digest_value(event.get("target_snapshot"))
            snapshot_generation += 1

        event["workflow_id"] = current_workflow
        event["target_identity"] = current_target
        event["target_snapshot"] = current_snapshot
        event["mode"] = current_mode
        if type(event.get("identity_generation")) is not int:
            event["identity_generation"] = identity_generation
        else:
            identity_generation = event["identity_generation"]
        if type(event.get("snapshot_generation")) is not int:
            event["snapshot_generation"] = snapshot_generation
        else:
            snapshot_generation = event["snapshot_generation"]

        if event_name == "candidate_edit":
            candidate_seen = True
        if candidate_seen and authority_envelope is not None:
            event["authority_binding"] = {
                "artifact_id": authority_envelope["artifact_id"],
                "digest": authority_envelope["envelope_digest"],
            }

        artifact_id = event.get("artifact_id")
        artifact_type = event.get("artifact_type")
        if isinstance(artifact_id, str) and isinstance(artifact_type, str):
            if artifact_type == "transition-ledger":
                event["receipt_chain"] = [
                    {
                        "sequence": receipt["sequence"],
                        "receipt_digest": receipt["receipt_digest"],
                    }
                    for receipt in prior_receipts
                ]
                event["head_receipt_digest"] = (
                    prior_receipts[-1]["receipt_digest"] if prior_receipts else None
                )
            if artifact_type in {"artifact-manifest", "terminal-manifest"}:
                prior_ids = list(artifact_envelopes)
                event["manifested_artifact_ids"] = prior_ids
                event["input_artifact_ids"] = prior_ids
                event["files"] = _manifest_files(artifact_envelopes)
            if artifact_type in MANIFEST_ARTIFACT_TYPES:
                files = event.get("files", [])
                manifest_body = {
                    "schema_version": "skill-builder-raw-manifest-v1",
                    "workflow_id": current_workflow,
                    "target_identity": current_target,
                    "collection_type": artifact_type,
                    "declared_item_count": len(files),
                    "observed_item_count": len(files),
                    "observed_byte_count": sum(
                        item.get("byte_count", 0)
                        for item in files
                        if isinstance(item, dict)
                    ),
                    "files": files,
                    "overflow": None,
                }
                event.update(manifest_body)
                event["manifest_digest"] = canonical_digest(manifest_body)
            if event_name == "review_recorded":
                review_ids = event.get("evidence", [])
                if isinstance(review_ids, list):
                    review_bindings = [
                        {
                            "artifact_id": review_id,
                            "type": artifact_envelopes[review_id]["artifact_type"],
                            "digest": artifact_envelopes[review_id][
                                "envelope_digest"
                            ],
                        }
                        for review_id in review_ids
                        if review_id in artifact_envelopes
                    ]
                    event["review_artifact_bindings"] = review_bindings
                    event["supplied_artifacts"] = [
                        binding["artifact_id"] for binding in review_bindings
                    ]
                    event["accessed_artifacts"] = [
                        binding["artifact_id"] for binding in review_bindings
                    ]
            input_ids = event.setdefault("input_artifact_ids", [])
            if not isinstance(input_ids, list):
                input_ids = []
                event["input_artifact_ids"] = input_ids
            input_bindings = [
                {
                    "artifact_id": "target-snapshot",
                    "artifact_digest": current_snapshot,
                }
            ]
            for input_id in input_ids:
                prior_envelope = artifact_envelopes.get(input_id)
                input_bindings.append(
                    {
                        "artifact_id": input_id,
                        "artifact_digest": (
                            prior_envelope["envelope_digest"]
                            if prior_envelope is not None
                            else digest_value({"missing_artifact": input_id})
                        ),
                    }
                )
            payload = _artifact_payload(event)
            payload_bytes = canonical_bytes(payload)
            envelope = {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "workflow_id": current_workflow,
                "target_identity": current_target,
                "mode": current_mode,
                "created_stage": _event_destination_stage(
                    str(event_name), source_stage
                ),
                "created_sequence": sequence,
                "producer": next(
                    (
                        event[key]
                        for key in (
                            "actor_identity",
                            "reviewer_identity",
                            "scorer_identity",
                            "judge_identity",
                            "verifier_identity",
                        )
                        if isinstance(event.get(key), str) and event[key]
                    ),
                    "main-agent-v1",
                ),
                "created_at": _timestamp(sequence),
                "input_bindings": input_bindings,
                "payload_path": f"artifacts/{sequence:04d}-{artifact_id}.json",
                "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
                "limitations": copy.deepcopy(event.get("limitations", [])),
            }
            if artifact_type in MANIFEST_ARTIFACT_TYPES:
                envelope["manifest_digest"] = event.get(
                    "manifest_digest", canonical_digest({"files": []})
                )
            envelope["envelope_digest"] = canonical_digest(
                envelope, digest_field="envelope_digest"
            )
            event["artifact_envelope"] = envelope
            artifact_envelopes[artifact_id] = {
                **envelope,
                "payload_byte_count": len(payload_bytes),
            }

        relevant = []
        envelope = event.get("artifact_envelope")
        if isinstance(envelope, dict):
            relevant.extend(
                {
                    "artifact_id": binding["artifact_id"],
                    "digest": binding["artifact_digest"],
                    "action": "consumed",
                }
                for binding in envelope["input_bindings"]
                if binding["artifact_id"] != "target-snapshot"
            )
            relevant.append(
                {
                    "artifact_id": envelope["artifact_id"],
                    "digest": envelope["envelope_digest"],
                    "action": "accepted",
                }
            )
        destination_stage = _event_destination_stage(str(event_name), source_stage)
        receipt = {
            "schema_version": "skill-builder-transition-receipt-v1",
            "workflow_id": current_workflow,
            "target_identity": current_target,
            "sequence": sequence,
            "prior_receipt_digest": prior_receipt_digest,
            "event": event_name,
            "source_stage": source_stage,
            "destination_stage": destination_stage,
            "relevant_artifact_digests": sorted(
                relevant,
                key=lambda item: (item["artifact_id"], item["action"]),
            ),
            "target_snapshot_digest": current_snapshot,
            "authority_event_digest": (
                authority_envelope["envelope_digest"]
                if authority_envelope is not None and candidate_seen
                else None
            ),
            "created_at": _timestamp(sequence),
        }
        receipt["receipt_digest"] = canonical_digest(
            receipt, digest_field="receipt_digest"
        )
        event["transition_receipt"] = receipt
        prior_receipts.append(receipt)
        prior_receipt_digest = receipt["receipt_digest"]
        source_stage = destination_stage
        if artifact_type == "authority-record" and isinstance(envelope, dict):
            authority_envelope = envelope

    return events


def reseal_declared_trace(
    trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reseal an existing trace without regenerating declared manifest entries."""

    try:
        canonical_bytes(trace)
    except (TypeError, UnicodeError, ValueError):
        return [
            {
                "event": None,
                "construction_error": "invalid-durable-input",
            }
        ]

    events = copy.deepcopy(trace)
    prior_receipt_digest: str | None = None
    source_stage: str | None = None
    authority_envelope: dict[str, Any] | None = None
    candidate_seen = False
    artifact_envelopes: dict[str, dict[str, Any]] = {}
    for sequence, event in enumerate(events):
        event_name = event.get("event")
        if event_name == "candidate_edit":
            candidate_seen = True
        if candidate_seen and authority_envelope is not None:
            event["authority_binding"] = {
                "artifact_id": authority_envelope["artifact_id"],
                "digest": authority_envelope["envelope_digest"],
            }
        artifact_id = event.get("artifact_id")
        artifact_type = event.get("artifact_type")
        envelope: dict[str, Any] | None = None
        if isinstance(artifact_id, str) and isinstance(artifact_type, str):
            if artifact_type in MANIFEST_ARTIFACT_TYPES:
                manifest_body = {
                    key: event.get(key)
                    for key in (
                        "schema_version",
                        "workflow_id",
                        "target_identity",
                        "collection_type",
                        "declared_item_count",
                        "observed_item_count",
                        "observed_byte_count",
                        "files",
                        "overflow",
                    )
                }
                event["manifest_digest"] = canonical_digest(manifest_body)
            old_envelope = event.get("artifact_envelope", {})
            input_bindings = [
                {
                    "artifact_id": "target-snapshot",
                    "artifact_digest": event.get("target_snapshot"),
                }
            ]
            for input_id in event.get("input_artifact_ids", []):
                prior = artifact_envelopes.get(input_id)
                input_bindings.append(
                    {
                        "artifact_id": input_id,
                        "artifact_digest": (
                            prior["envelope_digest"]
                            if prior is not None
                            else digest_value({"missing_artifact": input_id})
                        ),
                    }
                )
            envelope = {
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "workflow_id": event.get("workflow_id"),
                "target_identity": event.get("target_identity"),
                "mode": event.get("mode"),
                "created_stage": _event_destination_stage(
                    str(event_name), source_stage
                ),
                "created_sequence": sequence,
                "producer": old_envelope.get("producer", "main-agent-v1"),
                "created_at": _timestamp(sequence),
                "input_bindings": input_bindings,
                "payload_path": old_envelope.get(
                    "payload_path", f"artifacts/{sequence:04d}-{artifact_id}.json"
                ),
                "payload_digest": hashlib.sha256(
                    canonical_bytes(_artifact_payload(event))
                ).hexdigest(),
                "limitations": copy.deepcopy(event.get("limitations", [])),
            }
            if artifact_type in MANIFEST_ARTIFACT_TYPES:
                envelope["manifest_digest"] = event["manifest_digest"]
            envelope["envelope_digest"] = canonical_digest(
                envelope, digest_field="envelope_digest"
            )
            event["artifact_envelope"] = envelope
            artifact_envelopes[artifact_id] = envelope
        relevant: list[dict[str, str]] = []
        if envelope is not None:
            relevant.extend(
                {
                    "artifact_id": binding["artifact_id"],
                    "digest": binding["artifact_digest"],
                    "action": "consumed",
                }
                for binding in envelope["input_bindings"]
                if binding["artifact_id"] != "target-snapshot"
            )
            relevant.append(
                {
                    "artifact_id": artifact_id,
                    "digest": envelope["envelope_digest"],
                    "action": "accepted",
                }
            )
        destination_stage = _event_destination_stage(str(event_name), source_stage)
        receipt = {
            "schema_version": "skill-builder-transition-receipt-v1",
            "workflow_id": event.get("workflow_id"),
            "target_identity": event.get("target_identity"),
            "sequence": sequence,
            "prior_receipt_digest": prior_receipt_digest,
            "event": event_name,
            "source_stage": source_stage,
            "destination_stage": destination_stage,
            "relevant_artifact_digests": sorted(
                relevant,
                key=lambda item: (item["artifact_id"], item["action"]),
            ),
            "target_snapshot_digest": event.get("target_snapshot"),
            "authority_event_digest": (
                authority_envelope["envelope_digest"]
                if authority_envelope is not None and candidate_seen
                else None
            ),
            "created_at": _timestamp(sequence),
        }
        receipt["receipt_digest"] = canonical_digest(
            receipt, digest_field="receipt_digest"
        )
        event["transition_receipt"] = receipt
        prior_receipt_digest = receipt["receipt_digest"]
        source_stage = destination_stage
        if artifact_type == "authority-record" and envelope is not None:
            authority_envelope = envelope
    return events


def merge_accepted_fixture_events(
    canonical_events: list[dict[str, Any]],
    raw_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich legacy accepted shorthand without dropping its declared events."""

    merged_events = copy.deepcopy(canonical_events)
    cursor = 0
    generated_fields = {
        "artifact_envelope",
        "artifact_id",
        "artifact_type",
        "authority_binding",
        "identity_generation",
        "input_artifact_ids",
        "mode",
        "snapshot_generation",
        "target_identity",
        "transition_receipt",
        "workflow_id",
    }

    def matches(canonical: dict[str, Any], raw: dict[str, Any]) -> bool:
        if canonical.get("event") != raw.get("event"):
            return False
        if (
            isinstance(raw.get("artifact_type"), str)
            and canonical.get("artifact_type") != raw.get("artifact_type")
        ):
            return False
        if raw.get("event") == "candidate_edit" and isinstance(
            raw.get("candidate_revision"), str
        ):
            return canonical.get("candidate_revision") == raw.get(
                "candidate_revision"
            )
        if raw.get("event") == "category_scored" and isinstance(
            raw.get("category"), str
        ):
            return canonical.get("category") == raw.get("category")
        return True

    def enriched_findings(
        canonical: dict[str, Any], raw: dict[str, Any]
    ) -> object:
        raw_findings = raw.get("findings")
        if not isinstance(raw_findings, list):
            return raw_findings
        canonical_findings = canonical.get("findings")
        if not raw_findings or not isinstance(canonical_findings, list):
            return copy.deepcopy(raw_findings)
        by_id = {
            finding.get("id"): finding
            for finding in canonical_findings
            if isinstance(finding, dict) and isinstance(finding.get("id"), str)
        }
        enriched: list[object] = []
        for position, raw_finding in enumerate(raw_findings):
            if not isinstance(raw_finding, dict):
                enriched.append(copy.deepcopy(raw_finding))
                continue
            basis = by_id.get(raw_finding.get("id"))
            if basis is None and position < len(canonical_findings):
                candidate = canonical_findings[position]
                basis = candidate if isinstance(candidate, dict) else None
            finding = copy.deepcopy(basis) if isinstance(basis, dict) else {}
            finding.update(copy.deepcopy(raw_finding))
            if (
                "affected_criteria" not in raw_finding
                and isinstance(finding.get("affected_categories"), list)
            ):
                category = next(
                    (
                        item
                        for item in finding["affected_categories"]
                        if item in CATEGORY_CRITERION_IDS
                    ),
                    None,
                )
                if category is not None:
                    finding["affected_criteria"] = [
                        sorted(CATEGORY_CRITERION_IDS[category])[0]
                    ]
            enriched.append(finding)
        return enriched

    def enriched_criteria(
        canonical: dict[str, Any], raw: dict[str, Any]
    ) -> object:
        raw_criteria = raw.get("criteria")
        if not isinstance(raw_criteria, list):
            return raw_criteria
        canonical_criteria = canonical.get("criteria")
        if not isinstance(canonical_criteria, list):
            return copy.deepcopy(raw_criteria)
        by_id = {
            criterion.get("id"): criterion
            for criterion in canonical_criteria
            if isinstance(criterion, dict)
            and isinstance(criterion.get("id"), str)
        }
        enriched: list[object] = []
        for raw_criterion in raw_criteria:
            if not isinstance(raw_criterion, dict):
                enriched.append(copy.deepcopy(raw_criterion))
                continue
            basis = by_id.get(raw_criterion.get("id"))
            criterion = copy.deepcopy(basis) if isinstance(basis, dict) else {}
            criterion.update(copy.deepcopy(raw_criterion))
            enriched.append(criterion)
        return enriched

    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            merged_events.insert(cursor, copy.deepcopy(raw_event))
            cursor += 1
            continue
        match_index = next(
            (
                index
                for index in range(cursor, len(merged_events))
                if matches(merged_events[index], raw_event)
            ),
            None,
        )
        if match_index is None:
            injected = copy.deepcopy(raw_event)
            injected["fixture_source_event"] = copy.deepcopy(raw_event)
            merged_events.insert(cursor, injected)
            cursor += 1
            continue
        canonical = merged_events[match_index]
        canonical["fixture_source_event"] = copy.deepcopy(raw_event)
        for key, value in raw_event.items():
            if key not in generated_fields and key not in {
                "event",
                "findings",
                "criteria",
                "lanes",
            }:
                canonical[key] = copy.deepcopy(value)
        if isinstance(raw_event.get("lanes"), list):
            canonical_lanes = canonical.get("lanes")
            canonical_by_role = {
                lane.get("role"): lane
                for lane in canonical_lanes
                if isinstance(lane, dict) and isinstance(lane.get("role"), str)
            } if isinstance(canonical_lanes, list) else {}
            canonical["lanes"] = [
                {
                    **copy.deepcopy(
                        canonical_by_role.get(lane.get("role"), {})
                        if isinstance(lane, dict)
                        else {}
                    ),
                    **copy.deepcopy(lane),
                }
                if isinstance(lane, dict)
                else copy.deepcopy(lane)
                for lane in raw_event["lanes"]
            ]
        if "findings" in raw_event:
            canonical["findings"] = enriched_findings(canonical, raw_event)
        if "criteria" in raw_event:
            canonical["criteria"] = enriched_criteria(canonical, raw_event)
        cursor = match_index + 1
        if raw_event.get("event") == "candidate_edit" and isinstance(
            raw_event.get("write_scope"), str
        ):
            destination_scope = raw_event["write_scope"]
            merged_events.insert(
                cursor,
                {
                    "event": "write",
                    "destination_scope": destination_scope,
                    "effect": (
                        "isolated-candidate-write"
                        if destination_scope == "isolated-candidate"
                        else "production-target-write"
                    ),
                    "actor": canonical.get(
                        "actor_identity", "candidate-implementer-v1"
                    ),
                    "path": canonical.get("path"),
                    "fixture_source_event": copy.deepcopy(raw_event),
                },
            )
            cursor += 1
    research_card_count = next(
        (
            sum(
                lane.get("evidence_cards", 0)
                for lane in event.get("lanes", [])
                if isinstance(lane, dict)
                and type(lane.get("evidence_cards")) is int
            )
            for event in merged_events
            if event.get("event") == "research_pack"
            and isinstance(event.get("lanes"), list)
        ),
        None,
    )
    if isinstance(research_card_count, int):
        for event in merged_events:
            if event.get("event") != "evidence_sieved":
                continue
            source_event = event.get("fixture_source_event")
            if not isinstance(source_event, dict) or "card_count" not in source_event:
                event["card_count"] = research_card_count
            if not isinstance(source_event, dict) or "decisions" not in source_event:
                card_count = event.get("card_count")
                if type(card_count) is int and card_count >= 0:
                    event["decisions"] = ["adopt"] * card_count
    return merged_events


def build_fixture_trace(
    partition: str,
    trace_id: str,
    raw_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Migrate every fixture through the same strict trace construction path."""

    invalid_fixture = [
        {
            "event": None,
            "construction_error": "invalid-fixture-shape",
        }
    ]
    if (
        not isinstance(partition, str)
        or not isinstance(trace_id, str)
        or not isinstance(raw_events, list)
        or any(not isinstance(event, dict) for event in raw_events)
    ):
        return invalid_fixture
    try:
        canonical_bytes(raw_events)
    except (TypeError, UnicodeError, ValueError):
        return invalid_fixture
    for event in raw_events:
        event_name = event.get("event")
        artifact_type = event.get("artifact_type")
        artifact_id = event.get("artifact_id")
        input_artifact_ids = event.get("input_artifact_ids")
        files = event.get("files")
        if (
            not isinstance(event_name, str)
            or artifact_type is not None
            and not isinstance(artifact_type, str)
            or artifact_id is not None
            and not isinstance(artifact_id, str)
            or input_artifact_ids is not None
            and (
                not isinstance(input_artifact_ids, list)
                or any(
                    not isinstance(item, str) for item in input_artifact_ids
                )
            )
            or "files" in event
            and (
                not isinstance(files, list)
                or any(
                    not isinstance(item, dict)
                    or "byte_count" in item
                    and (
                        type(item["byte_count"]) is not int
                        or item["byte_count"] < 0
                    )
                    for item in files
                )
            )
            or "mode" in event
            and not isinstance(event["mode"], str)
        ):
            return invalid_fixture
        if event_name == "resolve":
            selected_mode = event.get("selected_mode")
            if not isinstance(selected_mode, str):
                return invalid_fixture
        if event_name == "research_pack":
            lanes = event.get("lanes", [])
            if not isinstance(lanes, list) or any(
                not isinstance(lane, dict)
                or not isinstance(lane.get("role"), str)
                or not lane["role"]
                for lane in lanes
            ):
                return invalid_fixture
        if event_name == "evidence_sieved":
            card_count = event.get("card_count", 0)
            if type(card_count) is not int or card_count < 0:
                return invalid_fixture
        if event_name == "candidate_edit":
            revision = event.get("candidate_revision")
            if trusted_candidate_locator("d" * 32, revision) is None:
                return invalid_fixture
        if event_name == "review_recorded":
            findings = event.get("findings")
            if not isinstance(findings, list) or any(
                not isinstance(finding, dict)
                or not isinstance(finding.get("id"), str)
                or "affected_categories" in finding
                and (
                    not isinstance(finding["affected_categories"], list)
                    or any(
                        not isinstance(category, str)
                        for category in finding["affected_categories"]
                    )
                )
                or "affected_criteria" in finding
                and (
                    not isinstance(finding["affected_criteria"], list)
                    or any(
                        not isinstance(criterion, str)
                        for criterion in finding["affected_criteria"]
                    )
                )
                for finding in findings
            ):
                return invalid_fixture
        if event_name == "category_scored":
            criteria = event.get("criteria")
            if (
                not isinstance(event.get("category"), str)
                or not isinstance(criteria, list)
                or any(
                    not isinstance(criterion, dict)
                    or not isinstance(criterion.get("id"), str)
                    or "evidence" in criterion
                    and (
                        not isinstance(criterion["evidence"], list)
                        or any(
                            not isinstance(evidence_id, str)
                            for evidence_id in criterion["evidence"]
                        )
                    )
                    for criterion in criteria
                )
            ):
                return invalid_fixture

    fixture_workflow = hashlib.sha256(
        f"{partition}:{trace_id}".encode("utf-8")
    ).hexdigest()[:32]
    accepted_candidate_events = [
        event
        for event in raw_events
        if isinstance(event, dict) and event.get("event") == "candidate_edit"
    ]
    if trace_id.startswith("accepted_") and accepted_candidate_events:
        revisions = [
            event.get("candidate_revision")
            for event in accepted_candidate_events
            if isinstance(event.get("candidate_revision"), str)
        ]
        if len(revisions) != len(accepted_candidate_events):
            return seal_trace(raw_events, workflow_id=fixture_workflow)
        raw_resolutions = [
            event for event in raw_events if event.get("event") == "resolve"
        ]
        if len(raw_resolutions) != 1:
            return invalid_fixture
        if any(
            isinstance(event, dict) and event.get("event") == "repair_completed"
            for event in raw_events
        ):
            complete = accepted_repair_trace(
                fixture_workflow,
                prior_revision=revisions[0],
                repaired_revision=revisions[-1],
            )
        else:
            complete = accepted_finalization_trace(revisions[-1])
        last_index = next(
            index
            for index, event in enumerate(complete)
            if event.get("artifact_type") == "candidate-manifest"
            and (
                len(revisions) == 1
                or event.get("candidate_revision") == revisions[-1]
            )
        )
        candidate_path = (
            copy.deepcopy(complete)
            if len(revisions) > 1
            else copy.deepcopy(complete[: last_index + 1])
        )
        raw_resolution = raw_resolutions[0]
        fixture_manifest = raw_resolution.get("target_manifest")
        fixture_target, inferred_mode = _target_from_manifest(fixture_manifest)
        fixture_mode = raw_resolution.get("selected_mode", inferred_mode)
        fixture_snapshot = digest_value(raw_resolution.get("target_snapshot"))
        raw_contract = next(
            (
                event.get("contract_digest")
                for event in reversed(raw_events)
                if isinstance(event, dict)
                and isinstance(event.get("contract_digest"), str)
            ),
            "contract-v1",
        )
        raw_evaluation = next(
            (
                event.get("evaluation_digest")
                for event in reversed(raw_events)
                if isinstance(event, dict)
                and isinstance(event.get("evaluation_digest"), str)
            ),
            "evaluation-v1",
        )
        candidate_path = merge_accepted_fixture_events(
            candidate_path,
            raw_events,
        )
        for event in candidate_path:
            event["workflow_id"] = fixture_workflow
            event["target_identity"] = fixture_target
            event["target_snapshot"] = fixture_snapshot
            event["mode"] = fixture_mode
            if "contract_digest" in event:
                event["contract_digest"] = raw_contract
            if "evaluation_digest" in event:
                event["evaluation_digest"] = raw_evaluation
        rubric = next(
            event
            for event in candidate_path
            if event.get("artifact_type") == "rubric"
        )
        evaluation = next(
            event
            for event in candidate_path
            if event.get("event") == "evaluation_frozen"
        )
        evaluation_source = evaluation.get("fixture_source_event")
        if (
            not isinstance(evaluation_source, dict)
            or "cases" not in evaluation_source
        ):
            evaluation["cases"] = expected_evaluation_cases(
                BUILDER_FIXTURES,
                sorted(CRITERION_CATEGORY),
                digest_value(raw_contract),
                rubric["artifact_id"],
                digest_value(rubric["rubric_digest"]),
            )
        candidate_path[0]["target_manifest"] = fixture_manifest
        candidate_path[0]["selected_mode"] = fixture_mode
        if "requested_mode" in raw_resolution:
            candidate_path[0]["requested_mode"] = raw_resolution["requested_mode"]
        bind_fixture_candidate_paths(candidate_path, fixture_workflow)
        return seal_trace(
            candidate_path,
            workflow_id=fixture_workflow,
            target_identity=fixture_target,
            mode=fixture_mode,
        )

    events = copy.deepcopy(raw_events)
    type_by_event = {
        "resolve": "resolution-record",
        "baseline_captured": "baseline-report",
        "research_pack": "research-pack",
        "evidence_sieved": "evidence-sieve",
        "designs_challenged": "design-record",
        "contract_written": "skill-contract",
        "contract_changed": "skill-contract",
        "user_confirmed": "user-confirmation-record",
        "evaluation_frozen": "evaluation-pack",
        "candidate_edit": "candidate-record",
        "review_recorded": "review-record",
        "verification_recorded": "verification-record",
        "release_evidence_retained": "release-record",
    }
    latest: dict[str, str] = {}
    current_owned_path: str | None = None

    def latest_ids(*types: str) -> list[str]:
        return [latest[item] for item in types if item in latest]

    for index, event in enumerate(events):
        event_name = event.get("event")
        if event_name == "write":
            event.setdefault(
                "effect",
                (
                    "isolated-candidate-write"
                    if event.get("destination_scope") == "isolated-candidate"
                    else "production-target-write"
                ),
            )
            if current_owned_path is not None:
                event.setdefault("path", current_owned_path)
        artifact_type = event.get("artifact_type") or type_by_event.get(event_name)
        if artifact_type is not None:
            event["artifact_type"] = artifact_type
            event.setdefault(
                "artifact_id", f"{trace_id}-{index:02d}-{artifact_type}"
            )
            if event_name == "resolve":
                event.setdefault("actor_identity", "main-agent-v1")
                event.setdefault("actor_role", "main-agent")
                event.setdefault("authority", _resolution_authority())
                event.setdefault("input_artifact_ids", [])
            elif event_name == "baseline_captured":
                event.setdefault(
                    "input_artifact_ids", latest_ids("resolution-record")
                )
            elif event_name == "research_pack":
                event.setdefault(
                    "input_artifact_ids", latest_ids("baseline-report")
                )
                for lane_index, lane in enumerate(event.get("lanes", []), start=1):
                    lane.setdefault(
                        "actor_identity", f"{trace_id}-research-{lane_index}"
                    )
            elif event_name == "evidence_sieved":
                event.setdefault(
                    "input_artifact_ids", latest_ids("research-pack")
                )
                card_count = event.get("card_count", 0)
                event.setdefault("decisions", ["adopt"] * card_count)
            elif event_name == "designs_challenged":
                event.setdefault("actor_identity", "design-owner-v1")
                event.setdefault("actor_role", "skill-designer")
                event.setdefault(
                    "input_artifact_ids", latest_ids("evidence-sieve")
                )
            elif event_name in {"contract_written", "contract_changed"}:
                event.setdefault(
                    "input_artifact_ids",
                    latest_ids("baseline-report", "evidence-sieve", "design-record"),
                )
            elif event_name == "user_confirmed":
                event.setdefault("accepted", True)
                event.setdefault(
                    "input_artifact_ids", latest_ids("skill-contract")
                )
            elif event_name == "evaluation_frozen":
                event.setdefault(
                    "case_ids",
                    [
                        f"case-{criterion_id.lower()}"
                        for criterion_id in sorted(CRITERION_CATEGORY)
                    ],
                )
                event.setdefault(
                    "frozen_parameter_ids",
                    [
                        f"parameter-{criterion_id}"
                        for criterion_id in sorted(CRITERION_CATEGORY)
                    ],
                )
                event.setdefault(
                    "input_artifact_ids",
                    latest_ids("skill-contract", "user-confirmation-record"),
                )
            elif event_name == "candidate_edit":
                candidate_revision = event.get("candidate_revision")
                event.setdefault("revision", candidate_revision)
                event.setdefault("actor_identity", "candidate-implementer-v1")
                event.setdefault("actor_role", "candidate-implementer")
                if isinstance(candidate_revision, str):
                    locator = trusted_candidate_locator(
                        fixture_workflow, candidate_revision
                    )
                    if locator is not None:
                        current_owned_path = candidate_owned_path(
                            fixture_workflow, candidate_revision
                        )
                        event.setdefault("write_scope", "isolated-candidate")
                        event.setdefault("isolated_locator", locator)
                        event.setdefault("owned_paths", [current_owned_path])
                        event.setdefault("path", current_owned_path)
                event.setdefault(
                    "input_artifact_ids",
                    latest_ids(
                        "authority-record",
                        "skill-contract",
                        "user-confirmation-record",
                        "evaluation-pack",
                    ),
                )
            else:
                event.setdefault("input_artifact_ids", [])
            event.setdefault("valid", True)
            latest[artifact_type] = event["artifact_id"]
    return seal_trace(events, workflow_id=fixture_workflow)


def validate_schema_boundary(
    trace: list[dict[str, Any]],
) -> tuple[list[OracleFailure], set[int]]:
    """Validate durable event, artifact, and receipt schemas before dispatch."""

    failures: list[OracleFailure] = []
    dispatchable: set[int] = set()
    prior_envelopes: dict[str, dict[str, Any]] = {}
    prior_payload_paths: dict[str, str] = {}
    prior_receipt_digest: str | None = None
    source_stage: str | None = None
    authority_digest: str | None = None
    candidate_seen = False
    receipt_chain_valid = True

    def digest_claims_are_valid(value: Any, field: str | None = None) -> bool:
        if isinstance(value, dict):
            return all(
                key == "fixture_source_event"
                or digest_claims_are_valid(item, key)
                for key, item in value.items()
            )
        if isinstance(value, list):
            if (
                isinstance(field, str)
                and field.endswith("_digests")
                and all(not isinstance(item, dict) for item in value)
            ):
                return all(
                    isinstance(item, str) and SHA256_RE.fullmatch(item)
                    for item in value
                )
            return all(digest_claims_are_valid(item) for item in value)
        if field == "target_snapshot" or field in {
            "digest",
            "artifact_digest",
            "payload_digest",
            "envelope_digest",
            "receipt_digest",
        } or (isinstance(field, str) and field.endswith("_digest")):
            return value is None or (
                isinstance(value, str) and SHA256_RE.fullmatch(value) is not None
            )
        return not isinstance(value, float)

    for index, event in enumerate(trace):
        if not isinstance(event, dict):
            failures.append(
                OracleFailure(
                    "INVALID_EVENT_SCHEMA",
                    index,
                    "an event was not a JSON object",
                )
            )
            failures.append(
                OracleFailure(
                    "INVALID_TRANSITION_RECEIPT",
                    index,
                    "a malformed event cannot carry a valid transition receipt",
                )
            )
            continue
        event_name = event.get("event")
        if not isinstance(event_name, str) or not event_name:
            failures.append(
                OracleFailure(
                    "INVALID_EVENT_SCHEMA",
                    index,
                    "an event lacked a nonempty dispatch name",
                )
            )
        elif event_name not in KNOWN_EVENTS:
            failures.append(
                OracleFailure(
                    "UNKNOWN_EVENT",
                    index,
                    f"unsupported transition event {event_name!r}",
                )
            )
        required = EVENT_REQUIRED_FIELDS.get(str(event_name), frozenset())
        required_present = required.issubset(event)
        common_valid = (
            isinstance(event.get("workflow_id"), str)
            and WORKFLOW_ID_RE.fullmatch(event["workflow_id"]) is not None
            and isinstance(event.get("target_identity"), str)
            and bool(event["target_identity"])
            and isinstance(event.get("target_snapshot"), str)
            and SHA256_RE.fullmatch(event["target_snapshot"]) is not None
            and event.get("mode") in {"create", "improve"}
            and type(event.get("identity_generation")) is int
            and event["identity_generation"] >= 1
            and type(event.get("snapshot_generation")) is int
            and event["snapshot_generation"] >= 1
            and digest_claims_are_valid(event)
        )
        shape_valid = True
        if event_name == "resolve":
            authority = event.get("authority")
            shape_valid = (
                all(
                    isinstance(event.get(field), str)
                    for field in (
                        "target_manifest",
                        "selected_mode",
                        "target_snapshot",
                    )
                )
                and isinstance(authority, dict)
                and set(authority) == set(_resolution_authority())
                and all(
                    isinstance(authority.get(field), list)
                    and all(
                        isinstance(item, str) and bool(item)
                        for item in authority.get(field, [])
                    )
                    for field in (
                        "allowed_reads",
                        "allowed_writes",
                        "candidate_effects",
                        "delivery_effects",
                    )
                )
                and isinstance(authority.get("delegation"), dict)
                and all(
                    isinstance(role, str)
                    and bool(role)
                    and isinstance(effects, list)
                    and all(
                        isinstance(effect, str) and bool(effect)
                        for effect in effects
                    )
                    for role, effects in authority.get("delegation", {}).items()
                )
            )
        elif event_name in {
            "target_activated",
            "target_finalized",
            "target_abandoned",
        }:
            shape_valid = isinstance(event.get("target_identity"), str)
        elif event_name in {"contract_written", "contract_changed"}:
            shape_valid = isinstance(event.get("contract_digest"), str)
        elif event_name == "target_snapshot_changed":
            shape_valid = isinstance(event.get("target_snapshot"), str)
        elif event_name == "paused":
            shape_valid = (
                type(event.get("state_validated")) is bool
                and isinstance(event.get("target_identity"), str)
            )
        elif event_name == "resumed":
            shape_valid = isinstance(event.get("target_identity"), str) and all(
                type(event.get(field)) is bool
                for field in (
                    "chain_revalidated",
                    "identity_revalidated",
                    "snapshot_revalidated",
                    "evidence_revalidated",
                )
            )
        elif event_name == "goal_changed":
            shape_valid = isinstance(event.get("invalidated"), list) and all(
                isinstance(item, str) for item in event.get("invalidated", [])
            )
        elif event_name == "write":
            shape_valid = (
                isinstance(event.get("destination_scope"), str)
                and bool(event["destination_scope"])
                and isinstance(event.get("effect"), str)
                and bool(event["effect"])
                and isinstance(event.get("actor"), str)
                and bool(event["actor"])
                and normalized_run_relative_path(event.get("path")) is not None
            )
        elif event_name == "delivery_attempt":
            shape_valid = (
                isinstance(event.get("destination_scope"), str)
                and bool(event["destination_scope"])
                and isinstance(event.get("effect"), str)
                and bool(event["effect"])
                and isinstance(event.get("actor"), str)
                and bool(event["actor"])
            )
        elif event_name == "context_read":
            shape_valid = all(
                isinstance(event.get(field), str)
                for field in ("path", "actor_role", "source_role", "actor")
            )
        elif event_name == "research_pack":
            shape_valid = isinstance(event.get("lanes"), list) and all(
                isinstance(lane, dict)
                and isinstance(lane.get("role"), str)
                and isinstance(lane.get("context_id"), str)
                and type(lane.get("blind")) is bool
                and isinstance(lane.get("contaminated_by"), list)
                and type(lane.get("evidence_cards")) is int
                for lane in event.get("lanes", [])
            )
        elif event_name == "evidence_sieved":
            shape_valid = (
                type(event.get("card_count")) is int
                and isinstance(event.get("decisions"), list)
                and all(
                    isinstance(decision, str)
                    for decision in event.get("decisions", [])
                )
            )
        elif event_name == "designs_challenged":
            shape_valid = type(event.get("alternative_count")) is int
        elif event_name == "evaluation_frozen":
            shape_valid = evaluation_event_shape_is_valid(event)
        elif event_name == "repair_completed":
            shape_valid = (
                isinstance(event.get("finding_ids"), list)
                and all(
                    isinstance(finding_id, str)
                    for finding_id in event.get("finding_ids", [])
                )
                and isinstance(event.get("prior_revision"), str)
                and isinstance(event.get("candidate_revision"), str)
            )
        elif event_name == "category_scored":
            category = event.get("category")
            shape_valid = (
                isinstance(category, str)
                and category in CATEGORY_CRITERION_IDS
                and type(event.get("score")) is int
                and isinstance(event.get("criteria"), list)
                and all(
                    isinstance(criterion, dict)
                    and isinstance(criterion.get("id"), str)
                    and type(criterion.get("passed")) is bool
                    and isinstance(criterion.get("evidence"), list)
                    for criterion in event.get("criteria", [])
                )
            )
        elif event_name == "candidate_edit":
            shape_valid = candidate_path_contract_is_valid(event)
        elif event_name == "user_confirmed":
            shape_valid = isinstance(event.get("contract_digest"), str)
        elif event_name == "review_recorded":
            shape_valid = isinstance(event.get("findings"), list) and all(
                isinstance(finding, dict) for finding in event.get("findings", [])
            )
        elif event_name == "target_scorecard_recorded":
            shape_valid = (
                isinstance(event.get("category_results"), list)
                and all(
                    isinstance(result, dict)
                    and isinstance(result.get("category"), str)
                    and result["category"] in CATEGORY_CRITERION_IDS
                    and unique_nonempty_strings(result.get("criterion_ids"))
                    and unique_nonempty_strings(
                        result.get("evidence_artifact_ids")
                    )
                    for result in event.get("category_results", [])
                )
                and isinstance(event.get("criterion_results"), list)
                and all(
                    isinstance(result, dict)
                    and isinstance(result.get("id"), str)
                    and result.get("id") in CRITERION_CATEGORY
                    and isinstance(result.get("category"), str)
                    and type(result.get("passed")) is bool
                    and type(result.get("score")) is int
                    and isinstance(result.get("frozen_parameter_id"), str)
                    and isinstance(result.get("case_id"), str)
                    and isinstance(result.get("evidence_artifact_id"), str)
                    and unique_nonempty_strings(result.get("raw_artifact_ids"))
                    and unique_nonempty_strings(result.get("trial_receipt_ids"))
                    and digest_is_valid(result.get("raw_evidence_digest"))
                    and digest_is_valid(result.get("trial_receipt_digest"))
                    and isinstance(result.get("finding_ids"), list)
                    and all(
                        isinstance(finding_id, str)
                        for finding_id in result.get("finding_ids", [])
                    )
                    for result in event.get("criterion_results", [])
                )
            )
        elif event_name == "verification_recorded":
            shape_valid = (
                type(event.get("behavioral_trials")) is int
                and type(event.get("exit_status")) is int
            )
        if not required_present or not common_valid or not shape_valid:
            failures.append(
                OracleFailure(
                    "INVALID_EVENT_SCHEMA",
                    index,
                    "event fields, binding digests, or integer generations were malformed",
                )
            )
        if (
            isinstance(event_name, str)
            and event_name in KNOWN_EVENTS
            and required_present
            and (shape_valid or event_name == "review_recorded")
        ):
            dispatchable.add(index)

        if event_name == "candidate_edit":
            candidate_seen = True
        artifact_id = event.get("artifact_id")
        artifact_type = event.get("artifact_type")
        envelope = event.get("artifact_envelope")
        envelope_valid = True
        if artifact_id is not None or artifact_type is not None or envelope is not None:
            is_manifest = (
                isinstance(artifact_type, str)
                and artifact_type in MANIFEST_ARTIFACT_TYPES
            )
            expected_fields = ARTIFACT_ENVELOPE_FIELDS | (
                {"manifest_digest"} if is_manifest else set()
            )
            input_ids = event.get("input_artifact_ids")
            expected_bindings: list[dict[str, str]] | None = None
            if isinstance(input_ids, list) and all(
                isinstance(input_id, str) for input_id in input_ids
            ):
                expected_bindings = [
                    {
                        "artifact_id": "target-snapshot",
                        "artifact_digest": event.get("target_snapshot"),
                    }
                ]
                for input_id in input_ids:
                    prior = prior_envelopes.get(input_id)
                    if prior is None:
                        expected_bindings = None
                        break
                    expected_bindings.append(
                        {
                            "artifact_id": input_id,
                            "artifact_digest": prior["envelope_digest"],
                        }
                    )
            try:
                payload_digest = hashlib.sha256(
                    canonical_bytes(_artifact_payload(event))
                ).hexdigest()
                canonical_envelope_digest = (
                    canonical_digest(envelope, digest_field="envelope_digest")
                    if isinstance(envelope, dict)
                    else None
                )
                manifest_digest_valid = True
                if is_manifest:
                    manifest_body = {
                        key: event.get(key)
                        for key in (
                            "schema_version",
                            "workflow_id",
                            "target_identity",
                            "collection_type",
                            "declared_item_count",
                            "observed_item_count",
                            "observed_byte_count",
                            "files",
                            "overflow",
                        )
                    }
                    manifest_digest_valid = event.get(
                        "manifest_digest"
                    ) == canonical_digest(manifest_body)
            except (TypeError, ValueError):
                payload_digest = None
                canonical_envelope_digest = None
                manifest_digest_valid = False
            envelope_valid = (
                isinstance(artifact_id, str)
                and bool(artifact_id)
                and isinstance(artifact_type, str)
                and bool(artifact_type)
                and isinstance(envelope, dict)
                and set(envelope) == expected_fields
                and envelope.get("artifact_id") == artifact_id
                and envelope.get("artifact_type") == artifact_type
                and envelope.get("workflow_id") == event.get("workflow_id")
                and envelope.get("target_identity") == event.get("target_identity")
                and envelope.get("mode") == event.get("mode")
                and envelope.get("created_stage")
                == _event_destination_stage(str(event_name), source_stage)
                and type(envelope.get("created_sequence")) is int
                and envelope.get("created_sequence") == index
                and isinstance(envelope.get("producer"), str)
                and bool(envelope["producer"])
                and isinstance(envelope.get("created_at"), str)
                and RFC3339_UTC_RE.fullmatch(envelope["created_at"]) is not None
                and envelope.get("input_bindings") == expected_bindings
                and normalized_run_relative_path(envelope.get("payload_path"))
                is not None
                and envelope.get("payload_digest") == payload_digest
                and isinstance(envelope.get("limitations"), list)
                and envelope.get("envelope_digest") == canonical_envelope_digest
                and (
                    not is_manifest
                    or (
                        envelope.get("manifest_digest")
                        == event.get("manifest_digest")
                        and manifest_digest_valid
                        and isinstance(envelope.get("manifest_digest"), str)
                        and SHA256_RE.fullmatch(envelope["manifest_digest"])
                        is not None
                    )
                )
            )
            if not envelope_valid:
                failures.append(
                    OracleFailure(
                        "INVALID_ARTIFACT_ENVELOPE",
                        index,
                        "artifact envelope or immutable payload binding was invalid",
                    )
                )
            elif artifact_id in prior_envelopes:
                failures.append(
                    OracleFailure(
                        "INVALID_ARTIFACT_ENVELOPE",
                        index,
                        "artifact envelope reused an immutable artifact identity",
                    )
                )
                envelope_valid = False
            elif envelope["payload_path"] in prior_payload_paths:
                failures.append(
                    OracleFailure(
                        "INVALID_ARTIFACT_ENVELOPE",
                        index,
                        "artifact envelope reused a retained payload path",
                    )
                )
                envelope_valid = False
            if envelope_valid:
                prior_envelopes[artifact_id] = envelope
                prior_payload_paths[envelope["payload_path"]] = artifact_id

        relevant: list[dict[str, str]] = []
        if isinstance(envelope, dict):
            bindings = envelope.get("input_bindings")
            if isinstance(bindings, list):
                relevant.extend(
                    {
                        "artifact_id": binding.get("artifact_id"),
                        "digest": binding.get("artifact_digest"),
                        "action": "consumed",
                    }
                    for binding in bindings
                    if isinstance(binding, dict)
                    and binding.get("artifact_id") != "target-snapshot"
                )
            relevant.append(
                {
                    "artifact_id": envelope.get("artifact_id"),
                    "digest": envelope.get("envelope_digest"),
                    "action": "accepted",
                }
            )
        destination_stage = _event_destination_stage(str(event_name), source_stage)
        receipt = event.get("transition_receipt")
        try:
            receipt_digest = (
                canonical_digest(receipt, digest_field="receipt_digest")
                if isinstance(receipt, dict)
                else None
            )
        except (TypeError, ValueError):
            receipt_digest = None
        receipt_valid = (
            receipt_chain_valid
            and isinstance(receipt, dict)
            and set(receipt) == TRANSITION_RECEIPT_FIELDS
            and receipt.get("schema_version")
            == "skill-builder-transition-receipt-v1"
            and receipt.get("workflow_id") == event.get("workflow_id")
            and receipt.get("target_identity") == event.get("target_identity")
            and type(receipt.get("sequence")) is int
            and receipt.get("sequence") == index
            and receipt.get("prior_receipt_digest") == prior_receipt_digest
            and receipt.get("event") == event_name
            and receipt.get("source_stage") == source_stage
            and receipt.get("destination_stage") == destination_stage
            and receipt.get("relevant_artifact_digests")
            == sorted(
                relevant,
                key=lambda item: (str(item["artifact_id"]), item["action"]),
            )
            and receipt.get("target_snapshot_digest")
            == event.get("target_snapshot")
            and receipt.get("authority_event_digest")
            == (authority_digest if candidate_seen else None)
            and isinstance(receipt.get("created_at"), str)
            and RFC3339_UTC_RE.fullmatch(receipt["created_at"]) is not None
            and receipt.get("receipt_digest") == receipt_digest
        )
        if not receipt_valid:
            receipt_chain_valid = False
            failures.append(
                OracleFailure(
                    "INVALID_TRANSITION_RECEIPT",
                    index,
                    "transition receipt was missing, malformed, noncanonical, or broke the hash chain",
                )
            )
        if isinstance(receipt, dict) and isinstance(
            receipt.get("receipt_digest"), str
        ):
            prior_receipt_digest = receipt["receipt_digest"]
        else:
            prior_receipt_digest = None
        source_stage = destination_stage
        if artifact_type == "authority-record" and envelope_valid:
            authority_digest = envelope["envelope_digest"]

    return failures, dispatchable


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
        criterion_id: f"accepted-{criterion_id.lower()}-proof{artifact_suffix}"
        for criterion_id in CRITERION_CATEGORY
    }
    resolution_id = "resolution-v1"
    host_rules_id = "host-rules-v1"
    baseline_id = "baseline-v1"
    regressions_id = "preserved-regressions-v1"
    research_id = "research-pack-v1"
    sieve_id = "evidence-sieve-v1"
    design_id = "design-record-v1"
    contract_id = "skill-contract-v1"
    confirmation_id = "user-confirmation-v1"
    confirmed_contract_id = "confirmed-contract-v1"
    rubric_id = "rubric-v1"
    evaluation_id = "evaluation-pack-v1"
    authority_id = "authority-record-v1"
    candidate_id = f"candidate-record-v1{artifact_suffix}"
    candidate_diff_id = f"candidate-diff-v1{artifact_suffix}"
    candidate_manifest_id = f"candidate-manifest-v1{artifact_suffix}"
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
    terminal_manifest_id = f"terminal-manifest-v1{artifact_suffix}"
    target_snapshot = "fixture-improve-snapshot-v1"
    isolated_locator = trusted_candidate_locator(workflow_id, revision)
    if isolated_locator is None:
        raise ValueError("accepted candidate lacked a trusted isolated locator")
    owned_candidate_path = candidate_owned_path(workflow_id, revision)
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
    resolution_authority = _resolution_authority()
    all_case_ids = [
        f"case-{criterion_id.lower()}" for criterion_id in sorted(CRITERION_CATEGORY)
    ]
    evaluation_cases = expected_evaluation_cases(
        BUILDER_FIXTURES,
        sorted(CRITERION_CATEGORY),
        digest_value("contract-v1"),
        rubric_id,
        digest_value("target-rubric-v1"),
    )
    evaluation_cases_by_id = {
        case["id"]: case for case in evaluation_cases
    }
    raw_case_evidence = [
        {
            "case_id": case_id,
            "raw_request_digest": evaluation_cases_by_id[case_id][
                "raw_request_digest"
            ],
            "raw_evidence_digest": digest_value(
                {"raw_trial_evidence": case_id}
            ),
        }
        for case_id in all_case_ids
    ]
    raw_evidence_by_case = {
        evidence["case_id"]: evidence["raw_evidence_digest"]
        for evidence in raw_case_evidence
    }
    case_receipts = [
        {
            "case_id": case_id,
            "raw_evidence_digest": raw_evidence_by_case[case_id],
            "receipt_digest": digest_value({"trial_receipt": case_id}),
            "fresh_context_id": f"trial-context-{index}",
        }
        for index, case_id in enumerate(all_case_ids, start=1)
    ]
    receipt_by_case = {
        receipt["case_id"]: receipt["receipt_digest"]
        for receipt in case_receipts
    }
    review_evidence = [
        artifact_manifest_id,
        candidate_diff_id,
        candidate_manifest_id,
        confirmed_contract_id,
        evaluation_id,
        host_rules_id,
        regressions_id,
        raw_trial_id,
        rubric_id,
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
            "artifact_id": category_proof_ids[criterion_id],
            "artifact_type": "category-evidence",
            "category": category,
            "criterion_id": criterion_id,
            "frozen_parameter_id": f"parameter-{criterion_id}",
            "case_id": f"case-{criterion_id.lower()}",
            "raw_artifact_ids": [raw_trial_id],
            "trial_receipt_ids": [trial_receipt_id],
            "raw_evidence_digest": raw_evidence_by_case[
                f"case-{criterion_id.lower()}"
            ],
            "trial_receipt_digest": receipt_by_case[
                f"case-{criterion_id.lower()}"
            ],
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
        for criterion_id in sorted(criterion_ids)
    ]
    scorecard_evidence = [
        conformance_id,
        scoring_review_id,
        rubric_id,
        *category_proof_ids.values(),
    ]
    final_review_evidence = list(review_evidence)
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
                    "evidence": [category_proof_ids[criterion_id]],
                }
                for criterion_id in sorted(criterion_ids)
            ],
            **binding,
        }
        for category, criterion_ids in CATEGORY_CRITERION_IDS.items()
    ]
    events = [
        {
            "event": "resolve",
            "selected_mode": "improve",
            "target_manifest": "targets/exact-improve/manifest.json",
            "target_snapshot": target_snapshot,
            "actor_identity": "main-agent-v1",
            "actor_role": "main-agent",
            "authority": resolution_authority,
            "artifact_id": resolution_id,
            "artifact_type": "resolution-record",
            "input_artifact_ids": [],
            "valid": True,
            **base_binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": host_rules_id,
            "artifact_type": "host-rules",
            "rules_digest": "host-rules-v1",
            "input_artifact_ids": [resolution_id],
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
            "event": "artifact_retained",
            "artifact_id": regressions_id,
            "artifact_type": "preserved-regressions",
            "case_ids": ["preserved-existing-behavior"],
            "input_artifact_ids": [baseline_id],
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
            "event": "artifact_retained",
            "artifact_id": confirmed_contract_id,
            "artifact_type": "confirmed-contract",
            "contract_artifact_id": contract_id,
            "confirmation_artifact_id": confirmation_id,
            "contract_digest": "contract-v1",
            "input_artifact_ids": [contract_id, confirmation_id],
            "valid": True,
            **contract_binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": rubric_id,
            "artifact_type": "rubric",
            "rubric_digest": "target-rubric-v1",
            "criterion_ids": sorted(CRITERION_CATEGORY),
            "input_artifact_ids": [confirmed_contract_id],
            "valid": True,
            **contract_binding,
        },
        {
            "event": "evaluation_frozen",
            "contract_digest": "contract-v1",
            "evaluation_digest": "evaluation-v1",
            "target_snapshot": target_snapshot,
            "partitions": ["visible", "frozen-validation", "hidden-release"],
            "case_ids": list(all_case_ids),
            "cases": evaluation_cases,
            "rubric_artifact_id": rubric_id,
            "rubric_digest": "target-rubric-v1",
            "frozen_parameter_ids": [
                f"parameter-{criterion_id}"
                for criterion_ids in CATEGORY_CRITERION_IDS.values()
                for criterion_id in sorted(criterion_ids)
            ],
            "artifact_id": evaluation_id,
            "artifact_type": "evaluation-pack",
            "input_artifact_ids": [
                contract_id,
                confirmation_id,
                rubric_id,
            ],
            "valid": True,
            **base_binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": authority_id,
            "artifact_type": "authority-record",
            "authorized_candidate_effects": list(
                resolution_authority["candidate_effects"]
            ),
            "authorized_delivery_scope": "none",
            "authorized_delivery_effects": list(
                resolution_authority["delivery_effects"]
            ),
            "cleanup_authorized": False,
            "resolution_artifact_id": resolution_id,
            "resolution_authority_digest": canonical_digest(
                resolution_authority
            ),
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
            "write_scope": "isolated-candidate",
            "isolated_locator": isolated_locator,
            "owned_paths": [owned_candidate_path],
            "path": owned_candidate_path,
            "artifact_id": candidate_id,
            "artifact_type": "candidate-record",
            "input_artifact_ids": [
                authority_id,
                contract_id,
                confirmation_id,
                evaluation_id,
            ],
            "valid": True,
            **evaluation_binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": candidate_diff_id,
            "artifact_type": "candidate-diff",
            "candidate_revision": revision,
            "diff_digest": f"diff-{revision}",
            "input_artifact_ids": [candidate_id, authority_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": candidate_manifest_id,
            "artifact_type": "candidate-manifest",
            "candidate_revision": revision,
            "files": [
                {
                    "path": owned_candidate_path,
                    "media_kind": "text/markdown",
                    "byte_count": 128,
                    "digest": f"candidate-content-{revision}",
                }
            ],
            "input_artifact_ids": [candidate_id, candidate_diff_id, authority_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": raw_trial_id,
            "artifact_type": "raw-trial-evidence",
            "case_ids": list(all_case_ids),
            "raw_artifact_digests": [
                entry["raw_evidence_digest"] for entry in raw_case_evidence
            ],
            "case_evidence": raw_case_evidence,
            "input_artifact_ids": [candidate_id, evaluation_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": trial_receipt_id,
            "artifact_type": "trial-receipt",
            "case_ids": list(all_case_ids),
            "fresh_context_ids": [
                f"trial-context-{index}" for index in range(1, 101)
            ],
            "case_receipts": case_receipts,
            "input_artifact_ids": [candidate_id, evaluation_id, raw_trial_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": trial_pack_id,
            "artifact_type": "trial-pack",
            "case_ids": list(all_case_ids),
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
            "category_results": [
                {
                    "category": category,
                    "score": 10,
                    "criterion_ids": sorted(criterion_ids),
                    "evidence_artifact_ids": [
                        category_proof_ids[criterion_id]
                        for criterion_id in sorted(criterion_ids)
                    ],
                }
                for category, criterion_ids in CATEGORY_CRITERION_IDS.items()
            ],
            "criterion_results": [
                {
                    "id": criterion_id,
                    "category": category,
                    "passed": True,
                    "score": 1,
                    "frozen_parameter_id": f"parameter-{criterion_id}",
                    "case_id": f"case-{criterion_id.lower()}",
                    "evidence_artifact_id": category_proof_ids[criterion_id],
                    "raw_artifact_ids": [raw_trial_id],
                    "trial_receipt_ids": [trial_receipt_id],
                    "raw_evidence_digest": raw_evidence_by_case[
                        f"case-{criterion_id.lower()}"
                    ],
                    "trial_receipt_digest": receipt_by_case[
                        f"case-{criterion_id.lower()}"
                    ],
                    "finding_ids": [],
                }
                for category, criterion_ids in CATEGORY_CRITERION_IDS.items()
                for criterion_id in sorted(criterion_ids)
            ],
            "findings": [],
            "repair_history": (
                []
                if revision == "candidate-v1"
                else [
                    {
                        "finding_id": "material-finding-v1",
                        "prior_revision": "candidate-v1",
                        "resulting_revision": revision,
                        "status": "repaired-and-retested",
                    }
                ]
            ),
            "rubric_artifact_id": rubric_id,
            "rubric_digest": "target-rubric-v1",
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
        {
            "event": "artifact_retained",
            "artifact_id": terminal_manifest_id,
            "artifact_type": "terminal-manifest",
            "valid": True,
            **binding,
        },
        {"event": "finalized", **binding},
    ]
    return seal_trace(events)


def accepted_repair_trace(
    workflow_id: str,
    *,
    prior_revision: str = "candidate-v1",
    repaired_revision: str = "candidate-v2",
) -> list[dict[str, Any]]:
    """Build a strict not-ready -> repair -> fresh-candidate control trace."""

    revision_two = accepted_finalization_trace(repaired_revision)
    candidate_index = next(
        index
        for index, event in enumerate(revision_two)
        if event.get("event") == "candidate_edit"
    )
    prefix = copy.deepcopy(revision_two[:candidate_index])
    suffix = copy.deepcopy(revision_two[candidate_index:])
    candidate_two = suffix[0]
    authority_id = next(
        event["artifact_id"]
        for event in prefix
        if event.get("artifact_type") == "authority-record"
    )
    evaluation_id = next(
        event["artifact_id"]
        for event in prefix
        if event.get("artifact_type") == "evaluation-pack"
    )
    concrete_ids = {
        event["artifact_type"]: event["artifact_id"]
        for event in prefix
        if event.get("artifact_type")
        in {
            "confirmed-contract",
            "evaluation-pack",
            "host-rules",
            "preserved-regressions",
            "rubric",
        }
    }
    binding = {
        "contract_digest": candidate_two["contract_digest"],
        "evaluation_digest": candidate_two["evaluation_digest"],
        "revision": prior_revision,
    }
    candidate_one_id = "repair-candidate-record-v1"
    diff_one_id = "repair-candidate-diff-v1"
    candidate_manifest_one_id = "repair-candidate-manifest-v1"
    raw_one_id = "repair-raw-trial-evidence-v1"
    receipt_one_id = "repair-trial-receipt-v1"
    trial_pack_one_id = "repair-trial-pack-v1"
    artifact_manifest_one_id = "repair-artifact-manifest-v1"
    review_one_id = "repair-review-v1"
    evaluation_record = next(
        event
        for event in prefix
        if event.get("artifact_type") == "evaluation-pack"
    )
    case_ids = list(evaluation_record["case_ids"])
    request_by_case = {
        case["id"]: case["raw_request_digest"]
        for case in evaluation_record["cases"]
    }
    raw_case_evidence = [
        {
            "case_id": case_id,
            "raw_request_digest": request_by_case[case_id],
            "raw_evidence_digest": digest_value(
                {"repair_raw_trial": case_id, "revision": prior_revision}
            ),
        }
        for case_id in case_ids
    ]
    raw_digest_by_case = {
        entry["case_id"]: entry["raw_evidence_digest"]
        for entry in raw_case_evidence
    }
    case_receipts = [
        {
            "case_id": case_id,
            "raw_evidence_digest": raw_digest_by_case[case_id],
            "receipt_digest": digest_value(
                {"repair_trial_receipt": case_id, "revision": prior_revision}
            ),
            "fresh_context_id": f"repair-context-{index}",
        }
        for index, case_id in enumerate(case_ids, start=1)
    ]
    review_evidence = [
        artifact_manifest_one_id,
        diff_one_id,
        candidate_manifest_one_id,
        concrete_ids["confirmed-contract"],
        concrete_ids["evaluation-pack"],
        concrete_ids["host-rules"],
        concrete_ids["preserved-regressions"],
        raw_one_id,
        concrete_ids["rubric"],
    ]
    repair_epoch = [
        {
            **copy.deepcopy(candidate_two),
            "candidate_revision": prior_revision,
            "revision": prior_revision,
            "artifact_id": candidate_one_id,
        },
        {
            "event": "artifact_retained",
            "artifact_id": diff_one_id,
            "artifact_type": "candidate-diff",
            "candidate_revision": prior_revision,
            "diff_digest": "repair-diff-v1",
            "input_artifact_ids": [candidate_one_id, authority_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": candidate_manifest_one_id,
            "artifact_type": "candidate-manifest",
            "candidate_revision": prior_revision,
            "files": [
                {
                    "path": "candidate-v1/SKILL.md",
                    "media_kind": "text/markdown",
                    "byte_count": 96,
                    "digest": "repair-candidate-content-v1",
                }
            ],
            "input_artifact_ids": [
                candidate_one_id,
                diff_one_id,
                authority_id,
            ],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": raw_one_id,
            "artifact_type": "raw-trial-evidence",
            "case_ids": case_ids,
            "raw_artifact_digests": [
                entry["raw_evidence_digest"] for entry in raw_case_evidence
            ],
            "case_evidence": raw_case_evidence,
            "input_artifact_ids": [candidate_one_id, evaluation_id],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": receipt_one_id,
            "artifact_type": "trial-receipt",
            "case_ids": case_ids,
            "fresh_context_ids": [
                f"repair-context-{index}" for index in range(1, 101)
            ],
            "case_receipts": case_receipts,
            "input_artifact_ids": [
                candidate_one_id,
                evaluation_id,
                raw_one_id,
            ],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": trial_pack_one_id,
            "artifact_type": "trial-pack",
            "case_ids": case_ids,
            "trial_receipt_ids": [receipt_one_id],
            "raw_artifact_ids": [raw_one_id],
            "input_artifact_ids": [
                candidate_one_id,
                evaluation_id,
                raw_one_id,
                receipt_one_id,
            ],
            "valid": True,
            **binding,
        },
        {
            "event": "artifact_retained",
            "artifact_id": artifact_manifest_one_id,
            "artifact_type": "artifact-manifest",
            "valid": True,
            **binding,
        },
        {
            "event": "review_recorded",
            "phase": "repair",
            "review_id": review_one_id,
            "reviewer_identity": "repair-reviewer-v1",
            "reviewer_role": "independent-target-reviewer",
            "valid": True,
            "verdict": "not ready",
            "independent": True,
            "read_only": True,
            "findings": [
                {
                    "id": "finding-repair-safety",
                    "severity": "Medium",
                    "evidence": [raw_one_id],
                    "impact": "Candidate v1 violates frozen safety criterion SA1.",
                    "correction": "Repair SA1 and rerun every frozen case.",
                    "affected_criteria": ["SA1"],
                    "affected_categories": ["safety"],
                }
            ],
            "supplied_artifacts": list(review_evidence),
            "accessed_artifacts": list(review_evidence),
            "forbidden_artifacts_accessed": [],
            "evidence": list(review_evidence),
            "artifact_id": review_one_id,
            "artifact_type": "review-record",
            "input_artifact_ids": list(review_evidence),
            **binding,
        },
        {
            "event": "repair_completed",
            "finding_ids": ["finding-repair-safety"],
            "prior_revision": prior_revision,
            "candidate_revision": repaired_revision,
            **binding,
        },
    ]
    repaired_events = [*prefix, *repair_epoch, *suffix]
    bind_fixture_candidate_paths(repaired_events, workflow_id)
    return seal_trace(repaired_events, workflow_id=workflow_id)


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

    return seal_trace([*prefix, *transitions, *replay])


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
    events = [
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
    return seal_trace(events, workflow_id=workflow_id)


def evaluate_trace(
    trace: list[dict[str, Any]],
    fixture_root: Path,
    scenario: dict[str, Any] | None = None,
) -> tuple[OracleFailure, ...]:
    failures, dispatchable_event_indices = validate_schema_boundary(trace)
    boundary_invalid_indices = {
        failure.event_index
        for failure in failures
        if failure.code
        in {
            "INVALID_EVENT_SCHEMA",
            "UNKNOWN_EVENT",
            "INVALID_ARTIFACT_ENVELOPE",
            "INVALID_TRANSITION_RECEIPT",
        }
    }
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
    terminal_manifest_record: dict[str, Any] | None = None
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
    run_lifecycle_state = "active"
    identity_ambiguous = False
    current_workflow_id: str | None = None
    current_target_identity: str | None = None
    current_mode: str | None = None
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
    category_evidence_ids_by_criterion: dict[str, list[str]] = {}
    ambiguous_category_evidence_criterion_ids: set[str] = set()
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
            and record.get("mode") == current_mode
            and record.get("target_snapshot") == current_snapshot
            and record.get("identity_generation") == identity_generation
            and record.get("snapshot_generation") == snapshot_generation
        )

    def artifact_envelope_is_valid(
        record: dict[str, Any], expected_type: str
    ) -> bool:
        envelope = record.get("artifact_envelope")
        return (
            isinstance(record.get("artifact_id"), str)
            and bool(record["artifact_id"])
            and record.get("artifact_type") == expected_type
            and record.get("valid") is True
            and record_event_indices.get(id(record), -1)
            not in boundary_invalid_indices
            and isinstance(envelope, dict)
            and envelope.get("artifact_id") == record.get("artifact_id")
            and envelope.get("artifact_type") == expected_type
            and envelope.get("envelope_digest")
            == canonical_digest(envelope, digest_field="envelope_digest")
            and record_identity_binding_is_current(record)
        )

    def authority_binding_is_current(record: dict[str, Any]) -> bool:
        if authority_record is None:
            return False
        envelope = authority_record.get("artifact_envelope")
        return (
            isinstance(envelope, dict)
            and artifact_is_current(str(authority_record.get("artifact_id")))
            and record_event_indices.get(id(authority_record), -1)
            < record_event_indices.get(id(record), -1)
            and record.get("authority_binding")
            == {
                "artifact_id": authority_record.get("artifact_id"),
                "digest": envelope.get("envelope_digest"),
            }
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

    def invalidate_artifact(record: dict[str, Any]) -> None:
        """Invalidate only schema-valid artifact identities."""

        artifact_id = record.get("artifact_id")
        if isinstance(artifact_id, str):
            invalidated_artifact_ids.add(artifact_id)

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
        records = resolved_artifacts(evidence_ids, consumer)
        return (
            records is not None
            and all(
                record.get("artifact_type") in acceptable_types
                and record_binding(record) == binding
                and (
                    not current_epoch
                    or record.get("artifact_type") in PRE_CANDIDATE_ARTIFACT_TYPES
                    or record_postdates_candidate(record)
                )
                for record in records
            )
        )

    def review_access_is_valid(review: dict[str, Any]) -> bool:
        supplied = review.get("supplied_artifacts")
        accessed = review.get("accessed_artifacts")
        forbidden = review.get("forbidden_artifacts_accessed")
        evidence = review.get("evidence")
        bindings = review.get("review_artifact_bindings")
        required_records = {
            artifact_type: current_artifacts_of_type(artifact_type)
            for artifact_type in REVIEW_REQUIRED_ARTIFACTS
        }
        required_ids = {
            records[0]["artifact_id"]
            for records in required_records.values()
            if len(records) == 1
        }
        expected_bindings = [
            {
                "artifact_id": artifact_id,
                "type": retained_artifacts[artifact_id]["artifact_type"],
                "digest": retained_artifacts[artifact_id]["artifact_envelope"][
                    "envelope_digest"
                ],
            }
            for artifact_id in evidence or []
            if artifact_id in retained_artifacts
            and isinstance(
                retained_artifacts[artifact_id].get("artifact_envelope"), dict
            )
        ]
        return (
            all(len(records) == 1 for records in required_records.values())
            and isinstance(evidence, list)
            and len(evidence) == len(REVIEW_REQUIRED_ARTIFACTS)
            and set(evidence) == required_ids
            and isinstance(supplied, list)
            and all(isinstance(item, str) for item in supplied)
            and len(supplied) == len(set(supplied))
            and set(supplied) == required_ids
            and isinstance(accessed, list)
            and all(isinstance(item, str) for item in accessed)
            and len(accessed) == len(set(accessed))
            and set(accessed) == required_ids
            and bindings == expected_bindings
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
        required_review_ids = {
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact_is_current(artifact_id)
            and artifact.get("artifact_type") in REVIEW_EVIDENCE_ARTIFACT_TYPES
        }
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
            and independence_record is not None
            and reviewer_identity
            == independence_record.get("actors", {}).get(
                "final_reviewer"
                if review.get("phase") == "final"
                else "scoring_reviewer"
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
            and record_schema_validity.get(id(review), False)
            and record_lifecycle_validity.get(id(review), False)
            and review_evidence_types == set(REVIEW_EVIDENCE_ARTIFACT_TYPES)
            and consumer_inputs_are_exact(
                review,
                {
                    artifact_id
                    for artifact_id in required_review_ids
                    if isinstance(artifact_id, str)
                },
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

    def category_evidence_record_is_valid(
        record: dict[str, Any],
        binding: tuple[Any, Any, Any, Any, Any],
        category: str,
        criterion_id: str,
        *,
        current_epoch: bool = False,
    ) -> bool:
        raw_ids = record.get("raw_artifact_ids")
        receipt_ids = record.get("trial_receipt_ids")
        raw_records = resolved_artifacts(raw_ids, record)
        receipt_records = resolved_artifacts(receipt_ids, record)
        if (
            raw_records is None
            or len(raw_records) != 1
            or raw_records[0].get("artifact_type") != "raw-trial-evidence"
            or receipt_records is None
            or len(receipt_records) != 1
            or receipt_records[0].get("artifact_type") != "trial-receipt"
        ):
            return False
        case_id = record.get("case_id")
        evaluation_cases = (frozen_evaluation or {}).get("cases")
        raw_case_evidence = raw_records[0].get("case_evidence")
        case_receipts = receipt_records[0].get("case_receipts")
        if (
            not isinstance(case_id, str)
            or not isinstance(evaluation_cases, list)
            or not all(evaluation_case_is_valid(case) for case in evaluation_cases)
            or not isinstance(raw_case_evidence, list)
            or not all(case_evidence_is_valid(entry) for entry in raw_case_evidence)
            or not isinstance(case_receipts, list)
            or not all(case_receipt_is_valid(entry) for entry in case_receipts)
        ):
            return False
        evaluation_matches = [
            case for case in evaluation_cases if case["id"] == case_id
        ]
        raw_matches = [
            entry for entry in raw_case_evidence if entry["case_id"] == case_id
        ]
        receipt_matches = [
            entry for entry in case_receipts if entry["case_id"] == case_id
        ]
        if not (
            len(evaluation_matches) == len(raw_matches) == len(receipt_matches) == 1
        ):
            return False
        evaluation_case = evaluation_matches[0]
        raw_case = raw_matches[0]
        case_receipt = receipt_matches[0]
        review_artifact_id = (scoring_review or {}).get("artifact_id")
        evaluation_artifact_id = (frozen_evaluation or {}).get("artifact_id")
        related_review_finding_ids = {
            finding.get("id")
            for finding in (scoring_review or {}).get("findings", [])
            if isinstance(finding, dict)
            and isinstance(finding.get("affected_criteria"), list)
            and criterion_id in finding["affected_criteria"]
        }
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
            record.get("artifact_type") == "category-evidence"
            and record.get("category") == category
            and record.get("criterion_id") == criterion_id
            and CRITERION_CATEGORY.get(criterion_id) == category
            and record.get("frozen_parameter_id")
            == f"parameter-{criterion_id}"
            and record.get("case_id") == f"case-{criterion_id.lower()}"
            and raw_case["raw_request_digest"]
            == evaluation_case["raw_request_digest"]
            and case_receipt["raw_evidence_digest"]
            == raw_case["raw_evidence_digest"]
            and record.get("raw_evidence_digest")
            == raw_case["raw_evidence_digest"]
            and record.get("trial_receipt_digest")
            == case_receipt["receipt_digest"]
            and isinstance(raw_ids, list)
            and len(raw_ids) == 1
            and evidence_types_resolve(
                raw_ids, frozenset({"raw-trial-evidence"}), record
            )
            and isinstance(receipt_ids, list)
            and len(receipt_ids) == 1
            and evidence_types_resolve(
                receipt_ids, frozenset({"trial-receipt"}), record
            )
            and unique_nonempty_strings(record.get("review_finding_ids"))
            and set(record["review_finding_ids"])
            == related_review_finding_ids
            and record.get("review_id") == (scoring_review or {}).get("review_id")
            and isinstance(review_artifact_id, str)
            and artifact_is_current(review_artifact_id)
            and scoring_review is not None
            and record_lifecycle_validity.get(id(scoring_review), False)
            and isinstance(evaluation_artifact_id, str)
            and artifact_is_current(evaluation_artifact_id)
            and exact_evidence_resolves(
                record.get("input_artifact_ids"), required_input_ids, record
            )
            and record_binding(record) == binding
            and (
                not current_epoch
                or record_postdates_candidate(record)
            )
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
        records = resolved_artifacts(evidence_ids, consumer)
        return (
            records is not None
            and len(records) == 1
            and category_evidence_record_is_valid(
                records[0],
                binding,
                category,
                criterion_id,
                current_epoch=current_epoch,
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
        nonlocal terminal_manifest_record
        invalidated_artifact_ids.update(
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact.get("artifact_type") in CANDIDATE_BOUND_ARTIFACT_TYPES
        )
        category_evidence_ids_by_criterion.clear()
        ambiguous_category_evidence_criterion_ids.clear()
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
        terminal_manifest_record = None

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
            for artifact_id, artifact in retained_artifacts.items()
            if artifact_id not in {manifest_id, conformance_id}
            and record_event_indices.get(id(artifact), -1) < conformance_index
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

    def terminal_manifest_covers_finalization(final_index: int) -> bool:
        if terminal_manifest_record is None:
            return False
        manifest_id = terminal_manifest_record.get("artifact_id")
        manifest_index = record_event_indices.get(id(terminal_manifest_record), -1)
        expected_ids = {
            artifact_id
            for artifact_id, artifact in retained_artifacts.items()
            if artifact_id != manifest_id
            and record_event_indices.get(id(artifact), final_index) < final_index
        }
        return (
            isinstance(manifest_id, str)
            and artifact_is_current(manifest_id)
            and record_schema_validity.get(id(terminal_manifest_record), False)
            and manifest_index < final_index
            and retained_manifest_is_valid(
                terminal_manifest_record,
                expected_ids,
                retained_artifacts,
            )
            and all(
                record_schema_validity.get(
                    id(retained_artifacts[artifact_id]), False
                )
                for artifact_id in expected_ids
            )
            and set(
                terminal_manifest_record.get("manifested_artifact_ids", [])
            )
            == expected_ids
            and set(terminal_manifest_record.get("input_artifact_ids", []))
            == expected_ids
            and all(
                record_event_indices.get(id(artifact), -1) <= manifest_index
                for artifact_id, artifact in retained_artifacts.items()
                if artifact_id != manifest_id
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

    inactive_forbidden_events = {
        "artifact_retained",
        "baseline_captured",
        "builder_conformance_recorded",
        "candidate_edit",
        "category_scored",
        "contract_changed",
        "contract_written",
        "delivery_attempt",
        "designs_challenged",
        "evaluation_frozen",
        "evidence_sieved",
        "finalized",
        "goal_changed",
        "release_evidence_retained",
        "repair_completed",
        "research_pack",
        "review_recorded",
        "spec_outcome_recorded",
        "target_scorecard_recorded",
        "target_snapshot_changed",
        "user_confirmed",
        "verification_recorded",
        "write",
    }

    for index, event in enumerate(trace):
        if index not in dispatchable_event_indices:
            continue
        event_name = event.get("event")
        record_event_indices[id(event)] = index
        resolution_bound_effect = (
            event_name
            in {
                "artifact_retained",
                "builder_conformance_recorded",
                "candidate_edit",
                "delivery_attempt",
                "release_evidence_retained",
                "repair_completed",
                "review_recorded",
                "spec_outcome_recorded",
                "target_scorecard_recorded",
                "verification_recorded",
                "write",
                "finalized",
            }
            or "artifact_id" in event
            or "artifact_type" in event
        )
        if (
            event_name != "resolve"
            and resolution_bound_effect
            and resolution_record is None
        ):
            failures.append(
                OracleFailure(
                    "EFFECT_BEFORE_RESOLUTION",
                    index,
                    "a typed workflow effect lacked a validated resolution record",
                )
            )
        if (
            current_candidate_event_index is not None
            and event_name
            in {
                "artifact_retained",
                "builder_conformance_recorded",
                "candidate_edit",
                "category_scored",
                "contract_changed",
                "contract_written",
                "delivery_attempt",
                "evaluation_frozen",
                "finalized",
                "release_evidence_retained",
                "repair_completed",
                "review_recorded",
                "spec_outcome_recorded",
                "target_scorecard_recorded",
                "target_snapshot_changed",
                "verification_recorded",
                "write",
            }
            and not authority_binding_is_current(event)
        ):
            failures.append(
                OracleFailure(
                    "DOWNSTREAM_WITHOUT_AUTHORITY",
                    index,
                    "a candidate-dependent effect did not bind validated authority",
                )
            )
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
            "write",
        }:
            failures.append(
                OracleFailure(
                    "EVENT_AFTER_FINALIZATION",
                    index,
                    "revision-dependent work occurred after the finalized terminal event",
                )
            )
            continue
        if (
            run_lifecycle_state != "active"
            and event_name in inactive_forbidden_events
        ):
            failures.append(
                OracleFailure(
                    "INACTIVE_RUN_EFFECT",
                    index,
                    f"{event_name!r} occurred while the run was {run_lifecycle_state}",
                )
            )
            continue
        if event_name == "actor_role_recorded":
            register_actor(event.get("actor_identity"), event.get("actor_role"))
        elif event_name == "resolve":
            manifest = None
            try:
                fixture_root_resolved = fixture_root.resolve()
                manifest_locator = normalized_run_relative_path(
                    event.get("target_manifest")
                )
                if manifest_locator is not None:
                    manifest_path = (
                        fixture_root_resolved / manifest_locator
                    ).resolve()
                    if manifest_path.is_relative_to(fixture_root_resolved):
                        manifest = json.loads(
                            manifest_path.read_text(encoding="utf-8")
                        )
            except (
                OSError,
                RuntimeError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                manifest = None
            if not fixture_target_manifest_is_valid(manifest):
                failures.append(
                    OracleFailure(
                        "INVALID_RESOLUTION_MANIFEST",
                        index,
                        "resolution referenced no readable target manifest object within the fixture root",
                    )
                )
                continue
            trusted_authority = _resolution_authority()
            if event.get("authority") != trusted_authority:
                failures.append(
                    OracleFailure(
                        "INVALID_RESOLUTION_AUTHORITY",
                        index,
                        "resolution authority did not equal the frozen fixture policy",
                    )
                )
                continue
            new_workflow_id = event.get("workflow_id")
            new_target_identity = manifest.get("canonical_target") or event.get(
                "target_identity"
            )
            identity_ambiguous = manifest.get("identity_ambiguous", False)
            expected_mode = (
                None
                if identity_ambiguous
                else (
                    "improve"
                    if manifest.get("exact_target_exists")
                    else "create"
                )
            )
            expected_snapshot = digest_value(manifest["snapshot_digest"])
            manifest_binding_valid = (
                event.get("target_snapshot") == expected_snapshot
                and (
                    identity_ambiguous
                    and event.get("selected_mode") == "unresolved"
                    or not identity_ambiguous
                    and event.get("selected_mode") == expected_mode
                    and event.get("mode") == expected_mode
                )
                and (
                    manifest.get("canonical_target") is None
                    or event.get("target_identity")
                    == manifest.get("canonical_target")
                )
            )
            if identity_resolution_seen:
                identity_generation += 1
                snapshot_generation += 1
                invalidate_identity_or_snapshot_chain()
            else:
                identity_generation = 1
                snapshot_generation = 1
            current_workflow_id = new_workflow_id
            current_target_identity = new_target_identity
            current_mode = (
                event.get("mode") if identity_ambiguous else expected_mode
            )
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
            if event["selected_mode"] == "create" and manifest.get(
                "exact_target_exists"
            ):
                failures.append(
                    OracleFailure(
                        "CREATE_TARGET_EXISTS",
                        index,
                        f"create mode would overwrite exact target {manifest.get('canonical_target')}",
                    )
                )
            current_snapshot = event["target_snapshot"]
            register_actor(event.get("actor_identity"), event.get("actor_role"))
            register_artifact(event, index)
            resolution_record = event
            record_schema_validity[id(event)] = (
                artifact_envelope_is_valid(event, "resolution-record")
                and manifest_binding_valid
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
            else:
                run_lifecycle_state = "active"
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
            invalidate_contract_and_downstream()
            contract_epoch += 1
            current_contract = event["contract_digest"]
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
            snapshot_generation += 1
            invalidate_identity_or_snapshot_chain()
        elif event_name == "paused":
            pause_binding_matches_active = (
                current_workflow_id is None
                and not identity_resolution_seen
                or event.get("workflow_id") == current_workflow_id
                and event.get("target_identity") == current_target_identity
                and event.get("target_snapshot") == current_snapshot
                and event.get("mode") == current_mode
                and event.get("identity_generation") == identity_generation
                and event.get("snapshot_generation") == snapshot_generation
            )
            pause_valid = (
                run_lifecycle_state == "active"
                and event.get("state_validated") is True
                and pause_binding_matches_active
            )
            if pause_valid:
                pause_record = event
                run_lifecycle_state = "paused"
            else:
                failures.append(
                    OracleFailure(
                        "INVALID_PAUSE_STATE",
                        index,
                        "pause did not bind a validated active run state",
                    )
                )
        elif event_name == "resumed":
            revalidation_fields = (
                "chain_revalidated",
                "identity_revalidated",
                "snapshot_revalidated",
                "evidence_revalidated",
            )
            resume_valid = (
                run_lifecycle_state == "paused"
                and pause_record is not None
                and pause_record.get("state_validated") is True
                and pause_record.get("workflow_id") == event.get("workflow_id")
                and pause_record.get("target_identity")
                == event.get("target_identity")
                and pause_record.get("target_snapshot")
                == event.get("target_snapshot")
                and pause_record.get("mode") == event.get("mode")
                and pause_record.get("identity_generation")
                == event.get("identity_generation")
                and pause_record.get("snapshot_generation")
                == event.get("snapshot_generation")
                and (
                    current_workflow_id is None
                    and not identity_resolution_seen
                    or event.get("workflow_id") == current_workflow_id
                    and event.get("target_identity")
                    == current_target_identity
                    and event.get("target_snapshot") == current_snapshot
                    and event.get("mode") == current_mode
                    and event.get("identity_generation")
                    == identity_generation
                    and event.get("snapshot_generation")
                    == snapshot_generation
                )
                and all(event.get(field) is True for field in revalidation_fields)
            )
            if not resume_valid:
                failures.append(
                    OracleFailure(
                        "RESUME_WITHOUT_REVALIDATION",
                        index,
                        "resume did not revalidate the paused chain, identity, snapshot, and evidence",
                    )
                )
            else:
                pause_record = None
                run_lifecycle_state = "active"
        elif event_name == "question_asked":
            run_lifecycle_state = "unresolved"
        elif event_name == "stopped":
            run_lifecycle_state = "stopped"
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
            resolved_authority = (resolution_record or {}).get("authority")
            delegation = (
                resolved_authority.get("delegation")
                if isinstance(resolved_authority, dict)
                else None
            )
            allowed_writes = (
                resolved_authority.get("allowed_writes", [])
                if isinstance(resolved_authority, dict)
                else []
            )
            candidate_effects = (
                resolved_authority.get("candidate_effects", [])
                if isinstance(resolved_authority, dict)
                else []
            )
            delegated_effects = (
                delegation.get(current_candidate_implementer_role, [])
                if isinstance(delegation, dict)
                and isinstance(current_candidate_implementer_role, str)
                else []
            )
            write_authorized = (
                authority_binding_is_current(event)
                and event.get("workflow_id") == current_workflow_id
                and event.get("target_identity") == current_target_identity
                and event.get("actor") == current_candidate_implementer
                and candidate_record is not None
                and artifact_is_current(
                    str(candidate_record.get("artifact_id"))
                )
                and candidate_path_contract_is_valid(candidate_record)
                and event.get("path") in candidate_record.get(
                    "owned_paths", []
                )
                and event.get("destination_scope") in allowed_writes
                and event.get("effect") in candidate_effects
                and event.get("effect") in delegated_effects
            )
            if not write_authorized:
                failures.append(
                    OracleFailure(
                        "UNAUTHORIZED_PRODUCTION_WRITE",
                        index,
                        f"{event['actor']} attempted a write outside canonical resolution authority",
                    )
                )
        elif event_name == "delivery_attempt":
            resolved_authority = (resolution_record or {}).get("authority")
            delivery_effects = (
                resolved_authority.get("delivery_effects", [])
                if isinstance(resolved_authority, dict)
                else []
            )
            expected_scope = (
                current_target_identity if delivery_effects else "none"
            )
            delivery_authorized = (
                authority_binding_is_current(event)
                and event.get("workflow_id") == current_workflow_id
                and event.get("target_identity") == current_target_identity
                and event.get("destination_scope") == expected_scope
                and event.get("effect") in delivery_effects
            )
            if not delivery_authorized:
                failures.append(
                    OracleFailure(
                        "UNAUTHORIZED_DELIVERY",
                        index,
                        f"{event['effect']} was outside canonical delivery authority or target scope",
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
            if resolution_record is not None:
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
            if resolution_record is not None:
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
            if resolution_record is not None:
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
                    and (
                        artifact_type not in CANDIDATE_BOUND_ARTIFACT_TYPES
                        or authority_binding_is_current(event)
                    )
                )
                if artifact_type == "host-rules":
                    schema_valid = schema_valid and artifact_inputs_match(
                        event, (resolution_record,)
                    )
                elif artifact_type == "preserved-regressions":
                    schema_valid = schema_valid and artifact_inputs_match(
                        event, (baseline_report,)
                    )
                elif artifact_type == "confirmed-contract":
                    schema_valid = (
                        schema_valid
                        and event.get("contract_digest") == current_contract
                        and artifact_inputs_match(
                            event, (contract_record, confirmation_record)
                        )
                    )
                elif artifact_type == "rubric":
                    confirmed_contracts = current_artifacts_of_type(
                        "confirmed-contract"
                    )
                    criterion_ids = event.get("criterion_ids")
                    schema_valid = (
                        schema_valid
                        and len(confirmed_contracts) == 1
                        and unique_nonempty_strings(criterion_ids)
                        and set(criterion_ids)
                        == set(CRITERION_CATEGORY)
                        and digest_is_valid(event.get("rubric_digest"))
                        and artifact_inputs_match(event, confirmed_contracts)
                    )
                elif artifact_type == "authority-record":
                    resolved_authority = (resolution_record or {}).get(
                        "authority"
                    )
                    delivery_effects = (
                        resolved_authority.get("delivery_effects", [])
                        if isinstance(resolved_authority, dict)
                        else []
                    )
                    expected_delivery_scope = (
                        current_target_identity if delivery_effects else "none"
                    )
                    schema_valid = (
                        schema_valid
                        and isinstance(resolved_authority, dict)
                        and event.get("authorized_candidate_effects")
                        == resolved_authority.get("candidate_effects")
                        and event.get("authorized_delivery_scope")
                        == expected_delivery_scope
                        and event.get("authorized_delivery_effects")
                        == delivery_effects
                        and event.get("cleanup_authorized") is False
                        and event.get("resolution_artifact_id")
                        == (resolution_record or {}).get("artifact_id")
                        and event.get("resolution_authority_digest")
                        == canonical_digest(resolved_authority)
                        and artifact_inputs_match(event, (resolution_record,))
                    )
                    if schema_valid:
                        authority_record = event
                elif artifact_type == "candidate-diff":
                    schema_valid = (
                        schema_valid
                        and event.get("candidate_revision")
                        == current_candidate_revision
                        and artifact_inputs_match(
                            event, (candidate_record, authority_record)
                        )
                    )
                elif artifact_type == "candidate-manifest":
                    candidate_diffs = current_artifacts_of_type("candidate-diff")
                    files = event.get("files")
                    candidate_owned_paths = (candidate_record or {}).get(
                        "owned_paths"
                    )
                    schema_valid = (
                        schema_valid
                        and len(candidate_diffs) == 1
                        and event.get("candidate_revision")
                        == current_candidate_revision
                        and isinstance(files, list)
                        and bool(files)
                        and all(candidate_file_is_valid(entry) for entry in files)
                        and unique_nonempty_strings(candidate_owned_paths)
                        and {entry["path"] for entry in files}
                        == set(candidate_owned_paths)
                        and len(files) == len(candidate_owned_paths)
                        and manifest_header_is_valid(event, files)
                        and len({entry["path"] for entry in files}) == len(files)
                        and any(
                            PurePosixPath(entry["path"]).name == "SKILL.md"
                            for entry in files
                        )
                        and artifact_inputs_match(
                            event,
                            (candidate_record, *candidate_diffs, authority_record),
                        )
                    )
                elif artifact_type == "raw-trial-evidence":
                    case_ids = event.get("case_ids")
                    raw_digests = event.get("raw_artifact_digests")
                    case_evidence = event.get("case_evidence")
                    evaluation_cases = (frozen_evaluation or {}).get("cases")
                    evaluation_request_by_case = {
                        case["id"]: case["raw_request_digest"]
                        for case in evaluation_cases
                    } if (
                        isinstance(evaluation_cases, list)
                        and all(evaluation_case_is_valid(case) for case in evaluation_cases)
                    ) else {}
                    schema_valid = (
                        schema_valid
                        and unique_nonempty_strings(case_ids)
                        and case_ids == (frozen_evaluation or {}).get("case_ids")
                        and isinstance(case_evidence, list)
                        and len(case_evidence) == len(case_ids)
                        and all(
                            case_evidence_is_valid(entry)
                            for entry in case_evidence
                        )
                        and [entry["case_id"] for entry in case_evidence]
                        == case_ids
                        and all(
                            entry["raw_request_digest"]
                            == evaluation_request_by_case.get(entry["case_id"])
                            for entry in case_evidence
                        )
                        and unique_nonempty_strings(raw_digests)
                        and raw_digests
                        == [
                            entry["raw_evidence_digest"]
                            for entry in case_evidence
                        ]
                        and artifact_inputs_match(
                            event, (candidate_record, frozen_evaluation)
                        )
                    )
                elif artifact_type == "trial-receipt":
                    context_ids = event.get("fresh_context_ids")
                    case_ids = event.get("case_ids")
                    case_receipts = event.get("case_receipts")
                    raw_case_evidence = (
                        current_raw_records[0].get("case_evidence")
                        if len(current_raw_records) == 1
                        else None
                    )
                    raw_digest_by_case = {
                        entry["case_id"]: entry["raw_evidence_digest"]
                        for entry in raw_case_evidence
                    } if (
                        isinstance(raw_case_evidence, list)
                        and all(
                            case_evidence_is_valid(entry)
                            for entry in raw_case_evidence
                        )
                    ) else {}
                    schema_valid = (
                        schema_valid
                        and len(current_raw_records) == 1
                        and unique_nonempty_strings(case_ids)
                        and case_ids == (frozen_evaluation or {}).get("case_ids")
                        and unique_nonempty_strings(context_ids)
                        and len(context_ids) == len(case_ids)
                        and isinstance(case_receipts, list)
                        and len(case_receipts) == len(case_ids)
                        and all(
                            case_receipt_is_valid(entry)
                            for entry in case_receipts
                        )
                        and [entry["case_id"] for entry in case_receipts]
                        == case_ids
                        and [
                            entry["fresh_context_id"]
                            for entry in case_receipts
                        ] == context_ids
                        and len(
                            {
                                entry["receipt_digest"]
                                for entry in case_receipts
                            }
                        ) == len(case_receipts)
                        and all(
                            entry["raw_evidence_digest"]
                            == raw_digest_by_case.get(entry["case_id"])
                            for entry in case_receipts
                        )
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
                    expected_receipt_chain = [
                        {
                            "sequence": prior_event["transition_receipt"].get(
                                "sequence"
                            ),
                            "receipt_digest": prior_event[
                                "transition_receipt"
                            ].get("receipt_digest"),
                        }
                        for prior_event in trace[:index]
                        if isinstance(prior_event, dict)
                        and isinstance(
                            prior_event.get("transition_receipt"), dict
                        )
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
                        and event.get("receipt_chain")
                        == expected_receipt_chain
                        and event.get("head_receipt_digest")
                        == (
                            expected_receipt_chain[-1]["receipt_digest"]
                            if expected_receipt_chain
                            else None
                        )
                        and artifact_inputs_match(
                            event, required_transition_records
                        )
                    )
                    if schema_valid:
                        transition_record = event
                elif artifact_type == "artifact-manifest":
                    manifested_ids = event.get("manifested_artifact_ids")
                    manifest_input_ids = event.get("input_artifact_ids")
                    prior_current_ids = {
                        artifact_id
                        for artifact_id, artifact in retained_artifacts.items()
                        if artifact_id != event.get("artifact_id")
                        and record_event_indices.get(id(artifact), index) < index
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
                        and retained_manifest_is_valid(
                            event,
                            prior_current_ids,
                            retained_artifacts,
                        )
                        and all(
                            record_schema_validity.get(
                                id(retained_artifacts[artifact_id]), False
                            )
                            for artifact_id in prior_current_ids
                        )
                        and unique_nonempty_strings(manifest_input_ids)
                        and set(manifest_input_ids) == prior_current_ids
                    )
                    if schema_valid:
                        artifact_manifest_record = event
                elif artifact_type == "terminal-manifest":
                    prior_ids = {
                        artifact_id
                        for artifact_id, artifact in retained_artifacts.items()
                        if artifact_id != event.get("artifact_id")
                        and record_event_indices.get(id(artifact), index) < index
                    }
                    terminal_input_ids = event.get("input_artifact_ids")
                    schema_valid = (
                        schema_valid
                        and release_evidence is not None
                        and record_event_indices.get(id(release_evidence), index)
                        < index
                        and retained_manifest_is_valid(
                            event,
                            prior_ids,
                            retained_artifacts,
                        )
                        and all(
                            record_schema_validity.get(
                                id(retained_artifacts[artifact_id]), False
                            )
                            for artifact_id in prior_ids
                        )
                        and unique_nonempty_strings(terminal_input_ids)
                        and set(terminal_input_ids) == prior_ids
                    )
                    if schema_valid:
                        terminal_manifest_record = event
                elif artifact_type == "category-evidence":
                    criterion_id = event.get("criterion_id")
                    category = event.get("category")
                    artifact_id = event.get("artifact_id")
                    if (
                        isinstance(criterion_id, str)
                        and criterion_id
                        and isinstance(artifact_id, str)
                        and artifact_id
                    ):
                        criterion_artifact_ids = (
                            category_evidence_ids_by_criterion.setdefault(
                                criterion_id, []
                            )
                        )
                        if criterion_artifact_ids:
                            ambiguous_category_evidence_criterion_ids.add(
                                criterion_id
                            )
                            failures.append(
                                OracleFailure(
                                    "DUPLICATE_CRITERION_EVIDENCE",
                                    index,
                                    "retained category evidence reused a criterion identity",
                                    "target-quality",
                                )
                            )
                        criterion_artifact_ids.append(artifact_id)
                        if len(criterion_artifact_ids) > 1:
                            for criterion_artifact_id in criterion_artifact_ids:
                                prior = retained_artifacts.get(
                                    criterion_artifact_id
                                )
                                if prior is not None:
                                    invalidate_artifact(prior)
                    schema_valid = (
                        schema_valid
                        and isinstance(category, str)
                        and isinstance(criterion_id, str)
                        and criterion_id
                        not in ambiguous_category_evidence_criterion_ids
                        and category_evidence_record_is_valid(
                            event,
                            current_binding(),
                            category,
                            criterion_id,
                            current_epoch=True,
                        )
                    )
                record_schema_validity[id(event)] = schema_valid
                if not schema_valid:
                    invalidate_artifact(event)
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            f"{artifact_type!r} lacked required current input bindings",
                        )
                    )
        elif event_name == "builder_conformance_recorded":
            if resolution_record is not None:
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
                    invalidate_artifact(event)
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "builder conformance lacked exact typed gate evidence",
                        )
                    )
            conformance_record = event
        elif event_name == "spec_outcome_recorded":
            if resolution_record is not None:
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
                    invalidate_artifact(event)
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
            if resolution_record is not None:
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
                    invalidate_artifact(event)
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
            if resolution_record is not None:
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
                    invalidate_artifact(event)
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
            trusted_review = (
                event.get("valid") is True
                and record_schema_validity.get(id(event), False)
                and record_lifecycle_validity.get(id(event), False)
            )
            if trusted_review:
                for finding in event.get("findings", []):
                    finding_id = finding.get("id")
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
                or event.get("workflow_id") != current_workflow_id
                or event.get("target_identity") != current_target_identity
                or any(
                    finding_binding[2] != current_candidate_revision
                    or finding_index <= current_candidate_event_index
                    or finding_binding != current_binding()
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
            register_artifact(event, index)
            category_evidence = {
                artifact.get("criterion_id"): artifact
                for artifact_id, artifact in retained_artifacts.items()
                if artifact_id != event.get("artifact_id")
                and artifact_is_current(artifact_id)
                and artifact.get("artifact_type") == "category-evidence"
                and isinstance(artifact.get("criterion_id"), str)
            }
            category_evidence_ids = {
                artifact["artifact_id"] for artifact in category_evidence.values()
            }
            rubrics = current_artifacts_of_type("rubric")
            required_ids = {
                record.get("artifact_id")
                for record in (conformance_record, scoring_review, *rubrics)
                if record is not None
                and isinstance(record.get("artifact_id"), str)
            } | category_evidence_ids
            category_results = event.get("category_results")
            criterion_results = event.get("criterion_results")
            scorecard_valid = (
                artifact_envelope_is_valid(event, "target-scorecard")
                and authority_binding_is_current(event)
                and record_binding(event) == current_binding()
                and event.get("review_id")
                == (scoring_review or {}).get("review_id")
                and event.get("categories") == list(CATEGORY_CRITERION_IDS)
                and set(category_scores) == set(CATEGORY_CRITERION_IDS)
                and all(
                    score_event.get("score") == 10
                    and score_proven
                    and record_event_indices.get(id(score_event), index) < index
                    for score_event, score_proven in category_scores.values()
                )
                and set(category_evidence) == set(CRITERION_CATEGORY)
                and len(category_evidence_ids) == 100
                and len(rubrics) == 1
                and event.get("rubric_artifact_id")
                == rubrics[0].get("artifact_id")
                and event.get("rubric_digest")
                == rubrics[0].get("rubric_digest")
                and isinstance(category_results, list)
                and len(category_results) == 10
                and [result.get("category") for result in category_results]
                == list(CATEGORY_CRITERION_IDS)
                and all(
                    result.get("score") == 10
                    and set(result.get("criterion_ids", []))
                    == set(CATEGORY_CRITERION_IDS[result["category"]])
                    and set(result.get("evidence_artifact_ids", []))
                    == {
                        category_evidence[criterion_id]["artifact_id"]
                        for criterion_id in CATEGORY_CRITERION_IDS[
                            result["category"]
                        ]
                    }
                    for result in category_results
                    if isinstance(result, dict)
                    and result.get("category") in CATEGORY_CRITERION_IDS
                )
                and isinstance(criterion_results, list)
                and len(criterion_results) == 100
                and {
                    result.get("id")
                    for result in criterion_results
                    if isinstance(result, dict)
                }
                == set(CRITERION_CATEGORY)
                and all(
                    isinstance(result, dict)
                    and result.get("category")
                    == CRITERION_CATEGORY[result.get("id")]
                    and result.get("passed") is True
                    and result.get("score") == 1
                    and result.get("frozen_parameter_id")
                    == category_evidence[result["id"]].get(
                        "frozen_parameter_id"
                    )
                    and result.get("case_id")
                    == category_evidence[result["id"]].get("case_id")
                    and result.get("evidence_artifact_id")
                    == category_evidence[result["id"]].get("artifact_id")
                    and result.get("raw_artifact_ids")
                    == category_evidence[result["id"]].get("raw_artifact_ids")
                    and result.get("trial_receipt_ids")
                    == category_evidence[result["id"]].get(
                        "trial_receipt_ids"
                    )
                    and result.get("raw_evidence_digest")
                    == category_evidence[result["id"]].get(
                        "raw_evidence_digest"
                    )
                    and result.get("trial_receipt_digest")
                    == category_evidence[result["id"]].get(
                        "trial_receipt_digest"
                    )
                    and result.get("finding_ids")
                    == category_evidence[result["id"]].get(
                        "review_finding_ids"
                    )
                    for result in criterion_results
                )
                and event.get("findings")
                == (scoring_review or {}).get("findings")
                and isinstance(event.get("repair_history"), list)
                and len(required_ids) == 103
                and consumer_inputs_are_exact(event, required_ids)
            )
            record_lifecycle_validity[id(event)] = scorecard_valid
            record_schema_validity[id(event)] = scorecard_valid
            if scorecard_valid:
                target_scorecard = event
            else:
                invalidate_artifact(event)
                failures.append(
                    OracleFailure(
                        "INVALID_TARGET_SCORECARD",
                        index,
                        "target scorecard lacked all ten scores and 100 exact criterion bindings",
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
            if target_scorecard is not None:
                failures.append(
                    OracleFailure(
                        "SCORE_AFTER_SCORECARD",
                        index,
                        "a category score followed and invalidated the sealed scorecard",
                        "target-quality",
                    )
                )
                invalidated_artifact_ids.add(
                    str(target_scorecard.get("artifact_id"))
                )
                target_scorecard = None
            scoring_review_valid = (
                scoring_review is not None
                and (
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
            )
            ten_is_proven = (
                len(criteria) == 10
                and {criterion["id"] for criterion in criteria} == expected_ids
                and scoring_review_valid
                and binding == current_binding()
                and record_identity_binding_is_current(event)
                and authority_binding_is_current(event)
                and all(
                    criterion["passed"] is True
                    and category_evidence_resolves(
                        criterion["evidence"],
                        binding,
                        event["category"],
                        criterion["id"],
                        event,
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
            if resolution_record is not None:
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
                    invalidate_artifact(event)
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
                or not record_identity_binding_is_current(event)
                or not authority_binding_is_current(event)
                or frozen_evaluation is None
                or frozen_evaluation.get("contract_digest") != current_contract
                or candidate_contract_epoch != contract_epoch
                or candidate_evaluation_epoch != evaluation_epoch
                or prerequisite_generation_invalid
                or candidate_record is None
                or not artifact_is_current(
                    str((candidate_record or {}).get("artifact_id"))
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
                conformance_is_valid(conformance_record)
                and record_schema_validity.get(id(conformance_record), False)
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
                and artifact_is_current(str(spec_outcome.get("artifact_id")))
                and record_lifecycle_validity.get(id(spec_outcome), False)
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
                and target_scorecard is not None
                and final_review is not None
                and record_lifecycle_validity.get(id(target_scorecard), False)
                and record_event_indices[id(target_scorecard)]
                < record_event_indices[id(final_review)]
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
                and artifact_is_current(str(verification.get("artifact_id")))
                and record_lifecycle_validity.get(id(verification), False)
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
                and artifact_is_current(str(release_evidence.get("artifact_id")))
                and record_lifecycle_validity.get(id(release_evidence), False)
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
            if not terminal_manifest_covers_finalization(index):
                failures.append(
                    OracleFailure(
                        "FINALIZE_WITHOUT_TERMINAL_MANIFEST",
                        index,
                        "finalization lacked a complete last-moment manifest of every retained artifact",
                    )
                )
            finalization_event_index = index
        elif event_name == "user_confirmed":
            if resolution_record is not None:
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
                    confirmed_contract = event["contract_digest"]
                    confirmed_contract_epoch = contract_epoch
                else:
                    confirmation_record = None
                    confirmed_contract = None
                    confirmed_contract_epoch = None
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
            if resolution_record is not None:
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
            if resolution_record is not None:
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
            if resolution_record is not None:
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
                rubrics = current_artifacts_of_type("rubric")
                expected_case_ids = [
                    f"case-{criterion_id.lower()}"
                    for criterion_id in sorted(CRITERION_CATEGORY)
                ]
                expected_cases = (
                    expected_evaluation_cases(
                        fixture_root,
                        sorted(CRITERION_CATEGORY),
                        current_contract,
                        rubrics[0]["artifact_id"],
                        rubrics[0]["rubric_digest"],
                    )
                    if isinstance(current_contract, str) and len(rubrics) == 1
                    else []
                )
                evaluation_valid = (
                    confirmation_precedes_freeze
                    and artifact_envelope_is_valid(event, "evaluation-pack")
                    and event.get("contract_digest") == current_contract
                    and event.get("target_snapshot") == current_snapshot
                    and evaluation_event_shape_is_valid(event)
                    and event.get("case_ids") == expected_case_ids
                    and len(event.get("cases", [])) == len(expected_case_ids)
                    and event.get("cases") == expected_cases
                    and set(event["frozen_parameter_ids"])
                    == {
                        f"parameter-{criterion_id}"
                        for criterion_id in CRITERION_CATEGORY
                    }
                    and len(rubrics) == 1
                    and event.get("rubric_artifact_id")
                    == rubrics[0].get("artifact_id")
                    and event.get("rubric_digest")
                    == rubrics[0].get("rubric_digest")
                    and artifact_inputs_match(
                        event, (contract_record, confirmation_record, *rubrics)
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
                    invalidate_artifact(event)
                    frozen_evaluation = None
                    frozen_contract_epoch = None
                    frozen_snapshot_epoch = None
                    failures.append(
                        OracleFailure(
                            "INVALID_ARTIFACT_SCHEMA",
                            index,
                            "evaluation pack did not bind exact retained case contracts",
                        )
                    )
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
            roles = {lane.get("role") for lane in research_lanes or []}
            if len(research_lanes or []) != 3 or roles != RESEARCH_ROLES:
                failures.append(
                    OracleFailure(
                        "RESEARCH_LANE_COUNT",
                        index,
                        "candidate entry requires exactly the three bounded research roles",
                    )
                )
            else:
                context_ids = {
                    lane.get("context_id") for lane in research_lanes or []
                }
                if len(context_ids) != 3:
                    failures.append(
                        OracleFailure(
                            "RESEARCH_NOT_INDEPENDENT",
                            index,
                            "the three research records do not have independent contexts",
                        )
                    )
                if any(
                    lane.get("blind") is not True
                    or bool(lane.get("contaminated_by"))
                    for lane in research_lanes or []
                ):
                    failures.append(
                        OracleFailure(
                            "RESEARCH_CONTAMINATED",
                            index,
                            "a research lane observed forbidden candidate or sibling context",
                        )
                    )
                if any(
                    type(lane.get("evidence_cards")) is not int
                    or lane["evidence_cards"] < 1
                    for lane in research_lanes or []
                ):
                    failures.append(
                        OracleFailure(
                            "RESEARCH_MISSING_EVIDENCE",
                            index,
                            "a required research lane retained no direct evidence card",
                        )
                    )
            candidate_revision = event["candidate_revision"]
            if (
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
                    and event.get("workflow_id") == current_workflow_id
                    and event.get("target_identity") == current_target_identity
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
            register_artifact(event, index)
            auxiliary_types = (
                "host-rules",
                "preserved-regressions",
                "confirmed-contract",
                "rubric",
            )
            auxiliary_records = {
                artifact_type: current_artifacts_of_type(artifact_type)
                for artifact_type in auxiliary_types
            }
            prerequisite_records = (
                resolution_record,
                *auxiliary_records["host-rules"],
                baseline_report,
                *auxiliary_records["preserved-regressions"],
                research_pack_record,
                sieve_record,
                design_record,
                contract_record,
                confirmation_record,
                *auxiliary_records["confirmed-contract"],
                *auxiliary_records["rubric"],
                frozen_evaluation,
                authority_record,
            )
            authority_valid = authority_binding_is_current(event)
            candidate_path_valid = candidate_path_contract_is_valid(event)
            if not authority_valid:
                failures.append(
                    OracleFailure(
                        "CANDIDATE_WITHOUT_AUTHORITY",
                        index,
                        "candidate effects lacked validated resolution-derived authority",
                    )
                )
            if not candidate_path_valid:
                failures.append(
                    OracleFailure(
                        "UNAUTHORIZED_PRODUCTION_WRITE",
                        index,
                        "candidate edit did not bind a trusted isolated owned path",
                    )
                )
            prerequisite_chain_valid = (
                all(
                    len(auxiliary_records[artifact_type]) == 1
                    for artifact_type in auxiliary_types
                )
                and all(record is not None for record in prerequisite_records)
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
                and authority_valid
                and candidate_path_valid
                and event.get("revision") == candidate_revision
                and isinstance(current_candidate_implementer, str)
                and bool(current_candidate_implementer)
                and current_candidate_implementer_role == "candidate-implementer"
                and not actor_has_barred_role(
                    current_candidate_implementer,
                    IMPLEMENTER_BARRED_ACTOR_ROLES,
                )
                and artifact_inputs_match(
                    event,
                    (
                        authority_record,
                        contract_record,
                        confirmation_record,
                        frozen_evaluation,
                    ),
                )
            )
            record_schema_validity[id(event)] = prerequisite_chain_valid
            if prerequisite_chain_valid:
                candidate_record = event
                prerequisite_generation_invalid = False
            else:
                candidate_record = None
                invalidate_artifact(event)
                failures.append(
                    OracleFailure(
                        "STALE_PREREQUISITE_GENERATION",
                        index,
                        "candidate entry reused or omitted identity, snapshot, baseline, confirmation, evaluation, or authority evidence",
                    )
                )

    finalization_indices = [
        index
        for index, event in enumerate(trace)
        if isinstance(event, dict) and event.get("event") == "finalized"
    ]
    terminal_intent = bool(finalization_indices) or any(
        isinstance(event, dict)
        and (
            event.get("event") == "release_evidence_retained"
            or event.get("artifact_type") == "terminal-manifest"
        )
        for event in trace
    )
    if terminal_intent and not finalization_indices:
        failures.append(
            OracleFailure(
                "MISSING_FINALIZATION",
                len(trace),
                "an accepted finalization trace lacked its terminal finalized transition",
            )
        )
    elif len(finalization_indices) > 1:
        failures.append(
            OracleFailure(
                "DUPLICATE_FINALIZATION",
                finalization_indices[1],
                "a finalization trace contained more than one terminal finalized transition",
            )
        )
    elif (
        len(finalization_indices) == 1
        and finalization_indices[0] in boundary_invalid_indices
    ):
        failures.append(
            OracleFailure(
                "INVALID_FINALIZATION_SCHEMA",
                finalization_indices[0],
                "the sole finalized transition did not cross the validated schema boundary",
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
    def test_strict_candidate_boundary_has_no_unbound_compatibility_mode(self) -> None:
        """Every fixture candidate must cross the same fully bound schema boundary."""

        trace = load_traces("visible")["accepted_exact_improve"]
        self.assertEqual(evaluate_trace(trace, BUILDER_FIXTURES), ())
        self.assertTrue(
            all(
                isinstance(event.get("workflow_id"), str)
                and WORKFLOW_ID_RE.fullmatch(event["workflow_id"])
                and isinstance(event.get("target_identity"), str)
                and isinstance(event.get("target_snapshot"), str)
                and re.fullmatch(r"[0-9a-f]{64}", event["target_snapshot"])
                and isinstance(event.get("transition_receipt"), dict)
                for event in trace
            )
        )

        mutant = copy.deepcopy(trace)
        candidate = next(
            event for event in mutant if event.get("event") == "candidate_edit"
        )
        candidate.pop("workflow_id")
        codes = {
            failure.code for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_EVENT_SCHEMA", codes)
        self.assertTrue(
            {"INVALID_ARTIFACT_SCHEMA", "STALE_PREREQUISITE_GENERATION"}
            & codes
        )

        repair_trace = load_traces("frozen-validation")[
            "accepted_negative_review_repair"
        ]
        self.assertEqual(evaluate_trace(repair_trace, BUILDER_FIXTURES), ())
        self.assertEqual(
            sum(event.get("event") == "candidate_edit" for event in repair_trace),
            2,
        )
        self.assertEqual(
            sum(
                event.get("event") == "review_recorded"
                and event.get("verdict") == "not ready"
                for event in repair_trace
            ),
            1,
        )
        self.assertEqual(
            sum(event.get("event") == "repair_completed" for event in repair_trace),
            1,
        )

    def test_event_schema_fails_closed_and_requires_one_terminal_finalization(self) -> None:
        """Malformed dispatch and terminal cardinality produce codes, never exceptions."""

        accepted = accepted_finalization_trace()
        self.assertEqual(evaluate_trace(accepted, BUILDER_FIXTURES), ())

        missing_final = copy.deepcopy(accepted[:-1])
        duplicate_final = copy.deepcopy(accepted)
        duplicate_final.append(copy.deepcopy(accepted[-1]))
        unknown = copy.deepcopy(accepted)
        unknown.insert(-1, {"event": "unsupported_transition"})
        malformed = copy.deepcopy(accepted)
        malformed.insert(-1, {})
        malformed_artifact_type = copy.deepcopy(accepted)
        next(
            event
            for event in malformed_artifact_type
            if event.get("event") == "artifact_retained"
        )["artifact_type"] = []
        missing_manifest = copy.deepcopy(accepted)
        missing_manifest[0]["target_manifest"] = "targets/missing/manifest.json"
        missing_manifest = seal_trace(missing_manifest)

        for mutant, expected in (
            (missing_final, "MISSING_FINALIZATION"),
            (duplicate_final, "DUPLICATE_FINALIZATION"),
            (unknown, "UNKNOWN_EVENT"),
            (malformed, "INVALID_EVENT_SCHEMA"),
            (malformed_artifact_type, "INVALID_ARTIFACT_ENVELOPE"),
            (missing_manifest, "INVALID_RESOLUTION_MANIFEST"),
        ):
            with self.subTest(expected=expected):
                try:
                    codes = {
                        failure.code
                        for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
                    }
                except (KeyError, TypeError, ValueError) as error:
                    self.fail(f"schema boundary leaked {type(error).__name__}: {error}")
                self.assertIn(expected, codes)

    def test_artifact_envelopes_and_transition_receipts_are_canonical(self) -> None:
        """Artifacts and transitions carry independently verifiable SHA-256 metadata."""

        trace = accepted_finalization_trace()
        self.assertEqual(evaluate_trace(trace, BUILDER_FIXTURES), ())
        previous_receipt_digest: str | None = None
        artifact_events = [event for event in trace if "artifact_id" in event]
        self.assertTrue(artifact_events)
        for sequence, event in enumerate(trace):
            receipt = event["transition_receipt"]
            self.assertEqual(receipt["sequence"], sequence)
            self.assertEqual(receipt["prior_receipt_digest"], previous_receipt_digest)
            receipt_body = {
                key: value for key, value in receipt.items() if key != "receipt_digest"
            }
            encoded = (
                json.dumps(
                    receipt_body,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            self.assertEqual(
                receipt["receipt_digest"], hashlib.sha256(encoded).hexdigest()
            )
            previous_receipt_digest = receipt["receipt_digest"]

        required_envelope_fields = {
            "artifact_id",
            "artifact_type",
            "workflow_id",
            "target_identity",
            "mode",
            "created_stage",
            "created_sequence",
            "producer",
            "created_at",
            "input_bindings",
            "payload_path",
            "payload_digest",
            "limitations",
            "envelope_digest",
        }
        self.assertTrue(
            all(
                set(event["artifact_envelope"])
                == required_envelope_fields
                | (
                    {"manifest_digest"}
                    if event["artifact_type"] in MANIFEST_ARTIFACT_TYPES
                    else set()
                )
                for event in artifact_events
            )
        )

        boolean_generation = copy.deepcopy(trace)
        boolean_generation[0]["identity_generation"] = True
        list_snapshot = copy.deepcopy(trace)
        list_snapshot[0]["target_snapshot"] = ["not-a-digest"]
        for mutant in (boolean_generation, list_snapshot):
            codes = {
                failure.code for failure in evaluate_trace(mutant, BUILDER_FIXTURES)
            }
            self.assertIn("INVALID_EVENT_SCHEMA", codes)

    def test_resolution_authority_precedes_and_binds_candidate_effects(self) -> None:
        """Resolution-derived authority is a candidate and downstream dependency."""

        trace = accepted_finalization_trace()
        resolution = trace[0]
        authority_record = next(
            event
            for event in trace
            if event.get("artifact_type") == "authority-record"
        )
        self.assertEqual(
            authority_record["resolution_authority_digest"],
            canonical_digest(resolution["authority"]),
        )
        authority_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("artifact_type") == "authority-record"
        )
        authority = trace.pop(authority_index)
        trial_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("artifact_type") == "trial-pack"
        )
        trace.insert(trial_index + 1, authority)

        codes = {failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)}
        self.assertIn("CANDIDATE_WITHOUT_AUTHORITY", codes)
        self.assertIn("FINALIZATION_BINDING_MISMATCH", codes)

        changed_authority = accepted_finalization_trace()
        changed_authority[0]["authority"]["candidate_effects"] = []
        changed_authority = seal_trace(changed_authority)
        changed_codes = {
            failure.code
            for failure in evaluate_trace(changed_authority, BUILDER_FIXTURES)
        }
        self.assertIn("CANDIDATE_WITHOUT_AUTHORITY", changed_codes)

    def test_reviews_consume_validated_artifact_ids_and_digests(self) -> None:
        """Review access binds concrete retained artifacts, not claimed type labels."""

        trace = accepted_finalization_trace()
        required_types = {
            "confirmed-contract",
            "host-rules",
            "candidate-diff",
            "candidate-manifest",
            "evaluation-pack",
            "raw-trial-evidence",
            "preserved-regressions",
            "artifact-manifest",
            "rubric",
        }
        retained_types = {
            event.get("artifact_type") for event in trace if "artifact_id" in event
        }
        self.assertTrue(required_types.issubset(retained_types))
        for review in (
            event for event in trace if event.get("event") == "review_recorded"
        ):
            bindings = review.get("review_artifact_bindings")
            self.assertEqual(
                {binding["type"] for binding in bindings}, required_types
            )
            self.assertTrue(
                all(re.fullmatch(r"[0-9a-f]{64}", binding["digest"]) for binding in bindings)
            )

        mutant = [
            copy.deepcopy(event)
            for event in trace
            if event.get("artifact_type")
            not in {
                "host-rules",
                "candidate-diff",
                "candidate-manifest",
                "preserved-regressions",
                "rubric",
            }
        ]
        codes = {failure.code for failure in evaluate_trace(mutant, BUILDER_FIXTURES)}
        self.assertIn("INVALID_REVIEW_SCHEMA", codes)
        self.assertIn("FINALIZE_WITHOUT_READY_REVIEW", codes)

    def test_each_criterion_and_scorecard_have_exact_complete_evidence(self) -> None:
        """One exact evidence record and one scorecard result are required per criterion."""

        trace = accepted_finalization_trace()
        evidence = [
            event
            for event in trace
            if event.get("artifact_type") == "category-evidence"
        ]
        self.assertEqual(len(evidence), 100)
        self.assertEqual(
            {event["criterion_id"] for event in evidence}, set(CRITERION_CATEGORY)
        )
        self.assertEqual(len({event["case_id"] for event in evidence}), 100)
        self.assertEqual(len({event["frozen_parameter_id"] for event in evidence}), 100)
        scorecard = next(
            event
            for event in trace
            if event.get("event") == "target_scorecard_recorded"
        )
        self.assertEqual(len(scorecard["criterion_results"]), 100)
        self.assertEqual(
            {result["id"] for result in scorecard["criterion_results"]},
            set(CRITERION_CATEGORY),
        )

        collapsed = copy.deepcopy(trace)
        for record in collapsed:
            if record.get("artifact_type") == "category-evidence":
                record["case_id"] = "visible-case-1"
        collapsed_codes = {
            failure.code for failure in evaluate_trace(collapsed, BUILDER_FIXTURES)
        }
        self.assertIn("FALSE_CATEGORY_TEN", collapsed_codes)

        late_score = copy.deepcopy(trace)
        scorecard_index = next(
            index
            for index, event in enumerate(late_score)
            if event.get("event") == "target_scorecard_recorded"
        )
        score = next(
            copy.deepcopy(event)
            for event in late_score
            if event.get("event") == "category_scored"
        )
        late_score.insert(scorecard_index + 1, score)
        late_codes = {
            failure.code for failure in evaluate_trace(late_score, BUILDER_FIXTURES)
        }
        self.assertIn("SCORE_AFTER_SCORECARD", late_codes)

    def test_terminal_manifest_and_finalization_are_closed_world(self) -> None:
        """The terminal manifest seals every retained artifact through finalization."""

        trace = accepted_finalization_trace()
        terminal_manifest = next(
            event
            for event in trace
            if event.get("artifact_type") == "terminal-manifest"
        )
        retained_before_manifest = {
            event["artifact_id"]
            for event in trace[: trace.index(terminal_manifest)]
            if "artifact_id" in event
        }
        self.assertEqual(
            {entry["artifact_id"] for entry in terminal_manifest["files"]},
            retained_before_manifest,
        )
        self.assertTrue(
            all(
                entry["byte_count"] > 0
                and re.fullmatch(r"[0-9a-f]{64}", entry["payload_digest"])
                and re.fullmatch(r"[0-9a-f]{64}", entry["envelope_digest"])
                for entry in terminal_manifest["files"]
            )
        )

        late = copy.deepcopy(trace)
        final_index = next(
            index for index, event in enumerate(late) if event.get("event") == "finalized"
        )
        late.insert(
            final_index,
            {
                "event": "artifact_retained",
                "artifact_id": "unmanifested-late-proof",
                "artifact_type": "category-evidence",
            },
        )
        codes = {failure.code for failure in evaluate_trace(late, BUILDER_FIXTURES)}
        self.assertIn("FINALIZE_WITHOUT_TERMINAL_MANIFEST", codes)

    def test_candidate_edit_before_confirmation_and_freeze_is_rejected(self) -> None:
        """Regression: pressure must not move candidate work ahead of acceptance gates."""

        trace = load_traces("visible")["mutant_candidate_edit_too_early"]

        failures = evaluate_trace(trace, BUILDER_FIXTURES)

        self.assertTrue(
            {"CANDIDATE_BEFORE_CONFIRMATION", "CANDIDATE_BEFORE_EVALUATION_FREEZE"}
            .issubset({failure.code for failure in failures})
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
                self.assertTrue(
                    expected_codes.issubset(
                        {failure.code for failure in failures}
                    )
                )

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
                self.assertTrue(
                    expected_codes.issubset(
                        {failure.code for failure in failures}
                    )
                )

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
                "CANDIDATE_BEFORE_CONFIRMATION",
                "CANDIDATE_BEFORE_EVALUATION_FREEZE",
            },
            "mutant_stale_target_snapshot": {
                "CANDIDATE_BEFORE_CONFIRMATION",
                "CANDIDATE_BEFORE_EVALUATION_FREEZE",
            },
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertTrue(
                    expected_codes.issubset(
                        {failure.code for failure in failures}
                    )
                )

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
                self.assertTrue(
                    expected_codes.issubset(
                        {failure.code for failure in failures}
                    )
                )

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
            "mutant_ten_with_material_finding": {"FALSE_CATEGORY_TEN"},
        }

        for trace_id, expected_codes in expected.items():
            with self.subTest(trace=trace_id):
                failures = evaluate_trace(traces[trace_id], BUILDER_FIXTURES)
                self.assertTrue(
                    expected_codes.issubset(
                        {failure.code for failure in failures}
                    )
                )

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

        self.assertTrue(
            {
                "FINALIZE_WITHOUT_CONFORMANCE",
                "FINALIZE_WITHOUT_READY_REVIEW",
                "FINALIZE_WITHOUT_SPEC_PASS",
                "FINALIZE_WITHOUT_TEN_SCORES",
                "FINALIZE_WITHOUT_VERIFICATION",
                "FINALIZE_WITHOUT_RELEASE_EVIDENCE",
                "FINALIZATION_BINDING_MISMATCH",
            }.issubset({failure.code for failure in failures})
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
        late_finding = seal_trace(late_finding)
        final_review = next(
            event
            for event in late_finding
            if event["event"] == "review_recorded" and event.get("phase") == "final"
        )
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
        repaired = seal_trace(repaired)
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
        trace = seal_trace(trace)

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
        trace[final_review_index]["findings"] = [
            evidence_bound_finding(
                trace, "duplicate-finding", "High", "RE1"
            )
        ]
        trace[final_review_index]["phase"] = "repair"
        trace = seal_trace(trace)

        failure_codes = {
            failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)
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
            criterion["evidence"] = ["accepted-sa1-proof"]

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
                == "accepted-wf1-proof"
            )
        )
        replacement["category"] = "safety"
        replacement["criterion_id"] = "SA1"
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
        workflow_proof["artifact_id"] = "accepted-wf1-proof"
        workflow_score = next(
            event
            for event in second_revision
            if event["event"] == "category_scored"
            and event["category"] == "workflow quality"
        )
        for criterion in workflow_score["criteria"]:
            criterion["evidence"] = ["accepted-wf1-proof"]
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
                self.assertTrue(
                    {
                        "FINALIZATION_BINDING_MISMATCH",
                        "INVALID_FINALIZATION_SCHEMA",
                    }
                    & failure_codes
                )
                self.assertTrue(
                    {"FINALIZE_WITHOUT_TEN_SCORES", "INVALID_FINALIZATION_SCHEMA"}
                    & failure_codes
                )

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
            "frozen_parameter_id",
            "case_id",
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
        prior_independent_role = seal_trace(prior_independent_role)
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
        valid_low = seal_trace(valid_low)
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
        masks_valid = seal_trace(masks_valid)

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

    def test_resolution_authority_not_caller_booleans_controls_effects(self) -> None:
        """A resealed caller flag cannot authorize a forbidden write or delivery."""

        trace = accepted_finalization_trace()
        final_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("event") == "finalized"
        )
        trace[final_index:final_index] = [
            {
                "event": "write",
                "destination_scope": "production-target",
                "effect": "production-target-write",
                "authority": True,
                "actor": "release-manager-v1",
                "path": "production/fixture-terse-summary/SKILL.md",
            },
            {
                "event": "delivery_attempt",
                "destination_scope": "production-target",
                "effect": "publish-production-target",
                "authority": True,
                "actor": "release-manager-v1",
            },
        ]
        trace = seal_trace(trace)

        codes = {failure.code for failure in evaluate_trace(trace, BUILDER_FIXTURES)}

        self.assertIn("UNAUTHORIZED_PRODUCTION_WRITE", codes)
        self.assertIn("UNAUTHORIZED_DELIVERY", codes)

    def test_resolution_cannot_expand_frozen_fixture_authority(self) -> None:
        """A self-consistent caller grant cannot broaden trusted fixture policy."""

        trace = accepted_finalization_trace()
        authority = trace[0]["authority"]
        authority["allowed_writes"].append("production-target")
        authority["delegation"]["candidate-implementer"].append(
            "production-target-write"
        )
        authority["candidate_effects"].append("production-target-write")
        authority["delivery_effects"].append("publish-production-target")
        authority_record = next(
            event
            for event in trace
            if event.get("artifact_type") == "authority-record"
        )
        authority_record["authorized_candidate_effects"] = list(
            authority["candidate_effects"]
        )
        authority_record["authorized_delivery_scope"] = "fixture-terse-summary"
        authority_record["authorized_delivery_effects"] = list(
            authority["delivery_effects"]
        )
        authority_record["resolution_authority_digest"] = canonical_digest(
            authority
        )
        final_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("event") == "finalized"
        )
        trace[final_index:final_index] = [
            {
                "event": "write",
                "destination_scope": "production-target",
                "effect": "production-target-write",
                "actor": "candidate-implementer-v1",
                "path": "production/fixture-terse-summary/SKILL.md",
            },
            {
                "event": "delivery_attempt",
                "destination_scope": "fixture-terse-summary",
                "effect": "publish-production-target",
                "actor": "candidate-implementer-v1",
            },
        ]

        codes = {
            failure.code
            for failure in evaluate_trace(seal_trace(trace), BUILDER_FIXTURES)
        }

        self.assertIn("INVALID_RESOLUTION_AUTHORITY", codes)
        self.assertIn("UNAUTHORIZED_PRODUCTION_WRITE", codes)
        self.assertIn("UNAUTHORIZED_DELIVERY", codes)

    def test_resolution_locator_with_nul_fails_closed(self) -> None:
        """Invalid path bytes cannot escape resolution as a ValueError."""

        trace = accepted_finalization_trace()
        trace[0]["target_manifest"] = (
            "targets/improve-exact/manifest.json\x00suffix"
        )

        try:
            codes = {
                failure.code
                for failure in evaluate_trace(
                    seal_trace(trace), BUILDER_FIXTURES
                )
            }
        except (OSError, RuntimeError, ValueError) as error:
            self.fail(
                f"invalid resolution locator leaked {type(error).__name__}: {error}"
            )

        self.assertIn("INVALID_RESOLUTION_MANIFEST", codes)

    def test_candidate_manifest_uses_shared_header_contract(self) -> None:
        """Candidate manifests cannot self-seal false counts or overflow."""

        trace = accepted_finalization_trace()
        manifest_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("artifact_type") == "candidate-manifest"
        )
        trace = trace[: manifest_index + 1]
        manifest = trace[-1]
        manifest.update(
            {
                "declared_item_count": 99,
                "observed_item_count": 98,
                "observed_byte_count": 0,
                "overflow": {"ignored": True},
            }
        )

        codes = {
            failure.code
            for failure in evaluate_trace(
                reseal_declared_trace(trace), BUILDER_FIXTURES
            )
        }

        self.assertIn("INVALID_ARTIFACT_SCHEMA", codes)

    def test_payload_paths_are_globally_unique_before_manifests(self) -> None:
        """Two retained artifacts cannot claim the same normalized payload path."""

        trace = accepted_finalization_trace()
        manifest_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("artifact_type") == "candidate-manifest"
        )
        trace = trace[: manifest_index + 1]
        artifact_events = [
            event
            for event in trace
            if isinstance(event.get("artifact_envelope"), dict)
        ]
        artifact_events[1]["artifact_envelope"]["payload_path"] = (
            artifact_events[0]["artifact_envelope"]["payload_path"]
        )

        codes = {
            failure.code
            for failure in evaluate_trace(
                reseal_declared_trace(trace), BUILDER_FIXTURES
            )
        }

        self.assertIn("INVALID_ARTIFACT_ENVELOPE", codes)

    def test_resolution_authority_allows_bound_isolated_candidate_write(self) -> None:
        """The canonical resolution grants its declared isolated candidate effect."""

        trace = accepted_finalization_trace()
        final_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("event") == "finalized"
        )
        trace.insert(
            final_index,
            {
                "event": "write",
                "destination_scope": "isolated-candidate",
                "effect": "isolated-candidate-write",
                "actor": "candidate-implementer-v1",
                "path": next(
                    event["path"]
                    for event in trace
                    if event.get("event") == "candidate_edit"
                ),
            },
        )

        self.assertEqual(evaluate_trace(seal_trace(trace), BUILDER_FIXTURES), ())

    def test_evaluation_requires_cases_rubric_and_unique_case_evidence(self) -> None:
        """Empty cases, a missing rubric, and duplicate evidence cannot finalize."""

        trace = accepted_finalization_trace()
        evaluation = next(
            event for event in trace if event.get("event") == "evaluation_frozen"
        )
        evaluation["cases"] = []
        evaluation["rubric_artifact_id"] = "missing-rubric"
        evaluation["rubric_digest"] = "f" * 64
        raw = next(
            event
            for event in trace
            if event.get("artifact_type") == "raw-trial-evidence"
        )
        raw["raw_artifact_digests"] = ["a" * 64, "a" * 64, "a" * 64]
        raw["case_evidence"] = [
            {
                "case_id": case_id,
                "raw_request_digest": "c" * 64,
                "raw_evidence_digest": "a" * 64,
            }
            for case_id in raw["case_ids"]
        ]
        receipt = next(
            event
            for event in trace
            if event.get("artifact_type") == "trial-receipt"
        )
        receipt["case_receipts"] = [
            {
                "case_id": case_id,
                "raw_evidence_digest": "a" * 64,
                "receipt_digest": "b" * 64,
                "fresh_context_id": context_id,
            }
            for case_id, context_id in zip(
                receipt["case_ids"], receipt["fresh_context_ids"], strict=True
            )
        ]

        codes = {
            failure.code
            for failure in evaluate_trace(seal_trace(trace), BUILDER_FIXTURES)
        }

        self.assertIn("INVALID_ARTIFACT_SCHEMA", codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", codes)

    def test_category_evidence_joins_exact_case_raw_and_receipt(self) -> None:
        """A criterion cannot cite aggregate labels for another frozen case."""

        trace = accepted_finalization_trace()
        raw = next(
            event
            for event in trace
            if event.get("artifact_type") == "raw-trial-evidence"
        )
        receipt = next(
            event
            for event in trace
            if event.get("artifact_type") == "trial-receipt"
        )
        raw_by_case = {
            case_id: hashlib.sha256(f"raw:{case_id}".encode()).hexdigest()
            for case_id in raw["case_ids"]
        }
        evaluation = next(
            event for event in trace if event.get("event") == "evaluation_frozen"
        )
        request_by_case = {
            case["id"]: case["raw_request_digest"]
            for case in evaluation["cases"]
        }
        receipt_by_case = {
            case_id: hashlib.sha256(f"receipt:{case_id}".encode()).hexdigest()
            for case_id in receipt["case_ids"]
        }
        raw["raw_artifact_digests"] = list(raw_by_case.values())
        raw["case_evidence"] = [
            {
                "case_id": case_id,
                "raw_request_digest": request_by_case[case_id],
                "raw_evidence_digest": raw_by_case[case_id],
            }
            for case_id in raw["case_ids"]
        ]
        receipt["case_receipts"] = [
            {
                "case_id": case_id,
                "raw_evidence_digest": raw_by_case[case_id],
                "receipt_digest": receipt_by_case[case_id],
                "fresh_context_id": context_id,
            }
            for case_id, context_id in zip(
                receipt["case_ids"], receipt["fresh_context_ids"], strict=True
            )
        ]
        evidence = [
            event
            for event in trace
            if event.get("artifact_type") == "category-evidence"
        ]
        for record in evidence:
            record["raw_evidence_digest"] = raw_by_case[record["case_id"]]
            record["trial_receipt_digest"] = receipt_by_case[record["case_id"]]
        first, second = evidence[:2]
        first["raw_evidence_digest"] = raw_by_case[second["case_id"]]

        codes = {
            failure.code
            for failure in evaluate_trace(seal_trace(trace), BUILDER_FIXTURES)
        }

        self.assertIn("FALSE_CATEGORY_TEN", codes)
        self.assertIn("FINALIZE_WITHOUT_TEN_SCORES", codes)

    def test_hostile_nested_values_return_deterministic_failure_codes(self) -> None:
        """Unhashable nested values and incomplete manifests never escape the oracle."""

        for field in ("partitions", "case_ids", "frozen_parameter_ids"):
            with self.subTest(field=field):
                trace = accepted_finalization_trace()
                evaluation = next(
                    event
                    for event in trace
                    if event.get("event") == "evaluation_frozen"
                )
                evaluation[field] = [{"hostile": True}]
                try:
                    codes = {
                        failure.code
                        for failure in evaluate_trace(
                            seal_trace(trace), BUILDER_FIXTURES
                        )
                    }
                except (KeyError, TypeError, ValueError) as error:
                    self.fail(
                        f"nested {field} leaked {type(error).__name__}: {error}"
                    )
                self.assertIn("INVALID_EVENT_SCHEMA", codes)

        with tempfile.TemporaryDirectory() as temporary:
            fixture_root = Path(temporary)
            (fixture_root / "manifest.json").write_text("{}\n", encoding="utf-8")
            trace = accepted_finalization_trace()
            trace[0]["target_manifest"] = "manifest.json"
            try:
                codes = {
                    failure.code
                    for failure in evaluate_trace(seal_trace(trace), fixture_root)
                }
            except (KeyError, TypeError, ValueError) as error:
                self.fail(
                    f"incomplete resolution manifest leaked {type(error).__name__}: {error}"
                )
            self.assertIn("INVALID_RESOLUTION_MANIFEST", codes)

    def test_manifests_reject_unsafe_entries_and_unbound_payload_paths(self) -> None:
        """Canonical resealing cannot legitimize traversal or forged file metadata."""

        candidate_trace = accepted_finalization_trace()
        candidate_manifest = next(
            event
            for event in candidate_trace
            if event.get("artifact_type") == "candidate-manifest"
        )
        candidate_manifest["files"] = [
            {
                "path": "../../production/SKILL.md",
                "media_kind": "",
                "byte_count": -1,
                "digest": "c" * 64,
            }
        ]
        candidate_codes = {
            failure.code
            for failure in evaluate_trace(
                seal_trace(candidate_trace), BUILDER_FIXTURES
            )
        }
        self.assertIn("INVALID_ARTIFACT_SCHEMA", candidate_codes)
        self.assertIn("FINALIZE_WITHOUT_TERMINAL_MANIFEST", candidate_codes)

        terminal_trace = accepted_finalization_trace()
        terminal = next(
            event
            for event in terminal_trace
            if event.get("artifact_type") == "terminal-manifest"
        )
        terminal["files"][0]["path"] = "../../outside/forged.json"
        terminal_trace = reseal_declared_trace(terminal_trace)
        terminal_codes = {
            failure.code
            for failure in evaluate_trace(terminal_trace, BUILDER_FIXTURES)
        }
        self.assertIn("INVALID_ARTIFACT_SCHEMA", terminal_codes)
        self.assertIn("FINALIZE_WITHOUT_TERMINAL_MANIFEST", terminal_codes)

    def test_category_evidence_duplicate_identity_fails_in_either_order(self) -> None:
        """Malformed duplicate criterion proofs cannot hide behind insertion order."""

        for position in ("before", "after"):
            with self.subTest(position=position):
                trace = accepted_finalization_trace()
                genuine_index = next(
                    index
                    for index, event in enumerate(trace)
                    if event.get("artifact_type") == "category-evidence"
                )
                bogus = copy.deepcopy(trace[genuine_index])
                bogus.update(
                    {
                        "artifact_id": f"bogus-duplicate-{position}",
                        "frozen_parameter_id": "parameter-bogus",
                        "case_id": "case-bogus",
                        "raw_artifact_ids": [],
                        "trial_receipt_ids": [],
                        "raw_evidence_digest": "f" * 64,
                        "trial_receipt_digest": "e" * 64,
                        "review_finding_ids": [],
                        "review_id": "bogus-review",
                        "input_artifact_ids": [],
                    }
                )
                insertion_index = genuine_index + (position == "after")
                trace.insert(insertion_index, bogus)

                codes = {
                    failure.code
                    for failure in evaluate_trace(
                        seal_trace(trace), BUILDER_FIXTURES
                    )
                }

                self.assertIn("DUPLICATE_CRITERION_EVIDENCE", codes)
                self.assertIn("INVALID_ARTIFACT_SCHEMA", codes)

    def test_every_manifest_requires_the_exact_shared_header_keys(self) -> None:
        """Null-valued header fields remain required on every manifest kind."""

        for artifact_type in MANIFEST_ARTIFACT_TYPES:
            with self.subTest(artifact_type=artifact_type):
                trace = accepted_finalization_trace()
                manifest = next(
                    event
                    for event in trace
                    if event.get("artifact_type") == artifact_type
                )
                manifest.pop("overflow")

                codes = {
                    failure.code
                    for failure in evaluate_trace(
                        reseal_declared_trace(trace), BUILDER_FIXTURES
                    )
                }

                self.assertIn("INVALID_ARTIFACT_SCHEMA", codes)

    def test_resolution_binds_snapshot_and_mode_to_exact_target_manifest(self) -> None:
        """Caller-selected snapshot and mode cannot replace observed target facts."""

        forged_snapshot = accepted_finalization_trace()
        forged_snapshot[0]["target_snapshot"] = "forged-initial-snapshot"
        snapshot_codes = {
            failure.code
            for failure in evaluate_trace(
                seal_trace(forged_snapshot), BUILDER_FIXTURES
            )
        }
        self.assertIn("INVALID_ARTIFACT_SCHEMA", snapshot_codes)

        forged_mode = accepted_finalization_trace()
        forged_mode[0].update(
            {
                "target_manifest": "targets/non-git-create/manifest.json",
                "target_snapshot": "fixture-create-snapshot-v1",
                "selected_mode": "improve",
            }
        )
        mode_codes = {
            failure.code
            for failure in evaluate_trace(
                seal_trace(
                    forged_mode,
                    target_identity="fixture-release-notes",
                    mode="improve",
                ),
                BUILDER_FIXTURES,
            )
        }
        self.assertIn("INVALID_ARTIFACT_SCHEMA", mode_codes)

    def test_authorized_write_labels_do_not_authorize_unowned_paths(self) -> None:
        """An isolated-write capability is valid only for a declared owned path."""

        hostile_paths = {
            "missing": None,
            "absolute": "/production/fixture-terse-summary/SKILL.md",
            "traversal": "candidate/../../production/SKILL.md",
            "out-of-scope": "production/fixture-terse-summary/SKILL.md",
        }
        for name, path in hostile_paths.items():
            with self.subTest(path_kind=name):
                trace = accepted_finalization_trace()
                final_index = next(
                    index
                    for index, event in enumerate(trace)
                    if event.get("event") == "finalized"
                )
                write = {
                    "event": "write",
                    "destination_scope": "isolated-candidate",
                    "effect": "isolated-candidate-write",
                    "actor": "candidate-implementer-v1",
                }
                if path is not None:
                    write["path"] = path
                trace.insert(final_index, write)

                codes = {
                    failure.code
                    for failure in evaluate_trace(
                        seal_trace(trace), BUILDER_FIXTURES
                    )
                }

                self.assertTrue(
                    {"INVALID_EVENT_SCHEMA", "UNAUTHORIZED_PRODUCTION_WRITE"}
                    & codes
                )

    def test_candidate_edit_cannot_self_declare_production_write_scope(self) -> None:
        """The candidate record itself must bind one trusted isolated owned path."""

        trace = accepted_finalization_trace()
        candidate = next(
            event for event in trace if event.get("event") == "candidate_edit"
        )
        candidate.update(
            {
                "write_scope": "production-target",
                "path": "/production/fixture-terse-summary/SKILL.md",
                "isolated_locator": "/production/fixture-terse-summary",
                "owned_paths": [
                    "/production/fixture-terse-summary/SKILL.md"
                ],
            }
        )

        codes = {
            failure.code
            for failure in evaluate_trace(seal_trace(trace), BUILDER_FIXTURES)
        }

        self.assertTrue(
            {"INVALID_EVENT_SCHEMA", "UNAUTHORIZED_PRODUCTION_WRITE"} & codes
        )

    def test_inactive_run_states_block_downstream_finalization(self) -> None:
        """Paused, stopped, and unresolved runs cannot keep mutating or finalize."""

        blockers = {
            "paused": {
                "event": "paused",
                "target_identity": "fixture-terse-summary",
                "state_validated": True,
            },
            "stopped": {
                "event": "stopped",
                "reason": "explicit-stop",
            },
            "unresolved": {
                "event": "question_asked",
                "question_count": 1,
                "decision": "awaiting-user-owned-decision",
            },
        }
        for state, blocker in blockers.items():
            with self.subTest(state=state):
                trace = accepted_finalization_trace()
                candidate_index = next(
                    index
                    for index, event in enumerate(trace)
                    if event.get("event") == "candidate_edit"
                )
                trace.insert(candidate_index, blocker)

                codes = {
                    failure.code
                    for failure in evaluate_trace(
                        seal_trace(trace), BUILDER_FIXTURES
                    )
                }

                self.assertIn("INACTIVE_RUN_EFFECT", codes)

    def test_dot_paths_fail_across_manifest_and_envelope_boundaries(self) -> None:
        """A dot is not a file path even though PurePosixPath has no parts for it."""

        candidate_trace = accepted_finalization_trace()
        candidate_manifest = next(
            event
            for event in candidate_trace
            if event.get("artifact_type") == "candidate-manifest"
        )
        candidate_manifest["files"].append(
            {
                "path": ".",
                "media_kind": "text/plain",
                "byte_count": 1,
                "digest": "dot-file",
            }
        )
        candidate_codes = {
            failure.code
            for failure in evaluate_trace(
                seal_trace(candidate_trace), BUILDER_FIXTURES
            )
        }
        self.assertIn("INVALID_ARTIFACT_SCHEMA", candidate_codes)

        envelope_trace = accepted_finalization_trace()
        terminal = next(
            event
            for event in envelope_trace
            if event.get("artifact_type") == "terminal-manifest"
        )
        terminal["artifact_envelope"]["payload_path"] = "."
        envelope_codes = {
            failure.code
            for failure in evaluate_trace(
                reseal_declared_trace(envelope_trace), BUILDER_FIXTURES
            )
        }
        self.assertIn("INVALID_ARTIFACT_ENVELOPE", envelope_codes)

    def test_frozen_cases_reject_placeholder_only_contracts(self) -> None:
        """A full score cannot rest on labels unrelated to retained case bytes."""

        trace = accepted_finalization_trace()
        evaluation = next(
            event for event in trace if event.get("event") == "evaluation_frozen"
        )
        for case in evaluation["cases"]:
            case["observable_assertions"] = ["placeholder"]
            case["forbidden_effects"] = ["placeholder"]
            case["pass_rule"] = "placeholder"

        codes = {
            failure.code
            for failure in evaluate_trace(seal_trace(trace), BUILDER_FIXTURES)
        }

        self.assertTrue(
            {"INVALID_EVENT_SCHEMA", "INVALID_ARTIFACT_SCHEMA"} & codes
        )

    def test_fixture_construction_rejects_floats_and_invalid_unicode_cleanly(self) -> None:
        """JSON-valid hostile values become deterministic failures, never exceptions."""

        raw_events = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )["traces"]["accepted_exact_improve"]
        mutants: dict[str, list[dict[str, Any]]] = {}

        float_resolution = copy.deepcopy(raw_events)
        float_resolution[0]["hostile"] = 1.25
        mutants["float-resolution"] = float_resolution

        float_lane = copy.deepcopy(raw_events)
        research = next(
            event for event in float_lane if event.get("event") == "research_pack"
        )
        research["lanes"][0]["evidence_cards"] = 1.25
        mutants["float-lane"] = float_lane

        invalid_unicode = copy.deepcopy(raw_events)
        invalid_unicode[0]["hostile"] = "\ud800"
        mutants["invalid-unicode"] = invalid_unicode

        for name, mutant in mutants.items():
            with self.subTest(mutant=name):
                try:
                    built = build_fixture_trace(
                        "visible", "accepted_exact_improve", mutant
                    )
                    codes = {
                        failure.code
                        for failure in evaluate_trace(built, BUILDER_FIXTURES)
                    }
                except (TypeError, UnicodeError, ValueError) as error:
                    self.fail(
                        f"fixture construction leaked {type(error).__name__}: {error}"
                    )
                self.assertTrue(
                    {"INVALID_EVENT_SCHEMA", "INVALID_ARTIFACT_SCHEMA"} & codes
                )

    def test_accepted_fixture_builder_does_not_sanitize_explicit_cases_or_paths(self) -> None:
        """Explicit hostile fixture values must reach validation unchanged."""

        payload = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        raw_events = copy.deepcopy(payload["traces"]["accepted_exact_improve"])
        evaluation = next(
            event
            for event in raw_events
            if event.get("event") == "evaluation_frozen"
        )
        evaluation["cases"] = [{"placeholder": True}]
        raw_events.append(
            {
                "event": "artifact_retained",
                "artifact_type": "candidate-manifest",
                "candidate_revision": "improve-candidate-v1",
                "files": [
                    {
                        "path": "/production/fixture-terse-summary/SKILL.md",
                        "media_kind": "text/markdown",
                        "byte_count": 128,
                        "digest": "hostile-production-path",
                    }
                ],
            }
        )

        built = build_fixture_trace(
            "visible", "accepted_exact_improve", raw_events
        )
        built_evaluation = next(
            event for event in built if event.get("event") == "evaluation_frozen"
        )
        built_manifest = next(
            event
            for event in built
            if event.get("artifact_type") == "candidate-manifest"
        )
        codes = {
            failure.code
            for failure in evaluate_trace(built, BUILDER_FIXTURES)
        }

        self.assertEqual(built_evaluation["cases"], [{"placeholder": True}])
        self.assertEqual(
            built_manifest["files"][0]["path"],
            "/production/fixture-terse-summary/SKILL.md",
        )
        self.assertIn("INVALID_ARTIFACT_SCHEMA", codes)

    def test_pause_resume_pair_must_bind_the_active_run(self) -> None:
        """A self-consistent foreign pause pair cannot suspend the active run."""

        trace = seal_trace(accepted_finalization_trace())
        final_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("event") == "finalized"
        )
        foreign_binding = {
            "workflow_id": "f" * 32,
            "target_identity": "foreign-target",
            "target_snapshot": digest_value("foreign-snapshot"),
            "mode": "create",
            "identity_generation": 9,
            "snapshot_generation": 9,
        }
        trace[final_index:final_index] = [
            {
                "event": "paused",
                "state_validated": True,
                **foreign_binding,
            },
            {
                "event": "resumed",
                "chain_revalidated": True,
                "identity_revalidated": True,
                "snapshot_revalidated": True,
                "evidence_revalidated": True,
                **foreign_binding,
            },
        ]

        codes = {
            failure.code
            for failure in evaluate_trace(
                reseal_declared_trace(trace), BUILDER_FIXTURES
            )
        }

        self.assertIn("INVALID_PAUSE_STATE", codes)
        self.assertIn("RESUME_WITHOUT_REVALIDATION", codes)

    def test_authorized_write_after_finalization_is_rejected(self) -> None:
        """Finalization forbids later candidate mutation even with prior authority."""

        trace = accepted_finalization_trace()
        final_index = next(
            index
            for index, event in enumerate(trace)
            if event.get("event") == "finalized"
        )
        owned_path = next(
            event["path"]
            for event in trace
            if event.get("event") == "candidate_edit"
        )
        trace.insert(
            final_index + 1,
            {
                "event": "write",
                "destination_scope": "isolated-candidate",
                "effect": "isolated-candidate-write",
                "actor": "candidate-implementer-v1",
                "path": owned_path,
            },
        )

        codes = {
            failure.code
            for failure in evaluate_trace(seal_trace(trace), BUILDER_FIXTURES)
        }

        self.assertIn("EVENT_AFTER_FINALIZATION", codes)

    def test_fixture_builder_rejects_float_control_values_without_throwing(self) -> None:
        """Accepted and ordinary fixture migration must fail closed on floats."""

        payload = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        accepted = copy.deepcopy(payload["traces"]["accepted_exact_improve"])
        next(
            event
            for event in accepted
            if event.get("event") == "candidate_edit"
        )["candidate_revision"] = 1.25
        ordinary = copy.deepcopy(payload["traces"]["accepted_exact_improve"])
        next(
            event
            for event in ordinary
            if event.get("event") == "evidence_sieved"
        )["card_count"] = 1.25

        for trace_id, raw_events in (
            ("accepted_exact_improve", accepted),
            ("mutant_float_card_count", ordinary),
        ):
            with self.subTest(trace=trace_id):
                try:
                    built = build_fixture_trace(
                        "visible", trace_id, raw_events
                    )
                    codes = {
                        failure.code
                        for failure in evaluate_trace(built, BUILDER_FIXTURES)
                    }
                except (IndexError, TypeError, UnicodeError, ValueError) as error:
                    self.fail(
                        f"fixture construction leaked {type(error).__name__}: {error}"
                    )
                self.assertTrue(
                    {"INVALID_EVENT_SCHEMA", "INVALID_ARTIFACT_SCHEMA"} & codes
                )

    def test_fixture_builder_rejects_json_valid_malformed_shapes_without_throwing(self) -> None:
        """Malformed event containers and required structure fail as oracle data."""

        payload = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        accepted_scalar = copy.deepcopy(
            payload["traces"]["accepted_exact_improve"]
        )
        accepted_scalar.append("hostile-scalar")
        accepted_missing_resolution = [
            event
            for event in copy.deepcopy(
                payload["traces"]["accepted_exact_improve"]
            )
            if event.get("event") != "resolve"
        ]
        ordinary_scalar: list[Any] = ["hostile-scalar"]
        ordinary_lane = copy.deepcopy(
            payload["traces"]["accepted_exact_improve"]
        )
        next(
            event
            for event in ordinary_lane
            if event.get("event") == "research_pack"
        )["lanes"] = ["hostile-scalar"]

        mutants = (
            ("accepted_scalar", "accepted_exact_improve", accepted_scalar),
            (
                "accepted_missing_resolution",
                "accepted_exact_improve",
                accepted_missing_resolution,
            ),
            ("ordinary_scalar", "mutant_scalar", ordinary_scalar),
            ("ordinary_lane", "mutant_lane", ordinary_lane),
        )
        for name, trace_id, raw_events in mutants:
            with self.subTest(mutant=name):
                try:
                    built = build_fixture_trace(
                        "visible", trace_id, raw_events
                    )
                    codes = {
                        failure.code
                        for failure in evaluate_trace(built, BUILDER_FIXTURES)
                    }
                except (
                    AttributeError,
                    IndexError,
                    KeyError,
                    StopIteration,
                    TypeError,
                    ValueError,
                ) as error:
                    self.fail(
                        f"fixture construction leaked {type(error).__name__}: {error}"
                    )
                self.assertIn("INVALID_EVENT_SCHEMA", codes)

    def test_fixture_builder_rejects_adjacent_json_shapes_without_throwing(self) -> None:
        """Unhashable controls and malformed manifest counts fail deterministically."""

        payload = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        source = payload["traces"]["accepted_exact_improve"]
        mutants: list[tuple[str, str, list[dict[str, Any]]]] = []

        for selected_mode in ([], {}):
            trace = copy.deepcopy(source)
            next(
                event for event in trace if event.get("event") == "resolve"
            )["selected_mode"] = selected_mode
            mutants.append(
                (
                    f"selected_mode_{type(selected_mode).__name__}",
                    "accepted_exact_improve",
                    trace,
                )
            )

        for revision in ("", "../escape"):
            trace = copy.deepcopy(source)
            next(
                event
                for event in trace
                if event.get("event") == "candidate_edit"
            )["candidate_revision"] = revision
            mutants.append(
                (
                    f"candidate_revision_{revision!r}",
                    "accepted_exact_improve",
                    trace,
                )
            )

        for role in ([], {}):
            trace = copy.deepcopy(source)
            research = next(
                event
                for event in trace
                if event.get("event") == "research_pack"
            )
            research["lanes"][0]["role"] = role
            mutants.append(
                (
                    f"lane_role_{type(role).__name__}",
                    "accepted_exact_improve",
                    trace,
                )
            )

        for trace_id in ("accepted_exact_improve", "mutant_manifest_count"):
            for byte_count in ("128", [], {}):
                trace = copy.deepcopy(source)
                candidate = next(
                    event
                    for event in trace
                    if event.get("event") == "candidate_edit"
                )
                candidate["artifact_type"] = "candidate-manifest"
                candidate["files"] = [{"byte_count": byte_count}]
                mutants.append(
                    (
                        f"{trace_id}_byte_count_{type(byte_count).__name__}",
                        trace_id,
                        trace,
                    )
                )

        for name, trace_id, raw_events in mutants:
            with self.subTest(mutant=name):
                try:
                    built = build_fixture_trace(
                        "visible", trace_id, raw_events
                    )
                    codes = {
                        failure.code
                        for failure in evaluate_trace(built, BUILDER_FIXTURES)
                    }
                except (
                    AttributeError,
                    IndexError,
                    KeyError,
                    StopIteration,
                    TypeError,
                    ValueError,
                ) as error:
                    self.fail(
                        f"fixture construction leaked {type(error).__name__}: {error}"
                    )
                self.assertTrue(
                    {"INVALID_EVENT_SCHEMA", "INVALID_ARTIFACT_SCHEMA"} & codes
                )

    def test_fixture_builder_rejects_nested_json_shapes_without_throwing(self) -> None:
        """Nested review and score identities must reach schema failure safely."""

        payload = json.loads(
            (BUILDER_FIXTURES / "frozen-validation" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        source = payload["traces"]["accepted_negative_review_repair"]
        mutants: list[tuple[str, list[dict[str, Any]]]] = []

        for finding_id in ([], {}):
            trace = copy.deepcopy(source)
            review = next(
                event
                for event in trace
                if event.get("event") == "review_recorded"
                and event.get("findings")
            )
            review["findings"][0]["id"] = finding_id
            mutants.append(
                (f"finding_id_{type(finding_id).__name__}", trace)
            )

        trace = copy.deepcopy(source)
        review = next(
            event
            for event in trace
            if event.get("event") == "review_recorded"
            and event.get("findings")
        )
        review["findings"][0]["affected_categories"] = [[]]
        mutants.append(("nested_affected_category", trace))

        for criterion_id in ([], {}):
            trace = copy.deepcopy(source)
            score = next(
                event
                for event in trace
                if event.get("event") == "category_scored"
            )
            score["criteria"][0]["id"] = criterion_id
            mutants.append(
                (f"criterion_id_{type(criterion_id).__name__}", trace)
            )

        for name, raw_events in mutants:
            with self.subTest(mutant=name):
                try:
                    built = build_fixture_trace(
                        "frozen-validation",
                        "accepted_negative_review_repair",
                        raw_events,
                    )
                    codes = {
                        failure.code
                        for failure in evaluate_trace(built, BUILDER_FIXTURES)
                    }
                except (
                    AttributeError,
                    IndexError,
                    KeyError,
                    StopIteration,
                    TypeError,
                    ValueError,
                ) as error:
                    self.fail(
                        f"fixture construction leaked {type(error).__name__}: {error}"
                    )
                self.assertIn("INVALID_EVENT_SCHEMA", codes)

    def test_accepted_fixture_builder_preserves_sieve_and_score_evidence(self) -> None:
        """Explicit malformed values must reach the oracle without replacement."""

        visible = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        sieve_source = copy.deepcopy(
            visible["traces"]["accepted_exact_improve"]
        )
        source_sieve = next(
            event
            for event in sieve_source
            if event.get("event") == "evidence_sieved"
        )
        source_sieve["card_count"] = 999
        source_sieve["decisions"] = ["reject"]

        built_sieve = build_fixture_trace(
            "visible", "accepted_exact_improve", sieve_source
        )
        built_sieve_event = next(
            event
            for event in built_sieve
            if event.get("event") == "evidence_sieved"
        )
        self.assertEqual(built_sieve_event["card_count"], 999)
        self.assertEqual(built_sieve_event["decisions"], ["reject"])
        self.assertTrue(evaluate_trace(built_sieve, BUILDER_FIXTURES))

        frozen = json.loads(
            (
                BUILDER_FIXTURES
                / "frozen-validation"
                / "traces.json"
            ).read_text(encoding="utf-8")
        )
        score_source = copy.deepcopy(
            frozen["traces"]["accepted_negative_review_repair"]
        )
        source_score = next(
            event
            for event in score_source
            if event.get("event") == "category_scored"
        )
        source_score["criteria"][0]["evidence"] = ["hostile-cross-case"]

        built_score = build_fixture_trace(
            "frozen-validation",
            "accepted_negative_review_repair",
            score_source,
        )
        built_score_event = next(
            event
            for event in built_score
            if event.get("event") == "category_scored"
            and event.get("category") == "safety"
        )
        self.assertEqual(
            built_score_event["criteria"][0]["evidence"],
            ["hostile-cross-case"],
        )
        self.assertTrue(evaluate_trace(built_score, BUILDER_FIXTURES))

    def test_accepted_fixture_builder_preserves_supplied_events(self) -> None:
        """A hostile event injected into an accepted fixture reaches the oracle."""

        payload = json.loads(
            (BUILDER_FIXTURES / "visible" / "traces.json").read_text(
                encoding="utf-8"
            )
        )
        raw_events = copy.deepcopy(payload["traces"]["accepted_exact_improve"])
        raw_events.append(
            {
                "event": "write",
                "destination_scope": "production-target",
                "effect": "production-target-write",
                "authority": True,
                "actor": "release-manager-v1",
                "path": "production/fixture-terse-summary/SKILL.md",
                "fixture_marker": "must-survive-migration",
            }
        )

        built = build_fixture_trace(
            "visible", "accepted_exact_improve", raw_events
        )

        self.assertTrue(
            any(
                event.get("fixture_marker") == "must-survive-migration"
                and event.get("fixture_source_event") == raw_events[-1]
                for event in built
            )
        )
        self.assertIn(
            "UNAUTHORIZED_PRODUCTION_WRITE",
            {failure.code for failure in evaluate_trace(built, BUILDER_FIXTURES)},
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
