from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.build_opencode_package as build_module
import scripts.install as install_module
from scripts.build_opencode_package import BuildError, build_opencode_package
from scripts.install import InstallError, install_opencode, uninstall_opencode


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "brainstorm",
    "design",
    "grill-me",
    "implement",
    "plan",
    "setup-ui-testing",
    "skill-builder",
    "test",
    "unslop",
    "use-expskill",
)
AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
PLUGINS = ("unslop.js", "execution-policy.js")


def seed_repository(path: Path) -> Path:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(ROOT / ".agents", path / ".agents", ignore=ignore)
    shutil.copytree(ROOT / "packages", path / "packages", ignore=ignore)
    shutil.copytree(ROOT / "scripts", path / "scripts", ignore=ignore)
    shutil.copy2(ROOT / "README.md", path / "README.md")
    return path


def receipt_path(state: Path) -> Path:
    return state / "expskill" / "install-opencode.json"


class FoundationCorrectionTests(unittest.TestCase):
    def test_reinstall_repairs_deleted_modified_and_unexpected_artifact_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            artifact = state / "expskill" / "opencode-artifact"
            (artifact / "agents" / "expskill-review.md").unlink()
            (artifact / "plugins" / "unslop.js").write_text("tampered\n", encoding="utf-8")
            (artifact / "unexpected.txt").write_text("foreign\n", encoding="utf-8")
            install_opencode(repo, config, state)
            self.assertTrue((artifact / "agents" / "expskill-review.md").is_file())
            self.assertNotEqual((artifact / "plugins" / "unslop.js").read_text(), "tampered\n")
            self.assertFalse((artifact / "unexpected.txt").exists())

    def test_legacy_receipt_migrates_and_uninstall_accepts_missing_old_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            legacy_root = repo / "packages" / "expskill"
            links = []
            for name in SKILLS:
                links.append({"source": str(legacy_root / "skills" / name), "destination": str(config / "skills" / name)})
            for name in SKILLS:
                links.append({"source": str(legacy_root / "opencode" / "commands" / f"{name}.md"), "destination": str(config / "commands" / f"{name}.md")})
            for name in AGENTS:
                links.append({"source": str(legacy_root / "opencode" / "agents" / f"{name}.md"), "destination": str(config / "agents" / f"{name}.md")})
            for name in PLUGINS:
                links.append({"source": str(legacy_root / "opencode" / "plugins" / name), "destination": str(config / "plugins" / name)})
            for entry in links:
                destination = Path(entry["destination"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(entry["source"])
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(json.dumps({
                "links": links,
                "marketplace_added": False,
                "plugin_installed": True,
                "repository_root": str(repo.resolve()),
            }), encoding="utf-8")
            install_opencode(repo, config, state)
            payload = json.loads(receipt_path(state).read_text(encoding="utf-8"))
            self.assertIn("artifact_root", payload)
            self.assertTrue(all(Path(entry["source"]).is_relative_to(state / "expskill") for entry in payload["links"]))
            uninstall_opencode(repo, config, state)
            self.assertFalse(receipt_path(state).exists())
            self.assertFalse(any(path.is_symlink() for path in config.rglob("*")))

    def test_legacy_receipt_rejects_forged_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(json.dumps({
                "links": [{"source": str(root / "outside"), "destination": str(config / "skills" / "plan")}],
                "marketplace_added": False,
                "plugin_installed": True,
                "repository_root": str(repo.resolve()),
            }), encoding="utf-8")
            with self.assertRaises(InstallError):
                uninstall_opencode(repo, config, state)
            self.assertTrue(receipt_path(state).exists())

    def test_legacy_receipt_rejects_forged_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            state = root / "state"
            forged_destination = root / "outside-config" / "plan"
            state.joinpath("expskill").mkdir(parents=True)
            receipt_path(state).write_text(json.dumps({
                "links": [{"source": str(repo / "packages/expskill/skills/plan"), "destination": str(forged_destination)}],
                "marketplace_added": False,
                "plugin_installed": True,
                "repository_root": str(repo.resolve()),
            }), encoding="utf-8")
            with self.assertRaises(InstallError):
                uninstall_opencode(repo, root / "config", state)

    def test_builder_uses_one_private_snapshot_when_live_source_changes_during_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            skill = repo / "packages/expskill/skills/unslop/SKILL.md"
            original_bytes = skill.read_bytes()
            real_render = build_module.render_all

            def mutate_then_render(snapshot: Path) -> dict[str, str]:
                skill.write_text(skill.read_text(encoding="utf-8").replace("Cut AI tells", "RACE"), encoding="utf-8")
                return real_render(snapshot)

            with mock.patch.object(build_module, "render_all", side_effect=mutate_then_render):
                artifact = build_opencode_package(repo, root / "artifact")
            self.assertNotIn("RACE", (artifact / "commands/unslop.md").read_text(encoding="utf-8"))
            provenance = json.loads((artifact / "provenance.json").read_text(encoding="utf-8"))
            digest = next(item["sha256"] for item in provenance["inputs"] if item["path"] == "packages/expskill/skills/unslop/SKILL.md")
            self.assertEqual(digest, __import__("hashlib").sha256(original_bytes).hexdigest())

    def test_backup_cleanup_fault_is_post_commit_success_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            install_opencode(repo, config, state)
            real_remove = install_module._remove_opencode_artifact

            def fail_backup(path: Path) -> None:
                if ".old-" in path.name:
                    raise InstallError("injected backup cleanup failure")
                real_remove(path)

            with mock.patch.object(install_module, "_remove_opencode_artifact", side_effect=fail_backup):
                install_opencode(repo, config, state)
            self.assertTrue((state / "expskill/opencode-artifact").is_dir())
            self.assertTrue(receipt_path(state).is_file())
            self.assertTrue(any(path.name.startswith(".opencode-artifact.old-") for path in (state / "expskill").iterdir()))
            install_opencode(repo, config, state)
            self.assertFalse(any(path.name.startswith(".opencode-artifact.old-") for path in (state / "expskill").iterdir()))

    def test_builder_race_cannot_clobber_output_created_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            output = root / "artifact"
            real_publish = build_module._replace_output

            def inject_target(staging: Path, target: Path) -> None:
                target.mkdir(parents=True)
                (target / "foreign").write_text("must survive\n", encoding="utf-8")
                real_publish(staging, target)

            with mock.patch.object(build_module, "_replace_output", side_effect=inject_target):
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, output)
            self.assertEqual((output / "foreign").read_text(encoding="utf-8"), "must survive\n")

    def test_dry_run_before_and_after_install_is_stable_and_read_only(self) -> None:
        from scripts.install import _print_opencode_dry_run
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            first = StringIO()
            with redirect_stdout(first):
                install_module._print_opencode_dry_run(repo, config, state)
            install_opencode(repo, config, state)
            before = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file())
            second = StringIO()
            with redirect_stdout(second):
                install_module._print_opencode_dry_run(repo, config, state)
            after = sorted((path.relative_to(root).as_posix(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file())
            self.assertEqual(before, after)
            self.assertNotIn("expskill-opencode-preflight-", second.getvalue())
            self.assertIn(str(state / "expskill/opencode-artifact"), second.getvalue())

    def test_invalid_policy_fails_before_creating_state_or_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            config = root / "config"
            state = root / "state"
            policy = repo / "packages" / "expskill" / "assets" / "execution-policy.json"
            policy.write_text("{ invalid\n", encoding="utf-8")
            with self.assertRaises(InstallError):
                install_opencode(repo, config, state)
            self.assertFalse(config.exists())
            self.assertFalse(state.exists())

    def test_platform_source_roster_rejects_generated_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            (repo / "packages" / "expskill" / "opencode" / "commands").mkdir()
            with self.assertRaises(BuildError):
                build_opencode_package(repo, root / "artifact")

    def test_output_ancestor_symlink_and_script_ancestry_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            outside = root / "outside"
            outside.mkdir()
            linked_parent = root / "link"
            linked_parent.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(BuildError):
                build_opencode_package(repo, linked_parent / "output")
            script = repo / "scripts" / "render_opencode.py"
            saved = script.read_bytes()
            script.unlink()
            script.symlink_to(outside / "renderer.py")
            try:
                with self.assertRaises(BuildError):
                    build_opencode_package(repo, root / "artifact")
            finally:
                script.unlink()
                script.write_bytes(saved)

    def test_dry_run_process_leaves_no_bytecode_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            state = root / "state"
            config = root / "config"
            env = dict(os.environ)
            env.update({"XDG_STATE_HOME": str(state), "OPENCODE_CONFIG_DIR": str(config), "PYTHONDONTWRITEBYTECODE": ""})
            result = subprocess.run([sys.executable, str(repo / "scripts" / "install.py"), "--target", "opencode", "--dry-run"], cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(state.exists())
            self.assertFalse(config.exists())
            self.assertFalse(any(path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts for path in repo.rglob("*")))

    def test_direct_build_and_validate_processes_leave_no_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = seed_repository(root / "repo")
            env = dict(os.environ)
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            output = root / "artifact"
            for command in (
                [sys.executable, str(repo / "scripts/build_opencode_package.py"), "--output-dir", str(output)],
                [sys.executable, str(repo / "scripts/validate.py")],
            ):
                result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts for path in repo.rglob("*")))


if __name__ == "__main__":
    unittest.main()
