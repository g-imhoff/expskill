from __future__ import annotations

import ast
import csv
import hashlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "plugins" / "expskill" / "content" / "skills" / "brainstorm"
ENTRYPOINT = SKILL_ROOT / "SKILL.md"
METADATA = (
    ROOT
    / "plugins"
    / "expskill"
    / "codex"
    / "skill-adapters"
    / "brainstorm"
    / "agents"
    / "openai.yaml"
)
CATALOG = SKILL_ROOT / "references" / "brainstorm-techniques.csv"

BMAD_REVISION = "890fcda760bade4d6080f5fa09aa8f658bc4a4a5"
BMAD_URL = (
    "https://github.com/bmad-code-org/BMAD-METHOD/blob/"
    f"{BMAD_REVISION}/web-bundles/brainstorming-coach/brain-methods.csv"
)
BMAD_SHA256 = "0ab5878b1dbc9e3fa98cb72abfc3920a586b9e2b42609211bb0516eefd542039"
BMAD_CATEGORIES = {
    "biomimetic",
    "collaborative",
    "creative",
    "cultural",
    "deep",
    "introspective_delight",
    "quantum",
    "structured",
    "theatrical",
    "wild",
}

BMAD_LICENSE = """MIT License

Copyright (c) 2025 BMad Code, LLC

This project incorporates contributions from the open source community.
See [CONTRIBUTORS.md](CONTRIBUTORS.md) for contributor attribution.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

TRADEMARK NOTICE:
BMad™, BMad Method™, and BMad Core™ are trademarks of BMad Code, LLC, covering all
casings and variations (including BMAD, bmad, BMadMethod, BMAD-METHOD, etc.). The use of
these trademarks in this software does not grant any rights to use the trademarks
for any other purpose. See [TRADEMARK.md](TRADEMARK.md) for detailed guidelines."""


def _comment_block(contents: str) -> str:
    return "\n".join("#" if not line else f"# {line}" for line in contents.splitlines())


BMAD_CATALOG_PREAMBLE = (
    f"# Source: {BMAD_URL}\n"
    f"# Source-Revision: {BMAD_REVISION}\n"
    f"# Upstream-SHA256: {BMAD_SHA256}\n"
    "#\n"
    f"{_comment_block(BMAD_LICENSE)}\n"
    "#\n"
)

ORDERED_WORKFLOW_HEADINGS = (
    "## Understand and confirm",
    "## Research the landscape",
    "## Choose techniques",
    "## Explore collaboratively",
    "## Shape and stress",
    "## Concept Brief",
)

REQUIRED_BOUNDARY_HEADINGS = (
    "## When to use",
    "## Boundaries and recovery",
)

CONCEPT_BRIEF_HEADINGS = (
    "### Concept snapshot",
    "### How it works",
    "### Evidence and differentiation",
    "### Key decisions and boundaries",
    "### Stress-test result",
    "### Deferred uncertainties and success signals",
)

FORBIDDEN_ROUTING_TOKENS = (
    "phase-handoff",
    "next_skill",
    "$plan",
    "$" + "acceptance",
    "$implement",
    "$review",
    "$verify",
    "$integrate",
    "$use-expskill",
)


def _parse_yaml_scalar(raw_value: str) -> object:
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {'"', "'"}:
        return ast.literal_eval(raw_value)
    return raw_value


def _parse_yaml_mapping(contents: str) -> dict[str, object]:
    result: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, result)]
    for line in contents.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key:
            raise ValueError(f"unsupported YAML line: {line!r}")
        while stack[-1][0] >= indentation:
            stack.pop()
        parent = stack[-1][1]
        if raw_value.strip():
            parent[key] = _parse_yaml_scalar(raw_value.strip())
            continue
        child: dict[str, object] = {}
        parent[key] = child
        stack.append((indentation, child))
    return result


def _frontmatter(contents: str) -> dict[str, object]:
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing frontmatter")
    end = lines.index("---", 1)
    return _parse_yaml_mapping("\n".join(lines[1:end]))


def _catalog_payload(contents: bytes) -> bytes:
    marker = b"category,technique_name,description"
    start = contents.find(marker)
    if start < 0:
        raise AssertionError("catalog payload header is missing")
    return contents[start:]


def _markdown_section(contents: str, heading: str) -> str:
    marker = f"{heading}\n"
    start = contents.find(marker)
    if start < 0:
        raise AssertionError(f"missing section: {heading}")
    start += len(marker)
    end = contents.find("\n## ", start)
    return contents[start:] if end < 0 else contents[start:end]


class BrainstormContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = ENTRYPOINT.read_text(encoding="utf-8")

    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        for relative in ("plugins", "scripts"):
            shutil.copytree(ROOT / relative, temporary / relative)
        return temporary

    def test_brainstorm_package_contains_exact_justified_files(self) -> None:
        actual_files = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            actual_files,
            {
                "SKILL.md",
                "references/brainstorm-techniques.csv",
            },
        )
        self.assertFalse(any(path.is_symlink() for path in SKILL_ROOT.rglob("*")))

    def test_catalog_is_pinned_complete_and_attributed(self) -> None:
        self.assertTrue(CATALOG.is_file(), CATALOG)
        raw_contents = CATALOG.read_bytes()
        payload = _catalog_payload(raw_contents)
        self.assertEqual(
            raw_contents[: -len(payload)],
            BMAD_CATALOG_PREAMBLE.encode("utf-8"),
        )
        self.assertEqual(hashlib.sha256(payload).hexdigest(), BMAD_SHA256)
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
        self.assertEqual(len(rows), 61)
        self.assertEqual({row["category"] for row in rows}, BMAD_CATEGORIES)
        names = [row["technique_name"] for row in rows]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(row["description"].strip() for row in rows))

    def test_validator_accepts_only_the_pinned_catalog(self) -> None:
        self.assertTrue(CATALOG.is_file(), CATALOG)
        self.assertEqual(
            [
                error
                for error in validate_repository(ROOT)
                if "brainstorm" in error and "catalog" in error
            ],
            [],
        )

        missing_root = self.copy_repository()
        missing_catalog = (
            missing_root
            / "plugins"
            / "expskill"
            / "content"
            / "skills"
            / "brainstorm"
            / "references"
            / "brainstorm-techniques.csv"
        )
        missing_catalog.unlink()
        self.assertTrue(
            any(
                "brainstorm" in error and "catalog" in error
                for error in validate_repository(missing_root, include_opencode=False)
            )
        )

        mutations = tuple(
            (line, f"{line} altered")
            for line in BMAD_CATALOG_PREAMBLE.splitlines()
            if line != "#"
        ) + (("Yes And Building", "Renamed Technique"),)
        for original, replacement in mutations:
            with self.subTest(mutation=original):
                changed_root = self.copy_repository()
                changed_catalog = (
                    changed_root
                    / "plugins"
                    / "expskill"
                    / "content"
                    / "skills"
                    / "brainstorm"
                    / "references"
                    / "brainstorm-techniques.csv"
                )
                changed_contents = changed_catalog.read_text(encoding="utf-8")
                self.assertIn(original, changed_contents)
                changed_catalog.write_text(
                    changed_contents.replace(original, replacement, 1), encoding="utf-8"
                )
                self.assertTrue(
                    any(
                        "brainstorm" in error and "catalog" in error
                        for error in validate_repository(changed_root, include_opencode=False)
                    )
                )

    def test_frontmatter_and_metadata_expose_the_public_skill_identity(self) -> None:
        frontmatter = _frontmatter(self.contents)
        self.assertEqual(set(frontmatter), {"name", "description"})
        self.assertEqual(frontmatter["name"], "brainstorm")
        self.assertTrue(str(frontmatter["description"]).strip())
        self.assertIn("$brainstorm", self.contents)

        metadata = _parse_yaml_mapping(METADATA.read_text(encoding="utf-8"))
        self.assertEqual(set(metadata), {"interface", "policy"})
        interface = metadata["interface"]
        policy = metadata["policy"]
        self.assertIsInstance(interface, dict)
        self.assertIsInstance(policy, dict)
        if not isinstance(interface, dict) or not isinstance(policy, dict):
            return
        self.assertEqual(interface["display_name"], "Brainstorm")
        self.assertTrue(25 <= len(str(interface["short_description"])) <= 64)
        self.assertIn("$brainstorm", str(interface["default_prompt"]))
        self.assertIs(policy["allow_implicit_invocation"], False)

    def test_boundary_headings_appear_once_and_workflow_headings_appear_once_in_order(self) -> None:
        for heading in REQUIRED_BOUNDARY_HEADINGS:
            self.assertEqual(self.contents.count(heading), 1, heading)
        positions = []
        for heading in ORDERED_WORKFLOW_HEADINGS:
            self.assertEqual(self.contents.count(heading), 1, heading)
            positions.append(self.contents.index(heading))
        self.assertEqual(positions, sorted(positions))

    def test_research_step_names_the_gatherer_model(self) -> None:
        section = _markdown_section(self.contents, "## Research the landscape")
        self.assertIn("gpt-6-luna", section)

    def test_early_user_stop_label_is_named_in_the_entrypoint(self) -> None:
        self.assertIn("`Incomplete concept`", self.contents)

    def test_entrypoint_uses_no_routing_identifiers(self) -> None:
        for token in FORBIDDEN_ROUTING_TOKENS:
            self.assertNotIn(token, self.contents)

    def test_entrypoint_references_the_catalog_by_path(self) -> None:
        self.assertIn("references/brainstorm-techniques.csv", self.contents)
        self.assertTrue(CATALOG.is_file(), CATALOG)

    def test_concept_brief_headings_are_ordered_and_entrypoint_stays_compact(self) -> None:
        positions = []
        for heading in CONCEPT_BRIEF_HEADINGS:
            self.assertEqual(self.contents.count(heading), 1, heading)
            positions.append(self.contents.index(heading))
        self.assertEqual(positions, sorted(positions))
        self.assertLess(len(self.contents.splitlines()), 500)


if __name__ == "__main__":
    unittest.main()
