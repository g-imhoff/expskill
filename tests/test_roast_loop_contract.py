"""Deterministic contract for the roast-loop fix-and-repeat skill."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILL = PLUGIN / "content" / "skills" / "roast-loop"
BODY = SKILL / "SKILL.md"
METADATA = PLUGIN / "codex" / "skill-adapters" / "roast-loop" / "agents" / "openai.yaml"
JARGON = re.compile(r"\b(?:quick|full|model|caps?)\b", re.IGNORECASE)


class RoastLoopContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = BODY.read_text(encoding="utf-8")

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
        self.assertEqual(frontmatter["name"].strip(), "roast-loop")
        description = frontmatter["description"].strip()
        self.assertTrue(20 <= len(description) <= 300, len(description))
        self.assertIn("$roast-loop", description)

    def test_each_pass_reuses_the_roast_skill(self) -> None:
        self.assertIn("$roast", self.raw)
        self.assertIn("../roast/SKILL.md", self.raw)

    def test_fixer_dispatch_uses_the_worktree_helper_script(self) -> None:
        helper_path = re.search(r"[\w./-]*scripts/worktrees\.py", self.raw)
        self.assertIsNotNone(helper_path)
        self.assertTrue(
            (SKILL / helper_path.group()).samefile(
                PLUGIN / "content" / "scripts" / "worktrees.py"
            )
        )

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
        self.assertIn("$roast-loop", contents)
        self.assertIn("allow_implicit_invocation: false", contents)


if __name__ == "__main__":
    unittest.main()
