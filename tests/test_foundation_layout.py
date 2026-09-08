from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.build_opencode_package import BuildError, build_opencode_package


ROOT = Path(__file__).resolve().parents[1]


class FoundationLayoutTests(unittest.TestCase):
    def test_build_is_universal_deterministic_and_provenance_bound(self) -> None:
        self.assertTrue((ROOT / "packages" / "expskill" / ".codex-plugin" / "plugin.json").is_file())
        self.assertFalse((ROOT / "packages" / "codex").exists())
        self.assertFalse((ROOT / "packages" / "opencode").exists())
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


if __name__ == "__main__":
    unittest.main()
