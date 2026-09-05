from __future__ import annotations

import json
import re
import shutil
import tomllib
import tempfile
import unittest
from pathlib import Path

from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-dev-flow"
IMPLEMENT = PLUGIN / "skills" / "implement" / "SKILL.md"
SKILL_BUILDER = PLUGIN / "skills" / "skill-builder" / "SKILL.md"
EVALUATION_RUBRIC = (
    PLUGIN
    / "skills"
    / "skill-builder"
    / "references"
    / "evaluation-rubric.md"
)


def normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


class ReviewContextContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "plugins", temporary / "plugins")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        return temporary

    def test_every_review_producer_has_the_same_hard_context_budget(self) -> None:
        for path in (IMPLEMENT, SKILL_BUILDER, EVALUATION_RUBRIC):
            body = normalized(path)
            with self.subTest(path=path):
                self.assertIn("aggregate authored review handoff", body)
                self.assertIn("300 physical lines", body)
                self.assertIn("inline dispatch text", body)
                self.assertIn("follow-up messages", body)
                self.assertIn("regardless of carrier or extension", body)
                self.assertIn("stop before dispatch", body)
                self.assertIn("specification", body)
                self.assertIn("referenced separately", body)
                self.assertIn("existed before review dispatch", body)

    def test_review_handoff_is_a_locator_not_a_copied_repository(self) -> None:
        required_fields = (
            "repository or candidate path",
            "base revision",
            "candidate revision",
            "what changed",
            "review scope",
            "claimed checks",
            "known concerns",
        )
        forbidden_payloads = (
            "diffs",
            "source files",
            "test logs",
            "terminal output",
            "transcripts",
        )
        for path in (IMPLEMENT, SKILL_BUILDER, EVALUATION_RUBRIC):
            body = normalized(path)
            with self.subTest(path=path):
                for field in required_fields:
                    self.assertIn(field, body)
                for payload in forbidden_payloads:
                    self.assertIn(payload, body)

    def test_review_agents_self_inspect_the_pinned_target(self) -> None:
        for name in ("devflow-review", "devflow-spec"):
            profile_path = PLUGIN / "assets" / "agents" / f"{name}.toml"
            profile = tomllib.loads(profile_path.read_text(encoding="utf-8"))
            instructions = " ".join(profile["developer_instructions"].lower().split())
            with self.subTest(profile=name):
                self.assertIn("self-inspect", instructions)
                self.assertIn("pinned", instructions)
                self.assertIn("do not request a copied diff", instructions)
                self.assertIn("300 physical lines", instructions)
                self.assertIn("invalid handoff", instructions)

    def test_user_selected_agent_runtime_settings_are_preserved(self) -> None:
        expected = {
            "devflow-explorer": ("gpt-5.6-luna", "max", "read-only"),
            "devflow-test-engineer": ("gpt-5.6-luna", "max", "read-only"),
            "devflow-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
            "devflow-review": ("gpt-5.6-sol", "xhigh", "read-only"),
            "devflow-spec": ("gpt-5.6-sol", "xhigh", "read-only"),
        }
        for name, settings in expected.items():
            profile_path = PLUGIN / "assets" / "agents" / f"{name}.toml"
            profile = tomllib.loads(profile_path.read_text(encoding="utf-8"))
            observed = (
                profile["model"],
                profile["model_reasoning_effort"],
                profile["sandbox_mode"],
            )
            with self.subTest(profile=name):
                self.assertEqual(observed, settings)

        policy = json.loads(
            (PLUGIN / "assets" / "execution-policy.json").read_text(encoding="utf-8")
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

    def test_repository_validator_rejects_a_removed_handoff_limit(self) -> None:
        guarded_paths = (
            Path("plugins/codex-dev-flow/skills/implement/SKILL.md"),
            Path("plugins/codex-dev-flow/skills/skill-builder/SKILL.md"),
            Path(
                "plugins/codex-dev-flow/skills/skill-builder/"
                "references/evaluation-rubric.md"
            ),
        )
        for relative_path in guarded_paths:
            root = self.copy_repository()
            path = root / relative_path
            contents = path.read_text(encoding="utf-8")
            self.assertRegex(contents, r"300\s+physical\s+lines")
            path.write_text(
                re.sub(
                    r"300\s+physical\s+lines",
                    "301 physical lines",
                    contents,
                    count=1,
                ),
                encoding="utf-8",
            )
            errors = validate_repository(root)
            with self.subTest(path=relative_path):
                self.assertTrue(
                    any("review handoff" in error.lower() for error in errors),
                    errors,
                )

    def test_repository_validator_rejects_handoff_policy_inversions(self) -> None:
        mutations = (
            (r"at\s+most\s+300", "at least 300"),
            (r"Do\s+not\s+copy\s+or\s+embed", "Copy or embed"),
        )
        guarded_paths = (
            Path("plugins/codex-dev-flow/skills/implement/SKILL.md"),
            Path("plugins/codex-dev-flow/skills/skill-builder/SKILL.md"),
            Path(
                "plugins/codex-dev-flow/skills/skill-builder/"
                "references/evaluation-rubric.md"
            ),
        )
        for relative_path in guarded_paths:
            for pattern, replacement in mutations:
                root = self.copy_repository()
                path = root / relative_path
                contents = path.read_text(encoding="utf-8")
                self.assertRegex(contents, pattern)
                path.write_text(
                    re.sub(pattern, replacement, contents, count=1),
                    encoding="utf-8",
                )
                errors = validate_repository(root)
                with self.subTest(path=relative_path, mutation=replacement):
                    self.assertTrue(
                        any("review handoff" in error.lower() for error in errors),
                        errors,
                    )

    def test_repository_validator_rejects_judge_policy_inversions(self) -> None:
        mutations = (
            ("at most 300", "at least 300"),
            ("Accept only a locator handoff", "Accept any handoff"),
            (
                "stop and return `invalid handoff` without a review verdict",
                "continue and return a review verdict",
            ),
            ("Do not request a copied diff", "Request a copied diff"),
        )
        for name in ("devflow-review", "devflow-spec"):
            for old, new in mutations:
                root = self.copy_repository()
                path = (
                    root
                    / "plugins"
                    / "codex-dev-flow"
                    / "assets"
                    / "agents"
                    / f"{name}.toml"
                )
                contents = path.read_text(encoding="utf-8")
                self.assertIn(old, contents)
                path.write_text(contents.replace(old, new, 1), encoding="utf-8")
                errors = validate_repository(root)
                with self.subTest(profile=name, mutation=new):
                    self.assertTrue(
                        any(name in error and "instructions" in error for error in errors),
                        errors,
                    )


if __name__ == "__main__":
    unittest.main()
