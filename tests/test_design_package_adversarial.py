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
PLUGIN = ROOT / "plugins" / "expskill"
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

    def test_readme_and_manifest_expose_twelve_skills_including_correct(self) -> None:
        """Regression: stale phase language hides a direct skill or contradicts the public roster."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        description = str(manifest.get("description", ""))
        long_description = str(manifest.get("interface", {}).get("longDescription", ""))
        skill_wording = re.compile(r"\btwelve independent skills and one optional lifecycle router\b", re.I)
        self.assertRegex(" ".join(readme.split()), skill_wording)
        self.assertIn("$design", readme)
        self.assertIn("$test", readme)
        self.assertIn("$skill-builder", readme)
        self.assertIn("$setup-ui-testing", readme)
        self.assertIn("$review", readme)
        self.assertIn("$correct", readme)
        self.assertIn("$autonomous-run", readme)
        self.assertIn("$test", long_description)
        self.assertIn("$skill-builder", long_description)
        self.assertIn("$setup-ui-testing", long_description)
        self.assertIn("$review", long_description)
        self.assertIn("$correct", long_description)
        self.assertIn("$autonomous-run", long_description)
        self.assertRegex(description, skill_wording)
        self.assertNotRegex(readme, re.compile(r"\b(?:six|seven|eight|nine) independent development phases\b", re.I))
        self.assertNotRegex(description, re.compile(r"\b(?:six|seven|eight|nine) standalone development phases\b", re.I))

    def test_validator_accepts_skill_builder_maximum_package_shape(self) -> None:
        """Regression: the joined state helper is allowed without becoming required in this lane."""

        root = self._copy_repository()
        scripts = root / "plugins" / "expskill" / "skills" / "skill-builder" / "scripts"
        scripts.mkdir(exist_ok=True)
        state_helper = scripts / "run_state.py"
        if not state_helper.exists():
            state_helper.write_text(
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
            "stale-eleven-skill-wording": ("readme", "twelve independent skills"),
            "use-expskill-route": (
                "skill-builder",
                "another product skill invocation token",
            ),
            "lifecycle-coupling": ("skill-builder", "canonical boundary section"),
            "router-coupling": (
                "use-expskill",
                "must not name skill-builder",
            ),
            "repository-local-duplicate": (".agents/skills/improve-skill", "must be absent"),
        }
        for mutation, fragments in expected_fragments.items():
            with self.subTest(mutation=mutation):
                root = self._copy_repository()
                plugin = root / "plugins" / "expskill"
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
                elif mutation == "stale-eleven-skill-wording":
                    readme = root / "README.md"
                    readme.write_text(
                        readme.read_text(encoding="utf-8").replace(
                            "twelve independent skills",
                            "eleven independent skills",
                            1,
                        ),
                        encoding="utf-8",
                    )
                elif mutation == "use-expskill-route":
                    contract = builder / "SKILL.md"
                    contract.write_text(
                        contract.read_text(encoding="utf-8")
                        + "\nUse $use-expskill after finalization.\n",
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
                    router = plugin / "skills" / "use-expskill" / "SKILL.md"
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

    def test_validator_rejects_other_product_skill_tokens_in_skill_builder(self) -> None:
        """Regression: sentiment and formatting cannot permit a cross-skill invocation token."""

        additions = (
            "Invoke $brainstorm after finalization.",
            "Never route to `$design`.",
            "Do not stop at finalization: invoke $plan after finalization.",
            "There is no delay: route through **$implement** after finalization.",
            "Run $test after finalization.",
            "Without delay, invoke $use-expskill after finalization.",
            "Refuse to invoke $grill-me.",
            "Cannot finish without invoking $unslop.",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                root = self._copy_repository()
                contract = (
                    root
                    / "plugins"
                    / "expskill"
                    / "skills"
                    / "skill-builder"
                    / "SKILL.md"
                )
                contract.write_text(
                    contract.read_text(encoding="utf-8") + "\n" + addition + "\n",
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "skill-builder" in error
                        and "another product skill invocation token" in error
                        for error in errors
                    ),
                    f"cross-skill token escaped validation: {addition!r}: {errors}",
                )

    def test_validator_rejects_any_skill_builder_name_in_use_expskill(self) -> None:
        """Regression: the router cannot mention Skill Builder under any sentiment or markup."""

        additions = (
            "Route to skill-builder after implementation.",
            "Never depend on `skill-builder` for authoring.",
            "There is no exception: invoke $skill-builder after implementation.",
            "Proceed to **$Skill-Builder** after implementation.",
            "Cannot finish without invoking Skill Builder.",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                root = self._copy_repository()
                router = (
                    root
                    / "plugins"
                    / "expskill"
                    / "skills"
                    / "use-expskill"
                    / "SKILL.md"
                )
                router.write_text(
                    router.read_text(encoding="utf-8") + "\n" + addition + "\n",
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "use-expskill" in error and "must not name skill-builder" in error
                        for error in errors
                    ),
                    f"Skill Builder name escaped router validation: {addition!r}: {errors}",
                )

    def test_validator_pins_the_canonical_skill_builder_boundary_section(self) -> None:
        """Regression: arbitrary negative prose cannot replace or extend the pinned boundary."""

        mutations = (
            (
                "replace",
                "Do not invoke or depend on a product lifecycle phase",
                "Remain separate from the product lifecycle",
            ),
            (
                "append",
                "",
                "\nRemain separate from the lifecycle.\n",
            ),
            (
                "append",
                "",
                "\nExclude lifecycle routing.\n",
            ),
        )
        for operation, original, replacement in mutations:
            with self.subTest(operation=operation, replacement=replacement):
                root = self._copy_repository()
                contract = (
                    root
                    / "plugins"
                    / "expskill"
                    / "skills"
                    / "skill-builder"
                    / "SKILL.md"
                )
                contents = contract.read_text(encoding="utf-8")
                if operation == "replace":
                    contents = contents.replace(original, replacement, 1)
                else:
                    contents += replacement
                contract.write_text(contents, encoding="utf-8")
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "skill-builder" in error
                        and (
                            "canonical boundary section" in error
                            or "lifecycle wording outside" in error
                        )
                        for error in errors
                    ),
                    f"boundary drift escaped validation: {errors}",
                )

    def test_manifest_requires_the_exact_skill_builder_token(self) -> None:
        """Regression: a collision or wrong-case name is not the public invocation token."""

        for replacement in (
            "prefix$skill-builder",
            "$skill-builder-v2",
            "$skill-builder-preview",
            "$Skill-Builder",
        ):
            with self.subTest(replacement=replacement):
                root = self._copy_repository()
                manifest_path = (
                    root
                    / "plugins"
                    / "expskill"
                    / ".codex-plugin"
                    / "plugin.json"
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["interface"]["longDescription"] = manifest["interface"][
                    "longDescription"
                ].replace("$skill-builder", replacement)
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n",
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "longdescription" in error
                        and "advertise $skill-builder" in error
                        for error in errors
                    ),
                    f"manifest token collision {replacement!r} escaped validation: {errors}",
                )

    def test_metadata_requires_exact_two_sided_invocation_tokens(self) -> None:
        """Regression: metadata cannot satisfy invocation visibility with a collision token."""

        cases = (
            ("skill-builder", "prefix$skill-builder"),
            ("skill-builder", "$skill-builder-v2"),
            ("skill-builder", "$Skill-Builder"),
            ("use-expskill", "prefix$use-expskill"),
            ("use-expskill", "$use-expskill-preview"),
            ("use-expskill", "$Use-ExpSkill"),
        )
        for skill, replacement in cases:
            with self.subTest(skill=skill, replacement=replacement):
                root = self._copy_repository()
                metadata_path = (
                    root
                    / "plugins"
                    / "expskill"
                    / "skills"
                    / skill
                    / "agents"
                    / "openai.yaml"
                )
                metadata_path.write_text(
                    metadata_path.read_text(encoding="utf-8").replace(
                        f"${skill}", replacement, 1
                    ),
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        f"skill '{skill}'" in error
                        and "default_prompt must invoke the matching skill" in error
                        for error in errors
                    ),
                    f"metadata collision {replacement!r} escaped validation: {errors}",
                )

    def test_manifest_default_prompt_requires_exact_use_expskill_token(self) -> None:
        """Regression: the router prompt must contain the exact case-sensitive invocation."""

        for replacement in (
            "prefix$use-expskill",
            "$use-expskill-preview",
            "$Use-ExpSkill",
        ):
            with self.subTest(replacement=replacement):
                root = self._copy_repository()
                manifest_path = (
                    root
                    / "plugins"
                    / "expskill"
                    / ".codex-plugin"
                    / "plugin.json"
                )
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["interface"]["defaultPrompt"] = manifest["interface"][
                    "defaultPrompt"
                ].replace("$use-expskill", replacement)
                manifest_path.write_text(
                    json.dumps(manifest, indent=2) + "\n",
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "defaultprompt" in error and "invoke $use-expskill" in error
                        for error in errors
                    ),
                    f"manifest collision {replacement!r} escaped validation: {errors}",
                )

    def test_readme_invocation_requires_the_exact_skill_builder_token(self) -> None:
        """Regression: a collision or wrong-case example cannot invoke the public skill."""

        invocation = "Use $skill-builder to create or improve one exact agent skill"
        for replacement in (
            "prefix$skill-builder",
            "$skill-builder-v2",
            "$skill-builder-preview",
            "$Skill-Builder",
        ):
            with self.subTest(replacement=replacement):
                root = self._copy_repository()
                readme_path = root / "README.md"
                readme = readme_path.read_text(encoding="utf-8")
                self.assertIn(invocation, readme)
                readme_path.write_text(
                    readme.replace(
                        invocation,
                        invocation.replace("$skill-builder", replacement),
                    ),
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "readme" in error
                        and "direct $skill-builder invocation" in error
                        for error in errors
                    ),
                    f"README token collision {replacement!r} escaped validation: {errors}",
                )

    def test_readme_allows_only_the_two_canonical_skill_builder_mentions(self) -> None:
        """Regression: unmarked Skill Builder prose is structural drift regardless of sentiment."""

        additions = (
            "Skill Builder remains separate from lifecycle routing.",
            "Do not route to $skill-builder from the code lifecycle.",
            "Evidence from skill-builder must never become a lifecycle phase.",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                root = self._copy_repository()
                readme_path = root / "README.md"
                readme_path.write_text(
                    readme_path.read_text(encoding="utf-8") + "\n" + addition + "\n",
                    encoding="utf-8",
                )
                errors = tuple(error.lower() for error in validate_repository(root))
                self.assertTrue(
                    any(
                        "readme" in error
                        and "skill builder mentions must be exactly" in error
                        for error in errors
                    ),
                    f"extra README mention escaped validation: {addition!r}: {errors}",
                )

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
                helper = root / "plugins" / "expskill" / "scripts" / "design_state.py"
                if mutation == "missing":
                    helper.unlink()
                elif mutation == "duplicate":
                    shadow = root / "plugins" / "expskill" / "assets" / "design_state.py"
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
        helper = root / "plugins" / "expskill" / "scripts" / "design_state.py"
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
