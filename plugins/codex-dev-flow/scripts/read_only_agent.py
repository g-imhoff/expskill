from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROLE_CONTRACTS = {
    "reviewer": ("devflow-reviewer", "gpt-5.6-sol", "xhigh", "read-only"),
    "explorer": ("devflow-explorer", "gpt-5.6-luna", "max", "read-only"),
}


class AgentError(Exception):
    pass


def resolve_repository(repository: Path) -> Path:
    requested = repository.expanduser()
    if not requested.is_dir():
        raise AgentError(f"repository does not exist or is not a directory: {requested}")
    result = subprocess.run(
        ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise AgentError(f"repository is not a Git worktree: {requested}")
    canonical = Path(result.stdout.strip()).resolve()
    if not canonical.is_dir():
        raise AgentError(f"Git worktree root does not exist: {canonical}")
    return canonical


def read_prompt(prompt_file: Path) -> str:
    requested = prompt_file.expanduser()
    if not requested.is_file():
        raise AgentError(f"prompt file does not exist or is not a file: {requested}")
    try:
        return requested.read_text(encoding="utf-8")
    except OSError as error:
        raise AgentError(f"prompt file could not be read: {error}") from error


def read_profile(role: str, plugin_root: Path) -> dict[str, str]:
    expected_name, expected_model, expected_effort, expected_sandbox = ROLE_CONTRACTS[role]
    path = plugin_root / "assets" / "agents" / f"{expected_name}.toml"
    try:
        profile = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise AgentError(f"{role} profile could not be read: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise AgentError(f"{role} profile is invalid TOML: {error}") from error
    expected = {
        "name": expected_name,
        "model": expected_model,
        "model_reasoning_effort": expected_effort,
        "sandbox_mode": expected_sandbox,
    }
    for field, expected_value in expected.items():
        if profile.get(field) != expected_value:
            raise AgentError(
                f"{role} profile {field} must be {expected_value!r}, got {profile.get(field)!r}"
            )
    instructions = profile.get("developer_instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise AgentError(f"{role} profile developer_instructions must be non-empty")
    return profile


def run_agent(role: str, repository: Path, prompt_file: Path) -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    canonical_repository = resolve_repository(repository)
    prompt = read_prompt(prompt_file)
    profile = read_profile(role, plugin_root)
    codex = shutil.which("codex")
    if codex is None:
        raise AgentError(f"{role} Codex executable was not found")
    argv = [
        codex,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--model",
        profile["model"],
        "-c",
        f"model_reasoning_effort={profile['model_reasoning_effort']}",
        "-c",
        f"developer_instructions={profile['developer_instructions']}",
        "-C",
        str(canonical_repository),
    ]
    try:
        result = subprocess.run(
            argv,
            cwd=canonical_repository,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise AgentError(f"{role} Codex process could not start: {error}") from error
    if result.returncode == 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return 0
    sys.stderr.write(result.stdout)
    sys.stderr.write(result.stderr)
    print(
        f"{role} Codex process failed with exit status {result.returncode}",
        file=sys.stderr,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an isolated read-only workflow agent.")
    parser.add_argument("role", choices=tuple(ROLE_CONTRACTS))
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--prompt-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return run_agent(args.role, args.repo, args.prompt_file)
    except AgentError as error:
        print(f"read-only agent: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
