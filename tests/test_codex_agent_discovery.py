from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest

from scripts.render_codex import render_agents
from tests import test_installer
from tests.cli_verification import ensure_binary


ROOT = Path(__file__).resolve().parents[1]


def _agent_warnings(binary: Path, codex_home: Path, project: Path) -> list[str]:
    environment = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "XDG_STATE_HOME": str(codex_home.parent / "state"),
    }
    with tempfile.TemporaryFile(mode="w+") as stderr:
        process = subprocess.Popen(
            [str(binary), "app-server"],
            cwd=project,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )
        messages: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            for line in process.stdout:
                messages.put(line)
            messages.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        warnings = []
        try:
            requests = (
                (1, "initialize", {"clientInfo": {"name": "expskill-agent-test", "version": "1"}}),
                (2, "config/read", {"cwd": str(project), "includeLayers": True}),
            )
            for ident, method, params in requests:
                process.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
                process.stdin.flush()
                while True:
                    line = messages.get(timeout=30)
                    if line is None:
                        raise AssertionError("Codex app-server exited before replying")
                    message = json.loads(line)
                    if message.get("method") == "configWarning":
                        warnings.append(message["params"]["summary"])
                    if message.get("id") == ident:
                        if "error" in message:
                            raise AssertionError(message["error"])
                        break
        finally:
            process.terminate()
            process.wait(timeout=10)
            reader.join(timeout=10)
            process.stdin.close()
            process.stdout.close()
        stderr.seek(0)
        warnings.extend(line for line in stderr.read().splitlines() if "malformed agent role" in line)
        return [warning for warning in warnings if "agent role" in warning]


class CodexAgentDiscoveryTests(unittest.TestCase):
    def test_real_codex_loads_updated_profiles_after_legacy_backup_migration(self) -> None:
        binary = ensure_binary("codex", ROOT / ".testbin")
        fixture = test_installer.InstallerTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.codex_commands()
        codex_home = fixture.root / "codex home"
        agents = codex_home / "agents"
        legacy = agents / "expskill-backup-legacy"
        legacy.mkdir(parents=True)
        rendered = render_agents(ROOT)
        for relative, contents in rendered.items():
            name = Path(relative).name
            (fixture.package / "agents" / name).write_text(contents)
            (agents / name).write_text(contents + "\n")
            (legacy / name).write_text(contents + "\n")

        before = _agent_warnings(binary, codex_home, fixture.root)
        self.assertTrue(before)
        for relative in rendered:
            name = Path(relative).stem
            self.assertTrue(any("duplicate agent role name `" + name + "`" in warning for warning in before))

        result = fixture.run_installer("1\n", CODEX_HOME=str(codex_home))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(_agent_warnings(binary, codex_home, fixture.root), [])
        self.assertEqual(len(list(agents.rglob("*.toml"))), 7)
        for relative, contents in rendered.items():
            self.assertEqual((agents / Path(relative).name).read_text(), contents)
        saved = list((codex_home / "agent-backups").rglob("*.toml"))
        self.assertEqual(len(saved), 14)
        for profile in saved:
            self.assertEqual(profile.read_text(), rendered["agents/" + profile.name] + "\n")


if __name__ == "__main__":
    unittest.main()
