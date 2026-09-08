from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.build_opencode_package import BuildError, build_opencode_package
from scripts.render_opencode import RenderError, render_agents, render_catalog


ROOT = Path(__file__).resolve().parents[1]


class FoundationLayoutTests(unittest.TestCase):
    def test_build_is_universal_deterministic_and_provenance_bound(self) -> None:
        self.assertTrue((ROOT / "packages" / "expskill" / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((ROOT / "packages" / "expskill").is_dir())
        self.assertTrue((ROOT / "packages" / "expskill" / "opencode").is_dir())
        platform_root = ROOT / "packages" / "expskill" / "opencode"
        for name in ("agents.json", "package.json", "README.md", "LICENSE", "index.js"):
            self.assertTrue((platform_root / name).is_file(), name)
        self.assertFalse((platform_root / "agents").exists())
        self.assertFalse((platform_root / "commands").exists())
        self.assertFalse((platform_root / "skills").exists())

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            first = temporary_root / "first"
            second = temporary_root / "second"
            build_opencode_package(ROOT, first)
            build_opencode_package(ROOT, second)
            first_files = sorted(path.relative_to(first).as_posix() for path in first.rglob("*"))
            second_files = sorted(path.relative_to(second).as_posix() for path in second.rglob("*"))
            self.assertEqual(first_files, second_files)
            for relative in first_files:
                left = first / relative
                right = second / relative
                if left.is_file():
                    self.assertEqual(left.read_bytes(), right.read_bytes(), relative)
                    self.assertFalse(left.is_symlink(), relative)
            self.assertEqual(json.loads((first / "catalog.json").read_text()), json.loads((second / "catalog.json").read_text()))
            provenance = json.loads((first / "provenance.json").read_text())
            self.assertEqual(provenance["schema_version"], "opencode-provenance.v1")
            paths = [item["path"] for item in provenance["inputs"]]
            self.assertEqual(paths, sorted(paths))
            for relative in ("scripts/build_opencode_package.py", "scripts/render_opencode.py"):
                with self.subTest(provenance_input=relative):
                    self.assertIn(relative, paths)
                    row = next(item for item in provenance["inputs"] if item["path"] == relative)
                    self.assertEqual(
                        row["sha256"],
                        hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
                    )
            for item in provenance["inputs"]:
                self.assertEqual(len(item["sha256"]), hashlib.sha256().digest_size * 2)
            self.assertFalse(any("__pycache__" in path or path.endswith((".pyc", ".pyo")) for path in first_files))

            source = temporary_root / "source"
            shutil.copytree(ROOT, source)
            skill = source / "packages" / "expskill" / "skills" / "unslop" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8").replace("Cut AI tells", "Changed skill marker"), encoding="utf-8")
            profile = source / "packages" / "expskill" / "assets" / "agents" / "expskill-review.toml"
            profile.write_text(profile.read_text(encoding="utf-8").replace("Independently review", "Changed agent marker"), encoding="utf-8")
            changed = temporary_root / "changed"
            build_opencode_package(source, changed)
            self.assertIn("Changed skill marker", (changed / "commands" / "unslop.md").read_text(encoding="utf-8"))
            self.assertIn("Changed skill marker", (changed / "catalog.json").read_text(encoding="utf-8"))
            self.assertIn("Changed agent marker", (changed / "agents" / "expskill-review.md").read_text(encoding="utf-8"))
            self.assertIn("Changed agent marker", (changed / "catalog.json").read_text(encoding="utf-8"))

    def test_builder_rejects_symlink_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            source = temporary_root / "source"
            shutil.copytree(ROOT, source)
            outside = temporary_root / "outside"
            outside.write_text("unsafe\n", encoding="utf-8")
            link = source / "packages" / "expskill" / "skills" / "unslop" / "unsafe.txt"
            link.symlink_to(outside)
            with self.assertRaises(BuildError):
                build_opencode_package(source, temporary_root / "output")

    def test_builder_rejects_unsafe_output_targets_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            source = temporary_root / "source"
            shutil.copytree(ROOT, source)
            sentinel = temporary_root / "sentinel"
            sentinel.mkdir()
            marker = sentinel / "must-survive.txt"
            marker.write_text("foreign content\n", encoding="utf-8")

            output_link = temporary_root / "output-link"
            output_link.symlink_to(sentinel, target_is_directory=True)
            with self.assertRaises(BuildError):
                build_opencode_package(source, output_link)
            self.assertEqual(marker.read_text(encoding="utf-8"), "foreign content\n")

            source_marker = source / "source-marker.txt"
            source_marker.write_text("source content\n", encoding="utf-8")
            for output in (source, source.parent, source / "generated-output"):
                with self.subTest(output=output):
                    with self.assertRaises(BuildError):
                        build_opencode_package(source, output)
                    self.assertEqual(source_marker.read_text(encoding="utf-8"), "source content\n")

            canonical_output = source / "packages" / "expskill" / "generated-output"
            with self.assertRaises(BuildError):
                build_opencode_package(source, canonical_output)
            self.assertEqual(source_marker.read_text(encoding="utf-8"), "source content\n")

            existing = temporary_root / "existing-output"
            existing.mkdir()
            existing_marker = existing / "must-survive.txt"
            existing_marker.write_text("existing content\n", encoding="utf-8")
            with self.assertRaises(BuildError):
                build_opencode_package(source, existing)
            self.assertEqual(existing_marker.read_text(encoding="utf-8"), "existing content\n")

    def test_renderer_requires_exact_canonical_overlay_agent_roster(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "root"
            shutil.copytree(ROOT, root)
            spec_path = root / "packages" / "expskill" / "opencode" / "agents.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))

            missing = json.loads(json.dumps(spec))
            missing["agents"].pop("expskill-spec")
            spec_path.write_text(json.dumps(missing), encoding="utf-8")
            with self.assertRaises(RenderError):
                render_agents(root)

            extra = json.loads(json.dumps(spec))
            extra["agents"]["expskill-extra"] = extra["agents"]["expskill-spec"]
            spec_path.write_text(json.dumps(extra), encoding="utf-8")
            with self.assertRaises(RenderError):
                render_agents(root)

            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            profile = root / "packages" / "expskill" / "assets" / "agents" / "expskill-extra.toml"
            profile.write_text(
                (root / "packages" / "expskill" / "assets" / "agents" / "expskill-spec.toml")
                .read_text(encoding="utf-8")
                .replace('name = "expskill-spec"', 'name = "expskill-extra"', 1),
                encoding="utf-8",
            )
            with self.assertRaises(RenderError):
                render_agents(root)

    def test_runtime_catalog_has_native_agent_and_command_shapes(self) -> None:
        catalog = render_catalog(ROOT)
        expected_agents = {
            "description",
            "mode",
            "model",
            "reasoningEffort",
            "permission",
            "prompt",
        }
        for name, entry in catalog["agents"].items():
            with self.subTest(agent=name):
                self.assertEqual(set(entry), expected_agents | ({"temperature"} if "temperature" in entry else set()))
                self.assertNotIn("closing", entry)
                self.assertIn("You run as an opencode subagent", entry["prompt"])
        for name, entry in catalog["commands"].items():
            with self.subTest(command=name):
                self.assertEqual(set(entry), {"description", "template"})
                self.assertIn(f"`{name}`", entry["template"])
                self.assertIn("$ARGUMENTS", entry["template"])
                self.assertNotIn("skill", entry)
                self.assertNotIn("metadata", entry)

    def test_runtime_catalog_tracks_instruction_and_command_source_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "root"
            shutil.copytree(ROOT, root)
            profile = root / "packages" / "expskill" / "assets" / "agents" / "expskill-review.toml"
            profile.write_text(
                profile.read_text(encoding="utf-8").replace(
                    "developer_instructions = \"\"\"", "developer_instructions = \"\"\"\nMutation marker: review prompt.\n", 1
                ),
                encoding="utf-8",
            )
            skill = root / "packages" / "expskill" / "skills" / "plan" / "SKILL.md"
            skill_contents = skill.read_text(encoding="utf-8")
            description_line = next(
                line for line in skill_contents.splitlines() if line.startswith("description:")
            )
            skill.write_text(
                skill_contents.replace(
                    description_line,
                    "description: Mutation marker: plan command description.",
                    1,
                ),
                encoding="utf-8",
            )
            catalog = render_catalog(root)
            self.assertIn("Mutation marker: review prompt.", catalog["agents"]["expskill-review"]["prompt"])
            self.assertEqual(
                catalog["commands"]["plan"]["description"],
                "Mutation marker: plan command description.",
            )
            self.assertIn("This command is explicit-only.", catalog["commands"]["plan"]["template"])

            skill.write_text(
                skill_contents.replace(
                    'opencode/autoinvoke: "false"',
                    'opencode/autoinvoke: "true"',
                    1,
                ),
                encoding="utf-8",
            )
            changed_catalog = render_catalog(root)
            self.assertIn(
                "This is the only skill that may activate without an explicit invocation.",
                changed_catalog["commands"]["plan"]["template"],
            )


if __name__ == "__main__":
    unittest.main()
