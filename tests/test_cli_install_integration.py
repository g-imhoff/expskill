"""Networked integration test for the documented CLI install flows.

The default ``required`` mode fails closed when a pinned CLI cannot be
verified. Set ``EXPSKILL_CLI_MODE=optional`` only for an explicitly requested
local/offline run that may skip acquisition failures. Explicit executable
overrides also require their companion SHA-256 variables; see the root README.
No model calls are made: only install, list, and remove commands run, so no
authentication is required.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from tests.cli_verification import ensure_binary
except ModuleNotFoundError:  # direct ``python tests/test_cli_install_integration.py``
    from cli_verification import ensure_binary

from scripts.build_opencode_package import build_opencode_package


ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = ROOT / "scripts" / "install.py"
BIN_DIR = ROOT / ".testbin"

CODEX_VERSION = "0.153.4"
CODEX_TAG = "rust-v0.153.4"
OPENCODE_VERSION = "1.18.29"

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

COMMAND_TIMEOUT = 300

NPM = shutil.which("npm")
needs_npm = unittest.skipUnless(NPM, "npm is required for packed OpenCode plugin integration")


def _ensure_binary(kind: str) -> Path:
    return ensure_binary(kind, BIN_DIR)


def _run(
    command: list[str],
    extra_env: dict[str, str] | None = None,
    *,
    cwd: Path | None = None,
    unset_env: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    for name in unset_env:
        env.pop(name, None)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        cwd=ROOT if cwd is None else cwd,
        env=env,
    )


def _run_json(
    command: list[str],
    extra_env: dict[str, str] | None = None,
    *,
    cwd: Path | None = None,
    unset_env: tuple[str, ...] = (),
) -> object:
    result = _run(command, extra_env, cwd=cwd, unset_env=unset_env)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(command)}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"command returned invalid JSON: {' '.join(command)}: {error}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def _await_agent_list(
    opencode: list[str],
    env: dict[str, str],
    names: tuple[str, ...],
    *,
    cwd: Path | None = None,
    unset_env: tuple[str, ...] = (),
) -> str:
    """Poll agent list until every expected agent is detected.

    Agent discovery can lag behind the install on a fresh config directory,
    so this waits briefly instead of asserting on a single snapshot.
    """
    import time as _time

    deadline = _time.monotonic() + 90
    last_stdout = ""
    last_stderr = ""
    while True:
        result = _run(opencode + ["agent", "list"], env, cwd=cwd, unset_env=unset_env)
        last_stdout = result.stdout
        last_stderr = result.stderr
        if result.returncode == 0 and all(name in result.stdout for name in names):
            return result.stdout
        if _time.monotonic() >= deadline:
            raise AssertionError(
                f"agents never detected: {names}\nstdout: {last_stdout}\nstderr: {last_stderr}"
            )
        _time.sleep(3)


class CliInstallIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.codex_bin = _ensure_binary("codex")
        cls.opencode_bin = _ensure_binary("opencode")

    def test_codex_cli_installs_plugin_and_agents_only_links_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex-home"
            state_home = root / "state-home"
            codex_home.mkdir(parents=True)
            env = {
                "CODEX_HOME": str(codex_home),
                "XDG_STATE_HOME": str(state_home),
            }
            codex = [str(self.codex_bin)]

            marketplace = _run_json(
                codex + ["plugin", "marketplace", "add", str(ROOT), "--json"], env
            )
            assert isinstance(marketplace, dict)
            self.assertEqual(marketplace.get("marketplaceName"), "expskill")

            plugin = _run_json(codex + ["plugin", "add", "expskill@expskill", "--json"], env)
            assert isinstance(plugin, dict)
            self.assertEqual(plugin.get("pluginId"), "expskill@expskill")

            installer = _run(
                [sys.executable, str(INSTALL_SCRIPT), "--agents-only"], env
            )
            self.assertEqual(installer.returncode, 0, installer.stderr)

            agents_root = codex_home / "agents"
            for name in AGENTS:
                with self.subTest(agent=name):
                    link = agents_root / f"{name}.toml"
                    self.assertTrue(link.is_symlink(), f"missing agent link: {link}")
                    self.assertEqual(
                        link.resolve().parent.parent.parent,
                        (ROOT / "plugins" / "expskill").resolve(),
                    )

            listed = _run_json(codex + ["plugin", "list", "--json"], env)
            assert isinstance(listed, dict)
            installed = listed.get("installed", [])
            assert isinstance(installed, list)
            matches = [
                entry
                for entry in installed
                if isinstance(entry, dict) and entry.get("pluginId") == "expskill@expskill"
            ]
            self.assertEqual(len(matches), 1)
            self.assertTrue(matches[0].get("enabled"))

            uninstaller = _run(
                [sys.executable, str(INSTALL_SCRIPT), "--agents-only", "--uninstall"], env
            )
            self.assertEqual(uninstaller.returncode, 0, uninstaller.stderr)
            remaining = (
                [path for path in agents_root.iterdir() if path.name.startswith("expskill-")]
                if agents_root.is_dir()
                else []
            )
            self.assertEqual(remaining, [])

            self.assertEqual(
                _run(codex + ["plugin", "remove", "expskill@expskill", "--json"], env).returncode,
                0,
            )
            self.assertEqual(
                _run(codex + ["plugin", "marketplace", "remove", "expskill", "--json"], env).returncode,
                0,
            )
            relisted = _run_json(codex + ["plugin", "list", "--json"], env)
            assert isinstance(relisted, dict)
            relisted_entries = relisted.get("installed", [])
            assert isinstance(relisted_entries, list)
            self.assertFalse(
                any(
                    isinstance(entry, dict) and entry.get("pluginId") == "expskill@expskill"
                    for entry in relisted_entries
                )
            )

    def test_opencode_cli_detects_installed_skills_commands_agents_and_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project_dir = root / "project"
            config_dir = root / "opencode-config"
            test_home = root / "opencode-test-home"
            xdg_config_home = root / "xdg-config"
            xdg_data_home = root / "xdg-data"
            state_home = root / "state-home"
            cache_home = root / "cache-home"
            project_dir.mkdir()
            env = {key: value for key, value in os.environ.items() if key != "EXPSKILL_HOME"}
            env.update({
                "OPENCODE_TEST_HOME": str(test_home),
                "OPENCODE_CONFIG_DIR": str(config_dir),
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
                "XDG_CONFIG_HOME": str(xdg_config_home),
                "XDG_DATA_HOME": str(xdg_data_home),
                "XDG_STATE_HOME": str(state_home),
                "XDG_CACHE_HOME": str(cache_home),
            })
            opencode = [str(self.opencode_bin)]

            installer = _run(
                [sys.executable, str(INSTALL_SCRIPT), "--target", "opencode"],
                env,
                cwd=project_dir,
                unset_env=("EXPSKILL_HOME",),
            )
            self.assertEqual(installer.returncode, 0, installer.stderr)

            for name in SKILLS:
                with self.subTest(skill=name):
                    skill = config_dir / "skills" / name
                    self.assertTrue(skill.is_symlink(), f"missing skill link: {skill}")
                    self.assertTrue((skill / "SKILL.md").is_file())
                    self.assertTrue((config_dir / "commands" / f"{name}.md").is_symlink())
            for name in AGENTS:
                with self.subTest(agent=name):
                    self.assertTrue((config_dir / "agents" / f"{name}.md").is_symlink())
            for name in ("unslop.js", "execution-policy.js"):
                with self.subTest(plugin=name):
                    self.assertTrue((config_dir / "plugins" / name).is_symlink())

            # OpenCode reports plugin startup failures only in logs and still exits 0.
            startup = _run(
                opencode + ["debug", "config", "--print-logs", "--log-level", "DEBUG"],
                env,
                cwd=project_dir,
                unset_env=("EXPSKILL_HOME",),
            )
            self.assertEqual(
                startup.returncode,
                0,
                f"OpenCode startup failed:\nstdout:\n{startup.stdout}\nstderr:\n{startup.stderr}",
            )
            self.assertNotIn("failed to load plugin", startup.stderr.lower())
            try:
                startup_config = json.loads(startup.stdout)
            except json.JSONDecodeError as error:
                self.fail(
                    f"OpenCode startup returned invalid config JSON: {error}\n"
                    f"stdout:\n{startup.stdout}\nstderr:\n{startup.stderr}"
                )
            assert isinstance(startup_config, dict)
            startup_plugins = startup_config.get("plugin", [])
            assert isinstance(startup_plugins, list)
            startup_specs = " ".join(str(entry) for entry in startup_plugins)
            for name in ("unslop.js", "execution-policy.js"):
                with self.subTest(loaded_plugin=name):
                    self.assertIn(name, startup_specs)

            agents_stdout = _await_agent_list(
                opencode,
                env,
                AGENTS,
                cwd=project_dir,
                unset_env=("EXPSKILL_HOME",),
            )
            for name in AGENTS:
                with self.subTest(agent=name):
                    self.assertIn(name, agents_stdout)

            commands = startup_config.get("command", {})
            assert isinstance(commands, dict)
            for name in SKILLS:
                with self.subTest(command=name):
                    self.assertIn(name, commands)

            uninstaller = _run(
                [sys.executable, str(INSTALL_SCRIPT), "--target", "opencode", "--uninstall"],
                env,
                cwd=project_dir,
                unset_env=("EXPSKILL_HOME",),
            )
            self.assertEqual(uninstaller.returncode, 0, uninstaller.stderr)
            leftovers = [
                path
                for subdir in ("skills", "commands", "agents", "plugins")
                for path in (config_dir / subdir).rglob("*")
                if path.is_symlink()
            ]
            self.assertEqual(leftovers, [])

    @needs_npm
    def test_opencode_cli_discovers_packed_native_plugin(self) -> None:
        assert NPM is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact"
            build_opencode_package(ROOT, artifact)
            packed = _run(
                [
                    NPM,
                    "pack",
                    str(artifact),
                    "--pack-destination",
                    str(root),
                ],
                cwd=ROOT,
            )
            self.assertEqual(
                packed.returncode,
                0,
                f"npm pack failed:\nstdout:\n{packed.stdout}\nstderr:\n{packed.stderr}",
            )
            tarballs = sorted(root.glob("opencode-expskill-*.tgz"))
            self.assertEqual(len(tarballs), 1, packed.stdout)

            project_dir = root / "project"
            config_dir = root / "opencode-config"
            test_home = root / "opencode-test-home"
            xdg_config_home = root / "xdg-config"
            xdg_data_home = root / "xdg-data"
            state_home = root / "state-home"
            cache_home = root / "cache-home"
            project_dir.mkdir()
            package_json = {
                "name": "native-opencode-consumer",
                "private": True,
                "type": "module",
            }
            (project_dir / "package.json").write_text(
                json.dumps(package_json) + "\n",
                encoding="utf-8",
            )
            installed = _run(
                [
                    NPM,
                    "install",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    "--package-lock=false",
                    str(tarballs[0]),
                ],
                cwd=project_dir,
            )
            self.assertEqual(
                installed.returncode,
                0,
                f"npm install failed:\nstdout:\n{installed.stdout}\nstderr:\n{installed.stderr}",
            )
            package_root = project_dir / "node_modules" / "opencode-expskill"
            self.assertTrue((package_root / "catalog.json").is_file())
            config_dir.mkdir()
            (config_dir / "opencode.json").write_text(
                json.dumps({"plugin": [package_root.as_uri()]}) + "\n",
                encoding="utf-8",
            )
            env = {key: value for key, value in os.environ.items() if key != "EXPSKILL_HOME"}
            env.update({
                "OPENCODE_TEST_HOME": str(test_home),
                "OPENCODE_CONFIG_DIR": str(config_dir),
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
                "XDG_CONFIG_HOME": str(xdg_config_home),
                "XDG_DATA_HOME": str(xdg_data_home),
                "XDG_STATE_HOME": str(state_home),
                "XDG_CACHE_HOME": str(cache_home),
            })
            opencode = [str(self.opencode_bin)]
            startup = _run(
                opencode + ["debug", "config", "--print-logs", "--log-level", "DEBUG"],
                env,
                cwd=project_dir,
                unset_env=("EXPSKILL_HOME",),
            )
            self.assertEqual(
                startup.returncode,
                0,
                f"OpenCode startup failed:\nstdout:\n{startup.stdout}\nstderr:\n{startup.stderr}",
            )
            self.assertNotIn("failed to load plugin", startup.stderr.lower())
            try:
                startup_config = json.loads(startup.stdout)
            except json.JSONDecodeError as error:
                self.fail(
                    f"OpenCode startup returned invalid config JSON: {error}\n"
                    f"stdout:\n{startup.stdout}\nstderr:\n{startup.stderr}"
                )
            assert isinstance(startup_config, dict)
            commands = startup_config.get("command", {})
            self.assertIsInstance(commands, dict)
            for name in (
                "brainstorm",
                "correct",
                "design",
                "grill-me",
                "implement",
                "plan",
                "review",
                "setup-ui-testing",
                "skill-builder",
                "test",
                "unslop",
                "use-expskill",
            ):
                with self.subTest(command=name):
                    self.assertIn(name, commands)
            skills = startup_config.get("skills", {})
            self.assertIsInstance(skills, dict)
            self.assertIn(str(package_root / "skills"), skills.get("paths", []))

            agents_stdout = _await_agent_list(
                opencode,
                env,
                AGENTS,
                cwd=project_dir,
                unset_env=("EXPSKILL_HOME",),
            )
            for name in AGENTS:
                with self.subTest(agent=name):
                    self.assertIn(name, agents_stdout)

            for name in AGENTS:
                with self.subTest(agent_options=name):
                    details = _run_json(
                        opencode + ["debug", "agent", name],
                        env,
                        cwd=project_dir,
                        unset_env=("EXPSKILL_HOME",),
                    )
                    self.assertIsInstance(details, dict)
                    assert isinstance(details, dict)
                    options = details.get("options")
                    self.assertIsInstance(options, dict)
                    assert isinstance(options, dict)
                    self.assertEqual(options.get("reasoningEffort"), "xhigh")


if __name__ == "__main__":
    unittest.main()
