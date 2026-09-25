from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_codex_marketplace import build_codex_marketplace
from scripts.build_codex_package import BuildError, build_codex_package


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"


class ContentHostLayoutTests(unittest.TestCase):
    def test_shared_runtime_markdown_has_one_content_source_and_explicit_host_adapters(self) -> None:
        """Shared Markdown must live under content and each host gets an adapter."""

        content = PLUGIN_ROOT / "content"
        self.assertTrue((content / "skills").is_dir())
        self.assertTrue((content / "agents").is_dir())
        self.assertTrue((PLUGIN_ROOT / "codex").is_dir())
        self.assertTrue((PLUGIN_ROOT / "opencode").is_dir())
        self.assertFalse((PLUGIN_ROOT / "skills").exists())
        self.assertFalse((PLUGIN_ROOT / "assets" / "agents").exists())

        markdown = [
            path
            for path in PLUGIN_ROOT.rglob("*.md")
            if "third-party" not in path.parts
            and "runtime" not in path.parts
            and (path.name == "SKILL.md" or path.parent.name in {"agents", "commands"})
        ]
        outside_content = [path for path in markdown if content not in path.parents]
        self.assertEqual(outside_content, [])

    def test_generated_host_packages_are_not_tracked(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "--", "plugins/expskill/codex", "plugins/expskill/opencode"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        generated_suffixes = ("/SKILL.md", ".md")
        self.assertEqual(
            [
                path
                for path in tracked
                if path.endswith(generated_suffixes)
                and ("/skills/" in path or "/agents/" in path or "/commands/" in path)
            ],
            [],
        )
        self.assertFalse((ROOT / ".agents" / "plugins" / "marketplace.json").exists())

    def test_codex_marketplace_builder_emits_the_only_installable_codex_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marketplace = build_codex_marketplace(
                ROOT,
                Path(temporary) / "marketplace",
            )
            plugin = marketplace / "plugins" / "expskill"
            self.assertEqual(len(tuple(plugin.glob("skills/*/SKILL.md"))), 15)
            self.assertEqual(
                len(tuple(plugin.glob("skills/*/agents/openai.yaml"))),
                15,
            )
            self.assertTrue(
                (marketplace / ".agents" / "plugins" / "marketplace.json").is_file()
            )

    def test_codex_builder_emits_policy_in_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = build_codex_package(ROOT, Path(temporary) / "runtime")
            self.assertEqual(
                (output / "assets" / "execution-policy.json").read_bytes(),
                (PLUGIN_ROOT / "content" / "policies" / "execution-policy.json").read_bytes(),
            )

    def test_codex_builder_requires_an_existing_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(BuildError):
                build_codex_package(ROOT, Path(temporary) / "missing" / "runtime")


if __name__ == "__main__":
    unittest.main()
