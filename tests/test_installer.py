"""Offline installer behavior: no real host, GitHub, or user configuration access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
REMOTE = "git@github.com:g-imhoff/expskill.git"
SHA = "a1" * 20
ROLES = ("designer", "explorer", "implementer", "planner", "review", "spec", "test-engineer")
FAKE = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
step = name
if name == "codex":
    step = "marketplace" if args[:2] == ["plugin", "marketplace"] else "plugin"
if os.environ.get("FAIL_STEP") == step:
    if name == "gh":
        print("echo partial-download-executed")
    print("simulated failure: " + step, file=sys.stderr)
    sys.exit(9)
if name == "git":
    print(os.environ.get("GIT_RESULT", os.environ["DIST_SHA"] + "\t" + args[-1]))
elif name == "codex" and step == "plugin":
    print(os.environ.get("CODEX_RESULT", json.dumps({"installedPath": os.environ["PACKAGE"]})))
elif name == "gh":
    print(pathlib.Path(os.environ["INSTALLER_SOURCE"]).read_text())
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="expskill-installer-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.package = self.root / "installed plugin"
        (self.package / "agents").mkdir(parents=True)
        for role in ROLES:
            (self.package / "agents" / f"expskill-{role}.toml").write_text(f"# {role}\n")
        self.log = self.root / "calls.jsonl"
        self.env = {**os.environ, "HOME": str(self.home), "PATH": str(self.bin),
                    "CALL_LOG": str(self.log), "PACKAGE": str(self.package),
                    "DIST_SHA": SHA, "INSTALLER_SOURCE": str(INSTALLER)}
        for key in ("CODEX_HOME", "FAIL_STEP", "GIT_RESULT", "CODEX_RESULT", "BASH_ENV"):
            self.env.pop(key, None)

    def commands(self, *names):
        for name in names:
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + FAKE)
            path.chmod(0o755)

    def codex_commands(self):
        self.commands("git", "codex")
        (self.bin / "python3").symlink_to(sys.executable)

    def run_installer(self, selection, **env):
        return subprocess.run(["/bin/bash", str(INSTALLER)], input=selection, text=True,
                              capture_output=True, env={**self.env, **env}, timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_failed(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("installed successfully", result.stdout)

    def test_opencode_needs_only_selected_cli_and_reprompts(self):
        self.commands("opencode")
        result = self.run_installer("wrong\n2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codex", result.stdout)
        self.assertIn("OpenCode", result.stdout)
        self.assertIn("Hermes", result.stdout)
        self.assertEqual(self.calls(), [["opencode", "plugin", "add", "opencode-expskill"]])
        self.assertFalse((self.home / ".codex").exists())

    def test_hermes_uses_exact_branch_and_immutable_sha(self):
        self.commands("git", "hermes")
        result = self.run_installer("Hermes\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["git", "ls-remote", REMOTE, "refs/heads/hermes-dist"],
            ["hermes", "plugins", "install", REMOTE, "--ref", SHA],
        ])

    def test_codex_installs_profiles_in_custom_home_and_preserves_existing(self):
        self.codex_commands()
        custom_home = self.root / "custom codex"
        agents = custom_home / "agents"
        agents.mkdir(parents=True)
        edited = agents / "expskill-designer.toml"
        edited.write_text("local customization\n")
        external = self.root / "external.toml"
        external.write_text("external untouched\n")
        linked = agents / "expskill-explorer.toml"
        linked.symlink_to(external)
        dangling = agents / "expskill-review.toml"
        dangling.symlink_to(self.root / "missing.toml")
        (agents / "unrelated.toml").write_text("unrelated\n")
        result = self.run_installer("Codex\n", CODEX_HOME=str(custom_home))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["git", "ls-remote", REMOTE, "refs/heads/codex-dist"],
            ["codex", "plugin", "marketplace", "add", REMOTE, "--ref", SHA],
            ["codex", "plugin", "add", "expskill@expskill", "--json"],
        ])
        for role in ROLES:
            destination = agents / f"expskill-{role}.toml"
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_text(), f"# {role}\n")
        backups = list(agents.glob("expskill-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / edited.name).read_text(), "local customization\n")
        self.assertTrue((backups[0] / linked.name).is_symlink())
        self.assertTrue((backups[0] / dangling.name).is_symlink())
        self.assertEqual(external.read_text(), "external untouched\n")
        self.assertEqual((agents / "unrelated.toml").read_text(), "unrelated\n")
        self.assertFalse((self.home / ".codex").exists())
        self.assertIn("/hooks", result.stdout)
        self.assertIn("new Codex session", result.stdout)

    def test_codex_default_home_and_identical_profiles_need_no_backup(self):
        self.codex_commands()
        for _ in range(2):
            result = self.run_installer("1\n")
            self.assertEqual(result.returncode, 0, result.stderr)
        agents = self.home / ".codex" / "agents"
        self.assertEqual(len(list(agents.glob("*.toml"))), 7)
        self.assertEqual(list(agents.glob("expskill-backup-*")), [])

    def test_no_input_fails_without_running_a_host(self):
        self.assert_failed(self.run_installer(""), "selection")
        self.assertEqual(self.calls(), [])

    def test_missing_selected_prerequisites_are_actionable(self):
        self.assert_failed(self.run_installer("2\n"), "opencode")
        self.commands("codex")
        self.assert_failed(self.run_installer("1\n"), "git")
        self.commands("git")
        self.assert_failed(self.run_installer("1\n"), "python3")
        self.assertEqual(self.calls(), [])

    def test_failed_commands_stop_and_report_remedy(self):
        self.codex_commands()
        self.commands("hermes", "opencode")
        for selection, step, remedy, count in (
            ("1\n", "git", "SSH", 1),
            ("1\n", "marketplace", "marketplace remove expskill", 2),
            ("1\n", "plugin", "plugin remove expskill@expskill", 3),
            ("2\n", "opencode", "plugin remove opencode-expskill", 1),
            ("3\n", "hermes", "plugins remove expskill", 2),
        ):
            with self.subTest(step=step):
                self.log.unlink(missing_ok=True)
                self.assert_failed(self.run_installer(selection, FAIL_STEP=step), remedy)
                self.assertEqual(len(self.calls()), count)
                self.assertFalse((self.home / ".codex").exists())

    def test_missing_malformed_or_ambiguous_branch_never_installs(self):
        self.commands("git", "hermes")
        for value in ("", "main\trefs/heads/hermes-dist", SHA + "\trefs/tags/hermes-dist",
                      SHA + "\trefs/heads/hermes-dist\n" + SHA + "\trefs/heads/hermes-dist",
                      "a" * 39 + "\trefs/heads/hermes-dist"):
            with self.subTest(value=value):
                self.log.unlink(missing_ok=True)
                self.assert_failed(self.run_installer("3\n", GIT_RESULT=value), "hermes-dist")
                self.assertEqual(len(self.calls()), 1)

    def test_invalid_codex_response_and_incomplete_package_fail(self):
        self.codex_commands()
        for response in ("not json", "{}", '{"installedPath": null}', '[]'):
            with self.subTest(response=response):
                self.assert_failed(self.run_installer("1\n", CODEX_RESULT=response), "profiles")
                self.assertFalse((self.home / ".codex").exists())
        (self.package / "agents" / "expskill-spec.toml").unlink()
        self.assert_failed(self.run_installer("1\n"), "profiles")
        self.assertFalse((self.home / ".codex").exists())

    def test_destination_directory_is_rejected_without_changing_profiles(self):
        self.codex_commands()
        agents = self.home / ".codex" / "agents"
        (agents / "expskill-review.toml").mkdir(parents=True)
        self.assert_failed(self.run_installer("1\n"), "profiles")
        self.assertEqual([path.name for path in agents.iterdir()], ["expskill-review.toml"])

    def test_symlinked_agents_directory_is_not_written_through(self):
        self.codex_commands()
        external = self.root / "external agents"
        external.mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "agents").symlink_to(external)
        self.assert_failed(self.run_installer("1\n"), "profiles")
        self.assertEqual(list(external.iterdir()), [])

    def test_symlinked_packaged_profile_is_rejected_before_copying(self):
        self.codex_commands()
        profile = self.package / "agents" / "expskill-review.toml"
        profile.unlink()
        profile.symlink_to(self.package / "agents" / "expskill-spec.toml")
        self.assert_failed(self.run_installer("1\n"), "profiles")
        self.assertFalse((self.home / ".codex").exists())

    def test_readme_launcher_downloads_before_executing_and_preserves_stdin(self):
        readme = (ROOT / "README.md").read_text()
        self.assertLessEqual(len(readme.splitlines()), 10)
        self.assertIn("docs/guide.md", readme)
        launcher = re.search(r"```bash\n(.*?)\n```", readme, re.S).group(1)
        self.commands("gh", "opencode")
        (self.bin / "bash").symlink_to("/bin/bash")
        result = subprocess.run(["/bin/bash", "-c", launcher], input="2\n", text=True,
                                capture_output=True, env=self.env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["gh", "api", "-H", "Accept: application/vnd.github.raw+json",
             "repos/g-imhoff/expskill/contents/install.sh?ref=main"],
            ["opencode", "plugin", "add", "opencode-expskill"],
        ])
        self.log.unlink()
        failed = subprocess.run(["/bin/bash", "-c", launcher], input="2\n", text=True,
                                capture_output=True, env={**self.env, "FAIL_STEP": "gh"}, timeout=15)
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("partial-download-executed", failed.stdout)
        self.assertEqual(len(self.calls()), 1)


if __name__ == "__main__":
    unittest.main()
