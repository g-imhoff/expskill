from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.build_codex_package import build_codex_package
from scripts.build_opencode_package import build_opencode_package
from scripts.render_codex import render_agents as render_codex_agents
from scripts.render_opencode import render_agents as render_opencode_agents


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"


class CodexSourceCorrectionTests(unittest.TestCase):
    def test_marketplace_builder_preserves_container_replaced_at_final_cleanup(self) -> None:
        from scripts.build_codex_marketplace import build_codex_marketplace

        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "marketplace"
                rename, rmdir = Path.rename, os.rmdir
                containers = []

                def published(path, target):
                    if target == output:
                        containers.append(path.parent)
                        if failed:
                            raise OSError(errno.EIO, "injected publication failure")
                    return rename(path, target)

                def cleanup(path, *args, **kwargs):
                    if containers and Path(path) == containers[0]:
                        rename(Path(path), root / "original-container")
                        Path(path).mkdir()
                        (Path(path) / "user-data").write_text("preserve replacement")
                    return rmdir(path, *args, **kwargs)

                with mock.patch.object(Path, "rename", published), mock.patch.object(os, "rmdir", cleanup):
                    if failed:
                        with self.assertRaises(OSError):
                            build_codex_marketplace(ROOT, output)
                    else:
                        self.assertEqual(build_codex_marketplace(ROOT, output), output)
                self.assertEqual((containers[0] / "user-data").read_text(), "preserve replacement")
                self.assertEqual(output.exists(), not failed)

    def test_marketplace_builder_retains_constructed_directory_before_publication(self) -> None:
        from scripts.build_codex_marketplace import (
            build_codex_marketplace, build_codex_marketplace_pinned,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "marketplace"
            rename = Path.rename
            original_identity = None

            def replaced(path, destination):
                nonlocal original_identity
                if destination == output:
                    original_identity = path.stat().st_ino
                    rename(path, root / "original")
                    path.mkdir()
                    (path / "user-data").write_text("preserve replacement")
                return rename(path, destination)

            with mock.patch.object(Path, "rename", replaced):
                path, descriptor = build_codex_marketplace_pinned(ROOT, output)
            try:
                self.assertEqual(path, output)
                self.assertEqual(os.fstat(descriptor).st_ino, original_identity)
                self.assertNotEqual(os.fstat(descriptor).st_ino, output.stat().st_ino)
                self.assertIn("plugins", os.listdir(descriptor))
            finally:
                os.close(descriptor)
            self.assertEqual((output / "user-data").read_text(), "preserve replacement")
            plain = root / "plain"
            self.assertEqual(build_codex_marketplace(ROOT, plain), plain)
            self.assertTrue((plain / "plugins/expskill/skills").is_dir())

    def test_agent_prose_has_one_neutral_source(self) -> None:
        content_path = PLUGIN_ROOT / "content" / "agents.json"
        content = json.loads(content_path.read_text(encoding="utf-8"))
        codex = json.loads(
            (PLUGIN_ROOT / "codex" / "agents.json").read_text(encoding="utf-8")
        )
        opencode = json.loads(
            (PLUGIN_ROOT / "opencode" / "agents.json").read_text(encoding="utf-8")
        )

        self.assertEqual(set(content), {"schema_version", "runtime_paragraph", "agents"})
        self.assertEqual(set(content["agents"]), set(codex["agents"]))
        self.assertEqual(set(content["agents"]), set(opencode["agents"]))
        for entry in codex["agents"].values():
            self.assertTrue({"description", "closing"}.isdisjoint(entry))
        for entry in opencode["agents"].values():
            self.assertTrue({"description", "closing"}.isdisjoint(entry))
        self.assertNotIn("runtime_paragraph", opencode)

    def test_agent_metadata_mutation_reaches_both_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "repo"
            import shutil

            shutil.copytree(ROOT, copied, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            content_path = copied / "plugins" / "expskill" / "content" / "agents.json"
            content = json.loads(content_path.read_text(encoding="utf-8"))
            marker = "Canonical metadata mutation marker."
            content["agents"]["expskill-review"]["description"] = marker
            content_path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")

            codex = render_codex_agents(copied)["agents/expskill-review.toml"]
            opencode = render_opencode_agents(copied)["expskill-review"]
            self.assertIn(marker, codex)
            self.assertIn(marker, opencode)

    def test_unslop_runtime_prose_is_canonical_content(self) -> None:
        policy_path = PLUGIN_ROOT / "content" / "policies" / "unslop-runtime.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(policy),
            {"schema_version", "scope", "compaction_reminder"},
        )
        self.assertIn("natural-language user-facing prose", policy["scope"])
        for adapter in (
            PLUGIN_ROOT / "codex" / "hooks" / "inject_unslop.py",
            PLUGIN_ROOT / "opencode" / "plugins" / "unslop.js",
        ):
            source = adapter.read_text(encoding="utf-8")
            self.assertNotIn(policy["scope"].strip(), source)
            self.assertNotIn("Cut grand claims", source)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = build_codex_package(ROOT, root / "codex")
            opencode = build_opencode_package(ROOT, root / "opencode")
            expected = policy_path.read_bytes()
            self.assertEqual((codex / "assets" / "unslop-runtime.json").read_bytes(), expected)
            self.assertEqual((opencode / "assets" / "unslop-runtime.json").read_bytes(), expected)

    def test_codex_agent_profiles_ship_inside_the_built_marketplace(self) -> None:
        from scripts.build_codex_marketplace import build_codex_marketplace

        with tempfile.TemporaryDirectory() as temporary:
            marketplace = build_codex_marketplace(ROOT, Path(temporary) / "marketplace")
            profiles = sorted(
                (marketplace / "plugins" / "expskill" / "agents").glob("*.toml")
            )

            self.assertEqual(len(profiles), 7)
            for profile in profiles:
                self.assertTrue(profile.is_file() and not profile.is_symlink())
                self.assertIn("developer_instructions", profile.read_text(encoding="utf-8"))

    def test_codex_package_is_regular_and_provenanced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = build_codex_package(ROOT, Path(temporary) / "package")
            self.assertEqual(
                len(tuple(output.glob("skills/*/SKILL.md"))),
                15,
            )
            self.assertEqual(
                len(tuple(output.glob("skills/*/agents/openai.yaml"))),
                15,
            )
            self.assertFalse(
                any(
                    "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}
                    for path in output.rglob("*")
                )
            )
            provenance = json.loads((output / "provenance.json").read_text(encoding="utf-8"))
            self.assertIn(
                "scripts/artifact_contract.py",
                {entry["path"] for entry in provenance["inputs"]},
            )
            self.assertIn(
                "scripts/build_codex_marketplace.py",
                {entry["path"] for entry in provenance["inputs"]},
            )

    def test_packaged_hook_uses_packaged_skill_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = build_codex_package(ROOT, Path(temporary) / "package")
            environment = dict(os.environ)
            environment.pop("PLUGIN_ROOT", None)
            result = subprocess.run(
                [sys.executable, str(output / "hooks" / "inject_unslop.py")],
                input=json.dumps(
                    {"hook_event_name": "SessionStart", "source": "startup"}
                )
                + "\n",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(
                "additionalContext",
                payload["hookSpecificOutput"],
            )


if __name__ == "__main__":
    unittest.main()
