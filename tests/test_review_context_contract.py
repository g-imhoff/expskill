from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
IMPLEMENT = PLUGIN / "content" / "skills" / "implement" / "SKILL.md"
SKILL_BUILDER = PLUGIN / "content" / "skills" / "skill-builder" / "SKILL.md"
EVALUATION_RUBRIC = (
    PLUGIN
    / "content"
    / "skills"
    / "skill-builder"
    / "references"
    / "evaluation-rubric.md"
)


class ReviewContextContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        if (ROOT / ".agents").is_dir():
            shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "plugins", temporary / "plugins")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        return temporary

    def test_every_review_producer_has_one_final_review_context_section(self) -> None:
        for path in (IMPLEMENT, SKILL_BUILDER, EVALUATION_RUBRIC):
            with self.subTest(path=path):
                raw = path.read_text(encoding="utf-8")
                self.assertEqual(raw.count("## Review context contract"), 1)
                final_section = raw.split("## Review context contract", 1)[1]
                self.assertNotIn("\n## ", final_section)

    def test_user_selected_agent_runtime_settings_are_preserved(self) -> None:
        expected = {
            "expskill-explorer": ("gpt-6-luna", "max", "read-only"),
            "expskill-test-engineer": ("gpt-6-luna", "max", "read-only"),
            "expskill-planner": ("gpt-6-luna", "max", "workspace-write"),
            "expskill-designer": ("gpt-6-luna", "max", "workspace-write"),
            "expskill-implementer": ("gpt-6-luna", "max", "workspace-write"),
            "expskill-review": ("gpt-6.1-sol", "xhigh", "read-only"),
            "expskill-spec": ("gpt-6.1-sol", "xhigh", "read-only"),
        }
        for name, settings in expected.items():
            profile = json.loads(
                (PLUGIN / "codex" / "agents.json").read_text(encoding="utf-8")
            )["agents"][name]
            observed = (
                profile["model"],
                profile["model_reasoning_effort"],
                profile["sandbox_mode"],
            )
            with self.subTest(profile=name):
                self.assertEqual(observed, settings)

        policy = json.loads(
            (PLUGIN / "content" / "policies" / "execution-policy.json").read_text(encoding="utf-8")
        )
        observed_policy = {
            name: (
                values["model"],
                values["effort"],
                values["sandbox_mode"],
            )
            for name, values in policy["profiles"].items()
        }
        self.assertEqual(observed_policy, expected)

    def test_review_and_spec_agents_keep_the_invalid_handoff_code_span(self) -> None:
        """Regression: both review agents keep the literal `invalid handoff` code span."""

        for name in ("expskill-review.md", "expskill-spec.md"):
            with self.subTest(agent=name):
                contents = (PLUGIN / "content" / "agents" / name).read_text(encoding="utf-8")
                self.assertIn("`invalid handoff`", contents)

    def test_review_contract_heading_must_be_live_top_level_markdown(self) -> None:
        guarded_paths = (
            Path("plugins/expskill/content/skills/implement/SKILL.md"),
            Path("plugins/expskill/content/skills/skill-builder/SKILL.md"),
            Path(
                "plugins/expskill/content/skills/skill-builder/"
                "references/evaluation-rubric.md"
            ),
        )
        for relative_path in guarded_paths:
            for opener in ("```text\n", "~~~text\n", "<!--\n", "<script>\n"):
                root = self.copy_repository()
                path = root / relative_path
                contents = path.read_text(encoding="utf-8")
                heading = "## Review context contract\n"
                self.assertEqual(contents.count(heading), 1)
                path.write_text(
                    contents.replace(heading, opener + heading, 1),
                    encoding="utf-8",
                )
                errors = validate_repository(root, include_opencode=False)
                with self.subTest(path=relative_path, opener=opener):
                    self.assertTrue(
                        any("review handoff" in error.lower() for error in errors),
                        errors,
                    )

    def test_review_contract_heading_must_be_unique(self) -> None:
        guarded_paths = (
            Path("plugins/expskill/content/skills/implement/SKILL.md"),
            Path("plugins/expskill/content/skills/skill-builder/SKILL.md"),
            Path(
                "plugins/expskill/content/skills/skill-builder/"
                "references/evaluation-rubric.md"
            ),
        )
        heading = "## Review context contract\n"
        for relative_path in guarded_paths:
            root = self.copy_repository()
            path = root / relative_path
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(contents.count(heading), 1)
            path.write_text(contents + "\n" + heading, encoding="utf-8")
            errors = validate_repository(root, include_opencode=False)
            with self.subTest(path=relative_path):
                self.assertTrue(
                    any(
                        "review handoff" in error.lower()
                        and "exactly one review context contract heading" in error.lower()
                        for error in errors
                    ),
                    errors,
                )

    def test_review_contract_sections_must_stay_identical(self) -> None:
        self.assertEqual(validate_repository(ROOT, include_opencode=False), ())
        for path in (IMPLEMENT, SKILL_BUILDER, EVALUATION_RUBRIC):
            root = self.copy_repository()
            drifted = root / path.relative_to(ROOT)
            drifted.write_text(
                drifted.read_text(encoding="utf-8") + "\nDrift.\n", encoding="utf-8"
            )
            errors = validate_repository(root, include_opencode=False)
            with self.subTest(path=path):
                self.assertTrue(
                    any(
                        "review handoff contract sections must be identical" in error
                        for error in errors
                    ),
                    errors,
                )

    def test_review_agent_limits_must_match_the_contract(self) -> None:
        section = IMPLEMENT.read_text(encoding="utf-8").split("## Review context contract", 1)[1]
        limit = re.search(r"\b\d+\b", section).group()
        for name in ("expskill-review", "expskill-spec"):
            root = self.copy_repository()
            agent = root / "plugins" / "expskill" / "content" / "agents" / f"{name}.md"
            contents = agent.read_text(encoding="utf-8")
            drifted = re.sub(rf"\b{limit}\b", str(int(limit) // 10), contents)
            self.assertNotEqual(drifted, contents)
            agent.write_text(drifted, encoding="utf-8")
            errors = validate_repository(root, include_opencode=False)
            with self.subTest(agent=name):
                self.assertEqual(
                    [error for error in errors if "numeric limits must match" in error],
                    [f"review agent {name!r} numeric limits must match the Review context contract"],
                )

    def test_review_contract_heading_requires_an_exact_column_zero_line(self) -> None:
        guarded_paths = (
            Path("plugins/expskill/content/skills/implement/SKILL.md"),
            Path("plugins/expskill/content/skills/skill-builder/SKILL.md"),
            Path(
                "plugins/expskill/content/skills/skill-builder/"
                "references/evaluation-rubric.md"
            ),
        )
        replacements = (
            "\\## Review context contract\n",
            "    ## Review context contract\n",
            "> ## Review context contract\n",
            "prefix ## Review context contract\n",
        )
        for relative_path in guarded_paths:
            for replacement in replacements:
                root = self.copy_repository()
                path = root / relative_path
                contents = path.read_text(encoding="utf-8")
                heading = "## Review context contract\n"
                self.assertEqual(contents.count(heading), 1)
                path.write_text(
                    contents.replace(heading, replacement, 1),
                    encoding="utf-8",
                )
                errors = validate_repository(root, include_opencode=False)
                with self.subTest(path=relative_path, replacement=replacement):
                    self.assertTrue(
                        any("review handoff" in error.lower() for error in errors),
                        errors,
                    )


if __name__ == "__main__":
    unittest.main()
