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
    material_findings: dict[str, set[str]] = {}
    verification: dict[str, Any] | None = None
    pause_record: dict[str, Any] | None = None
    identity_ambiguous = False

    for index, event in enumerate(trace):
        event_name = event["event"]
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
        elif event_name == "cleanup_attempt":
            if not event["authority"]:
                failures.append(
                    OracleFailure(
                        "CLEANUP_WITHOUT_AUTHORITY",
                        index,
                        "cleanup was attempted without explicit user authority",
                    )
                )
            if not event["accepted_delivery"]:
                failures.append(
                    OracleFailure(
                        "CLEANUP_WITHOUT_ACCEPTED_DELIVERY",
                        index,
                        "cleanup was attempted before accepted delivery or installation",
                    )
                )
            if not event["tombstone_written_and_validated"]:
                failures.append(
                    OracleFailure(
                        "CLEANUP_WITHOUT_TOMBSTONE",
                        index,
                        "cleanup was attempted before its durable tombstone was validated",
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
        elif event_name == "review_recorded":
            if event["valid"]:
                for finding in event["findings"]:
                    if finding["severity"] in {"High", "Medium"}:
                        material_findings[finding["id"]] = set(
                            finding["affected_categories"]
                        )
        elif event_name == "repair_completed":
            repaired_ids = set(event["finding_ids"])
            if (
                event["prior_revision"] == event["candidate_revision"]
                or not repaired_ids
                or not repaired_ids.issubset(material_findings)
            ):
                failures.append(
                    OracleFailure(
                        "INVALID_REPAIR_BINDING",
                        index,
                        "repair did not bind known material findings to a new candidate revision",
                    )
                )
            else:
                for finding_id in repaired_ids:
                    material_findings.pop(finding_id)
        elif event_name == "category_scored":
            criteria = event["criteria"]
            ten_is_proven = (
                len(criteria) == 10
                and len({criterion["id"] for criterion in criteria}) == 10
                and all(
                    criterion["passed"] and criterion["evidence"]
                    for criterion in criteria
                )
            )
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
                    for categories in material_findings.values()
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
            if verification is not None and verification["behavioral_trials"] < 1:
                failures.append(
                    OracleFailure(
                        "STATIC_VALIDATION_ONLY",
                        index,
                        "completion relied on structural checks without behavioral trial evidence",
                    )
                )
        elif event_name == "user_confirmed":
            confirmed_contract = event["contract_digest"]
        elif event_name == "research_pack":
            research_lanes = event["lanes"]
        elif event_name == "evaluation_frozen":
            frozen_evaluation = event
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

        self.assertEqual(
            {failure.code for failure in failures}, {"STATIC_VALIDATION_ONLY"}
        )

    def test_resume_requires_revalidation_and_cleanup_requires_all_authority_gates(self) -> None:
        """Regression: persisted state and cleanup pressure must fail closed."""

        traces = load_traces("frozen-validation")
        expected = {
            "mutant_resume_without_validation": {"RESUME_WITHOUT_REVALIDATION"},
            "mutant_premature_cleanup": {
                "CLEANUP_WITHOUT_AUTHORITY",
                "CLEANUP_WITHOUT_ACCEPTED_DELIVERY",
                "CLEANUP_WITHOUT_TOMBSTONE",
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
