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
REMOTE = "https://github.com/g-imhoff/expskill.git"
SHA = "a1" * 20
ROLES = ("designer", "explorer", "implementer", "planner", "review", "spec", "test-engineer")
FAKE = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as stream:
    stream.write(json.dumps([name, *args]) + "\n")
state_path = pathlib.Path(os.environ["CALL_LOG"]).with_suffix(".state.json")
state = json.loads(state_path.read_text()) if state_path.exists() else {}
step = name
if name == "codex":
    step = "marketplace" if args[:2] == ["plugin", "marketplace"] else "plugin"
    if args == ["plugin", "marketplace", "list", "--json"]:
        step = "inspect-marketplace"
elif name == "opencode" and args == ["plugin", "list"]:
    step = "inspect-opencode"
if (os.environ.get("FAIL_STEP") == step or
        os.environ.get("FAIL_COMMAND") == " ".join([name, *args])):
    if name == "curl":
        print("echo partial-download-executed")
    print("simulated failure: " + step, file=sys.stderr)
    sys.exit(9)
if name == "git":
    print(os.environ.get("GIT_RESULT", os.environ["DIST_SHA"] + "\t" + args[-1]))
elif name == "codex":
    if step == "inspect-marketplace":
        entries = [{"name": "expskill"}] if state.get("codex-marketplace") else []
        print(os.environ.get("CODEX_MARKETPLACES", json.dumps({"marketplaces": entries})))
    elif args[:3] == ["plugin", "marketplace", "remove"]:
        state.pop("codex-marketplace", None)
    elif args[:3] == ["plugin", "marketplace", "add"]:
        if state.get("codex-marketplace"):
            sys.exit("marketplace already exists; refresh it before adding a new ref")
        state["codex-marketplace"] = args[-1]
    elif step == "plugin":
        state["codex-installed"] = True
        print(os.environ.get("CODEX_RESULT", json.dumps({"installedPath": os.environ["PACKAGE"]})))
elif name == "opencode":
    if step == "inspect-opencode":
        listing = ("ID VERSION SOURCE\nexpskill 0.1.0 " + state["opencode-target"]
                   if state.get("opencode-target") else "No plugins found")
        print(os.environ.get("OPENCODE_PLUGINS", listing))
    elif args[:2] == ["plugin", "add"]:
        if state.get("opencode-target"):
            sys.exit("plugin already installed; use update")
        state["opencode-target"] = args[-1]
    elif args[:2] == ["plugin", "update"]:
        state["opencode-updated"] = args[-1]
elif name == "hermes":
    if state.get("hermes-ref") and "--force" not in args:
        sys.exit("plugin already installed; use --force for a new pin")
    state["hermes-ref"] = args[args.index("--ref") + 1]
elif name == "curl":
    print(pathlib.Path(os.environ["INSTALLER_SOURCE"]).read_text())
