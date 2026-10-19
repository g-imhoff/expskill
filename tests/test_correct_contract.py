from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILLS = PLUGIN / "content" / "skills"


def _metadata(name: str) -> dict[str, dict[str, object]]:
    path = PLUGIN / "codex" / "skill-adapters" / name / "agents" / "openai.yaml"
    result: dict[str, dict[str, object]] = {}
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw = line.strip().partition(":")
        if not separator:
            raise AssertionError(f"invalid metadata line: {line!r}")
        if indentation == 0:
            section = key
            result[section] = {}
            continue
        value = raw.strip()
        if value == "true":
            parsed: object = True
        elif value == "false":
            parsed = False
        else:
            parsed = ast.literal_eval(value)
        result[section][key] = parsed
    return result


# Correct behavior is evaluated through fresh-context Skill Builder trials.
# This file checks the static package boundary and that the router names $correct.
class CorrectContractTests(unittest.TestCase):
    def test_correct_has_only_the_small_direct_package(self) -> None:
        for name in ("correct",):
            with self.subTest(skill=name):
                root = SKILLS / name
                files = {
                    path.relative_to(root).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(files, {"SKILL.md"})
                metadata = _metadata(name)
                self.assertEqual(set(metadata), {"interface", "policy"})
                self.assertIs(
                    metadata["policy"]["allow_implicit_invocation"],
                    False,
                )
                self.assertIn(
                    f"${name}",
                    str(metadata["interface"]["default_prompt"]),
                )

    def test_router_names_the_correct_skill(self) -> None:
        router = (SKILLS / "use-expskill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("$correct", router)


if __name__ == "__main__":
    unittest.main()
