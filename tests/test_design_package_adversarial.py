from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import install
from scripts.validate import validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-dev-flow"
HELPER = PLUGIN / "scripts" / "design_state.py"


class _NeverCalledRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> object:
        self.calls.append(command)
        raise AssertionError(f"installer reached external command before preflight failed: {command}")


class DesignPackageAdversarialTests(unittest.TestCase):
    def _copy_repository(self) -> Path:
        temporary = Path(tempfile.mkdtemp(prefix="design-package-adversarial-"))
        self.addCleanup(shutil.rmtree, temporary, ignore_errors=True)
        for name in (".agents", "plugins", "scripts"):
            shutil.copytree(ROOT / name, temporary / name)
        shutil.copy2(ROOT / "README.md", temporary / "README.md")
        return temporary

    def test_readme_and_manifest_expose_seven_skills_design_and_skill_builder(self) -> None:
        """Regression: stale phase language hides a direct skill or contradicts the public roster."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        description = str(manifest.get("description", ""))
        long_description = str(manifest.get("interface", {}).get("longDescription", ""))
        skill_wording = re.compile(r"\bseven independent skills and one optional lifecycle router\b", re.I)
        self.assertRegex(" ".join(readme.split()), skill_wording)
        self.assertIn("$design", readme)
        self.assertIn("$skill-builder", readme)
        self.assertIn("$skill-builder", long_description)
        self.assertRegex(description, skill_wording)
        self.assertNotRegex(readme, re.compile(r"\b(?:six|seven) independent development phases\b", re.I))
        self.assertNotRegex(description, re.compile(r"\b(?:six|seven) standalone development phases\b", re.I))

    def test_validator_accepts_skill_builder_maximum_package_shape(self) -> None:
        """Regression: the joined state helper is allowed without becoming required in this lane."""

        root = self._copy_repository()
        scripts = root / "plugins" / "codex-dev-flow" / "skills" / "skill-builder" / "scripts"
        scripts.mkdir()
        (scripts / "run_state.py").write_text(
            "from __future__ import annotations\n",
            encoding="utf-8",
        )
        self.assertEqual(validate_repository(root), ())

    def test_validator_rejects_skill_builder_integration_mutations(self) -> None:
        """Regression: public visibility, isolation, package bounds, and duplicate removal fail closed."""

        expected_fragments = {
            "missing-builder": ("skill-builder", "missing"),
            "unexpected-file": ("skill 'skill-builder'", "unexpected file", "notes.txt"),
            "unexpected-directory": ("skill 'skill-builder'", "unexpected directory", "scratch"),
            "implicit-invocation": ("skill 'skill-builder'", "implicit invocation policy drift"),
            "omitted-manifest-token": ("longdescription", "advertise $skill-builder"),
            "stale-six-skill-wording": ("readme", "seven independent skills"),
            "use-expand-route": (
                "skill-builder",
                "affirmative lifecycle route or dependency",
            ),
            "lifecycle-coupling": ("skill-builder", "lifecycle independence contract"),
            "router-coupling": (
                "use-expand",
                "affirmatively route to or depend on skill-builder",
            ),
            "repository-local-duplicate": (".agents/skills/improve-skill", "must be absent"),
        }
        for mutation, fragments in expected_fragments.items():
            with self.subTest(mutation=mutation):
                root = self._copy_repository()
                plugin = root / "plugins" / "codex-dev-flow"
                builder = plugin / "skills" / "skill-builder"
                if mutation == "missing-builder":
                    shutil.rmtree(builder)
                elif mutation == "unexpected-file":
                    (builder / "notes.txt").write_text("drift\n", encoding="utf-8")
                elif mutation == "unexpected-directory":
                    (builder / "scratch").mkdir()
                elif mutation == "implicit-invocation":
                    metadata = builder / "agents" / "openai.yaml"
                    metadata.write_text(
                        metadata.read_text(encoding="utf-8").replace(
                            "allow_implicit_invocation: false",
                            "allow_implicit_invocation: true",
                        ),
                        encoding="utf-8",
                    )
                elif mutation == "omitted-manifest-token":
                    manifest_path = plugin / ".codex-plugin" / "plugin.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["interface"]["longDescription"] = manifest["interface"][
                        "longDescription"
                    ].replace("$skill-builder", "skill-builder")
                    manifest_path.write_text(
                        json.dumps(manifest, indent=2) + "\n",
                        encoding="utf-8",
                    )
                elif mutation == "stale-six-skill-wording":
                    readme = root / "README.md"
                    readme.write_text(
                        readme.read_text(encoding="utf-8").replace(
                            "seven independent skills",
                            "six independent skills",
                            1,
                        ),
                        encoding="utf-8",
                    )
                elif mutation == "use-expand-route":
                    contract = builder / "SKILL.md"
                    contract.write_text(
                        contract.read_text(encoding="utf-8")
                        + "\nUse $use-expand after finalization.\n",
                        encoding="utf-8",
                    )
                elif mutation == "lifecycle-coupling":
                    contract = builder / "SKILL.md"
                    contract.write_text(
                        contract.read_text(encoding="utf-8").replace(
                            "Do not invoke or depend on a product lifecycle phase",
                            "Invoke and depend on a product lifecycle phase",
                            1,
                        ),
                        encoding="utf-8",
                    )
                elif mutation == "router-coupling":
                    router = plugin / "skills" / "use-expand" / "SKILL.md"
                    router.write_text(
                        router.read_text(encoding="utf-8")
                        + "\nRoute to $skill-builder after implementation.\n",
                        encoding="utf-8",
                    )
                else:
                    duplicate = root / ".agents" / "skills" / "improve-skill"
                    duplicate.mkdir(parents=True)
                    (duplicate / "SKILL.md").write_text(
                        "---\n"
                        "name: improve-skill\n"
                        "description: Improve an existing agent skill\n"
                        "---\n\n"
                        "Removed duplicate.\n",
                        encoding="utf-8",
                    )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(all(fragment in error for fragment in fragments) for error in errors),
                    f"mutation {mutation} was accepted or failed for an unrelated reason: {errors}",
                )

    def test_validator_rejects_affirmative_skill_builder_lifecycle_relations(self) -> None:
        """Regression: formatting a phase name cannot hide routing or dependency coupling."""

        lifecycle_skills = ("brainstorm", "design", "plan", "implement", "use-expand")
        relation_templates = (
            "Invoke {skill} after finalization.\n",
            "Route to `{skill}` after finalization.\n",
            "Depend on ${skill} for delivery.\n",
        )
        for lifecycle_skill in lifecycle_skills:
            for relation in relation_templates:
                with self.subTest(skill=lifecycle_skill, relation=relation):
                    root = self._copy_repository()
                    contract = (
                        root
                        / "plugins"
                        / "codex-dev-flow"
                        / "skills"
                        / "skill-builder"
                        / "SKILL.md"
                    )
                    contract.write_text(
                        contract.read_text(encoding="utf-8")
                        + "\n"
                        + relation.format(skill=lifecycle_skill),
                        encoding="utf-8",
                    )
                    errors = tuple(error.lower() for error in validate_repository(root))
                    self.assertTrue(
                        any(
                            "skill-builder" in error
                            and "affirmative lifecycle route or dependency" in error
                            for error in errors
                        ),
                        f"affirmative {lifecycle_skill!r} relation escaped validation: {errors}",
                    )

    def test_validator_rejects_affirmative_router_relations_to_skill_builder(self) -> None:
        """Regression: bare and formatted builder names stay outside router ownership."""

        relations = (
            "Route to skill-builder after implementation.\n",
            "Depend on `skill-builder` for authoring.\n",
            "Invoke $skill-builder after implementation.\n",
        )
        for relation in relations:
            with self.subTest(relation=relation):
                root = self._copy_repository()
                router = (
                    root
                    / "plugins"
                    / "codex-dev-flow"
                    / "skills"
                    / "use-expand"
                    / "SKILL.md"
                )
                router.write_text(
                    router.read_text(encoding="utf-8") + "\n" + relation,
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "use-expand" in error
                        and "affirmatively route to or depend on skill-builder" in error
                        for error in errors
                    ),
                    f"affirmative router relation escaped validation: {errors}",
                )

    def test_validator_allows_explicit_negative_lifecycle_boundaries(self) -> None:
        """Regression: naming forbidden dependencies must not itself create coupling."""

        root = self._copy_repository()
        skills = root / "plugins" / "codex-dev-flow" / "skills"
        builder = skills / "skill-builder" / "SKILL.md"
        builder.write_text(
            builder.read_text(encoding="utf-8")
            + "\nDo not invoke implement.\n"
            + "Never route to `plan`.\n"
            + "Must not depend on $design.\n"
            + "Do not invoke `$use-expand`.\n",
            encoding="utf-8",
        )
        router = skills / "use-expand" / "SKILL.md"
        router.write_text(
            router.read_text(encoding="utf-8")
            + "\nDo not route to skill-builder.\n"
            + "Never depend on `skill-builder`.\n"
            + "Must not invoke $skill-builder.\n",
            encoding="utf-8",
        )
        self.assertEqual(validate_repository(root), ())

    def test_validator_accepts_the_complete_design_package(self) -> None:
        """Regression: package validation must have an independent Design helper boundary."""
        errors = validate_repository(ROOT)
        self.assertEqual(errors, (), "validator must accept the complete Design package: " + "; ".join(errors))

    def test_validator_rejects_each_design_state_helper_mutation(self) -> None:
        """Regression: missing, duplicate, empty, nonregular, and symlink helpers must fail closed."""
        self.assertTrue(HELPER.is_file(), HELPER)
        mutations = ("missing", "duplicate", "empty", "directory", "symlink")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                root = self._copy_repository()
                helper = root / "plugins" / "codex-dev-flow" / "scripts" / "design_state.py"
                if mutation == "missing":
                    helper.unlink()
                elif mutation == "duplicate":
                    shadow = root / "plugins" / "codex-dev-flow" / "assets" / "design_state.py"
                    shadow.write_bytes(helper.read_bytes())
                elif mutation == "empty":
                    helper.write_bytes(b"")
                elif mutation == "directory":
                    helper.unlink()
                    helper.mkdir()
                else:
                    outside = root / "outside-design-state.py"
                    outside.write_text("outside\n", encoding="utf-8")
                    helper.unlink()
                    helper.symlink_to(outside)
                errors = validate_repository(root)
                self.assertTrue(
                    any("design state helper" in error.lower() for error in errors),
                    f"mutation {mutation} was accepted or failed for an unrelated reason: {errors}",
                )

    def test_installer_does_not_mutate_on_design_helper_preflight_failure(self) -> None:
        """Regression: invalid Design package state must fail before links, receipts, or external commands change."""
        root = self._copy_repository()
        helper = root / "plugins" / "codex-dev-flow" / "scripts" / "design_state.py"
        helper.unlink()
        codex_home = root / "codex-home"
        state_home = root / "state-home"
        runner = _NeverCalledRunner()
        with self.assertRaises(install.InstallError):
            install.install(root, codex_home, state_home, runner)
        self.assertEqual(runner.calls, [])
        self.assertFalse(codex_home.exists())
        self.assertFalse(state_home.exists())


if __name__ == "__main__":
    unittest.main()
