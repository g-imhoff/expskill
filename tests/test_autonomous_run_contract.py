from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILLS = PLUGIN / "content" / "skills"
ADAPTERS = PLUGIN / "codex" / "skill-adapters"
RUNNER = SKILLS / "autonomous-run" / "SKILL.md"
JARGON = re.compile(
    r"\b(?:quick|full|models?|caps?|scaffold|private[- ]marketplace|local plugin)\b",
    re.IGNORECASE,
)


# These checks cover the static package and delivery boundaries. Following
# workflow transitions needs behavioral trials, not phrase checks.
class AutonomousRunContractTests(unittest.TestCase):
    def test_runner_has_one_canonical_body_and_one_codex_adapter(self) -> None:
        skill = SKILLS / "autonomous-run"
        self.assertTrue((skill / "SKILL.md").is_file())
        observed_files = {
            path.relative_to(skill).as_posix()
            for path in skill.rglob("*")
            if path.is_file()
        }
        self.assertEqual(observed_files, {"SKILL.md"})
        adapter = ADAPTERS / "autonomous-run"
        self.assertEqual(
            {
                path.relative_to(adapter).as_posix()
                for path in adapter.rglob("*")
                if path.is_file()
            },
            {"agents/openai.yaml"},
        )

    def test_runner_is_explicit_only(self) -> None:
        text = (ADAPTERS / "autonomous-run" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("allow_implicit_invocation: false", text)
        self.assertIn("$autonomous-run", text)

    def test_runner_stops_at_human_review(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        self.assertIn("as a draft", body)
        for forbidden in (
            "approve, merge",
            "auto-merge",
            "merge queue",
        ):
            self.assertIn(forbidden, body)
        self.assertIn("Never approve", body)
        self.assertIn("Never push the protected branch.", body)

    def test_runner_pr_description_explains_choices(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        for phrase in (
            "rejected alternatives and their evidence",
            "retained risks",
            "head SHA",
        ):
            self.assertIn(phrase, body)

    def test_invoking_agent_owns_delivery_and_cumulative_run_budget(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        for phrase in (
            "You are the delivery actor in this invoking conversation",
            "router never pushes or creates the PR",
            "Verify the returned URL, head, base, and draft state",
            "one cumulative run identifier",
            "never resets this allowance",
            "3N + 2",
            "independent reviewer provenance are unchanged",
        ):
            self.assertIn(phrase, body)
        self.assertNotIn("required even if the workflow used", body)

    def test_runner_prose_passes_package_hygiene(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn("\N{EM DASH}", body)
        self.assertNotIn(";", body)
        self.assertIsNone(JARGON.search(body))


if __name__ == "__main__":
    unittest.main()


def test_lifecycle_budget_initialization_and_child_accounting_are_explicit():
    body = RUNNER.read_text()
    for clause in (
        "80 AI dispatches", "six retries", "four hours of wall-clock", "at most six in flight",
        "limit source", "unique dispatch ID", "only new descendant dispatch IDs",
        "uncertain spend", "outstanding reservations", "minimum of the lifecycle remainder",
        "Missing or contradictory accounting blocks another launch", "coordinator attestations",
    ):
        assert clause in body
    for name in ("brainstorm", "plan"):
        research = (RUNNER.parent.parent / name / "SKILL.md").read_text()
        assert "six researcher turns" in research
        assert "twenty minutes of active research" in research
        assert "dispatch IDs" in research
    grill = (RUNNER.parent.parent / "grill-me" / "SKILL.md").read_text()
    assert "two fact-exploration turns" in grill
    assert "ten minutes of active exploration" in grill
