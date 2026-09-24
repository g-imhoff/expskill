from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.artifact_contract import HERMES_PROVENANCE_SCHEMA_VERSION
from scripts.build_hermes_package import BuildError, build_hermes_package
from scripts.render_hermes import render_agents, skill_inventory


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"


def hermes_cli() -> Path | None:
    for candidate in (
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes",
    ):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


class HermesPackageTests(unittest.TestCase):
    def build_artifact(self, root: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        artifact = Path(temporary.name) / "artifact"
        build_hermes_package(root, artifact)
        self.addCleanup(temporary.cleanup)
        return temporary, artifact

    def test_build_is_deterministic_across_runs(self) -> None:
        _first_temporary, first = self.build_artifact(ROOT)
        _second_temporary, second = self.build_artifact(ROOT)
        first_files = sorted(
            path.relative_to(first).as_posix() for path in first.rglob("*") if path.is_file()
        )
        second_files = sorted(
            path.relative_to(second).as_posix() for path in second.rglob("*") if path.is_file()
        )
        self.assertEqual(first_files, second_files)
        for relative in first_files:
            with self.subTest(path=relative):
                self.assertEqual(
                    (first / relative).read_bytes(), (second / relative).read_bytes()
                )

    def test_artifact_holds_no_symlinks_or_caches_and_normalized_metadata(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        for path in artifact.rglob("*"):
            with self.subTest(path=str(path.relative_to(artifact))):
                metadata = os.lstat(path)
                self.assertFalse(stat.S_ISLNK(metadata.st_mode))
                self.assertNotIn("__pycache__", path.parts)
                self.assertNotIn(path.suffix, {".pyc", ".pyo"})
                expected = 0o755 if stat.S_ISDIR(metadata.st_mode) else 0o644
                self.assertEqual(stat.S_IMODE(metadata.st_mode), expected)
                self.assertEqual(metadata.st_mtime_ns, 0)

    def test_explicit_output_is_required_and_must_live_outside_the_repo(self) -> None:
        with self.assertRaises(BuildError):
            build_hermes_package(ROOT, None)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as temporary:
            inside = Path(temporary) / "repo" / "out"
            with self.assertRaises(BuildError):
                build_hermes_package(Path(temporary) / "repo", inside)

    def test_platform_roster_rejects_extra_checked_in_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            shutil.copytree(
                ROOT,
                root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            (root / "plugins" / "expskill" / "hermes" / "agents").mkdir()
            artifact = Path(temporary) / "artifact"
            with self.assertRaisesRegex(BuildError, "roster is invalid"):
                build_hermes_package(root, artifact)

    def test_provenance_schema_and_sorted_unique_inputs(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        provenance = json.loads((artifact / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(set(provenance), {"schema_version", "inputs"})
        self.assertEqual(provenance["schema_version"], HERMES_PROVENANCE_SCHEMA_VERSION)
        paths = [entry["path"] for entry in provenance["inputs"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(set(paths)), len(paths))
        for entry in provenance["inputs"]:
            self.assertEqual(set(entry), {"path", "sha256"})
            source = ROOT / entry["path"]
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(entry["sha256"], digest)

    def test_skills_roster_matches_canonical_inventory(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        names = skill_inventory(ROOT)
        self.assertEqual(
            sorted(path.name for path in (artifact / "skills").iterdir()), list(names)
        )
        self.assertEqual(len(names), 14)

    def test_agents_match_pure_renderer(self) -> None:
        _temporary, artifact = self.build_artifact(ROOT)
        rendered = render_agents(ROOT)
        actual = {
            path.name
            for path in (artifact / "agents").iterdir()
            if path.is_file() and not path.is_symlink()
        }
        self.assertEqual(actual, {f"{name}.md" for name in rendered})
        for name, contents in rendered.items():
            with self.subTest(agent=name):
                self.assertEqual((artifact / "agents" / f"{name}.md").read_text(encoding="utf-8"), contents)

    def test_built_package_passes_hermes_plugin_validation(self) -> None:
        cli = hermes_cli()
        if cli is None:
            self.skipTest("hermes CLI is not installed")
        _temporary, artifact = self.build_artifact(ROOT)
        result = subprocess.run(
            [str(cli), "plugins", "validate", str(artifact)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Validation passed", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
