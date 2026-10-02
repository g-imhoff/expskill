"""Deterministic contract for the review-loop pre-PR gate skill."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILL = PLUGIN / "content" / "skills" / "review-loop"
BODY = SKILL / "SKILL.md"
METADATA = PLUGIN / "codex" / "skill-adapters" / "review-loop" / "agents" / "openai.yaml"
JARGON = re.compile(r"\b(?:quick|full|model|caps?)\b", re.IGNORECASE)


class ReviewLoopContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = BODY.read_text(encoding="utf-8")
        cls.body = " ".join(cls.raw.lower().split())

    def test_canonical_body_and_codex_adapter_have_exact_files(self) -> None:
        observed = {
            path.relative_to(SKILL).as_posix()
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertEqual(observed, {"SKILL.md"})
        self.assertFalse(any(path.is_symlink() for path in SKILL.rglob("*")))
        adapter = METADATA.parents[1]
        self.assertEqual(
            {
                path.relative_to(adapter).as_posix()
                for path in adapter.rglob("*")
                if path.is_file()
            },
            {"agents/openai.yaml"},
        )

    def test_frontmatter_names_the_skill(self) -> None:
        lines = self.raw.splitlines()
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        frontmatter = dict(
            line.split(":", 1) for line in lines[1:end] if ":" in line
        )
        self.assertEqual(
            set(key.strip() for key in frontmatter), {"name", "description"}
        )
        self.assertEqual(frontmatter["name"].strip(), "review-loop")
        description = frontmatter["description"].strip()
        self.assertTrue(20 <= len(description) <= 300, len(description))
        self.assertIn("$review-loop", description)

    def test_categories_come_from_the_diff(self) -> None:
        for phrase in ("derive", "categor", "diff", "weight"):
            self.assertIn(phrase, self.body)
        self.assertRegex(self.body, r"100 percent|100%")

    def test_reviewer_output_contract_is_json_with_score_and_top_issue(self) -> None:
        for field in ('"category"', '"score"', '"top_issue"', '"evidence"'):
            self.assertIn(field, self.raw)
        self.assertRegex(self.body, r"score.*0.*10|0.*10.*score")
        self.assertIn("json", self.body)
        self.assertRegex(self.body, r"one.*reviewer.*one categor|one categor.*one.*reviewer")
        self.assertIn("read only", self.body)

    def test_fixer_dispatch_uses_one_fixer_per_issue_with_worktrees(self) -> None:
        self.assertIn("one fixer", self.body)
        self.assertIn("worktree", self.body)
        helper_path = re.search(r"[\w./-]*scripts/worktrees\.py", self.raw)
        self.assertIsNotNone(helper_path)
        self.assertTrue(
            (SKILL / helper_path.group()).samefile(
                PLUGIN / "content" / "scripts" / "worktrees.py"
            )
        )

    def test_loop_gate_requires_nine_with_cycle_limit_and_fail_closed(self) -> None:
        self.assertRegex(self.body, r"9 or higher|>=9|9 of 10")
        self.assertRegex(self.body, r"(max|at most|no more than).*three.*cycl|three.*cycl.*(max|limit|total)")
        self.assertIn("fail closed", self.body)
        self.assertRegex(self.body, r"never.*fourth|never.*inflat|inflation")

    def test_prose_stays_plain_and_bounded(self) -> None:
        self.assertLessEqual(len(self.raw.splitlines()), 220)
        self.assertNotIn("\u2014", self.raw)
        self.assertNotIn(";", self.raw)
        self.assertIsNone(JARGON.search(self.raw), JARGON.search(self.raw))

    def test_metadata_invokes_the_matching_skill(self) -> None:
        contents = METADATA.read_text(encoding="utf-8")
        self.assertIn("display_name", contents)
        self.assertIn("short_description", contents)
        self.assertIn("default_prompt", contents)
        self.assertIn("$review-loop", contents)
        self.assertIn("allow_implicit_invocation: false", contents)


if __name__ == "__main__":
    unittest.main()
