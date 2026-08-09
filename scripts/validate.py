from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


MARKETPLACE_NAME = "codex-dev-flow"
PLUGIN_NAME = "codex-dev-flow"
PLUGIN_VERSION = "0.1.0"
PLUGIN_VERSION_PATTERN = re.compile(
    rf"{re.escape(PLUGIN_VERSION)}(?:\+codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
REPOSITORY_URL = "https://github.com/g-imhoff/codex-dev-flow"
PLUGIN_CATEGORY = "Developer Tools"
SKILLS_PATH = "./skills/"
AGENTS_PATH = "assets/agents"
PLACEHOLDER = "[TODO:"
EXPECTED_SKILLS = {
    "full-code-change",
    "quick-code-change",
    "route-code-change",
}

EXPECTED_AGENTS = {
    "devflow-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "devflow-test-engineer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-reviewer": ("gpt-5.6-sol", "xhigh", "read-only"),
    "devflow-verifier": ("gpt-5.6-luna", "max", "workspace-write"),
}

REQUIRED_AGENT_FIELDS = (
    "name",
    "description",
    "model",
    "model_reasoning_effort",
    "sandbox_mode",
    "developer_instructions",
)

AGENT_BOUNDARIES = {
    "devflow-explorer": ("read-only", "no fixes", "no delegation"),
    "devflow-test-engineer": (
        "test strategy",
        "shared acceptance tests",
        "regression",
        "no product implementation",
    ),
    "devflow-implementer": (
        "exactly one brief",
        "red-green-refactor",
        "one owned branch",
        "no delegation",
        "no scope expansion",
    ),
    "devflow-reviewer": (
        "read-only",
        "severity",
        "evidence",
        "impact",
        "correction",
        "ready",
        "not ready",
    ),
    "devflow-verifier": (
        "exact commands",
        "exit evidence",
        "no tracked-source edits",
        "no reliance on another agent's claims",
    ),
}


def validate_repository(root: Path) -> tuple[str, ...]:
    repository_root = Path(root)
    errors: list[str] = []
    marketplace_path = repository_root / ".agents" / "plugins" / "marketplace.json"
    marketplace = _load_json_object(marketplace_path, "marketplace.json", errors)
    if marketplace is not None:
        _validate_marketplace(marketplace, repository_root, errors)

    plugin_root = repository_root / "plugins" / PLUGIN_NAME
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = _load_json_object(manifest_path, "plugin.json", errors)
    if manifest is not None:
        _validate_plugin_manifest(manifest, plugin_root, errors)

    _validate_agents(plugin_root, errors)
    return tuple(errors)


def _load_json_object(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        errors.append(f"{label} could not be read: {error}")
        return None
    except json.JSONDecodeError as error:
        errors.append(f"{label} is not valid JSON: {error.msg}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    _reject_placeholders(payload, label, errors)
    return payload


def _reject_placeholders(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        if PLACEHOLDER in value:
            errors.append(f"{path} contains a placeholder")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_placeholders(item, f"{path}[{index}]", errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_placeholders(item, f"{path}.{key}", errors)


def _validate_marketplace(
    marketplace: dict[str, Any], repository_root: Path, errors: list[str]
) -> None:
    if marketplace.get("name") != MARKETPLACE_NAME:
        errors.append(
            f"marketplace name must be {MARKETPLACE_NAME!r}, got {marketplace.get('name')!r}"
        )
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append("marketplace plugins must be an array")
        return

    plugin_names: list[str] = []
    matching_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(plugins):
        label = f"marketplace plugins[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name must be a non-empty string")
            continue
        plugin_names.append(name)
        if name != PLUGIN_NAME:
            continue
        matching_entries.append(entry)
        source = entry.get("source")
        if not isinstance(source, dict):
            errors.append(f"{label}.source must be an object")
        else:
            if source.get("source") != "local":
                errors.append(f"{label}.source.source must be 'local'")
            source_path = source.get("path")
            if source_path != "./plugins/codex-dev-flow":
                errors.append(
                    f"{label}.source.path must be './plugins/codex-dev-flow', got {source_path!r}"
                )
            elif not (repository_root / "plugins" / PLUGIN_NAME).is_dir():
                errors.append(f"{label}.source.path does not resolve to the plugin directory")
        policy = entry.get("policy")
        if not isinstance(policy, dict):
            errors.append(f"{label}.policy must be an object")
        else:
            if policy.get("installation") != "AVAILABLE":
                errors.append(f"{label}.policy.installation must be 'AVAILABLE'")
            if policy.get("authentication") != "ON_INSTALL":
                errors.append(f"{label}.policy.authentication must be 'ON_INSTALL'")
        if entry.get("category") != PLUGIN_CATEGORY:
            errors.append(f"{label}.category must be {PLUGIN_CATEGORY!r}")

    duplicates = sorted({name for name in plugin_names if plugin_names.count(name) > 1})
    for name in duplicates:
        errors.append(f"marketplace plugin name {name!r} is duplicated")
    if not matching_entries:
        errors.append("marketplace is missing plugin 'codex-dev-flow'")
    elif len(matching_entries) > 1:
        errors.append("marketplace plugin 'codex-dev-flow' is duplicated")


def _validate_plugin_manifest(
    manifest: dict[str, Any], plugin_root: Path, errors: list[str]
) -> None:
    if manifest.get("name") != PLUGIN_NAME:
        errors.append(f"plugin name must be {PLUGIN_NAME!r}, got {manifest.get('name')!r}")
    version = manifest.get("version")
    if not isinstance(version, str) or PLUGIN_VERSION_PATTERN.fullmatch(version) is None:
        errors.append(
            f"plugin version must be {PLUGIN_VERSION!r} or a Codex cachebuster, got {version!r}"
        )
    if manifest.get("repository") != REPOSITORY_URL:
        errors.append(
            f"plugin repository must be {REPOSITORY_URL!r}, got {manifest.get('repository')!r}"
        )
    if manifest.get("skills") != SKILLS_PATH:
        errors.append(
            f"plugin skills path must be {SKILLS_PATH!r}, got {manifest.get('skills')!r}"
        )

    forbidden_fields = {"hooks", "mcpServers", "apps", "icons", "authentication"}
    for field in sorted(forbidden_fields.intersection(manifest)):
        errors.append(f"plugin manifest must not define {field!r}")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin interface must be an object")
    elif interface.get("category") != PLUGIN_CATEGORY:
        errors.append(
            f"plugin interface category must be {PLUGIN_CATEGORY!r}, got {interface.get('category')!r}"
        )

    _validate_skills(plugin_root / "skills", errors)


def _validate_skills(skills_root: Path, errors: list[str]) -> None:
    if not skills_root.is_dir():
        if skills_root.exists():
            errors.append(f"skills path must be a directory: {skills_root}")
        else:
            errors.append(f"skills directory is missing: {skills_root}")
        return
    entries = {path.name: path for path in skills_root.iterdir()}
    for name in sorted(EXPECTED_SKILLS - entries.keys()):
        errors.append(f"required skill {name!r} is missing")
    for name in sorted(entries.keys() - EXPECTED_SKILLS):
        errors.append(f"unexpected skill entry {name!r}")
    names: list[str] = []
    for skill_root in sorted(entries.values(), key=lambda path: path.name):
        if not skill_root.is_dir():
            errors.append(f"skill {skill_root.name!r} must be a directory")
            continue
        skill_path = skill_root / "SKILL.md"
        if not skill_path.is_file():
            errors.append(f"skill {skill_root.name!r} is missing SKILL.md")
            continue
        try:
            contents = skill_path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"skill {skill_root.name!r} could not be read: {error}")
            continue
        if PLACEHOLDER in contents:
            errors.append(f"skill {skill_root.name!r} contains a placeholder")
        frontmatter = _parse_frontmatter(contents, skill_root.name, errors)
        if frontmatter is None:
            continue
        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if not name:
            errors.append(f"skill {skill_root.name!r} has no frontmatter name")
        else:
            names.append(name)
        if not description:
            errors.append(f"skill {skill_root.name!r} has no frontmatter description")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        errors.append(f"skill name {name!r} is duplicated")


def _parse_frontmatter(
    contents: str, skill_directory: str, errors: list[str]
) -> dict[str, str] | None:
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"skill {skill_directory!r} must start with frontmatter")
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append(f"skill {skill_directory!r} frontmatter is not closed")
        return None
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _validate_agents(plugin_root: Path, errors: list[str]) -> None:
    agents_root = plugin_root / AGENTS_PATH
    if not agents_root.is_dir():
        errors.append(f"agent directory is missing: {agents_root}")
        return

    expected_filenames = {f"{name}.toml" for name in EXPECTED_AGENTS}
    for path in sorted(agents_root.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        if path.suffix == ".toml" and path.name not in expected_filenames:
            errors.append(f"unexpected agent profile {path.stem!r}")
        elif path.name.startswith("devflow-") and path.name not in expected_filenames:
            errors.append(f"unexpected agent profile {path.name!r}")
    for expected_name in EXPECTED_AGENTS:
        path = agents_root / f"{expected_name}.toml"
        if not path.is_file():
            errors.append(f"missing agent profile {expected_name!r}")
            continue
        _validate_agent_profile(path, expected_name, errors)


def _validate_agent_profile(path: Path, expected_name: str, errors: list[str]) -> None:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"agent profile {expected_name!r} could not be read: {error}")
        return
    if PLACEHOLDER in contents:
        errors.append(f"agent profile {expected_name!r} contains a placeholder")
    try:
        profile = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as error:
        errors.append(f"agent profile {expected_name!r} is invalid TOML: {error}")
        return
    if not isinstance(profile, dict):
        errors.append(f"agent profile {expected_name!r} must be a TOML table")
        return

    for field in REQUIRED_AGENT_FIELDS:
        value = profile.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"agent profile {expected_name!r} field {field!r} must be non-empty")
    actual_name = profile.get("name")
    if actual_name != expected_name:
        errors.append(
            f"agent profile {expected_name!r} name must be {expected_name!r}, got {actual_name!r}"
        )

    expected_model, expected_effort, expected_sandbox = EXPECTED_AGENTS[expected_name]
    for field, expected_value in (
        ("model", expected_model),
        ("model_reasoning_effort", expected_effort),
        ("sandbox_mode", expected_sandbox),
    ):
        actual_value = profile.get(field)
        if actual_value != expected_value:
            errors.append(
                f"agent profile {expected_name!r} {field} must be {expected_value!r}, "
                f"got {actual_value!r}"
            )

    instructions = profile.get("developer_instructions")
    if isinstance(instructions, str):
        normalized = instructions.lower()
        for phrase in AGENT_BOUNDARIES[expected_name]:
            if phrase not in normalized:
                errors.append(
                    f"agent profile {expected_name!r} instructions must include {phrase!r}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the codex-dev-flow repository contract.")
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = validate_repository(args.root)
    if errors:
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Repository contract passed: {Path(args.root).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
