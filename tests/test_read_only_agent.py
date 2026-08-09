from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "plugins" / "codex-dev-flow" / "scripts" / "read_only_agent.py"


class ReadOnlyAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary = Path(temporary_directory.name)
        self.repository = self.temporary / "repository"
        self.repository.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(self.repository)],
            check=True,
            text=True,
            capture_output=True,
        )
        self.prompt = self.temporary / "prompt.md"
        self.prompt.write_text("Inspect the bounded change.\n", encoding="utf-8")
        self.capture = self.temporary / "capture.json"
        self.codex = self.temporary / "bin" / "codex"
        self.codex.parent.mkdir()
        self.codex.write_text(
            """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

pathlib.Path(os.environ["READ_ONLY_AGENT_CAPTURE"]).write_text(
    json.dumps(
        {
            "argv": sys.argv,
            "cwd": os.getcwd(),
            "stdin": sys.stdin.read(),
        }
    ),
    encoding="utf-8",
)
sys.stdout.write(os.environ.get("READ_ONLY_AGENT_STDOUT", "final response\\n"))
sys.stderr.write(os.environ.get("READ_ONLY_AGENT_STDERR", "codex diagnostic\\n"))
sys.exit(int(os.environ.get("READ_ONLY_AGENT_EXIT", "0")))
""",
            encoding="utf-8",
        )
        self.codex.chmod(self.codex.stat().st_mode | stat.S_IXUSR)

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.codex.parent}{os.pathsep}{environment['PATH']}",
                "READ_ONLY_AGENT_CAPTURE": str(self.capture),
            }
        )
        environment.update(overrides)
        return environment

    def run_agent(
        self,
        role: str,
        *,
        repository: Path | None = None,
        prompt: Path | None = None,
        runner: Path = RUNNER,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(runner),
                role,
                "--repo",
                str(repository or self.repository),
                "--prompt-file",
                str(prompt or self.prompt),
            ],
            cwd=self.temporary,
            env=environment or self.environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    def captured_invocation(self) -> dict[str, object]:
        return json.loads(self.capture.read_text(encoding="utf-8"))

    def expected_arguments(self, role: str, model: str, effort: str) -> list[str]:
        profile = tomllib.loads(
            (
                ROOT
                / "plugins"
                / "codex-dev-flow"
                / "assets"
                / "agents"
                / f"devflow-{role}.toml"
            ).read_text(encoding="utf-8")
        )
        return [
            str(self.codex),
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--model",
            model,
            "-c",
            f"model_reasoning_effort={effort}",
            "-c",
            f"developer_instructions={profile['developer_instructions']}",
            "-C",
            str(self.repository.resolve()),
        ]

    def test_reviewer_uses_exact_isolated_argv_and_never_evaluates_prompt_in_a_shell(self) -> None:
        marker = self.temporary / "shell-evaluated"
        prompt_text = f"Review this literally: $(touch {marker})\n"
        self.prompt.write_text(prompt_text, encoding="utf-8")

        result = self.run_agent("reviewer")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "final response\n")
        self.assertEqual(result.stderr, "codex diagnostic\n")
        captured = self.captured_invocation()
        self.assertEqual(captured["argv"], self.expected_arguments("reviewer", "gpt-5.6-sol", "xhigh"))
        self.assertEqual(captured["cwd"], str(self.repository.resolve()))
        self.assertEqual(captured["stdin"], prompt_text)
        self.assertFalse(marker.exists())

    def test_explorer_uses_exact_isolated_luna_max_argv(self) -> None:
        result = self.run_agent("explorer")

        self.assertEqual(result.returncode, 0)
        captured = self.captured_invocation()
        self.assertEqual(captured["argv"], self.expected_arguments("explorer", "gpt-5.6-luna", "max"))
        self.assertEqual(captured["cwd"], str(self.repository.resolve()))
        self.assertEqual(captured["stdin"], "Inspect the bounded change.\n")
        self.assertEqual(result.stdout, "final response\n")
        self.assertEqual(result.stderr, "codex diagnostic\n")

    def test_unknown_role_refuses_before_codex_runs(self) -> None:
        result = self.run_agent("implementer")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewer", result.stderr)
        self.assertIn("explorer", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_invalid_inputs_refuse_before_codex_runs(self) -> None:
        missing_repository = self.temporary / "missing-repository"
        non_git_repository = self.temporary / "not-git"
        non_git_repository.mkdir()
        missing_prompt = self.temporary / "missing-prompt.md"
        cases = (
            ("missing repository", missing_repository, self.prompt, "repository"),
            ("non-Git repository", non_git_repository, self.prompt, "Git worktree"),
            ("missing prompt", self.repository, missing_prompt, "prompt file"),
        )

        for label, repository, prompt, expected_error in cases:
            with self.subTest(label=label):
                self.capture.unlink(missing_ok=True)
                result = self.run_agent("reviewer", repository=repository, prompt=prompt)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                self.assertFalse(self.capture.exists())

    def test_mutated_writable_or_wrong_model_profile_refuses_before_codex_runs(self) -> None:
        mutations = (
            ('sandbox_mode = "read-only"', 'sandbox_mode = "workspace-write"'),
            ('model = "gpt-5.6-sol"', 'model = "gpt-5.6-luna"'),
        )

        for original, replacement in mutations:
            with self.subTest(replacement=replacement):
                plugin = self.temporary / replacement.split('"')[1]
                shutil.copytree(ROOT / "plugins" / "codex-dev-flow", plugin)
                profile = plugin / "assets" / "agents" / "devflow-reviewer.toml"
                profile.write_text(
                    profile.read_text(encoding="utf-8").replace(original, replacement),
                    encoding="utf-8",
                )
                self.capture.unlink(missing_ok=True)

                result = self.run_agent("reviewer", runner=plugin / "scripts" / RUNNER.name)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("reviewer profile", result.stderr)
                self.assertFalse(self.capture.exists())

    def test_codex_failure_is_propagated_without_a_success_response(self) -> None:
        result = self.run_agent(
            "reviewer",
            environment=self.environment(
                READ_ONLY_AGENT_EXIT="7",
                READ_ONLY_AGENT_STDOUT="Ready\n",
                READ_ONLY_AGENT_STDERR="codex failed\n",
            ),
        )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "")
        self.assertIn("Ready\n", result.stderr)
        self.assertIn("codex failed\n", result.stderr)
        self.assertIn("reviewer Codex process failed with exit status 7", result.stderr)

    def test_success_creates_no_runner_artifact_in_reviewed_repository(self) -> None:
        before = sorted(path.relative_to(self.repository) for path in self.repository.rglob("*"))

        result = self.run_agent("reviewer")

        after = sorted(path.relative_to(self.repository) for path in self.repository.rglob("*"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
