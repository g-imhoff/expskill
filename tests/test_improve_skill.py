from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAINTAINER_SKILL_ROOT = ROOT / ".agents" / "skills" / "improve-skill"
PUBLIC_SKILLS_ROOT = ROOT / "plugins" / "codex-dev-flow" / "skills"
EXPECTED_PUBLIC_SKILLS = {
    "use-expand",
    "brainstorm",
    "plan",
    "implement",
    "review",
    "verify",
    "integrate",
}
EXPECTED_STAGES = (
    "Establish the stack program",
    "Select one target skill",
    "Capture the baseline",
    "Run blind research",
    "Sieve the evidence",
    "Facilitate the design workshop",
    "Lock the skill contract",
    "Define acceptance before implementation",
    "Implement one candidate",
    "Run fresh-context trials",
    "Repair the lowest category",
    "Review, verify, and release one skill",
    "Audit the completed stack",
)
EXPECTED_ARTIFACTS = (
    "stack map",
    "stack contract",
    "upgrade queue",
    "baseline report",
    "research pack",
    "evidence sieve",
    "design record",
    "skill contract",
    "evaluation pack",
    "candidate diff",
    "scorecard",
    "review record",
    "verification record",
    "release record",
)
EXPECTED_CATEGORIES = (
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


def parse_metadata(contents: str) -> dict[str, dict[str, object]]:
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
        elif len(str(value)) >= 2 and str(value)[0] == str(value)[-1] and str(value)[0] in {'\"', "'"}:
            value = ast.literal_eval(str(value))
        current[key] = value
    return result


class ImproveSkillContractTests(unittest.TestCase):
    def skill_body(self) -> str:
        return (MAINTAINER_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def test_maintainer_skill_is_repo_local_and_public_roster_is_unchanged(self) -> None:
        self.assertTrue((MAINTAINER_SKILL_ROOT / "SKILL.md").is_file())
        self.assertFalse((PUBLIC_SKILLS_ROOT / "improve-skill").exists())
        self.assertEqual({path.name for path in PUBLIC_SKILLS_ROOT.iterdir()}, EXPECTED_PUBLIC_SKILLS)

    def test_frontmatter_and_metadata_make_the_skill_explicit_only(self) -> None:
        frontmatter = parse_frontmatter(self.skill_body())
        self.assertEqual(set(frontmatter), {"name", "description"})
        self.assertEqual(frontmatter["name"], "improve-skill")
        description = frontmatter["description"].lower()
        for phrase in ("explicit", "improve", "agent skill", "stack"):
            self.assertIn(phrase, description)

        metadata = parse_metadata(
            (MAINTAINER_SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(set(metadata), {"interface", "policy"})
        self.assertEqual(
            set(metadata["interface"]),
            {"display_name", "short_description", "default_prompt"},
        )
        self.assertIn("$improve-skill", str(metadata["interface"]["default_prompt"]))
        self.assertIs(metadata["policy"]["allow_implicit_invocation"], False)

    def test_workflow_stages_are_complete_and_ordered(self) -> None:
        body = self.skill_body()
        positions = []
        for stage in EXPECTED_STAGES:
            match = re.search(rf"^## [0-9]+\. {re.escape(stage)}$", body, re.MULTILINE)
            self.assertIsNotNone(match, stage)
            positions.append(match.start() if match is not None else -1)
        self.assertEqual(positions, sorted(positions))

    def test_workflow_names_every_required_artifact(self) -> None:
        body = self.skill_body().lower()
        for artifact in EXPECTED_ARTIFACTS:
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, body)

    def test_upgrade_queue_has_exactly_seven_public_targets_and_no_removed_phase(self) -> None:
        """Regression: the maintainer queue must not select or preserve acceptance as a target."""

        body = self.skill_body().lower()
        self.assertIn("seven production skills", body)
        self.assertNotIn("eight production skills", body)
        self.assertNotIn("$" + "acceptance", body)
        self.assertNotIn("→ acceptance", body)
        self.assertNotIn("acceptance →", body)
        removed_public_skill = "accept" + "ance"
        self.assertNotIn(f"{removed_public_skill} phase", body)
        for skill in EXPECTED_PUBLIC_SKILLS:
            with self.subTest(skill=skill):
                self.assertIn(skill, body)

    def test_generic_acceptance_terms_remain_in_maintainer_contract(self) -> None:
        """Regression: ordinary acceptance criteria/tests remain valid vocabulary after phase removal."""

        body = self.skill_body().lower()
        self.assertIn("acceptance evidence", body)
        self.assertIn("acceptance case", body)

    def test_research_is_independent_and_evidence_only(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "at least three",
            "gpt-5.6-luna",
            "max reasoning",
            "domain techniques",
            "agent-skill design",
            "evaluation and failure modes",
            "evidence only",
            "do not reveal the candidate design",
            "do not edit",
        ):
            self.assertIn(phrase, body)

    def test_research_has_explicit_cost_and_output_bounds(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "at most six",
            "1,500 words",
            "stop browsing",
            "do not start another research wave",
            "maximum of four research-agent sessions",
            "at most two research waves",
            "24 evidence cards",
            "6,000 words",
            "before exceeding any limit",
        ):
            self.assertIn(phrase, body)

    def test_disposable_validation_mode_cannot_claim_stack_completion(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "workflow-validation mode",
            "fixture-only queue",
            "disposable fixture",
            "unique temporary run directory",
            "checked-in fixture read-only",
            "fixture-only terminal audit",
            "substitutes for the production queue",
            "never call this stack completion",
            "cannot satisfy target completion, stack completion, or release",
        ):
            self.assertIn(phrase, body)

    def test_fresh_trials_use_the_repository_isolation_boundary(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "codex exec --ephemeral --ignore-user-config --json",
            "disposable repository",
            "full invocation",
            "json event stream",
        ):
            self.assertIn(phrase, body)

    def test_trial_receipts_bind_inputs_candidate_and_observed_results(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "trial receipt",
            "candidate digest",
            "exact request",
            "loaded-skill digest",
            "output digest",
            "filesystem manifest",
        ):
            self.assertIn(phrase, body)

    def test_reviewer_must_have_verified_access_to_review_evidence(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "embed the required artifacts",
            "explicit read-only access",
            "verify access before review",
        ):
            self.assertIn(phrase, body)

    def test_not_ready_and_serious_findings_block_release(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "every high or medium finding",
            "any `not ready` verdict",
            "blocks verification, release, and target completion",
            "lower each affected category below 10",
            "fresh independent review",
        ):
            self.assertIn(phrase, body)

    def test_initial_review_precedes_scoring_and_supports_zero_repair(self) -> None:
        body = self.skill_body().lower()
        initial_review = body.index("obtain an initial independent review")
        initial_score = body.index("score the current revision against")
        final_review = body.index("run a final same-revision review")
        verification = body.index("give an independent verifier")
        self.assertLess(initial_review, initial_score)
        self.assertLess(initial_score, final_review)
        self.assertLess(final_review, verification)
        self.assertIn("even when no repair was needed", body)

    def test_scorecard_has_exact_categories_and_fail_closed_ten_gate(self) -> None:
        body = self.skill_body().lower()
        for category in EXPECTED_CATEGORIES:
            with self.subTest(category=category):
                self.assertRegex(body, rf"(?m)^- `{re.escape(category)}`:")
        for phrase in (
            "lowest-scoring category",
            "fresh evidence",
            "do not average",
            "all ten categories",
            "exactly 10",
            "no high or medium finding",
            "tie-break",
        ):
            self.assertIn(phrase, body)

    def test_user_decisions_and_writes_block_later_stages(self) -> None:
        body = self.skill_body().lower()
        for phrase in (
            "ask exactly one question",
            "do not guess",
            "stop the run",
            "before any implementation edit",
            "disposable copy",
            "never test by editing a production skill",
            "missing evidence fails closed",
        ):
            self.assertIn(phrase, body)

    def test_router_is_last_and_each_skill_is_completed_once(self) -> None:
        body = self.skill_body().lower()
        self.assertIn("brainstorm → plan → implement → review → verify → integrate → use-expand", body)
        self.assertIn("complete one target before selecting another", body)
        self.assertIn("upgrade `use-expand` last", body)

    def test_skill_stays_within_progressive_disclosure_budget(self) -> None:
        body = self.skill_body()
        self.assertLess(len(body.splitlines()), 500)
        self.assertNotIn("[TODO:", body)

    def test_disposable_fixture_is_not_a_production_skill(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "improve-skill" / "sample-skill" / "SKILL.md"
        charter = ROOT / "tests" / "fixtures" / "improve-skill" / "charter.md"
        self.assertTrue(fixture.is_file())
        self.assertTrue(charter.is_file())
        self.assertFalse(fixture.is_relative_to(PUBLIC_SKILLS_ROOT))
        self.assertEqual(parse_frontmatter(fixture.read_text(encoding="utf-8"))["name"], "summarize-changes")


if __name__ == "__main__":
    unittest.main()