state_path.write_text(json.dumps(state))
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
        for key in ("CODEX_HOME", "FAIL_STEP", "FAIL_COMMAND", "GIT_RESULT", "CODEX_RESULT",
                    "CODEX_MARKETPLACES", "OPENCODE_PLUGINS", "BASH_ENV"):
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

    def state(self):
        return json.loads(self.log.with_suffix(".state.json").read_text())

    def assert_failed(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("successfully for", result.stdout)

    def test_opencode_needs_only_selected_cli_and_reprompts(self):
        self.commands("opencode")
        result = self.run_installer("wrong\n ,\t\n2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Codex", result.stdout)
        self.assertIn("OpenCode", result.stdout)
        self.assertIn("Hermes", result.stdout)
        self.assertEqual(self.calls(), [
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "add", "opencode-expskill"],
        ])
        self.assertFalse((self.home / ".codex").exists())

    def test_hermes_uses_exact_branch_and_immutable_sha(self):
        self.commands("git", "hermes")
        result = self.run_installer("Hermes\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["git", "ls-remote", REMOTE, "refs/heads/hermes-dist"],
            ["hermes", "plugins", "install", REMOTE, "--ref", SHA, "--force"],
        ])

    def test_multiple_hosts_install_in_selection_order(self):
        self.commands("opencode", "git", "hermes")
        result = self.run_installer("2 3\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "add", "opencode-expskill"],
            ["git", "ls-remote", REMOTE, "refs/heads/hermes-dist"],
            ["hermes", "plugins", "install", REMOTE, "--ref", SHA, "--force"],
        ])

    def test_multiple_names_and_commas_preserve_codex_profiles(self):
        self.codex_commands()
        self.commands("hermes")
        result = self.run_installer("CoDeX, Hermes\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in self.calls()], ["git", "codex", "codex", "codex", "git", "hermes"])
        self.assertEqual(len(list((self.home / ".codex" / "agents").glob("*.toml"))), 7)

    def test_all_hosts_installs_each_provider_once(self):
        self.codex_commands()
        self.commands("opencode", "hermes")
        result = self.run_installer("ALL\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in self.calls()], ["git", "codex", "codex", "codex", "opencode", "opencode", "git", "hermes"])
        for host in ("Codex", "OpenCode", "Hermes"):
            self.assertIn("installed or updated successfully for " + host, result.stdout)

    def test_duplicate_hosts_are_not_reinstalled(self):
        self.commands("opencode", "git", "hermes")
        result = self.run_installer("2,OpenCode,2 3 Hermes 3\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in self.calls()], ["opencode", "opencode", "git", "hermes"])

    def test_invalid_combined_selection_never_installs_partial_choice(self):
        self.commands("opencode", "git", "hermes")
        result = self.run_installer("2 unknown\n3\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in self.calls()], ["git", "hermes"])

    def test_wildcard_selection_is_rejected_without_expanding_filenames(self):
        self.commands("opencode")
        (self.root / "2").touch()
        result = subprocess.run(["/bin/bash", str(INSTALLER)], input="*\n2\n", text=True,
                                capture_output=True, env=self.env, cwd=self.root, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Enter", result.stdout)
        self.assertEqual(self.calls(), [
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "add", "opencode-expskill"],
        ])

    def test_preflight_checks_every_selected_cli_before_installing(self):
        self.commands("opencode", "git")
        self.assert_failed(self.run_installer("2 3\n"), "hermes")
        self.assertEqual(self.calls(), [])

    def test_combined_install_stops_after_failure_and_reports_completed_host(self):
        self.codex_commands()
        self.commands("opencode", "hermes")
        result = self.run_installer("2 3 1\n", FAIL_STEP="hermes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hermes installation or update failed", result.stderr)
        self.assertIn("installed or updated successfully for OpenCode", result.stdout)
        self.assertNotIn("installed or updated successfully for Hermes", result.stdout)
        self.assertEqual([call[0] for call in self.calls()], ["opencode", "opencode", "git", "hermes"])
        self.assertFalse((self.home / ".codex").exists())

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
            ["codex", "plugin", "marketplace", "list", "--json"],
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

    def test_rerun_updates_all_hosts_to_new_release_and_backs_up_edited_profiles(self):
        self.codex_commands()
        self.commands("opencode", "hermes")
        first = self.run_installer("all\n")
        self.assertEqual(first.returncode, 0, first.stderr)
        agents = self.home / ".codex" / "agents"
        (agents / "expskill-designer.toml").write_text("local customization\n")
        (self.package / "agents" / "expskill-designer.toml").write_text("new release\n")
        self.log.unlink()
        next_sha = "b2" * 20
        updated = self.run_installer("all\n", DIST_SHA=next_sha)
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(self.calls(), [
            ["git", "ls-remote", REMOTE, "refs/heads/codex-dist"],
            ["codex", "plugin", "marketplace", "list", "--json"],
            ["codex", "plugin", "marketplace", "remove", "expskill"],
            ["codex", "plugin", "marketplace", "add", REMOTE, "--ref", next_sha],
            ["codex", "plugin", "add", "expskill@expskill", "--json"],
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "update", "opencode-expskill"],
            ["git", "ls-remote", REMOTE, "refs/heads/hermes-dist"],
            ["hermes", "plugins", "install", REMOTE, "--ref", next_sha, "--force"],
        ])
        self.assertEqual(self.state()["codex-marketplace"], next_sha)
        self.assertEqual(self.state()["hermes-ref"], next_sha)
        self.assertEqual(self.state()["opencode-updated"], "opencode-expskill")
        self.assertEqual((agents / "expskill-designer.toml").read_text(), "new release\n")
        backups = list(agents.glob("expskill-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "expskill-designer.toml").read_text(), "local customization\n")

    def test_multiple_hosts_can_mix_an_update_and_fresh_install(self):
        self.commands("opencode", "git", "hermes")
        first = self.run_installer("2\n")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.log.unlink()
        result = self.run_installer("2 3\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "update", "opencode-expskill"],
            ["git", "ls-remote", REMOTE, "refs/heads/hermes-dist"],
            ["hermes", "plugins", "install", REMOTE, "--ref", SHA, "--force"],
        ])

    def test_opencode_updates_exact_configured_package_target(self):
        self.commands("opencode")
        for target in ("opencode-expskill", "opencode-expskill@0.1.0"):
            with self.subTest(target=target):
                self.log.unlink(missing_ok=True)
                listing = "ID VERSION SOURCE\nother 1.0 opencode-expskill-extra\nexp 0.1 " + target
                result = self.run_installer("2\n", OPENCODE_PLUGINS=listing)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls(), [
                    ["opencode", "plugin", "list"],
                    ["opencode", "plugin", "update", target],
                ])

    def test_opencode_does_not_mistake_similar_package_or_id_for_expskill(self):
        self.commands("opencode")
        listing = "ID VERSION SOURCE\nopencode-expskill 1.0 unrelated\nexp 0.1 opencode-expskill-extra"
        result = self.run_installer("2\n", OPENCODE_PLUGINS=listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], ["opencode", "plugin", "add", "opencode-expskill"])

    def test_codex_leaves_unrelated_marketplaces_registered(self):
        self.codex_commands()
        listing = json.dumps({"marketplaces": [{"name": "expskill-extra"}, {"name": "other"}]})
        result = self.run_installer("1\n", CODEX_MARKETPLACES=listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("remove" in call for call in self.calls()))

    def test_invalid_codex_marketplace_list_stops_before_changes(self):
        self.codex_commands()
        for listing in ("invalid", "{}", "[]", '{"marketplaces": null}',
                        '{"marketplaces": [{}]}', '{"marketplaces": [null]}'):
            with self.subTest(listing=listing):
                self.log.unlink(missing_ok=True)
                self.assert_failed(self.run_installer("1\n", CODEX_MARKETPLACES=listing), "inspect")
                self.assertEqual(self.calls(), [
                    ["git", "ls-remote", REMOTE, "refs/heads/codex-dist"],
                    ["codex", "plugin", "marketplace", "list", "--json"],
                ])
                self.assertFalse((self.home / ".codex").exists())

    def test_inspection_failures_do_not_attempt_install_or_update(self):
        self.codex_commands()
        self.commands("opencode")
        for selection, step, count in (("1\n", "inspect-marketplace", 2),
                                       ("2\n", "inspect-opencode", 1)):
            with self.subTest(step=step):
                self.log.unlink(missing_ok=True)
                self.assert_failed(self.run_installer(selection, FAIL_STEP=step), "inspect")
                self.assertEqual(len(self.calls()), count)

    def test_failed_codex_refresh_keeps_plugin_cache_and_can_be_retried(self):
        self.codex_commands()
        self.commands("opencode")
        first = self.run_installer("1\n")
        self.assertEqual(first.returncode, 0, first.stderr)
        agents = self.home / ".codex" / "agents"
        edited = agents / "expskill-designer.toml"
        edited.write_text("local customization\n")
        self.log.unlink()
        failed = self.run_installer("1 2\n", FAIL_STEP="marketplace")
        self.assert_failed(failed, "refresh")
        self.assertEqual(self.calls()[-1], ["codex", "plugin", "marketplace", "remove", "expskill"])
        self.assertTrue(self.state()["codex-installed"])
        self.assertEqual(edited.read_text(), "local customization\n")
        self.log.unlink()
        failed = self.run_installer("1 2\n", FAIL_COMMAND="codex plugin marketplace add " + REMOTE + " --ref " + SHA)
        self.assert_failed(failed, "registration failed")
        self.assertTrue(self.state()["codex-installed"])
        self.assertNotIn("codex-marketplace", self.state())
        self.assertFalse(any(call[0] == "opencode" for call in self.calls()))
        recovered = self.run_installer("1 2\n")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertTrue(self.state()["codex-installed"])

    def test_failed_opencode_update_stops_later_hosts_without_removing_plugin(self):
        self.commands("opencode", "git", "hermes")
        first = self.run_installer("2\n")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.log.unlink()
        failed = self.run_installer("2 3\n", FAIL_STEP="opencode")
        self.assert_failed(failed, "OpenCode update failed")
        self.assertEqual(self.calls(), [
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "update", "opencode-expskill"],
        ])
        self.assertEqual(self.state()["opencode-target"], "opencode-expskill")

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
            ("1\n", "git", "network", 1),
            ("1\n", "marketplace", "network", 3),
            ("1\n", "plugin", "retry this installer", 4),
            ("2\n", "opencode", "npm access", 2),
            ("3\n", "hermes", "network", 2),
        ):
            with self.subTest(step=step):
                self.log.unlink(missing_ok=True)
                self.log.with_suffix(".state.json").unlink(missing_ok=True)
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
        self.commands("curl", "opencode", "git", "hermes")
        (self.bin / "bash").symlink_to("/bin/bash")
        result = subprocess.run(["/bin/bash", "-c", launcher], input="2 3\n", text=True,
                                capture_output=True, env=self.env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            ["curl", "-fsSL", "https://raw.githubusercontent.com/g-imhoff/expskill/main/install.sh"],
            ["opencode", "plugin", "list"],
            ["opencode", "plugin", "add", "opencode-expskill"],
            ["git", "ls-remote", REMOTE, "refs/heads/hermes-dist"],
            ["hermes", "plugins", "install", REMOTE, "--ref", SHA, "--force"],
        ])
        self.log.unlink()
        failed = subprocess.run(["/bin/bash", "-c", launcher], input="2 3\n", text=True,
                                capture_output=True, env={**self.env, "FAIL_STEP": "curl"}, timeout=15)
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn("partial-download-executed", failed.stdout)
        self.assertEqual(len(self.calls()), 1)


if __name__ == "__main__":
    unittest.main()
