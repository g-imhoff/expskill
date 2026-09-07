"""Networked integration test for the documented CLI install flows.

Downloads pinned codex and opencode binaries into the repository local
.testbin directory on first run and reuses them afterwards. Set
EXPSKILL_TEST_CODEX_BIN or EXPSKILL_TEST_OPENCODE_BIN to bypass the download
with an existing executable. Any download failure skips the suite instead of
failing it. No model calls are made: only install, list, and remove commands
run, so no authentication is required.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


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

PLATFORM_ASSETS = {
    ("linux", "x86_64"): {
        "codex_asset": "codex-x86_64-unknown-linux-musl.tar.gz",
        "opencode_asset": "opencode-linux-x64.tar.gz",
    },
    ("linux", "aarch64"): {
        "codex_asset": None,
        "opencode_asset": "opencode-linux-arm64.tar.gz",
    },
    ("darwin", "arm64"): {
        "codex_asset": "codex-aarch64-apple-darwin.tar.gz",
        "opencode_asset": "opencode-darwin-arm64.zip",
    },
}

NETWORK_TIMEOUT = 300
COMMAND_TIMEOUT = 300


def _platform_key() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        machine = "x86_64"
    return system, machine


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "expskill-integration-test"})
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
            with destination.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 256)
    except (OSError, urllib.error.URLError) as error:
        raise unittest.SkipTest(f"CLI download is unavailable: {error}")


def _extract_single_binary(archive: Path, target_dir: Path, preferred: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            names = [info.filename for info in bundle.infolist() if not info.is_dir()]
            bundle.extractall(target_dir)
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            names = [member.name for member in bundle.getmembers() if member.isfile()]
            bundle.extractall(target_dir, filter="data")
    top_level = sorted({name for name in names if "/" not in name.rstrip("/")})
    candidates = [target_dir / name for name in top_level if (target_dir / name).is_file()]
    if len(candidates) == 1:
        binary = candidates[0]
    else:
        matches = [path for path in candidates if path.name == preferred]
        if len(matches) != 1:
            raise unittest.SkipTest(f"unexpected CLI archive layout in {archive.name}")
        binary = matches[0]
    mode = binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    binary.chmod(mode)
    return binary


def _ensure_binary(kind: str) -> Path:
    override = os.environ.get(f"EXPSKILL_TEST_{kind.upper()}_BIN")
    if override:
        binary = Path(override)
        if not binary.is_file():
            raise unittest.SkipTest(f"override binary is missing: {binary}")
        return binary
    assets = PLATFORM_ASSETS.get(_platform_key())
    if assets is None or not assets.get(f"{kind}_asset"):
        raise unittest.SkipTest(f"no pinned {kind} asset for this platform")
    version = CODEX_VERSION if kind == "codex" else OPENCODE_VERSION
    cached = BIN_DIR / f"{kind}-{version}" / "bin"
    marker = BIN_DIR / f"{kind}-{version}" / ".ready"
    if cached.is_file() and marker.is_file():
        return cached
    if kind == "codex":
        url = f"https://github.com/openai/codex/releases/download/{CODEX_TAG}/{assets['codex_asset']}"
    else:
        url = f"https://github.com/sst/opencode/releases/download/v{OPENCODE_VERSION}/{assets['opencode_asset']}"
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    archive = BIN_DIR / assets[f"{kind}_asset"]
    if not archive.is_file():
        _download(url, archive)
    workdir = BIN_DIR / f"{kind}-{version}.work"
    if workdir.exists():
        shutil.rmtree(workdir)
    extracted = _extract_single_binary(archive, workdir, kind)
    cached.parent.mkdir(parents=True, exist_ok=True)
    if cached.exists():
        cached.unlink()
    shutil.move(str(extracted), cached)
    shutil.rmtree(workdir, ignore_errors=True)
    marker.write_text(version, encoding="utf-8")
    return cached


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
                        (ROOT / "packages" / "codex").resolve(),
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


if __name__ == "__main__":
    unittest.main()
