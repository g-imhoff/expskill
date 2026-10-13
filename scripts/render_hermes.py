"""Pure renderer for the Hermes package surface.

Canonical skills and agent bodies live under ``plugins/expskill/content``.
Hermes' Markdown agents are generated output, not sources. This module only
reads inputs and returns deterministic values.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True


SCHEMA_VERSION = "hermes-agents.v1"
MODEL_POLICY = "active-hermes-provider"
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
    """Raised when canonical or Hermes overlay input cannot be rendered."""


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


def repository_root() -> Path:
    return Path(__file__).absolute().parents[1]


def _root(value: Path | str | None) -> Path:
    candidate = Path(value).expanduser() if value is not None else repository_root()
    _reject_symlink_components(candidate, "repository root")
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RenderError(f"repository root cannot be resolved: {candidate}: {error}") from error


def _regular_file(path: Path, label: str) -> Path:
    _reject_symlink_components(path, label)
    if path.is_symlink():
        raise RenderError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise RenderError(f"{label} is not a regular file: {path}")
    return path


def _regular_directory(path: Path, label: str) -> Path:
    _reject_symlink_components(path, label)
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
    """Load and validate the Hermes agent overlay from ``package_root``."""

    spec = _read_json(package_root / "agents.json", "Hermes agent overlay")
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise RenderError(
            f"Hermes agent overlay schema_version must be {SCHEMA_VERSION!r}"
        )
    agents = spec.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise RenderError("Hermes agent overlay must declare agents")
    policy = spec.get("model_policy")
    if policy != MODEL_POLICY:
        raise RenderError(
            f"Hermes agent overlay model_policy must be {MODEL_POLICY!r}"
        )
    runtime = spec.get("runtime_paragraph")
    if not isinstance(runtime, str) or not runtime.strip():
        raise RenderError("Hermes agent overlay has no runtime_paragraph")
    for name, entry in agents.items():
        if not isinstance(entry, Mapping) or set(entry) != {"role", "sandbox"}:
            raise RenderError(
                f"Hermes agent overlay entry {name!r} must contain role and sandbox"
            )
        for field in ("role", "sandbox"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RenderError(
                    f"Hermes agent overlay entry {name!r} has no {field}"
                )
    return spec


def load_agent_content(canonical_root: Path) -> dict[str, Any]:
    """Load the neutral authored metadata shared by every host."""

    spec = _read_json(canonical_root / "content" / "agents.json", "canonical agent metadata")
    if spec.get("schema_version") != "agent-content.v1":
        raise RenderError("canonical agent metadata has an unsupported schema")
    agents = spec.get("agents")
    if not isinstance(agents, dict) or set(agents) != set(EXPECTED_AGENT_NAMES):
        raise RenderError("canonical agent metadata must contain exactly seven agents")
    for name, entry in agents.items():
        if not isinstance(entry, Mapping) or set(entry) != {"description", "closing"}:
            raise RenderError(
                f"canonical agent metadata {name!r} must contain description and closing"
            )
        for field in ("description", "closing"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RenderError(f"canonical agent metadata {name!r} has no {field}")
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
    skills_root = _regular_directory(
        canonical_root / "content" / "skills", "canonical skills directory"
    )
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
    """Return the sorted canonical skill inventory used for the package."""

    root = _root(repo_root)
    canonical_root = root / "plugins" / "expskill"
    return tuple(item[0] for item in _skill_inputs(canonical_root))


def _load_profile(
    canonical_root: Path,
    name: str,
) -> dict[str, Any]:
    """Load one canonical agent body with Hermes-facing metadata."""

    body_path = canonical_root / "content" / "agents" / f"{name}.md"
    body = _read_text(body_path, f"canonical agent body {name!r}").strip()
    if not body:
        raise RenderError(f"canonical agent body {name!r} is empty")
    if '"""' in body:
        raise RenderError(f"canonical agent body {name!r} must not contain triple quotes")
    policy = _read_json(canonical_root / "content/policies/authoring-runtime.json", "authoring runtime policy")
    instructions = policy.get("instructions")
    if set(policy) != {"schema_version", "instructions"} or policy.get("schema_version") != "authoring-runtime.v1":
        raise RenderError("authoring runtime policy has an unsupported schema")
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 1000:
        raise RenderError("authoring runtime instructions must contain 1-1000 characters")
    return {"name": name, "developer_instructions": f"{body}\n\n{instructions}"}


