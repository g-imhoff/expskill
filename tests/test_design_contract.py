from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"
SKILLS = PLUGIN / "content" / "skills"
DESIGN = SKILLS / "design"
ACCEPTANCE = "accept" + "ance"
EXPECTED_SKILLS = {
    "brainstorm",
    "plan",
    "design",
    "grill-me",
    "implement",
    "correct",
    "review",
    "setup-design",
    "setup-test",
    "skill-builder",
    "test",
    "unslop",
    "use-expskill",
    "autonomous-run",
    "review-loop",
}
EXPECTED_DESIGN_FILES = {
    "SKILL.md",
    "references/rules-index.md",
    "references/geometry.md",
    "references/typography.md",
    "references/interaction.md",
    "references/forms.md",
    "references/responsive.md",
    "references/accessibility.md",
    "references/motion.md",
    "references/data-display.md",
    "references/yodea-preview.md",
}


def _frontmatter(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise AssertionError(f"missing design entrypoint: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("design entrypoint has no frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError("design frontmatter is unterminated") from error
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip().strip('"')
    return values


def _metadata(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"missing design metadata: {path}")
    return path.read_text(encoding="utf-8")


def _body(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"missing design contract body: {path}")
    return path.read_text(encoding="utf-8")


class DesignContractTests(unittest.TestCase):
    def test_design_is_in_exact_public_roster_and_acceptance_is_absent(self) -> None:
        """Regression: Design must ship while the removed acceptance phase stays unreachable."""
        self.assertTrue(SKILLS.is_dir(), f"missing skills directory: {SKILLS}")
        observed = {path.name for path in SKILLS.iterdir()}
        self.assertEqual(observed, EXPECTED_SKILLS)
        self.assertFalse((SKILLS / ACCEPTANCE).exists())
        for surface in (ROOT / "README.md", ROOT / "docs", PLUGIN, ROOT / "scripts", ROOT / "tests"):
            files = [surface] if surface.is_file() else list(surface.rglob("*"))
            for path in files:
                if path.is_file() and path.suffix in {".md", ".py", ".json", ".toml", ".yaml", ".yml"}:
                    text = path.read_text(encoding="utf-8").lower()
                    self.assertNotIn(f"${ACCEPTANCE}", text, path)

    def test_design_package_has_exact_regular_resources(self) -> None:
        """Regression: missing, shadowed, or substituted rule resources break isolated review."""
        self.assertTrue(DESIGN.is_dir(), f"missing design package: {DESIGN}")
        observed = {
            path.relative_to(DESIGN).as_posix()
            for path in DESIGN.rglob("*")
            if path.is_file()
        }
        self.assertEqual(observed, EXPECTED_DESIGN_FILES)
        self.assertFalse(any(path.is_symlink() for path in DESIGN.rglob("*")))
        for relative in EXPECTED_DESIGN_FILES:
            path = DESIGN / relative
            self.assertTrue(path.is_file() and path.stat().st_size > 0, path)

    def test_design_state_helper_is_unique_regular_and_packaged(self) -> None:
        """Regression: state must not be absent, empty, symlinked, or duplicated in the package."""
        helper = PLUGIN / "content" / "scripts" / "design_state.py"
        self.assertTrue(helper.is_file(), f"missing design state helper: {helper}")
        self.assertFalse(helper.is_symlink())
        self.assertGreater(helper.stat().st_size, 0)
        matches = [
            path
            for path in PLUGIN.rglob("design_state.py")
            if path.is_file()
            and path.relative_to(PLUGIN).parts[:2] != ("codex", "runtime")
        ]
        self.assertEqual(matches, [helper])

    def test_design_metadata_is_explicit_and_public(self) -> None:
        """Regression: ambient activation or private routing vocabulary must not leak publicly."""
        frontmatter = _frontmatter(DESIGN / "SKILL.md")
        self.assertEqual(frontmatter.get("name"), "design")
        description = frontmatter.get("description", "").lower()
        self.assertIn("explicit", description)
        metadata = _metadata(PLUGIN / "codex" / "skill-adapters" / "design" / "agents" / "openai.yaml")
        self.assertIn("$design", metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)
        public_text = (DESIGN / "SKILL.md").read_text(encoding="utf-8") + metadata
        self.assertNotRegex(public_text, re.compile(r"\b(?:quick|full|caps?|private[- ]state)\b", re.I))

    def test_design_workflow_is_ordered_and_compact(self) -> None:
        """Regression: skipped gates, empty ceremony, and context bloat weaken design quality."""
        body = _body(DESIGN / "SKILL.md")
        self.assertLess(len(body.splitlines()), 500)
        positions = [body.lower().find(f"\n## {heading.lower()}\n") for heading in ("Ground", "Choose", "Build", "Review", "Deliver")]
        self.assertTrue(all(position >= 0 for position in positions), positions)
        self.assertEqual(positions, sorted(positions))
        for phrase in ("responsive", "state", "approval", "technical", "synthetic"):
            self.assertIn(phrase, body.lower())

    def test_design_has_no_downstream_routing(self) -> None:
        """Regression: direct Design must stop at its own delivered bundle."""
        body = _body(DESIGN / "SKILL.md").lower()
        for marker in ("next_skill", "selected_phase", "phase-handoff-v1", "invoke `$implement`", "route `$implement`"):
            self.assertNotIn(marker, body)
        self.assertIn("stop", body)
        self.assertNotIn("begin implementation", body)

    def test_design_scope_and_preview_dependency_direction_are_explicit(self) -> None:
        """Regression: component construction must not become feature integration or preview leakage."""
        body = _body(DESIGN / "SKILL.md").lower()
        for phrase in ("production-intended", "component", "temporary", "preview", "integration"):
            self.assertIn(phrase, body)
        for forbidden in ("backend", "application state", "navigation", "live side effects", "customer data"):
            self.assertIn(forbidden, body)
        self.assertIn("production code never imports", body)

    def test_rules_are_adaptive_and_project_grounded(self) -> None:
        """Regression: one loading strategy, fixed breakpoints, and heuristic authority create generic UI."""
        body = " ".join(
            _body(path)
            for path in (DESIGN / "SKILL.md", DESIGN / "references" / "rules-index.md")
        ).lower()
        for phrase in (
            "project convention",
            "narrow component",
            "selectively",
            "complex composite",
            "complete category catalog",
            "applicable",
            "normative",
            "conflict",
            "actual pressure",
        ):
            self.assertIn(phrase, body)
        for forbidden in ("always load every", "universal device numbers", "fixed state matrix", "aesthetic score"):
            self.assertNotIn(forbidden, body)

    def test_failed_quality_gates_cannot_reach_visual_approval(self) -> None:
        """Regression: a polished but overflowing or inaccessible candidate must be repaired before review."""
        body = _body(DESIGN / "SKILL.md").lower()
        for phrase in (
            "before any approval request",
            "compact, intermediate, and wide",
            "page-level overflow",
            "zero serious or critical accessibility findings",
            "reduced-motion",
            "format",
            "lint",
            "type",
            "build",
            "repair",
            "rerun",
        ):
            self.assertIn(phrase, body, phrase)
        self.assertIn("do not present", body)
        self.assertIn("stop blocked", body)

    def test_plugin_and_installed_tree_advertise_the_same_roster(self) -> None:
        """Regression: manifest, installer-visible tree, and shared contract must not drift."""
        manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
        self.assertTrue(manifest_path.is_file(), manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        long_description = str(manifest.get("interface", {}).get("longDescription", ""))
        self.assertIn("$design", long_description)
        self.assertNotIn(f"${ACCEPTANCE}", long_description)
        self.assertEqual({path.name for path in SKILLS.iterdir()}, EXPECTED_SKILLS)

    def test_static_mutation_controls_fail_for_key_boundary_changes(self) -> None:
        """Regression: assertions must detect a changed contract, not merely missing files."""
        self.assertTrue(DESIGN.is_dir(), DESIGN)
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "design"
            shutil.copytree(DESIGN, copy)
            body_path = copy / "SKILL.md"
            body = _body(body_path)
            positions = [body.lower().find(f"\n## {heading.lower()}\n") for heading in ("Ground", "Choose", "Build", "Review", "Deliver")]
            self.assertTrue(all(position >= 0 for position in positions))
            self.assertEqual(positions, sorted(positions))
            mutated = re.sub(r"^## Review$", "## Build", body, count=1, flags=re.MULTILINE)
            body_path.write_text(mutated, encoding="utf-8")
            mutated_positions = [mutated.lower().find(f"\n## {heading.lower()}\n") for heading in ("Ground", "Choose", "Build", "Review", "Deliver")]
            self.assertNotEqual(mutated_positions, sorted(mutated_positions))
            self.assertNotEqual(mutated, body)

            metadata_path = Path(temporary) / "openai.yaml"
            metadata_path.write_text(
                (PLUGIN / "codex" / "skill-adapters" / "design" / "agents" / "openai.yaml").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            metadata = _metadata(metadata_path)
            self.assertIn("allow_implicit_invocation: false", metadata)
            metadata_path.write_text(metadata.replace("false", "true", 1), encoding="utf-8")
            self.assertNotIn("allow_implicit_invocation: false", _metadata(metadata_path))

            resource = copy / "references" / "rules-index.md"
            resource.unlink()
            self.assertNotEqual(
                {path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file()},
                EXPECTED_DESIGN_FILES,
            )


if __name__ == "__main__":
    unittest.main()


def test_native_preview_and_local_fallback_preserve_explicit_hosted_obligations():
    body = _body(DESIGN / "SKILL.md")
    reference = (DESIGN / "references" / "yodea-preview.md").read_text()
    for clause in (
        "Use the project's native stack",
        "Do not introduce React, Vite, or another framework only to satisfy a preview host",
        "publication is forbidden, unavailable, incompatible",
        "It does not complete a separately requested hosted-publication obligation",
        "exact launch command",
        "transfer the exact artifacts before removing the worktree",
    ):
        assert clause in body
    assert "use native local review" in reference
    assert "on every Design run" not in reference
