"""Pure renderers for the Codex runtime adapter.

Codex agent TOML is a runtime format, not authored content.  Every
``developer_instructions`` value emitted here is read from exactly one Markdown
body under ``plugins/expskill/content/agents``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True


EXPECTED_AGENT_NAMES = (
    "expskill-designer",
    "expskill-explorer",
    "expskill-implementer",
    "expskill-planner",
    "expskill-review",
    "expskill-spec",
    "expskill-test-engineer",
)


class RenderError(RuntimeError):
    """Raised when canonical content or Codex metadata is invalid."""


def _reject_symlink_components(path: Path, label: str) -> None:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError as error:
            raise RenderError(f"{label} cannot be inspected: {current}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise RenderError(f"{label} path component must not be a symlink: {current}")


def _root(value: Path | str | None) -> Path:
    candidate = Path(value).expanduser() if value is not None else Path(__file__).absolute().parents[1]
    _reject_symlink_components(candidate, "repository root")
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RenderError(f"repository root cannot be resolved: {candidate}: {error}") from error


def _regular_file(path: Path, label: str) -> Path:
    _reject_symlink_components(path, label)
    if path.is_symlink() or not path.is_file():
        raise RenderError(f"{label} is not a regular file: {path}")
    return path


def _read_text(path: Path, label: str) -> str:
    _regular_file(path, label)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RenderError(f"{label} could not be read: {error}") from error


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(_read_text(path, label))
    except json.JSONDecodeError as error:
        raise RenderError(f"{label} is not valid JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise RenderError(f"{label} must contain an object")
    return payload


def load_adapter(repo_root: Path | str | None = None) -> tuple[Path, dict[str, Any]]:
    root = _root(repo_root)
    package = root / "plugins" / "expskill"
    spec = _read_json(package / "codex" / "agents.json", "Codex agent metadata")
    if spec.get("schema_version") != "codex-agents.v1":
        raise RenderError("Codex agent metadata has an unsupported schema")
    agents = spec.get("agents")
    if not isinstance(agents, dict) or set(agents) != set(EXPECTED_AGENT_NAMES):
        raise RenderError("Codex agent metadata must contain exactly seven agents")
    return package, spec


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_agent(name: str, metadata: Mapping[str, Any], body: str) -> str:
    if metadata.get("name", name) != name:
        raise RenderError(f"Codex agent metadata name does not match {name!r}")
    required = ("description", "model", "model_reasoning_effort", "sandbox_mode")
    for field in required:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RenderError(f"Codex agent {name!r} has no {field}")
    if not body.strip():
        raise RenderError(f"canonical agent body {name!r} is empty")
    body = body.rstrip("\n")
    if '"""' in body:
        raise RenderError(f"canonical agent body {name!r} contains unsupported TOML delimiter")
    return (
        f'name = {_quote(name)}\n'
        f'description = {_quote(str(metadata["description"]))}\n'
        f'model = {_quote(str(metadata["model"]))}\n'
        f'model_reasoning_effort = {_quote(str(metadata["model_reasoning_effort"]))}\n'
        f'sandbox_mode = {_quote(str(metadata["sandbox_mode"]))}\n'
        'developer_instructions = """\n'
        f"{body}\n"
        '"""\n'
    )


def render_agents(repo_root: Path | str | None = None) -> dict[str, str]:
    package, spec = load_adapter(repo_root)
    content_agents = package / "content" / "agents"
    result: dict[str, str] = {}
    for name in EXPECTED_AGENT_NAMES:
        body = _read_text(content_agents / f"{name}.md", f"canonical agent body {name!r}")
        metadata = spec["agents"][name]
        if not isinstance(metadata, Mapping):
            raise RenderError(f"Codex agent metadata {name!r} must be an object")
        result[f"agents/{name}.toml"] = render_agent(name, metadata, body)
    return result


def skill_inventory(repo_root: Path | str | None = None) -> tuple[str, ...]:
    package, _spec = load_adapter(repo_root)
    root = package / "content" / "skills"
    if not root.is_dir() or root.is_symlink():
        raise RenderError(f"canonical skills directory is missing: {root}")
    names = tuple(sorted(path.name for path in root.iterdir() if path.is_dir() and not path.is_symlink()))
    if len(names) != 12:
        raise RenderError(f"canonical skill roster must contain exactly 12 skills, found {len(names)}")
    for name in names:
        _regular_file(root / name / "SKILL.md", f"canonical skill {name!r}")
    return names


def render_all(repo_root: Path | str | None = None) -> dict[str, str]:
    skill_inventory(repo_root)
    return render_agents(repo_root)


render = render_all


if __name__ == "__main__":
    print(json.dumps({key: len(value) for key, value in sorted(render_all().items())}, sort_keys=True))