def _canonical_agent_names(canonical_root: Path) -> tuple[str, ...]:
    agents_root = _regular_directory(
        canonical_root / "content" / "agents", "canonical agent bodies directory"
    )
    names: list[str] = []
    for path in sorted(agents_root.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise RenderError(f"canonical agent profile entry must not be a symlink: {path}")
        if path.is_file() and path.name.startswith("expskill-") and path.suffix == ".md":
            names.append(path.stem)
    observed = set(names)
    expected = set(EXPECTED_AGENT_NAMES)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing!r}")
        if extra:
            details.append(f"extra {extra!r}")
        raise RenderError(
            "canonical agent body roster must contain exactly seven agents ("
            + "; ".join(details)
            + ")"
        )
    return EXPECTED_AGENT_NAMES


def _validate_agent_roster(canonical_root: Path, spec: Mapping[str, Any]) -> tuple[str, ...]:
    canonical_names = _canonical_agent_names(canonical_root)
    entries = spec.get("agents")
    if not isinstance(entries, Mapping):
        raise RenderError("Hermes agent overlay must declare agents")
    if set(entries) != set(canonical_names):
        missing = sorted(set(canonical_names) - set(entries))
        extra = sorted(set(entries) - set(canonical_names))
        details: list[str] = []
        if missing:
            details.append(f"missing {missing!r}")
        if extra:
            details.append(f"extra {extra!r}")
        raise RenderError(
            "Hermes agent overlay entries must exactly match canonical profiles ("
            + "; ".join(details)
            + ")"
        )
    return canonical_names


def _agent_title(name: str) -> str:
    suffix = name[len("expskill-") :] if name.startswith("expskill-") else name
    return "Expskill " + suffix.replace("-", " ")


def render_agent(
    name: str,
    profile: Mapping[str, Any],
    overlay_entry: Mapping[str, Any],
    content_entry: Mapping[str, Any],
    model_policy: str,
    runtime_paragraph: str,
) -> str:
    """Render one Hermes agent markdown document."""

    description = content_entry.get("description")
    closing = content_entry.get("closing")
    role = overlay_entry.get("role")
    sandbox = overlay_entry.get("sandbox")
    if not isinstance(description, str) or not description.strip():
        raise RenderError(f"canonical agent metadata {name!r} has no description")
    if not isinstance(closing, str) or not closing.strip():
        raise RenderError(f"canonical agent metadata {name!r} has no closing")
    for field, value in (("role", role), ("sandbox", sandbox)):
        if not isinstance(value, str) or not value.strip():
            raise RenderError(f"Hermes agent overlay entry {name!r} has no {field}")
    if not isinstance(model_policy, str) or not model_policy.strip():
        raise RenderError("Hermes agent overlay has no model_policy")
    if not isinstance(runtime_paragraph, str) or not runtime_paragraph.strip():
        raise RenderError("Hermes agent overlay has no runtime_paragraph")
    instructions = profile.get("developer_instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise RenderError(f"canonical agent profile {name!r} has no developer instructions")
    lines = [
        "---",
        f"name: {name}",
        f"role: {role}",
        f"sandbox: {sandbox}",
        f"model_policy: {model_policy}",
        "---",
        "",
        f"# {_agent_title(name)}",
        "",
        description.strip(),
        "",
        "## Working agreement",
        "",
        instructions.strip(),
        "",
        f"{runtime_paragraph.strip()} {closing.strip()}",
    ]
    return "\n".join(lines) + "\n"


def render_agents(repo_root: Path | str | None = None) -> dict[str, str]:
    """Render every agent described by the canonical content and overlay."""

    root = _root(repo_root)
    canonical_root = root / "plugins" / "expskill"
    package_root = canonical_root / "hermes"
    spec = load_overlay(package_root)
    content = load_agent_content(canonical_root)
    entries = spec["agents"]
    result: dict[str, str] = {}
    for name in _validate_agent_roster(canonical_root, spec):
        entry = entries[name]
        if not isinstance(entry, Mapping):
            raise RenderError(f"Hermes agent overlay entry {name!r} must be an object")
        content_entry = content["agents"][name]
        if not isinstance(content_entry, Mapping):
            raise RenderError(f"canonical agent metadata {name!r} must be an object")
        result[name] = render_agent(
            name,
            _load_profile(canonical_root, name),
            entry,
            content_entry,
            str(spec["model_policy"]),
            str(spec["runtime_paragraph"]),
        )
    return result


def render_all(repo_root: Path | str | None = None) -> dict[str, str]:
    """Render every generated Hermes file keyed by package-relative path."""

    rendered = render_agents(repo_root)
    return {f"agents/{name}.md": rendered[name] for name in rendered}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the Hermes package surface")
    parser.add_argument("--root", default=None, help="repository root")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate inputs without writing output",
    )
    args = parser.parse_args(argv)
    try:
        rendered = render_all(args.root)
    except RenderError as error:
        print(f"hermes render failed: {error}", file=sys.stderr)
        return 1
    if args.check:
        print(f"hermes render ok: {len(rendered)} agents")
        return 0
    for relative, contents in sorted(rendered.items()):
        print(f"=== {relative} ===")
        print(contents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
