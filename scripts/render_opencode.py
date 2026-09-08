"""Pure renderers for the OpenCode package surface.

The repository keeps one canonical skill and agent source under
``packages/expskill``.  OpenCode's markdown agents and command wrappers are
build outputs, not sources.  This module only reads those inputs and returns
deterministic strings and JSON-compatible values; it never writes files.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "opencode-agents.v1"
CATALOG_SCHEMA_VERSION = "opencode-runtime.v1"
AGENT_FRONTMATTER_FIELDS = ("description", "mode", "model", "reasoningEffort")


class RenderError(RuntimeError):
    """Raised when canonical or OpenCode overlay input cannot be rendered."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _root(value: Path | str | None) -> Path:
    candidate = Path(value).expanduser() if value is not None else repository_root()
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RenderError(f"repository root cannot be resolved: {candidate}: {error}") from error


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise RenderError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise RenderError(f"{label} is not a regular file: {path}")
    return path


def _regular_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise RenderError(f"{label} must not be a symlink: {path}")
    if not path.is_dir():
        raise RenderError(f"{label} is not a regular directory: {path}")
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
        raise RenderError(f"{label} must contain a JSON object")
    return payload


def load_overlay(package_root: Path) -> dict[str, Any]:
    """Load and validate the OpenCode agent overlay from ``package_root``."""

    spec = _read_json(package_root / "agents.json", "OpenCode agent overlay")
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise RenderError(
            f"OpenCode agent overlay schema_version must be {SCHEMA_VERSION!r}"
        )
    agents = spec.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise RenderError("OpenCode agent overlay must declare agents")
    profiles = spec.get("model_profiles")
    default_profile = spec.get("default_model_profile")
    if not isinstance(profiles, dict) or not profiles:
        raise RenderError("OpenCode agent overlay must declare model_profiles")
    if not isinstance(default_profile, str) or default_profile not in profiles:
        raise RenderError(
            "OpenCode agent overlay default_model_profile must name a model profile"
        )
    active = profiles[default_profile]
    if not isinstance(active, dict):
        raise RenderError(f"OpenCode model profile {default_profile!r} must be an object")
    model = active.get("model")
    effort = active.get("reasoningEffort")
    if not isinstance(model, str) or not model.strip():
        raise RenderError(f"OpenCode model profile {default_profile!r} has no model")
    if not isinstance(effort, str) or not effort.strip():
        raise RenderError(
            f"OpenCode model profile {default_profile!r} has no reasoningEffort"
        )
    runtime = spec.get("runtime_paragraph")
    if not isinstance(runtime, str) or not runtime.strip():
        raise RenderError("OpenCode agent overlay has no runtime_paragraph")
    return spec


def _parse_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise RenderError(f"invalid frontmatter scalar {raw!r}") from error
        if not isinstance(parsed, str):
            raise RenderError(f"frontmatter scalar is not text: {raw!r}")
        return parsed
    return value


