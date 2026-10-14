from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_claude_package import build_claude_package
from scripts.build_codex_marketplace import build_codex_marketplace
from scripts.build_hermes_package import build_hermes_package
from scripts.build_opencode_package import build_opencode_package
from scripts.render_opencode import parse_skill_frontmatter


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILLS = PLUGIN / "content" / "skills"
AGENTS = PLUGIN / "content" / "agents"
LINE_BUDGET_SKILLS = ("roast-loop", "roast", "review")
DIGEST_BUILDERS = (
    ("claude", build_claude_package),
    ("hermes", build_hermes_package),
    ("opencode", build_opencode_package),
    ("codex-marketplace", build_codex_marketplace),
)


def _digest(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _source_files() -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for skill in sorted(path for path in SKILLS.iterdir() if path.is_dir()):
        sources[f"skills/{skill.name}/SKILL.md"] = skill / "SKILL.md"
        for reference in sorted((skill / "references").glob("*")) if (skill / "references").is_dir() else []:
            if reference.is_file():
                sources[f"skills/{skill.name}/references/{reference.name}"] = reference
    return sources


class SkillStructureTests(unittest.TestCase):
    def test_every_skill_directory_has_valid_frontmatter(self) -> None:
        directories = sorted(path for path in SKILLS.iterdir() if path.is_dir())
        self.assertTrue(directories)
        self.assertLessEqual(set(LINE_BUDGET_SKILLS), {path.name for path in directories})
        for directory in directories:
            with self.subTest(skill=directory.name):
                skill = directory / "SKILL.md"
                self.assertTrue(skill.is_file())
                frontmatter = parse_skill_frontmatter(skill.read_text(encoding="utf-8"), directory.name)
                self.assertEqual(frontmatter["name"], directory.name)
                description = frontmatter["description"]
                self.assertIsInstance(description, str)
                self.assertTrue(description.strip())
                self.assertGreaterEqual(len(description), 20)
                self.assertLessEqual(len(description), 300)
                lines = skill.read_text(encoding="utf-8").splitlines()
                body_lines = len(lines) - (lines.index("---", 1) + 1)
                self.assertLess(body_lines, 500)
                if directory.name in LINE_BUDGET_SKILLS:
                    self.assertLessEqual(len(lines), 220)

    def test_every_named_agent_has_a_canonical_profile(self) -> None:
        content = json.loads((PLUGIN / "content" / "agents.json").read_text(encoding="utf-8"))
        self.assertTrue(content["agents"])
        for name in content["agents"]:
            with self.subTest(agent=name):
                self.assertTrue((AGENTS / f"{name}.md").is_file())

    def test_host_builds_carry_every_skill_and_reference_byte_for_byte(self) -> None:
        sources = _source_files()
        self.assertTrue(any(key.endswith("/SKILL.md") for key in sources))
        for host, builder in DIGEST_BUILDERS:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / host
                builder(ROOT, output)
                built = [path for path in output.rglob("*") if path.is_file()]
                for relative, source in sources.items():
                    with self.subTest(file=relative):
                        matches = [path for path in built if path.relative_to(output).as_posix().endswith(relative)]
                        self.assertTrue(matches, relative)
                        expected = _digest(source)
                        for match in matches:
                            self.assertEqual(_digest(match), expected, match.relative_to(output).as_posix())


if __name__ == "__main__":
    unittest.main()
