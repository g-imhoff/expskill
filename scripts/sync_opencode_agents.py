"""Render packages/opencode/agents/*.md from one shared source of truth.

Descriptions and developer instructions come from the canonical Codex agent
profiles in packages/codex/assets/agents/*.toml. Everything opencode specific
(model profiles, temperature, permission matrices, runtime paragraphs) comes
from packages/opencode/agents.json. The checked in .md files must always match
this generator byte for byte. Re-render them with:

    python3 scripts/sync_opencode_agents.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


AGENT_NAMES = (
    "expskill-explorer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
SCHEMA_VERSION = "opencode-agents.v1"
SPEC_FILENAME = "agents.json"


class SyncError(RuntimeError):
    pass


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_spec(package_root: Path) -> dict[str, Any]:
    try:
        spec = json.loads((package_root / SPEC_FILENAME).read_text(encoding="utf-8"))
    except OSError as error:
        raise SyncError(f"opencode agent spec could not be read: {error}") from error
    except json.JSONDecodeError as error:
        raise SyncError(f"opencode agent spec is not valid JSON: {error.msg}") from error
    if not isinstance(spec, dict):
        raise SyncError("opencode agent spec must contain a JSON object")
    return spec


def _load_profile(codex_root: Path, name: str) -> dict[str, Any]:
    try:
        profile = tomllib.loads(
            (codex_root / "assets" / "agents" / f"{name}.toml").read_text(encoding="utf-8")
        )
    except OSError as error:
        raise SyncError(f"canonical agent profile {name!r} could not be read: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise SyncError(f"canonical agent profile {name!r} is invalid TOML: {error}") from error
    if not isinstance(profile, dict):
        raise SyncError(f"canonical agent profile {name!r} must be a TOML table")
    return profile


def _quote_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return '"' + key.replace('"', '\\"') + '"'


def _render_mapping(mapping: dict[str, Any], indent: int) -> list[str]:
    lines: list[str] = []
    for key, value in mapping.items():
        prefix = " " * indent + _quote_key(str(key)) + ":"
        if isinstance(value, dict):
            lines.append(prefix)
            lines.extend(_render_mapping(value, indent + 2))
        else:
            lines.append(f"{prefix} {value}")
    return lines


def render_agent(
    name: str,
    profile: dict[str, Any],
    model: str,
    effort: str,
    entry: dict[str, Any],
    runtime_paragraph: str,
) -> str:
    description = profile.get("description")
    instructions = profile.get("developer_instructions")
    if not isinstance(description, str) or not description.strip():
        raise SyncError(f"canonical agent profile {name!r} has no description")
    if not isinstance(instructions, str) or not instructions.strip():
        raise SyncError(f"canonical agent profile {name!r} has no developer instructions")
    closing = entry.get("closing")
    if not isinstance(closing, str) or not closing.strip():
        raise SyncError(f"opencode agent spec entry {name!r} has no closing")
    permission = entry.get("permission")
    if not isinstance(permission, dict) or not permission:
        raise SyncError(f"opencode agent spec entry {name!r} has no permission mapping")
    lines = [
        "---",
        f"description: {description.strip()}",
        "mode: subagent",
        f"model: {model}",
        f"reasoningEffort: {effort}",
    ]
    temperature = entry.get("temperature")
    if temperature is not None:
        lines.append(f"temperature: {temperature}")
    lines.append("permission:")
    lines.extend(_render_mapping(permission, 2))
    lines.append("---")
    lines.append("")
    lines.append(instructions.strip())
    lines.append("")
    lines.append(f"{runtime_paragraph.strip()} {closing.strip()}")
    return "\n".join(lines) + "\n"


def render_all(repo_root: Path | None = None) -> dict[str, str]:
    root = Path(repo_root).expanduser() if repo_root is not None else _repository_root()
    codex_root = root / "packages" / "codex"
    package_root = root / "packages" / "opencode"
    spec = _load_spec(package_root)
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise SyncError(f"opencode agent spec schema_version must be {SCHEMA_VERSION!r}")
    profiles = spec.get("model_profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise SyncError("opencode agent spec must declare model_profiles")
    default_name = spec.get("default_model_profile")
    if not isinstance(default_name, str) or default_name not in profiles:
        raise SyncError("opencode agent spec default_model_profile must name a declared profile")
    active = profiles[default_name]
    if not isinstance(active, dict):
        raise SyncError(f"opencode model profile {default_name!r} must be a mapping")
    model = active.get("model")
    effort = active.get("reasoningEffort")
    if not isinstance(model, str) or not model.strip():
        raise SyncError(f"opencode model profile {default_name!r} has no model")
    if not isinstance(effort, str) or not effort.strip():
        raise SyncError(f"opencode model profile {default_name!r} has no reasoningEffort")
    runtime_paragraph = spec.get("runtime_paragraph")
    if not isinstance(runtime_paragraph, str) or not runtime_paragraph.strip():
        raise SyncError("opencode agent spec has no runtime_paragraph")
    entries = spec.get("agents")
    if not isinstance(entries, dict) or set(entries) != set(AGENT_NAMES):
        raise SyncError("opencode agent spec agents must cover the exact agent roster")
    rendered: dict[str, str] = {}
    for name in AGENT_NAMES:
        entry = entries[name]
        if not isinstance(entry, dict):
            raise SyncError(f"opencode agent spec entry {name!r} must be a mapping")
        profile = _load_profile(codex_root, name)
        rendered[name] = render_agent(
            name, profile, model.strip(), effort.strip(), entry, runtime_paragraph
        )
    return rendered


def sync(repo_root: Path | None = None) -> dict[str, Path]:
    root = Path(repo_root).expanduser() if repo_root is not None else _repository_root()
    agents_root = root / "packages" / "opencode" / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, contents in render_all(root).items():
        path = agents_root / f"{name}.md"
        path.write_text(contents, encoding="utf-8")
        written[name] = path
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render opencode agents from shared sources.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when a checked in agent differs from its rendered source",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.check:
            root = _repository_root()
            agents_root = root / "packages" / "opencode" / "agents"
            failures = []
            for name, contents in render_all(root).items():
                try:
                    current = (agents_root / f"{name}.md").read_text(encoding="utf-8")
                except OSError:
                    current = None
                if current != contents:
                    failures.append(name)
            if failures:
                print(f"stale opencode agents: {', '.join(failures)}")
                return 1
            print("opencode agents match their shared sources")
            return 0
        written = sync()
        for name in written:
            print(f"rendered {written[name]}")
    except SyncError as error:
        print(f"sync error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