def parse_skill_frontmatter(contents: str, skill_name: str) -> dict[str, Any]:
    """Parse the small YAML frontmatter subset used by canonical skills."""

    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RenderError(f"skill {skill_name!r} must start with frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise RenderError(f"skill {skill_name!r} frontmatter is not closed") from error

    fields: dict[str, Any] = {}
    metadata: dict[str, str] | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            key, separator, raw = stripped.partition(":")
            if not separator or not key:
                raise RenderError(f"skill {skill_name!r} has invalid frontmatter")
            if key in fields:
                raise RenderError(f"skill {skill_name!r} repeats frontmatter key {key!r}")
            if raw.strip():
                fields[key] = _parse_scalar(raw)
            else:
                if key != "metadata":
                    raise RenderError(
                        f"skill {skill_name!r} has unsupported frontmatter mapping {key!r}"
                    )
                metadata = {}
                fields[key] = metadata
        elif indent == 2 and metadata is not None:
            key, separator, raw = stripped.partition(":")
            if not separator or not key or not raw.strip():
                raise RenderError(f"skill {skill_name!r} has invalid metadata")
            if key in metadata:
                raise RenderError(f"skill {skill_name!r} repeats metadata key {key!r}")
            metadata[key] = _parse_scalar(raw)
        else:
            raise RenderError(f"skill {skill_name!r} has unsupported frontmatter indentation")
    name = fields.get("name")
    description = fields.get("description")
    if name != skill_name:
        raise RenderError(f"skill {skill_name!r} frontmatter name must match its directory")
    if not isinstance(description, str) or not description.strip():
        raise RenderError(f"skill {skill_name!r} has no description")
    return fields


def _skill_inputs(canonical_root: Path) -> list[tuple[str, Path, dict[str, Any], str]]:
    skills_root = _regular_directory(canonical_root / "skills", "canonical skills directory")
    result: list[tuple[str, Path, dict[str, Any], str]] = []
    for path in sorted(skills_root.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise RenderError(f"canonical skill entry must not be a symlink: {path}")
        if not path.is_dir():
            continue
        skill_path = path / "SKILL.md"
        contents = _read_text(skill_path, f"canonical skill {path.name!r}")
        result.append((path.name, skill_path, parse_skill_frontmatter(contents, path.name), contents))
    if not result:
        raise RenderError("canonical skills directory has no skills")
    return result


def skill_inventory(repo_root: Path | str | None = None) -> tuple[str, ...]:
    """Return the sorted canonical skill inventory used for commands."""

    root = _root(repo_root)
    canonical_root = root / "packages" / "expskill"
    return tuple(item[0] for item in _skill_inputs(canonical_root))


def _load_profile(canonical_root: Path, name: str) -> dict[str, Any]:
    path = canonical_root / "assets" / "agents" / f"{name}.toml"
    try:
        profile = tomllib.loads(_read_text(path, f"canonical agent profile {name!r}"))
    except tomllib.TOMLDecodeError as error:
        raise RenderError(f"canonical agent profile {name!r} is invalid TOML: {error}") from error
    if not isinstance(profile, dict):
        raise RenderError(f"canonical agent profile {name!r} must be a TOML table")
    if profile.get("name") != name:
        raise RenderError(f"canonical agent profile {name!r} name must match its filename")
    for field in ("description", "developer_instructions"):
        value = profile.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RenderError(f"canonical agent profile {name!r} has no {field}")
    return profile


def _quote_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if value and re.fullmatch(r"[A-Za-z0-9_./:@+,-]+", value):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _render_mapping(mapping: Mapping[str, Any], indent: int) -> list[str]:
    lines: list[str] = []
    for key in mapping:
        value = mapping[key]
        prefix = " " * indent + _quote_key(str(key)) + ":"
        if isinstance(value, Mapping):
            lines.append(prefix)
            lines.extend(_render_mapping(value, indent + 2))
        else:
            lines.append(f"{prefix} {_yaml_scalar(value)}")
    return lines


def render_agent(
    name: str,
    profile: Mapping[str, Any],
    overlay_entry: Mapping[str, Any],
    model: str,
    effort: str,
    runtime_paragraph: str,
) -> str:
    """Render one OpenCode agent markdown document."""

    description = profile.get("description")
    instructions = profile.get("developer_instructions")
    closing = overlay_entry.get("closing")
    permission = overlay_entry.get("permission")
    if not isinstance(description, str) or not description.strip():
        raise RenderError(f"canonical agent profile {name!r} has no description")
    if not isinstance(instructions, str) or not instructions.strip():
        raise RenderError(f"canonical agent profile {name!r} has no developer instructions")
    if not isinstance(closing, str) or not closing.strip():
        raise RenderError(f"OpenCode agent overlay entry {name!r} has no closing")
    if not isinstance(permission, Mapping) or not permission:
        raise RenderError(f"OpenCode agent overlay entry {name!r} has no permission mapping")
    lines = [
        "---",
        f"description: {_yaml_scalar(description.strip())}",
        "mode: subagent",
        f"model: {_yaml_scalar(model.strip())}",
        f"reasoningEffort: {_yaml_scalar(effort.strip())}",
    ]
    if overlay_entry.get("temperature") is not None:
        lines.append(f"temperature: {_yaml_scalar(overlay_entry['temperature'])}")
    lines.append("permission:")
    lines.extend(_render_mapping(permission, 2))
    lines.extend(("---", "", instructions.strip(), "", f"{runtime_paragraph.strip()} {closing.strip()}"))
    return "\n".join(lines) + "\n"


def render_agents(repo_root: Path | str | None = None) -> dict[str, str]:
    """Render every agent described by the canonical TOML and overlay."""

    root = _root(repo_root)
    canonical_root = root / "packages" / "expskill"
    package_root = canonical_root / "opencode"
    spec = load_overlay(package_root)
    profiles = spec["model_profiles"]
    active = profiles[spec["default_model_profile"]]
    entries = spec["agents"]
    result: dict[str, str] = {}
    for name in sorted(entries):
        entry = entries[name]
        if not isinstance(entry, Mapping):
            raise RenderError(f"OpenCode agent overlay entry {name!r} must be an object")
        result[name] = render_agent(
            name,
            _load_profile(canonical_root, name),
            entry,
            str(active["model"]),
            str(active["reasoningEffort"]),
            str(spec["runtime_paragraph"]),
        )
    return result


def _command_description(frontmatter: Mapping[str, Any]) -> str:
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise RenderError("canonical skill description must be non-empty")
    return description.strip()


def render_command(name: str, frontmatter: Mapping[str, Any]) -> str:
    """Render one thin command wrapper from a canonical skill frontmatter."""

    description = _command_description(frontmatter)
    metadata = frontmatter.get("metadata")
    autoinvoke = isinstance(metadata, Mapping) and metadata.get("opencode/autoinvoke") == "true"
    activation = (
        "This is the only skill that may activate without an explicit invocation."
        if autoinvoke
        else "This command is explicit-only."
    )
    return (
        "---\n"
        f"description: {_yaml_scalar(description)}\n"
        "---\n"
        f"Load the `{name}` skill through the skill tool and follow that skill exactly. "
        "Apply it to the following request.\n\n"
        "$ARGUMENTS\n\n"
        f"When $ARGUMENTS is empty, ask the user for the request before doing anything else. "
        f"{activation} Resolve helper scripts relative to the real path of the loaded "
        "SKILL.md file after resolving any symlinks, then follow the skill relative script "
        "paths from there. Never push, merge, approve, or deliver remotely.\n"
    )


def render_commands(repo_root: Path | str | None = None) -> dict[str, str]:
    """Render one command wrapper for every canonical skill."""

    root = _root(repo_root)
    canonical_root = root / "packages" / "expskill"
    return {
        name: render_command(name, frontmatter)
        for name, _path, frontmatter, _contents in _skill_inputs(canonical_root)
    }


def render_catalog(repo_root: Path | str | None = None) -> dict[str, Any]:
    """Return the machine-readable runtime catalog for generated consumers."""

    root = _root(repo_root)
    canonical_root = root / "packages" / "expskill"
    package_root = canonical_root / "opencode"
    spec = load_overlay(package_root)
    profiles = spec["model_profiles"]
    active = profiles[spec["default_model_profile"]]
    agents: dict[str, Any] = {}
    for name in sorted(spec["agents"]):
        entry = spec["agents"][name]
        if not isinstance(entry, Mapping):
            raise RenderError(f"OpenCode agent overlay entry {name!r} must be an object")
        profile = _load_profile(canonical_root, name)
        config: dict[str, Any] = {
            "description": profile["description"],
            "mode": "subagent",
            "model": active["model"],
            "reasoningEffort": active["reasoningEffort"],
        }
        if "temperature" in entry:
            config["temperature"] = entry["temperature"]
        if "permission" in entry:
            config["permission"] = copy.deepcopy(entry["permission"])
        if "closing" in entry:
            config["closing"] = entry["closing"]
        agents[name] = config

    commands: dict[str, Any] = {}
    for name, _path, frontmatter, _contents in _skill_inputs(canonical_root):
        metadata = frontmatter.get("metadata")
        metadata_values = dict(metadata) if isinstance(metadata, Mapping) else {}
        commands[name] = {
            "description": _command_description(frontmatter),
            "skill": name,
            "metadata": metadata_values,
        }
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "agents": agents,
        "commands": commands,
    }


def render_runtime_catalog(repo_root: Path | str | None = None) -> str:
    return json.dumps(render_catalog(repo_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_all(repo_root: Path | str | None = None) -> dict[str, str]:
    """Render all generated package documents keyed by publish-relative path."""

    return {
        **{f"agents/{name}.md": text for name, text in render_agents(repo_root).items()},
        **{f"commands/{name}.md": text for name, text in render_commands(repo_root).items()},
        "catalog.json": render_runtime_catalog(repo_root),
    }


# A short alias is useful to callers that treat this module as a pure renderer.
render = render_all
render_opencode = render_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render OpenCode package documents without writing files.")
    parser.add_argument("--root", type=Path, default=None, help="repository root (defaults to this checkout)")
    arguments = parser.parse_args(argv)
    try:
        payload = render_all(arguments.root)
    except RenderError as error:
        print(f"render error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({key: len(value) for key, value in sorted(payload.items())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
