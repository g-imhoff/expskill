from __future__ import annotations

import ast
import json
import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.build_codex_marketplace import build_codex_marketplace
from scripts.validate import validate_repository
from scripts.render_codex import render_agents


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
ROUTER_ROOT = PLUGIN_ROOT / "content" / "skills" / "use-expskill"
SKILL_BUILDER_ROOT = PLUGIN_ROOT / "content" / "skills" / "skill-builder"
PHASE_ROOTS = {
    name: PLUGIN_ROOT / "content" / "skills" / name
    for name in (
        "brainstorm",
        "design",
        "grill-me",
        "setup-design",
        "setup-test",
        "plan",
        "implement",
        "correct",
        "review",
        "test",
        "unslop",
        "autonomous-run",
        "review-loop",
    )
}
PUBLIC_SKILL_ROOTS = {
    "use-expskill": ROUTER_ROOT,
    **PHASE_ROOTS,
    "skill-builder": SKILL_BUILDER_ROOT,
}
EXPECTED_AGENTS = {
    "expskill-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "expskill-test-engineer": ("gpt-5.6-luna", "max", "read-only"),
    "expskill-planner": ("gpt-5.6-luna", "max", "workspace-write"),
    "expskill-designer": ("gpt-5.6-luna", "max", "workspace-write"),
    "expskill-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "expskill-review": ("gpt-5.6-sol", "xhigh", "read-only"),
    "expskill-spec": ("gpt-5.6-sol", "xhigh", "read-only"),
}
EXPECTED_SKILLS = {
    "use-expskill",
    "brainstorm",
    "design",
    "grill-me",
    "setup-design",
    "setup-test",
    "plan",
    "implement",
    "correct",
    "review",
    "test",
    "skill-builder",
    "unslop",
    "autonomous-run",
    "review-loop",
}
REMOVED_PUBLIC_SKILL = "accept" + "ance"
REMOVED_PUBLIC_TOKEN = "$" + REMOVED_PUBLIC_SKILL
PLUGIN_INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
    "defaultPrompt",
}
PUBLIC_SKILL_TOKENS = {f"${name}" for name in EXPECTED_SKILLS}
PUBLIC_METADATA_JARGON = re.compile(
    r"\b(?:quick|full|models?|caps?|scaffold|private[- ]marketplace|local plugin)\b",
    re.IGNORECASE,
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


class ContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        if (ROOT / ".agents").is_dir():
            shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "plugins", temporary / "plugins")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        shutil.copy2(ROOT / "README.md", temporary / "README.md")
        (temporary / "docs").mkdir()
        shutil.copy2(ROOT / "docs" / "guide.md", temporary / "docs" / "guide.md")
        return temporary

    def load_manifest(self, root: Path) -> dict[str, object]:
        return json.loads(
            (root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )

    def load_marketplace(self, root: Path) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temporary:
            output = build_codex_marketplace(root, Path(temporary) / "marketplace")
            return json.loads(
                (output / ".agents" / "plugins" / "marketplace.json").read_text(
                    encoding="utf-8"
                )
            )

    def load_skill_frontmatter(self, skill_root: Path) -> dict[str, object]:
        try:
            lines = (skill_root / "SKILL.md").read_text(encoding="utf-8").splitlines()
        except OSError as error:
            self.fail(f"missing readable skill entrypoint: {skill_root / 'SKILL.md'} ({error})")
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        return _parse_yaml_mapping("\n".join(lines[1:end]))

    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual(validate_repository(ROOT), ())

    def test_legacy_project_identity_is_rejected(self) -> None:
        legacy_markers = (
            "-".join(("codex", "dev", "flow")),
            "$use-" + "expand",
            "dev" + "flow-review",
        )
        for marker in legacy_markers:
            with self.subTest(marker=marker):
                root = self.copy_repository()
                readme = root / "README.md"
                readme.write_text(
                    readme.read_text(encoding="utf-8") + f"\nLegacy marker: {marker}\n",
                    encoding="utf-8",
                )
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(
                    any("legacy project identity" in error.lower() for error in errors),
                    errors,
                )

    def test_missing_skills_directory_is_rejected(self) -> None:
        root = self.copy_repository()
        skills_path = root / "plugins" / "expskill" / "content" / "skills"
        shutil.rmtree(skills_path)
        errors = validate_repository(root, include_opencode=False)
        self.assertIn(f"skills directory is missing: {skills_path}", errors)

    def test_skills_file_is_rejected(self) -> None:
        root = self.copy_repository()
        skills_path = root / "plugins" / "expskill" / "content" / "skills"
        shutil.rmtree(skills_path, ignore_errors=True)
        skills_path.write_text("not a directory\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertIn(f"skills path must be a directory: {skills_path}", errors)

    def test_missing_required_skill_is_rejected(self) -> None:
        root = self.copy_repository()
        missing = root / "plugins" / "expskill" / "content" / "skills" / "brainstorm"
        self.assertTrue(missing.is_dir(), missing)
        if not missing.is_dir():
            return
        shutil.rmtree(missing)
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("brainstorm" in error and "missing" in error for error in errors))

    def test_missing_skill_builder_is_rejected(self) -> None:
        """Regression: the public creator must remain in the installable roster."""

        root = self.copy_repository()
        missing = root / "plugins" / "expskill" / "content" / "skills" / "skill-builder"
        shutil.rmtree(missing)
        errors = validate_repository(root, include_opencode=False)
        self.assertIn("required skill 'skill-builder' is missing", errors)

    def test_skill_builder_references_are_required_nonempty_utf8_regular_files(self) -> None:
        """Regression: missing, unreadable, or substituted references make the builder unusable."""

        references = (
            "references/artifact-contracts.md",
            "references/evaluation-rubric.md",
        )
        mutations = ("missing", "empty", "directory", "symlink", "invalid-utf8")
        for relative in references:
            for mutation in mutations:
                with self.subTest(reference=relative, mutation=mutation):
                    root = self.copy_repository()
                    reference = (
                        root
                        / "plugins"
                        / "expskill"
                        / "content"
                        / "skills"
                        / "skill-builder"
                        / relative
                    )
                    if mutation == "missing":
                        reference.unlink()
                    elif mutation == "empty":
                        reference.write_bytes(b"")
                    elif mutation == "directory":
                        reference.unlink()
                        reference.mkdir()
                    elif mutation == "symlink":
                        outside = root / f"outside-{reference.name}"
                        outside.write_text("outside\n", encoding="utf-8")
                        reference.unlink()
                        reference.symlink_to(outside)
                    else:
                        reference.write_bytes(b"\xff\xfe")
                    errors = tuple(error.lower() for error in validate_repository(root, include_opencode=False))
                    self.assertTrue(
                        any(
                            "skill 'skill-builder' required reference" in error
                            and relative in error
                            and (mutation != "invalid-utf8" or "utf-8 text" in error)
                            for error in errors
                        ),
                        f"{relative} mutation {mutation} escaped its required-file check: {errors}",
                    )

    def test_unexpected_skill_is_rejected(self) -> None:
        root = self.copy_repository()
        unexpected = root / "plugins" / "expskill" / "content" / "skills" / "surprise"
        unexpected.mkdir()
        (unexpected / "SKILL.md").write_text(
            "---\nname: surprise\ndescription: Unexpected skill\n---\n\nBody.\n",
            encoding="utf-8",
        )
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("surprise" in error and "unexpected" in error for error in errors))

    def test_required_skill_file_is_rejected(self) -> None:
        root = self.copy_repository()
        skill_path = root / "plugins" / "expskill" / "content" / "skills" / "brainstorm"
        self.assertTrue(skill_path.is_dir(), skill_path)
        if not skill_path.is_dir():
            return
        shutil.rmtree(skill_path)
        skill_path.write_text("not a directory\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("brainstorm" in error and "directory" in error for error in errors))

    def test_required_skill_without_skill_markdown_is_rejected(self) -> None:
        root = self.copy_repository()
        skill_path = root / "plugins" / "expskill" / "content" / "skills" / "brainstorm"
        self.assertTrue(skill_path.is_dir(), skill_path)
        if not skill_path.is_dir():
            return
        (skill_path / "SKILL.md").unlink()
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("brainstorm" in error and "SKILL.md" in error for error in errors))

    def test_repository_contains_exact_required_skill_roster(self) -> None:
        skills_root = PLUGIN_ROOT / "content" / "skills"
        self.assertEqual({path.name for path in skills_root.iterdir()}, EXPECTED_SKILLS)

    def test_removed_acceptance_skill_directory_is_absent(self) -> None:
        """Regression: the deleted phase must not remain installable as a public skill."""

        self.assertFalse((PLUGIN_ROOT / "content" / "skills" / REMOVED_PUBLIC_SKILL).exists())
        self.assertNotIn(REMOVED_PUBLIC_SKILL, EXPECTED_SKILLS)

    def test_removed_repository_local_improve_skill_is_absent(self) -> None:
        """Regression: the superseded private copy must not shadow the public builder."""

        self.assertFalse((ROOT / ".agents" / "skills" / "improve-skill").exists())

    def test_public_surface_contains_no_removed_phase_route_or_token(self) -> None:
        """Regression: stale token, alias, or route keeps the removed phase publicly reachable."""

        surface_roots = (
            ROOT / "README.md",
            ROOT / "docs" / "guide.md",
            ROOT / ".agents" / "skills" / "improve-skill",
            ROOT / "docs" / "plans",
            ROOT / "docs" / "specs",
            ROOT / "plugins" / "expskill",
            ROOT / "scripts",
            ROOT / "tests",
        )
        files: list[Path] = []
        for surface_root in surface_roots:
            files.extend(
                [surface_root]
                if surface_root.is_file()
                else [
                    path
                    for path in surface_root.rglob("*")
                    if path.is_file()
                    and path.suffix
                    in {".md", ".py", ".yaml", ".yml", ".json", ".toml", ".csv", ".txt"}
                ]
            )
        route_markers = (
            rf"\bname\s*[:=]\s*{re.escape(REMOVED_PUBLIC_SKILL)}\b",
            rf"\bselected_phase\s*[:=]\s*{re.escape(REMOVED_PUBLIC_SKILL)}\b",
            rf"\bnext_skill\s*[:=]\s*{re.escape(REMOVED_PUBLIC_SKILL)}\b",
            rf"\b(?:alias|aliases|deprecated|route)\b\s*[:=]?\s*[`$]?{re.escape(REMOVED_PUBLIC_SKILL)}\b",
            (
                rf"\btarget\b(?:\s*[:=]\s*[`$]?{re.escape(REMOVED_PUBLIC_SKILL)}\b"
                rf"|[ \t]+[`$]?{re.escape(REMOVED_PUBLIC_SKILL)}\b"
                r"(?![ \t]+\w))"
            ),
            rf"\b(?:deprecated|alias|aliases)\b.{{0,32}}\b{re.escape(REMOVED_PUBLIC_SKILL)}\b",
            rf"(?:require|recommend|route|select|invoke)\s+[`$]?{re.escape(REMOVED_PUBLIC_SKILL)}\b",
            rf"\b{re.escape(REMOVED_PUBLIC_SKILL)}\s+phase\b",
        )
        stale_route_mutations = (
            f"target {REMOVED_PUBLIC_SKILL}",
        )
        ordinary_noun_phrases = (
            f"target {REMOVED_PUBLIC_SKILL} check",
            f"target {REMOVED_PUBLIC_SKILL} criteria",
            f"target {REMOVED_PUBLIC_SKILL} tests",
        )
        for mutation in stale_route_mutations:
            with self.subTest(stale_route_mutation=mutation):
                self.assertTrue(
                    any(re.search(marker, mutation, re.IGNORECASE) for marker in route_markers),
                    mutation,
                )
        for phrase in ordinary_noun_phrases:
            with self.subTest(ordinary_noun_phrase=phrase):
                self.assertFalse(
                    any(re.search(marker, phrase, re.IGNORECASE) for marker in route_markers),
                    phrase,
                )
        for path in files:
            if path.name in {"test_design_contract.py", "test_design_state.py", "test_design_acceptance.py"}:
                continue
            contents = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn(REMOVED_PUBLIC_TOKEN, contents)
                for marker in route_markers:
                    self.assertIsNone(re.search(marker, contents, re.IGNORECASE), marker)

    def test_generic_acceptance_terminology_remains_valid(self) -> None:
        """Regression: removing the phase must not ban ordinary acceptance vocabulary."""

        root = self.copy_repository()
        plan = root / "plugins" / "expskill" / "content" / "skills" / "plan" / "SKILL.md"
        plan.write_text(
            plan.read_text(encoding="utf-8")
            + "\nDocument acceptance criteria and the acceptance test suite here.\n",
            encoding="utf-8",
        )
        self.assertEqual(validate_repository(root, include_opencode=False), ())

    def test_reintroduced_acceptance_skill_is_rejected(self) -> None:
        """Regression: a stale acceptance directory must fail repository validation."""

        root = self.copy_repository()
        acceptance = root / "plugins" / "expskill" / "content" / "skills" / REMOVED_PUBLIC_SKILL
        self.assertFalse(acceptance.exists())
        acceptance.mkdir()
        (acceptance / "SKILL.md").write_text(
            "---\nname: "
            + REMOVED_PUBLIC_SKILL
            + "\ndescription: Define observable evidence for a change\n---\n\nStale.\n",
            encoding="utf-8",
        )
        agents = acceptance / "agents"
        agents.mkdir()
        (agents / "openai.yaml").write_text(
            "interface:\n"
            "  display_name: \"Acceptance\"\n"
            "  short_description: \"Define tests and acceptance evidence\"\n"
            f"  default_prompt: \"Use {REMOVED_PUBLIC_TOKEN} to define evidence.\"\n"
            "policy:\n"
            "  allow_implicit_invocation: false\n",
            encoding="utf-8",
        )
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(
            any(REMOVED_PUBLIC_SKILL in error and "unexpected" in error for error in errors),
            errors,
        )

    def test_reintroduced_acceptance_token_is_rejected(self) -> None:
        """Regression: a stale public token must fail validation independently of a stale directory."""

        root = self.copy_repository()
        manifest_path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["interface"]["longDescription"] += f" {REMOVED_PUBLIC_TOKEN}"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any(REMOVED_PUBLIC_TOKEN in error for error in errors), errors)

    def test_plugin_and_marketplace_identities_are_exact(self) -> None:
        manifest = self.load_manifest(ROOT)
        marketplace = self.load_marketplace(ROOT)
        self.assertEqual(manifest["name"], "expskill")
        self.assertEqual(manifest["version"].split("+", 1)[0], "0.1.8")
        self.assertEqual(manifest["repository"], "https://github.com/g-imhoff/expskill")
        self.assertEqual(manifest["skills"], "./content/skills/")
        self.assertEqual(manifest["interface"]["category"], "Developer Tools")
        self.assertEqual(marketplace["name"], "expskill")
        self.assertEqual(marketplace["plugins"][0]["name"], "expskill")
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], "./plugins/expskill")
        self.assertEqual(marketplace["plugins"][0]["category"], "Developer Tools")

    def test_plugin_metadata_describes_the_actual_standalone_skill_surface(self) -> None:
        """Regression: generated scaffold copy hides the plugin's real public workflow."""

        manifest = self.load_manifest(ROOT)
        description = manifest.get("description")
        self.assertIsInstance(description, str)
        self.assertLessEqual(len(str(description)), 120)
        for phrase in ("fourteen", "independent", "skills", "optional", "lifecycle router"):
            self.assertIn(phrase, str(description).lower())
        self.assertNotRegex(str(description), PUBLIC_METADATA_JARGON)
        self.assertEqual(manifest.get("author"), {"name": "g-imhoff"})

        interface = manifest.get("interface")
        self.assertIsInstance(interface, dict)
        if not isinstance(interface, dict):
            return
        self.assertEqual(set(interface), PLUGIN_INTERFACE_FIELDS)
        self.assertEqual(interface.get("displayName"), "ExpSkill")
        self.assertEqual(interface.get("developerName"), "g-imhoff")
        self.assertEqual(interface.get("category"), "Developer Tools")
        self.assertEqual(interface.get("capabilities"), [])
        self.assertLessEqual(len(str(interface.get("shortDescription"))), 80)
        self.assertIn("skills", str(interface.get("shortDescription")).lower())
        self.assertIn("routing", str(interface.get("shortDescription")).lower())
        self.assertNotRegex(str(interface.get("shortDescription")), PUBLIC_METADATA_JARGON)
        long_description = str(interface.get("longDescription"))
        self.assertLessEqual(len(long_description), 320)
        for token in PUBLIC_SKILL_TOKENS:
            self.assertIn(token, long_description)
        for phrase in ("directly", "next lifecycle step", "implementation review", "specification gates"):
            self.assertIn(phrase, long_description.lower())
        self.assertNotRegex(long_description, PUBLIC_METADATA_JARGON)
        default_prompt = str(interface.get("defaultPrompt"))
        self.assertLessEqual(len(default_prompt), 160)
        self.assertIn("$use-expskill", default_prompt)
        self.assertIn("next lifecycle step", default_prompt.lower())
        self.assertNotRegex(default_prompt, PUBLIC_METADATA_JARGON)

        mutations = (
            ("description", None, "Generic plugin scaffold."),
            ("author", None, {"name": "Local developer"}),
            ("interface", "displayName", "Other Flow"),
            ("interface", "shortDescription", "Use ExpSkill."),
            ("interface", "shortDescription", "Quick model phase routing"),
            ("interface", "longDescription", "ExpSkill adds a local Codex plugin scaffold."),
            ("interface", "developerName", "Local developer"),
            ("interface", "category", "Other"),
            ("interface", "capabilities", ["undeclared"]),
            ("interface", "defaultPrompt", "Help me use ExpSkill."),
        )
        for section, field, replacement in mutations:
            with self.subTest(section=section, field=field):
                root = self.copy_repository()
                path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                if field is None:
                    payload[section] = replacement
                else:
                    payload[section][field] = replacement
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(
                    any(section in error and (field is None or field in error) for error in errors),
                    errors,
                )

        for field in sorted(PLUGIN_INTERFACE_FIELDS):
            with self.subTest(missing_field=field):
                root = self.copy_repository()
                path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["interface"].pop(field)
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(any("interface" in error and field in error for error in errors), errors)

        wrong_types = {
            "displayName": [],
            "shortDescription": [],
            "longDescription": [],
            "developerName": [],
            "category": [],
            "capabilities": {},
            "defaultPrompt": [],
        }
        for field, replacement in wrong_types.items():
            with self.subTest(wrong_type=field):
                root = self.copy_repository()
                path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["interface"][field] = replacement
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(any("interface" in error and field in error for error in errors), errors)

        for token in sorted(PUBLIC_SKILL_TOKENS):
            with self.subTest(missing_phase_token=token):
                root = self.copy_repository()
                path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["interface"]["longDescription"] = payload["interface"]["longDescription"].replace(
                    token, token.removeprefix("$"),
                )
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(
                    any("longDescription" in error and token in error for error in errors),
                    errors,
                )

        root = self.copy_repository()
        path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["interface"]["unexpected"] = True
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("interface" in error and "unexpected" in error for error in errors), errors)

    def test_guide_and_manifest_count_standalone_skills(self) -> None:
        """Regression: public documentation must expose the lean skill surface."""

        expected = re.compile(
            r"\bfourteen independent skills and one optional lifecycle router\b"
        )
        paths = (
            ROOT / "docs" / "guide.md",
            PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
        )
        for path in paths:
            normalized = " ".join(path.read_text(encoding="utf-8").lower().split())
            with self.subTest(path=path):
                self.assertRegex(normalized, expected)

    def test_root_readme_is_a_required_nonempty_regular_file(self) -> None:
        """Regression: validation cannot silently skip the repository's public contract."""

        expected_fragments = {
            "missing": ("readme", "missing"),
            "empty": ("readme", "non-empty regular file"),
            "directory": ("readme", "regular file"),
            "symlink": ("readme", "symlink"),
            "invalid-utf8": ("readme", "utf-8 text"),
        }
        for mutation, fragments in expected_fragments.items():
            with self.subTest(mutation=mutation):
                root = self.copy_repository()
                readme = root / "README.md"
                if mutation == "missing":
                    readme.unlink()
                elif mutation == "empty":
                    readme.write_bytes(b"")
                elif mutation == "directory":
                    readme.unlink()
                    readme.mkdir()
                elif mutation == "symlink":
                    outside = root / "outside-readme.md"
                    outside.write_text("outside\n", encoding="utf-8")
                    readme.unlink()
                    readme.symlink_to(outside)
                else:
                    readme.write_bytes(b"\xff\xfe")
                try:
                    errors = tuple(error.lower() for error in validate_repository(root, include_opencode=False))
                except UnicodeError as error:
                    self.fail(f"README mutation {mutation} leaked a decode exception: {error}")
                self.assertTrue(
                    any(all(fragment in error for fragment in fragments) for error in errors),
                    f"README mutation {mutation} escaped its required-file check: {errors}",
                )

    def test_public_guide_is_a_required_nonempty_regular_file(self) -> None:
        """Regression: validation cannot silently skip the repository's public contract."""

        expected_fragments = {
            "missing": ("guide", "missing"),
            "empty": ("guide", "non-empty regular file"),
            "directory": ("guide", "regular file"),
            "symlink": ("guide", "symlink"),
            "invalid-utf8": ("guide", "utf-8 text"),
        }
        for mutation, fragments in expected_fragments.items():
            with self.subTest(mutation=mutation):
                root = self.copy_repository()
                guide = root / "docs" / "guide.md"
                if mutation == "missing":
                    guide.unlink()
                elif mutation == "empty":
                    guide.write_bytes(b"")
                elif mutation == "directory":
                    guide.unlink()
                    guide.mkdir()
                elif mutation == "symlink":
                    outside = root / "outside-guide.md"
                    outside.write_text("outside\n", encoding="utf-8")
                    guide.unlink()
                    guide.symlink_to(outside)
                else:
                    guide.write_bytes(b"\xff\xfe")
                try:
                    errors = tuple(error.lower() for error in validate_repository(root, include_opencode=False))
                except UnicodeError as error:
                    self.fail(f"Guide mutation {mutation} leaked a decode exception: {error}")
                self.assertTrue(
                    any(all(fragment in error for fragment in fragments) for error in errors),
                    f"Guide mutation {mutation} escaped its required-file check: {errors}",
                )

    def test_skill_builder_is_visible_as_an_independent_direct_skill(self) -> None:
        """Regression: the evidence-gated creator stays public without joining the lifecycle."""

        readme = (ROOT / "docs" / "guide.md").read_text(encoding="utf-8")
        manifest = self.load_manifest(ROOT)
        long_description = str(manifest["interface"]["longDescription"])
        self.assertIn("$skill-builder", long_description)
        self.assertRegex(
            readme,
            re.compile(
                r"(?m)^- `\$skill-builder` .*create.*improve.*one exact agent skill",
                re.IGNORECASE,
            ),
        )
        self.assertRegex(readme, re.compile(r"(?m)^Use \$skill-builder\b"))
        self.assertNotIn("$use-expskill", SKILL_BUILDER_ROOT.joinpath("SKILL.md").read_text(encoding="utf-8"))
        self.assertNotIn(
            "$skill-builder",
            ROUTER_ROOT.joinpath("SKILL.md").read_text(encoding="utf-8"),
        )

    def test_setup_design_is_a_bounded_independent_setup_skill(self) -> None:
        """Regression: design-sketch setup stays reusable and independently callable."""

        root = PHASE_ROOTS["setup-design"]
        files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            files,
            {
                "SKILL.md",
                "references/record-format.md",
            },
        )
        adapter = (
            PLUGIN_ROOT / "codex" / "skill-adapters" / root.name / "agents" / "openai.yaml"
        )
        self.assertTrue(adapter.is_file(), adapter)
        self.assertIn("allow_implicit_invocation: false", adapter.read_text(encoding="utf-8"))
        skill = root.joinpath("SKILL.md").read_text(encoding="utf-8")
        record_format = root.joinpath("references/record-format.md").read_text(
            encoding="utf-8"
        )
        router = ROUTER_ROOT.joinpath("SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            ".expskill/setup-design.md",
            "ordinary invocation",
            "explicit confirmation",
        ):
            self.assertIn(phrase, skill)
        self.assertIn("expskill.setup-design.v1", record_format)
        self.assertIn("$setup-design", router)
        self.assertIn("There is no setup gate.", router)
        self.assertNotIn("inspect_setup.py", router)
        self.assertNotIn("$setup-ui-testing", router)
        self.assertIn("parallel-plan-design", router)

    def test_setup_test_is_a_bounded_independent_setup_skill(self) -> None:
        """Regression: test-method setup stays reusable and independently callable."""

        root = PHASE_ROOTS["setup-test"]
        files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            files,
            {
                "SKILL.md",
                "references/record-format.md",
            },
        )
        adapter = (
            PLUGIN_ROOT / "codex" / "skill-adapters" / root.name / "agents" / "openai.yaml"
        )
        self.assertTrue(adapter.is_file(), adapter)
        self.assertIn("allow_implicit_invocation: false", adapter.read_text(encoding="utf-8"))
        skill = root.joinpath("SKILL.md").read_text(encoding="utf-8")
        record_format = root.joinpath("references/record-format.md").read_text(
            encoding="utf-8"
        )
        router = ROUTER_ROOT.joinpath("SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            ".expskill/setup-test.md",
            "ordinary invocation",
            "explicit confirmation",
        ):
            self.assertIn(phrase, skill)
        self.assertIn("expskill.setup-test.v1", record_format)
        self.assertIn("$setup-test", router)
        self.assertIn("There is no setup gate.", router)
        self.assertNotIn("inspect_setup.py", router)
        self.assertNotIn("$setup-ui-testing", router)
        self.assertIn("parallel-plan-design", router)

    def test_codex_cachebuster_versions_are_valid(self) -> None:
        for version in ("0.1.8", "0.1.8+codex.cache-1", "0.1.8+codex.a.b-2"):
            with self.subTest(version=version):
                root = self.copy_repository()
                manifest_path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["version"] = version
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                self.assertEqual(validate_repository(root, include_opencode=False), ())

    def test_invalid_codex_cachebuster_versions_are_rejected(self) -> None:
        invalid_versions = (
            "0.1.3",
            "0.1.8+other.cache",
            "0.1.8+codex.",
            "0.1.8+codex.a..b",
            "0.1.8+codex.a b",
            "0.1.8+codex.a/b",
            "0.1.8+codex.a_b",
        )
        for version in invalid_versions:
            with self.subTest(version=version):
                root = self.copy_repository()
                manifest_path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["version"] = version
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(any("version" in error for error in errors))

    def test_agent_profiles_match_exact_roster_and_required_fields(self) -> None:
        observed: dict[str, tuple[str, str, str]] = {}
        for relative, contents in sorted(render_agents(ROOT).items()):
            path = Path(relative)
            profile = tomllib.loads(contents)
            for field in (
                "name",
                "description",
                "model",
                "model_reasoning_effort",
                "sandbox_mode",
                "developer_instructions",
            ):
                self.assertIn(field, profile, path.name)
                self.assertIsInstance(profile[field], str, path.name)
                self.assertTrue(profile[field].strip(), path.name)
            observed[profile["name"]] = (
                profile["model"],
                profile["model_reasoning_effort"],
                profile["sandbox_mode"],
            )
        self.assertEqual(observed, EXPECTED_AGENTS)

    def test_agent_instructions_state_the_required_boundaries(self) -> None:
        required_phrases = {
            "expskill-explorer": ("read-only", "no fixes", "no delegation"),
            "expskill-test-engineer": (
                "test strategy",
                "shared acceptance tests",
                "regression",
                "no product implementation",
            ),
            "expskill-implementer": (
                "exactly one accepted node",
                "red-green-refactor",
                "one owned branch",
                "no delegation",
                "no scope expansion",
            ),
            "expskill-planner": (
                "private plan graph",
                "never edit",
                "only plan graph writer",
                "do not delegate",
            ),
            "expskill-designer": (
                "isolated helper-owned worktree",
                "one coherent local candidate commit",
                "do not write the plan graph",
                "do not delegate",
            ),
            "expskill-review": (
                "read-only",
                "severity",
                "evidence",
                "impact",
                "correction",
                "ready",
                "not ready",
            ),
            "expskill-spec": (
                "every accepted behavior",
                "criterion-by-criterion evidence",
                "no tracked-source edits",
                "pass or fail",
                "do not implement fixes",
                "do not expand scope",
                "do not delegate",
            ),
        }
        for name, phrases in required_phrases.items():
            profile = tomllib.loads(render_agents(ROOT)[f"agents/{name}.toml"])
            instructions = profile["developer_instructions"].lower()
            for phrase in phrases:
                self.assertIn(phrase, instructions, name)

    def test_manifest_has_no_unsupported_runtime_dependencies(self) -> None:
        manifest = self.load_manifest(ROOT)
        self.assertEqual(manifest.get("hooks"), "./codex/hooks/hooks.json")
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("icons", manifest)
        self.assertNotIn("authentication", manifest)

    def test_validator_rejects_banned_punctuation_in_any_skill_owned_text(self) -> None:
        for character, label in (("\N{EM DASH}", "em dash"), (";", "semicolon")):
            for surface in ("public", "repository-local"):
                with self.subTest(character=label, surface=surface):
                    root = self.copy_repository()
                    if surface == "public":
                        path = (
                            root
                            / "plugins"
                            / "expskill"
                            / "content"
                            / "skills"
                            / "unslop"
                            / "SKILL.md"
                        )
                        contents = path.read_text(encoding="utf-8")
                    else:
                        path = root / ".agents" / "skills" / "punctuation-probe" / "SKILL.md"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        contents = (
                            "---\n"
                            "name: punctuation-probe\n"
                            "description: Exercise repository-local punctuation validation\n"
                            "---\n"
                        )
                    path.write_text(
                        contents + f"\nBanned {character} mark.\n",
                        encoding="utf-8",
                    )
                    errors = validate_repository(root, include_opencode=False)
                    relative = path.relative_to(root).as_posix()
                    self.assertTrue(
                        any(label in error.lower() and relative in error for error in errors),
                        errors,
                    )

    def test_validator_rejects_missing_or_tampered_unslop_hook(self) -> None:
        for mutation in (
            "missing-config",
            "tampered-command",
            "missing-script",
            "tampered-script",
            "extra-source",
        ):
            with self.subTest(mutation=mutation):
                root = self.copy_repository()
                hook_root = root / "plugins" / "expskill" / "codex" / "hooks"
                if mutation == "missing-config":
                    (hook_root / "hooks.json").unlink()
                elif mutation == "tampered-command":
                    path = hook_root / "hooks.json"
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "true"
                    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                elif mutation == "missing-script":
                    (hook_root / "inject_unslop.py").unlink()
                elif mutation == "tampered-script":
                    path = hook_root / "inject_unslop.py"
                    path.write_text(
                        path.read_text(encoding="utf-8").replace(
                            '"additionalContext"',
                            '"wrongContext"',
                            1,
                        ),
                        encoding="utf-8",
                    )
                else:
                    (hook_root / "surprise.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(any("unslop hook" in error.lower() for error in errors), errors)

    def test_validator_rejects_tampered_pinned_upstream_source(self) -> None:
        root = self.copy_repository()
        path = (
            root
            / "plugins"
            / "expskill"
            / "content"
            / "third-party"
            / "sources"
            / "pstack"
            / "unslop"
            / "SKILL.md"
        )
        path.write_text(path.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("upstream" in error.lower() and "digest" in error.lower() for error in errors), errors)

    def test_validator_rejects_drift_in_public_third_party_derived_skills(self) -> None:
        for name in ("unslop", "grill-me"):
            with self.subTest(skill=name):
                root = self.copy_repository()
                path = root / "plugins" / "expskill" / "content" / "skills" / name / "SKILL.md"
                path.write_text(
                    path.read_text(encoding="utf-8") + "\nUntracked behavioral addition.\n",
                    encoding="utf-8",
                )
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(
                    any(name in error.lower() and "derived" in error.lower() for error in errors),
                    errors,
                )

    def test_missing_profile_is_rejected(self) -> None:
        root = self.copy_repository()
        (root / "plugins" / "expskill" / "content" / "agents" / "expskill-review.md").unlink()
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("expskill-review" in error and "missing" in error for error in errors))

    def test_unexpected_profile_is_rejected(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "expskill" / "content" / "agents" / "expskill-surprise.md"
        path.write_text("Unexpected profile.\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("expskill-surprise" in error and "unexpected" in error for error in errors))

    def test_duplicate_skill_name_is_rejected(self) -> None:
        root = self.copy_repository()
        skills_root = root / "plugins" / "expskill" / "content" / "skills"
        for directory in (skills_root / "first", skills_root / "second"):
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                "---\nname: duplicate-skill\ndescription: A test skill\n---\n\nBody.\n",
                encoding="utf-8",
            )
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("duplicate-skill" in error and "duplicate" in error for error in errors))

    def test_wrong_explorer_model_is_rejected(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "expskill" / "codex" / "agents.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["agents"]["expskill-explorer"]["model"] = "gpt-5.6-not-allowed"
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("explorer" in error.lower() and "model" in error.lower() for error in errors))

    def test_reviewer_must_be_read_only(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "expskill" / "codex" / "agents.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["agents"]["expskill-review"]["sandbox_mode"] = "workspace-write"
        path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("expskill-review" in error and "read-only" in error for error in errors))

    def test_invalid_manifest_path_is_rejected(self) -> None:
        root = self.copy_repository()
        manifest_path = root / "plugins" / "expskill" / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["skills"] = "./missing-skills/"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("skills" in error and "path" in error for error in errors))

    def test_placeholder_text_is_rejected(self) -> None:
        root = self.copy_repository()
        path = root / "plugins" / "expskill" / "content" / "agents" / "expskill-spec.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n# [TODO: remove this]\n", encoding="utf-8")
        errors = validate_repository(root, include_opencode=False)
        self.assertTrue(any("placeholder" in error.lower() or "todo" in error.lower() for error in errors))

    def test_plan_graph_helper_is_unique_nonempty_and_package_owned(self) -> None:
        """Regression: graph-backed phases must not load a missing or shadow helper."""

        for mutation in ("missing", "symlink", "empty", "duplicate"):
            with self.subTest(mutation=mutation):
                root = self.copy_repository()
                plugin = root / "plugins" / "expskill"
                helper = plugin / "content" / "scripts" / "plan_graph.py"
                if mutation == "missing":
                    helper.unlink()
                elif mutation == "symlink":
                    target = root / "outside-plan-graph.py"
                    target.write_text("outside\n", encoding="utf-8")
                    helper.unlink()
                    helper.symlink_to(target)
                elif mutation == "empty":
                    helper.write_bytes(b"")
                else:
                    shadow = plugin / "content" / "duplicate" / "plan_graph.py"
                    shadow.parent.mkdir(parents=True, exist_ok=True)
                    shadow.write_bytes(helper.read_bytes())
                errors = validate_repository(root, include_opencode=False)
                self.assertTrue(
                    any("plan graph helper" in error.lower() for error in errors),
                    errors,
                )

    def test_validation_aggregates_independent_errors(self) -> None:
        root = self.copy_repository()
        adapter = root / "plugins" / "expskill" / "codex" / "agents.json"
        metadata = json.loads(adapter.read_text(encoding="utf-8"))
        metadata["agents"]["expskill-review"]["sandbox_mode"] = "workspace-write"
        adapter.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        (root / "plugins" / "expskill" / "content" / "agents" / "expskill-explorer.md").unlink()
        errors = validate_repository(root, include_opencode=False)
        self.assertGreaterEqual(len(errors), 2)
        self.assertTrue(any("expskill-review" in error for error in errors))
        self.assertTrue(any("explorer" in error for error in errors))

    def test_public_skill_frontmatter_matches_each_directory(self) -> None:
        """Regression: a copied or renamed skill is advertised under the wrong name."""

        for name, skill_root in PUBLIC_SKILL_ROOTS.items():
            with self.subTest(skill=name):
                frontmatter = self.load_skill_frontmatter(skill_root)
                self.assertEqual(set(frontmatter), {"name", "description"})
                self.assertEqual(frontmatter["name"], name)
                self.assertTrue(str(frontmatter["description"]).strip())

    def test_public_skill_metadata_has_exact_invocation_contract(self) -> None:
        """Regression: an independent skill becomes ambient or the router cannot be selected."""

        for name, skill_root in PUBLIC_SKILL_ROOTS.items():
            with self.subTest(skill=name):
                metadata_path = (
                    PLUGIN_ROOT
                    / "codex"
                    / "skill-adapters"
                    / name
                    / "agents"
                    / "openai.yaml"
                )
                self.assertTrue(metadata_path.is_file(), metadata_path)
                if not metadata_path.is_file():
                    continue
                metadata = _parse_yaml_mapping(
                    metadata_path.read_text(encoding="utf-8")
                )
                self.assertEqual(set(metadata), {"interface", "policy"})
                self.assertEqual(
                    set(metadata["interface"]),
                    {"display_name", "short_description", "default_prompt"},
                )
                self.assertEqual(set(metadata["policy"]), {"allow_implicit_invocation"})
                self.assertIs(metadata["policy"]["allow_implicit_invocation"], name == "use-expskill")
                self.assertIn(f"${name}", str(metadata["interface"]["default_prompt"]))

    def test_public_skill_entrypoints_are_not_reference_files(self) -> None:
        """Regression: a short placeholder body claims a skill without defining its boundary."""

        for name, skill_root in PUBLIC_SKILL_ROOTS.items():
            with self.subTest(skill=name):
                skill_path = skill_root / "SKILL.md"
                self.assertTrue(skill_path.is_file(), skill_path)
                if not skill_path.is_file():
                    continue
                body = skill_path.read_text(encoding="utf-8")
                self.assertGreater(len(body.splitlines()), 4, name)
                if name not in {
                    "brainstorm",
                    "design",
                    "setup-design",
                    "setup-test",
                    "skill-builder",
                    "test",
                }:
                    self.assertNotIn("references/", body.lower(), name)
                self.assertNotIn("route-code-change", body, name)
                self.assertNotIn("quick-code-change", body, name)
                self.assertNotIn("full-code-change", body, name)

    def test_plan_owns_implementation_and_proof_design_without_writing_tests(self) -> None:
        """Regression: plan must define implementation proof while remaining no-code/no-test."""

        body = (PHASE_ROOTS["plan"] / "SKILL.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "implementation and proof",
            "observable acceptance criteria",
            "positive and negative behavior",
            "verification intent",
            "relevant test surfaces and commands",
            "test work",
            "writes no code or tests",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)
        self.assertNotIn(f"next_skill: {REMOVED_PUBLIC_SKILL}", body)

    def test_implement_owns_workers_gates_corrections_and_local_integration(self) -> None:
        """Regression: Implement stays a lean coordinator while preserving every local gate."""

        body = (PHASE_ROOTS["implement"] / "SKILL.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "fresh `expskill-implementer`",
            "red-green-refactor",
            "fresh `expskill-review`",
            "fresh `expskill-spec`",
            "launch a new `expskill-implementer`",
            "three non-improving attempts",
            "integrate an accepted node",
            "whole target branch",
            "do not push",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)
        self.assertNotIn("implement_state.py", body)
        self.assertNotIn("command-attestation", body)

    def test_public_review_does_not_reintroduce_other_internal_gate_skills(self) -> None:
        """Regression: the public audit must not recreate lifecycle gate skills."""

        skills = PLUGIN_ROOT / "content" / "skills"
        self.assertTrue((skills / "review").is_dir())
        for name in ("verify", "integrate"):
            with self.subTest(skill=name):
                self.assertFalse((skills / name).exists())

    def test_use_expskill_routes_to_product_skills_not_internal_gates(self) -> None:
        """Regression: use-expskill selects product skills while Implement owns its internal gates."""

        body = " ".join(ROUTER_ROOT.joinpath("SKILL.md").read_text(encoding="utf-8").lower().split())
        for marker in (REMOVED_PUBLIC_TOKEN, "$review", "$verify", "$integrate"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, body)
        for phase in ("brainstorm", "plan", "design", "implement", "correct"):
            with self.subTest(phase=phase):
                self.assertIn(phase, body)
        self.assertIn("no separate review, verification, or integration routes", body)

    def test_public_skill_private_policy_vocabulary_is_rejected_on_both_surfaces(self) -> None:
        """Regression: phase instructions or UI copy leak private routing policy terms."""

        for token in ("quick", "full", "model", "cap", "caps"):
            with self.subTest(token=token, surface="entrypoint"):
                root = self.copy_repository()
                skill_path = (
                    root
                    / "plugins"
                    / "expskill"
                    / "content"
                    / "skills"
                    / "brainstorm"
                    / "SKILL.md"
                )
                skill_path.write_text(
                    f"{skill_path.read_text(encoding='utf-8')}\nReserved probe: {token}.\n",
                    encoding="utf-8",
                )
                self.assertIn(
                    "skill 'brainstorm' contains private policy vocabulary",
                    validate_repository(root, include_opencode=False),
                )

            with self.subTest(token=token, surface="metadata"):
                root = self.copy_repository()
                metadata_path = (
                    root
                    / "plugins"
                    / "expskill"
                    / "codex"
                    / "skill-adapters"
                    / "brainstorm"
                    / "agents"
                    / "openai.yaml"
                )
                lines = metadata_path.read_text(encoding="utf-8").splitlines()
                for index, line in enumerate(lines):
                    if line.strip().startswith("short_description:"):
                        prefix, separator, raw_value = line.partition(":")
                        self.assertTrue(separator)
                        value = ast.literal_eval(raw_value.strip())
                        lines[index] = f'{prefix}: "{value} {token}"'
                        break
                else:
                    self.fail("brainstorm metadata has no short_description")
                metadata_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.assertIn(
                    "skill 'brainstorm' interface.short_description contains private policy vocabulary",
                    validate_repository(root, include_opencode=False),
                )

    def test_unsupported_read_only_agent_runner_is_removed(self) -> None:
        self.assertFalse((PLUGIN_ROOT / "content" / "scripts" / "read_only_agent.py").exists())
        self.assertFalse((ROOT / "tests" / "test_read_only_agent.py").exists())

    def test_shipped_skills_describe_context_free_named_agent_isolation(self) -> None:
        for relative_path in (
            "plugins/expskill/content/skills/implement/SKILL.md",
            "plugins/expskill/content/skills/skill-builder/SKILL.md",
        ):
            body = (ROOT / relative_path).read_text(encoding="utf-8")
            with self.subTest(path=relative_path):
                self.assertIn("context-free", body)
                self.assertNotIn("read_only_agent.py", body)
                self.assertNotRegex(body, re.compile(r"isolated (?:read-only )?(?:Codex )?process", re.I))

    def test_repository_docs_do_not_reference_retired_workflow(self) -> None:
        references = []
        retired_name = "super" + "powers"
        for docs_root in (ROOT / "docs" / "plans", ROOT / "docs" / "specs"):
            for path in docs_root.glob("*.md"):
                if retired_name in path.read_text(encoding="utf-8").lower():
                    references.append(path.relative_to(ROOT))
        self.assertEqual(references, [])


if __name__ == "__main__":
    unittest.main()
