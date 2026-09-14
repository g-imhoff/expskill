from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.build_opencode_package import BuildError, build_opencode_package
from scripts.artifact_contract import (
    PLATFORM_FILES,
    PLATFORM_PLUGIN_DIRECTORY,
    PLATFORM_PLUGIN_FILES,
    artifact_output_relative,
)
from scripts.render_opencode import RenderError, render_agents, render_all, render_catalog


ROOT = Path(__file__).resolve().parents[1]


class FoundationLayoutTests(unittest.TestCase):
    def test_artifact_output_relative_declares_canonical_source_mapping(self) -> None:
        package_root = Path("plugins/expskill")
        cases = [
            *[
                (package_root / "opencode" / name, name)
                for name in PLATFORM_FILES
            ],
            *[
                (
                    package_root / "opencode" / PLATFORM_PLUGIN_DIRECTORY / name,
                    f"{PLATFORM_PLUGIN_DIRECTORY}/{name}",
                )
                for name in PLATFORM_PLUGIN_FILES
            ],
            (package_root / "opencode" / PLATFORM_PLUGIN_DIRECTORY / "unrelated.js", None),
            (
                package_root
                / "opencode"
                / PLATFORM_PLUGIN_DIRECTORY
                / "nested"
                / "unrelated.js",
                None,
            ),
            (package_root / "skills" / "unslop" / "SKILL.md", "skills/unslop/SKILL.md"),
            (package_root / "scripts" / "design_state.py", "scripts/design_state.py"),
            (package_root / "assets" / "execution-policy.json", "assets/execution-policy.json"),
            (
                package_root / "third-party" / "licenses" / "mattpocock-skills-MIT.txt",
                "third-party/licenses/mattpocock-skills-MIT.txt",
            ),
            (
                package_root / "third-party" / "licenses" / "pstack-MIT.txt",
                "third-party/licenses/pstack-MIT.txt",
            ),
            (Path("packages/expskill/opencode/agents.json"), None),
            (Path("packages/opencode/package.json"), None),
            (Path("packages/codex/README.md"), None),
            (Path("packages/expskill/skills/unslop/SKILL.md"), None),
            (Path("plugins/other/skills/unslop/SKILL.md"), None),
            (package_root / "opencode" / "catalog.json", None),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(artifact_output_relative(source), expected)

    def test_artifact_output_relative_rejects_noncanonical_lexical_spellings(self) -> None:
        package_root = "plugins/expskill"
        adversarial: list[tuple[str | Path, None]] = [
            # These must be rejected from the raw string before Path can
            # normalize the spelling away.
            (f"{package_root}//opencode/agents.json", None),
            (f"{package_root}/./opencode/agents.json", None),
            (f"/{package_root}/opencode/agents.json", None),
            (f"{package_root}/opencode/agents.json/", None),
            (f"{package_root}/opencode//agents.json", None),
            (f"{package_root}\\opencode\\agents.json", None),
            (Path(r"plugins\expskill\opencode\agents.json"), None),
            (Path(package_root) / "opencode" / "agents.json\\", None),
            (f"{package_root}/skills/../skills/unslop/SKILL.md", None),
            (Path(package_root) / "skills" / ".." / "skills" / "unslop" / "SKILL.md", None),
            (f"{package_root}/opencode/agents\x00.json", None),
            (Path(package_root) / "opencode" / "agents\x00.json", None),
            ("", None),
            (f"{package_root}/skills", None),
            (f"{package_root}/scripts", None),
            (f"{package_root}/third-party/licenses", None),
            (Path(package_root) / "skills", None),
            (Path(package_root) / "scripts", None),
            (Path(package_root) / "third-party" / "licenses", None),
        ]
        for source, expected in adversarial:
            with self.subTest(source=source):
                self.assertEqual(artifact_output_relative(source), expected)

    def test_artifact_output_relative_uses_one_text_fspath_result(self) -> None:
        class ConflictingPath(os.PathLike[str]):
            def __init__(self) -> None:
                self.fspath_calls = 0

            def __str__(self) -> str:
                return "plugins/expskill/skills/unslop/SKILL.md"

            def __fspath__(self) -> str:
                self.fspath_calls += 1
                return "plugins/expskill/skills/../escape.txt"

        source = ConflictingPath()
        self.assertIsNone(artifact_output_relative(source))
        self.assertEqual(source.fspath_calls, 1)

    def test_artifact_output_relative_rejects_str_subclass_from_pathlike(self) -> None:
        class HostileText(str):
            def split(self, *args: object, **kwargs: object) -> list[str]:
                return ["plugins", "expskill", "opencode", "agents.json"]

        class HostilePath(os.PathLike[str]):
            def __init__(self) -> None:
                self.fspath_calls = 0

            def __fspath__(self) -> str:
                self.fspath_calls += 1
                return HostileText("plugins/expskill/skills/../escape.txt")

        source = HostilePath()
        self.assertIsNone(artifact_output_relative(source))
        self.assertEqual(source.fspath_calls, 1)

    def test_artifact_output_relative_rejects_non_text_path_values(self) -> None:
        class BytesPath(os.PathLike[bytes]):
            def __fspath__(self) -> bytes:
                return b"plugins/expskill/opencode/agents.json"

        unsupported: tuple[object, ...] = (
            b"plugins/expskill/opencode/agents.json",
            BytesPath(),
            None,
            42,
            object(),
        )
        for source in unsupported:
            with self.subTest(source=source):
                self.assertIsNone(artifact_output_relative(source))  # type: ignore[arg-type]

    def test_plugin_tree_is_the_only_canonical_shared_source(self) -> None:
        plugin_root = ROOT / "plugins" / "expskill"
        self.assertTrue((plugin_root / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((plugin_root / "opencode").is_dir())
        self.assertEqual(len([path for path in (plugin_root / "skills").iterdir() if path.is_dir()]), 12)
        self.assertEqual(len(list((plugin_root / "assets" / "agents").glob("expskill-*.toml"))), 7)
        tracked = subprocess.run(
            ["git", "ls-files", "--", "packages/codex", "packages/opencode", "packages/expskill"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertEqual(tracked, [])

    def test_builder_uses_plugin_tree_when_legacy_package_mirror_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(ROOT, source)

            # A stale checkout can still contain an untracked package-shaped
            # mirror.  It must never become a second build input.
            legacy = source / "packages" / "expskill"
            shutil.copytree(source / "plugins" / "expskill", legacy, dirs_exist_ok=True)
            legacy_skill = legacy / "skills" / "unslop" / "SKILL.md"
            legacy_skill.write_text(
                legacy_skill.read_text(encoding="utf-8").replace(
                    "Cut AI tells", "legacy package mirror marker"
                ),
                encoding="utf-8",
            )

            artifact = source.parent / "artifact"
            build_opencode_package(source, artifact)
            self.assertEqual(len([path for path in (artifact / "skills").iterdir() if path.is_dir()]), 12)
            self.assertEqual(len(list((artifact / "commands").glob("*.md"))), 12)
            self.assertEqual(len(list((artifact / "agents").glob("*.md"))), 7)
            canonical_skill = source / "plugins" / "expskill" / "skills" / "unslop" / "SKILL.md"
            self.assertEqual(
                (artifact / "skills" / "unslop" / "SKILL.md").read_bytes(),
                canonical_skill.read_bytes(),
            )
            self.assertNotIn(
                "legacy package mirror marker",
                (artifact / "commands" / "unslop.md").read_text(encoding="utf-8"),
            )
            provenance = json.loads((artifact / "provenance.json").read_text(encoding="utf-8"))
            self.assertTrue(provenance["inputs"])
            self.assertFalse(any(item["path"].startswith("packages/") for item in provenance["inputs"]))

    def test_build_is_universal_deterministic_and_provenance_bound(self) -> None:
        self.assertTrue((ROOT / "plugins" / "expskill" / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((ROOT / "plugins" / "expskill").is_dir())
        self.assertTrue((ROOT / "plugins" / "expskill" / "opencode").is_dir())
        platform_root = ROOT / "plugins" / "expskill" / "opencode"
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
            skill = source / "plugins" / "expskill" / "skills" / "unslop" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8").replace("Cut AI tells", "Changed skill marker"), encoding="utf-8")
            profile = source / "plugins" / "expskill" / "assets" / "agents" / "expskill-review.toml"
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
            link = source / "plugins" / "expskill" / "skills" / "unslop" / "unsafe.txt"
            link.symlink_to(outside)
            with self.assertRaises(BuildError):
                build_opencode_package(source, temporary_root / "output")

    def test_builder_rejects_output_inside_real_source_tree_through_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            source = temporary_root / "source"
            shutil.copytree(ROOT, source)
            real_plugins = temporary_root / "real-plugins"
            shutil.move(source / "plugins", real_plugins)
            (source / "plugins").symlink_to(real_plugins, target_is_directory=True)
            output = real_plugins / "generated-output"
            with self.assertRaises(BuildError):
                build_opencode_package(source, output)
            self.assertFalse(output.exists())

    def test_build_and_pack_are_umask_deterministic(self) -> None:
        if shutil.which("npm") is None:
            self.skipTest("npm is required")
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            builds: list[Path] = []
            tarballs: list[Path] = []
            original_umask = os.umask(0o022)
            try:
                for value in (0o022, 0o077):
                    os.umask(value)
                    output = temporary_root / f"build-{value:o}"
                    build_opencode_package(ROOT, output)
                    builds.append(output)
                    pack_destination = temporary_root / f"pack-{value:o}"
                    pack_destination.mkdir()
                    result = subprocess.run(
                        ["npm", "pack", "--json", "--pack-destination", str(pack_destination)],
                        cwd=output,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    filename = json.loads(result.stdout)[0]["filename"]
                    tarballs.append(pack_destination / filename)
            finally:
                os.umask(original_umask)
            for build in builds:
                for path in build.rglob("*"):
                    mode = stat.S_IMODE(path.stat().st_mode)
                    expected = 0o755 if path.is_dir() else 0o644
                    self.assertEqual(mode, expected, path)
            self.assertEqual(
                hashlib.sha256(tarballs[0].read_bytes()).hexdigest(),
                hashlib.sha256(tarballs[1].read_bytes()).hexdigest(),
            )
            with tarfile.open(tarballs[0], "r:gz") as first, tarfile.open(tarballs[1], "r:gz") as second:
                first_metadata = [
                    (member.name, member.mode, member.mtime, member.uid, member.gid, member.uname, member.gname, member.type, member.size)
                    for member in first.getmembers()
                ]
                second_metadata = [
                    (member.name, member.mode, member.mtime, member.uid, member.gid, member.uname, member.gname, member.type, member.size)
                    for member in second.getmembers()
                ]
            self.assertEqual(first_metadata, second_metadata)

    def test_rendered_command_descriptions_are_bounded_and_catalog_parity_holds(self) -> None:
        rendered = render_all(ROOT)
        catalog = json.loads(rendered["catalog.json"])
        for name, entry in catalog["commands"].items():
            description = entry["description"]
            self.assertGreaterEqual(len(description), 1)
            self.assertLessEqual(len(description), 160)
            frontmatter = rendered[f"commands/{name}.md"].splitlines()
            command_description = frontmatter[1].split(":", 1)[1].strip()
            self.assertEqual(command_description.strip('"'), description)

    def test_validate_uses_artifact_renderer_and_rejects_malformed_overlay(self) -> None:
        from scripts.validate import validate_repository

        before = sorted(path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*"))
        self.assertEqual(validate_repository(ROOT), ())
        after = sorted(path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*"))
        self.assertEqual(before, after)
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "repo"
            shutil.copytree(ROOT, copied)
            overlay = copied / "plugins" / "expskill" / "opencode" / "agents.json"
            spec = json.loads(overlay.read_text(encoding="utf-8"))
            spec["agents"]["expskill-review"].pop("closing")
            overlay.write_text(json.dumps(spec), encoding="utf-8")
            errors = validate_repository(copied)
            self.assertTrue(any("closing" in error for error in errors), errors)

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

            canonical_output = source / "plugins" / "expskill" / "generated-output"
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
            spec_path = root / "plugins" / "expskill" / "opencode" / "agents.json"
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
            profile = root / "plugins" / "expskill" / "assets" / "agents" / "expskill-extra.toml"
            profile.write_text(
                (root / "plugins" / "expskill" / "assets" / "agents" / "expskill-spec.toml")
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
            profile = root / "plugins" / "expskill" / "assets" / "agents" / "expskill-review.toml"
            profile.write_text(
                profile.read_text(encoding="utf-8").replace(
                    "developer_instructions = \"\"\"", "developer_instructions = \"\"\"\nMutation marker: review prompt.\n", 1
                ),
                encoding="utf-8",
            )
            skill = root / "plugins" / "expskill" / "skills" / "plan" / "SKILL.md"
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
                    "\n---\n",
                    '\nmetadata:\n  opencode/slash: "true"\n  opencode/autoinvoke: "true"\n---\n',
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
