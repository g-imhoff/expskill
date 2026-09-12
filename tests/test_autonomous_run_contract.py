from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILLS = PLUGIN / "skills"
RUNNER = SKILLS / "autonomous-run" / "SKILL.md"
JARGON = re.compile(
    r"\b(?:quick|full|models?|caps?|scaffold|private[- ]marketplace|local plugin)\b",
    re.IGNORECASE,
)


# Autonomous-run behavior is evaluated through fresh provider-CLI trials.
# This file checks the static package and lifecycle boundaries.
class AutonomousRunContractTests(unittest.TestCase):
    def test_runner_has_only_the_small_direct_package(self) -> None:
        skill = SKILLS / "autonomous-run"
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue((skill / "agents" / "openai.yaml").is_file())
        observed_files = {
            path.relative_to(skill).as_posix()
            for path in skill.rglob("*")
            if path.is_file()
        }
        self.assertEqual(observed_files, {"SKILL.md", "agents/openai.yaml"})
        observed_dirs = {
            path.relative_to(skill).as_posix()
            for path in skill.rglob("*")
            if path.is_dir()
        }
        self.assertEqual(observed_dirs, {"agents"})

    def test_runner_is_explicit_only(self) -> None:
        text = (SKILLS / "autonomous-run" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("allow_implicit_invocation: false", text)
        self.assertIn("$autonomous-run", text)

    def test_runner_uses_fresh_cli_conversations_not_subagents(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        self.assertIn("Never run phase work as in-session subagents.", body)
        self.assertIn("codex exec", body)
        self.assertIn("claude -p", body)
        self.assertIn("opencode run", body)

    def test_runner_preserves_phase_gates(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        for phrase in (
            "Concept Brief",
            "Plan Graph",
            "Design receipt",
            "review and spec",
            "whole-branch gates",
            "$grill-me",
        ):
            self.assertIn(phrase, body)

    def test_runner_stops_at_human_review(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        self.assertIn("ready-for-human-review", body)
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
            "rejected directions and why they lost",
            "rejected alternatives and their evidence",
            "retained risks",
            "head SHA",
        ):
            self.assertIn(phrase, body)

    def test_runner_prose_passes_package_hygiene(self) -> None:
        body = RUNNER.read_text(encoding="utf-8")
        self.assertNotIn("\N{EM DASH}", body)
        self.assertNotIn(";", body)
        self.assertIsNone(JARGON.search(body))


if __name__ == "__main__":
    unittest.main()
