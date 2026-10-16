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

    def test_skill_package_is_small_and_explicit_only(self) -> None:
        files = {
            path.relative_to(SKILL).as_posix()
            for path in SKILL.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files, {"SKILL.md"})
        metadata = (PLUGIN / "codex" / "skill-adapters" / "implement" / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('default_prompt: "Use $implement', metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_retired_implement_state_helper_stays_absent(self) -> None:
        self.assertFalse((PLUGIN / "content" / "scripts" / "implement_state.py").exists())
        self.assertNotIn("implement_state.py", self.body)

    def test_skill_names_the_fresh_worker_and_judge_profiles(self) -> None:
        for name in ("expskill-implementer", "expskill-review", "expskill-spec"):
            with self.subTest(profile=name):
                self.assertIn(name, self.body)

    def test_three_runtime_profiles_keep_their_sandbox_boundaries(self) -> None:
        expected = {
            "expskill-implementer": "workspace-write",
            "expskill-review": "read-only",
            "expskill-spec": "read-only",
        }
        rendered = render_agents(ROOT)
        for name, sandbox in expected.items():
            with self.subTest(profile=name):
                profile = tomllib.loads(rendered[f"agents/{name}.toml"])
                self.assertEqual(profile["name"], name)
                self.assertEqual(profile["sandbox_mode"], sandbox)


if __name__ == "__main__":
    unittest.main()
