from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from scripts.render_codex import render_agents


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILL = PLUGIN / "content" / "skills" / "implement"


class ImplementContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.body = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.normalized = " ".join(self.body.lower().split())

    def test_skill_package_is_small_and_explicit_only(self) -> None:
        files = {
            path.relative_to(SKILL).as_posix()
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files, {"SKILL.md"})
        metadata = (PLUGIN / "codex" / "skills" / "implement" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('default_prompt: "Use $implement', metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_skill_is_orchestration_not_a_private_runtime(self) -> None:
        self.assertFalse((PLUGIN / "content" / "scripts" / "implement_state.py").exists())
        for marker in (
            "implement_state.py",
            "command-attestation",
            "private ledger",
            "receipt schema",
            "state database",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.normalized)
        self.assertIn("do not invent a second state format", self.normalized)
        self.assertIn("plan graph", self.normalized)

    def test_each_node_uses_one_fresh_tdd_worker(self) -> None:
        for phrase in (
            "one fresh `expskill-implementer` per node",
            "meaningful failing test",
            "smallest implementation",
            "one coherent local commit",
            "may not delegate or expand scope",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)

    def test_candidate_has_independent_parallel_review_and_spec_gates(self) -> None:
        self.assertIn("launch these fresh agents concurrently", self.normalized)
        self.assertIn("`expskill-review`", self.normalized)
        self.assertIn("`expskill-spec`", self.normalized)
        self.assertIn("neither judge sees or edits the other's conclusion", self.normalized)
        self.assertIn("implementing worker does not review itself", self.normalized)

    def test_corrections_use_new_workers_and_are_bounded(self) -> None:
        for phrase in (
            "launch a new `expskill-implementer`",
            "rerun both judges",
            "do not try to keep one agent alive",
            "three non-improving attempts",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)

    def test_parallel_lanes_integrate_incrementally_and_cleanup_safely(self) -> None:
        for phrase in (
            "one cheap concurrency pass",
            "separate external worktrees",
            "integrate an accepted node",
            "run affected checks on the integrated result",
            "only after its exact commit is present on the target",
            "whole target branch",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)

    def test_remote_and_protected_branch_boundary_is_closed(self) -> None:
        for phrase in (
            "exact non-protected target branch",
            "do not push",
            "merge request",
            "approve",
            "auto-merge",
            "merge queue",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized)

    def test_three_runtime_profiles_have_the_accepted_boundaries(self) -> None:
        expected = {
            "expskill-implementer": ("workspace-write", "red-green-refactor"),
            "expskill-review": ("read-only", "ready or not ready"),
            "expskill-spec": ("read-only", "criterion-by-criterion evidence"),
        }
        for name, (sandbox, boundary) in expected.items():
            with self.subTest(profile=name):
                profile = tomllib.loads(render_agents(ROOT)[f"agents/{name}.toml"])
                self.assertEqual(profile["name"], name)
                self.assertEqual(profile["sandbox_mode"], sandbox)
                self.assertIn(boundary, profile["developer_instructions"].lower())
                self.assertRegex(profile["developer_instructions"].lower(), r"\bno delegation\b|\bdo not delegate\b")


if __name__ == "__main__":
    unittest.main()
