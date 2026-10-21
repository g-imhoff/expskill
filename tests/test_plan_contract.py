from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
PLAN_ROOT = PLUGIN_ROOT / "content" / "skills" / "plan"
HELPER_PATH = PLUGIN_ROOT / "content" / "scripts" / "plan_graph.py"
PHASE_ROOTS = {
    name: PLUGIN_ROOT / "content" / "skills" / name
    for name in ("implement", "use-expskill")
}


def _parse_simple_yaml_mapping(contents: str) -> dict[str, object]:
    result: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, result)]
    for raw_line in contents.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        key, separator, raw_value = raw_line.strip().partition(":")
        if not separator:
            raise AssertionError(f"unsupported YAML line: {raw_line!r}")
        while stack[-1][0] >= indentation:
            stack.pop()
        parent = stack[-1][1]
        value = raw_value.strip()
        if not value:
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indentation, child))
        elif value in {"true", "false"}:
            parent[key] = value == "true"
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            parent[key] = value[1:-1]
        else:
            parent[key] = value
    return result


def _frontmatter(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"missing frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError(f"unterminated frontmatter: {path}") from error
    return _parse_simple_yaml_mapping("\n".join(lines[1:end]))


class PlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_path = PLAN_ROOT / "SKILL.md"
        self.metadata_path = PLUGIN_ROOT / "codex" / "skill-adapters" / "plan" / "agents" / "openai.yaml"
        self.body = self.skill_path.read_text(encoding="utf-8")

    def test_plan_package_is_minimal_regular_and_contains_no_runtime_shadow_resource(self) -> None:
        self.assertEqual(
            {
                path.relative_to(PLAN_ROOT).as_posix()
                for path in PLAN_ROOT.rglob("*")
                if path.is_file()
            },
            {"SKILL.md"},
        )
        self.assertEqual(
            {
                path.relative_to(PLAN_ROOT).as_posix()
                for path in PLAN_ROOT.rglob("*")
                if path.is_dir()
            },
            set(),
        )
        self.assertFalse(any(path.is_symlink() for path in PLAN_ROOT.rglob("*")))

    def test_frontmatter_and_metadata_remain_explicit_plan_only(self) -> None:
        frontmatter = _frontmatter(self.skill_path)
        self.assertEqual(set(frontmatter), {"name", "description"})
        self.assertEqual(frontmatter["name"], "plan")
        self.assertIsInstance(frontmatter["description"], str)
        self.assertTrue(str(frontmatter["description"]).strip())
        self.assertIn("$plan", str(frontmatter["description"]))
        metadata = _parse_simple_yaml_mapping(self.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(set(metadata), {"interface", "policy"})
        self.assertEqual(
            set(metadata["interface"]),
            {"display_name", "short_description", "default_prompt"},
        )
        self.assertEqual(metadata["policy"], {"allow_implicit_invocation": False})
        self.assertIn("$plan", str(metadata["interface"]["default_prompt"]))

    def test_plan_retired_routing_identifiers_are_absent(self) -> None:
        lowered = self.body.lower()
        for forbidden in ("phase-handoff-v1", "selected_phase", "next_skill"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_plan_section_headings_appear_exactly_once(self) -> None:
        for heading in (
            "Boundary",
            "Workflow",
            "Canonical private graph",
            "Implementation, proof, and Git execution contract",
            "Stop and downstream boundary",
        ):
            with self.subTest(heading=heading):
                matches = re.findall(
                    rf"^## {re.escape(heading)}\s*$", self.body, flags=re.MULTILINE
                )
                self.assertEqual(len(matches), 1, heading)

    def test_plan_defines_the_private_canonical_graph_helper_path(self) -> None:
        self.assertTrue(HELPER_PATH.is_file(), HELPER_PATH)
        self.assertIn("plugins/expskill/content/scripts/plan_graph.py", self.body)
        self.assertIn("../../scripts/plan_graph.py", self.body)

    def test_plan_names_the_graph_state_and_concurrency_values(self) -> None:
        for value in (
            "`not-ready`",
            "`ready`",
            "`serial`",
            "`parallel-candidate`",
            "`parallel-safe`",
            "`ready-for-human-review`",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.body)

    def test_shared_consumers_reference_the_plan_graph_helper_by_path(self) -> None:
        for name, root in PHASE_ROOTS.items():
            with self.subTest(skill=name):
                body = (root / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("plugins/expskill/content/scripts/plan_graph.py", body)
                self.assertIn("../../scripts/plan_graph.py", body)


if __name__ == "__main__":
    unittest.main()
