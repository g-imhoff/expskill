from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
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
POLICY_PATH = "assets/execution-policy.json"
HELPER_PATH = "scripts/worktrees.py"
PLAN_GRAPH_HELPER_PATH = "scripts/plan_graph.py"
PLACEHOLDER = "[TODO:"
PLUGIN_AUTHOR_NAME = "g-imhoff"
PLUGIN_INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
    "defaultPrompt",
}
PUBLIC_PHASE_TOKENS = {
    "$brainstorm",
    "$plan",
    "$implement",
    "$review",
    "$verify",
    "$integrate",
    "$use-expand",
    "$design",
}
PUBLIC_METADATA_JARGON = re.compile(
    r"\b(?:quick|full|models?|caps?|scaffold|private[- ]marketplace|local plugin)\b",
    re.IGNORECASE,
)
EXPECTED_SKILLS = {
    "use-expand",
    "design",
    "brainstorm",
    "plan",
    "implement",
    "review",
    "verify",
    "integrate",
}
RETIRED_SKILLS = {"full-code-change", "quick-code-change", "route-code-change"}
PUBLIC_SKILL_JARGON = re.compile(r"\b(?:quick|full|model|caps?)\b", re.IGNORECASE)
BRAINSTORM_CATALOG_RELATIVE = "skills/brainstorm/references/brainstorm-techniques.csv"
BRAINSTORM_CATALOG_SHA256 = "0ab5878b1dbc9e3fa98cb72abfc3920a586b9e2b42609211bb0516eefd542039"
BRAINSTORM_CATALOG_PREAMBLE = (
    "# Source: https://github.com/bmad-code-org/BMAD-METHOD/blob/"
    "890fcda760bade4d6080f5fa09aa8f658bc4a4a5/"
    "web-bundles/brainstorming-coach/brain-methods.csv\n"
    "# Source-Revision: 890fcda760bade4d6080f5fa09aa8f658bc4a4a5\n"
    "# Upstream-SHA256: 0ab5878b1dbc9e3fa98cb72abfc3920a586b9e2b42609211bb0516eefd542039\n"
    "#\n"
    "# MIT License\n"
    "#\n"
    "# Copyright (c) 2025 BMad Code, LLC\n"
    "#\n"
    "# This project incorporates contributions from the open source community.\n"
    "# See [CONTRIBUTORS.md](CONTRIBUTORS.md) for contributor attribution.\n"
    "#\n"
    "# Permission is hereby granted, free of charge, to any person obtaining a copy\n"
    "# of this software and associated documentation files (the \"Software\"), to deal\n"
    "# in the Software without restriction, including without limitation the rights\n"
    "# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
    "# copies of the Software, and to permit persons to whom the Software is\n"
    "# furnished to do so, subject to the following conditions:\n"
    "#\n"
    "# The above copyright notice and this permission notice shall be included in all\n"
    "# copies or substantial portions of the Software.\n"
    "#\n"
    "# THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n"
    "# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
    "# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
    "# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
    "# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
    "# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
    "# SOFTWARE.\n"
    "#\n"
    "# TRADEMARK NOTICE:\n"
    "# BMad™, BMad Method™, and BMad Core™ are trademarks of BMad Code, LLC, covering all\n"
    "# casings and variations (including BMAD, bmad, BMadMethod, BMAD-METHOD, etc.). The use of\n"
    "# these trademarks in this software does not grant any rights to use the trademarks\n"
    "# for any other purpose. See [TRADEMARK.md](TRADEMARK.md) for detailed guidelines.\n"
    "#\n"
)

EXPECTED_AGENTS = {
    "devflow-explorer": ("gpt-5.6-terra", "medium", "read-only"),
    "devflow-test-engineer": ("gpt-5.6-luna", "high", "workspace-write"),
    "devflow-implementer": ("gpt-5.6-luna", "medium", "workspace-write"),
    "devflow-implementer-high": ("gpt-5.6-luna", "high", "workspace-write"),
    "devflow-reviewer": ("gpt-5.6-terra", "medium", "read-only"),
    "devflow-critical-reviewer": ("gpt-5.6-sol", "high", "read-only"),
    "devflow-verifier": ("gpt-5.6-luna", "medium", "workspace-write"),
    "devflow-verifier-low": ("gpt-5.6-luna", "low", "workspace-write"),
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
    "devflow-implementer-high": (
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
    "devflow-critical-reviewer": (
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
    "devflow-verifier-low": (
        "exact commands",
        "exit evidence",
        "no tracked-source edits",
        "no reliance on another agent's claims",
    ),
}

EXPECTED_POLICY_PROFILES = {
    "devflow-explorer": {
        "agent_type": "devflow-explorer",
        "role": "explorer",
        "model": "gpt-5.6-terra",
        "effort": "medium",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "devflow-implementer": {
        "agent_type": "devflow-implementer",
        "role": "implementer",
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "devflow-implementer-high": {
        "agent_type": "devflow-implementer-high",
        "role": "implementer",
        "model": "gpt-5.6-luna",
        "effort": "high",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "devflow-reviewer": {
        "agent_type": "devflow-reviewer",
        "role": "reviewer",
        "model": "gpt-5.6-terra",
        "effort": "medium",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "devflow-critical-reviewer": {
        "agent_type": "devflow-critical-reviewer",
        "role": "critical-reviewer",
        "model": "gpt-5.6-sol",
        "effort": "high",
        "sandbox_mode": "read-only",
        "escalation": "critical-review",
    },
    "devflow-verifier": {
        "agent_type": "devflow-verifier",
        "role": "verifier",
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "devflow-verifier-low": {
        "agent_type": "devflow-verifier-low",
        "role": "verifier",
        "model": "gpt-5.6-luna",
        "effort": "low",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "devflow-test-engineer": {
        "agent_type": "devflow-test-engineer",
        "role": "test-engineer",
        "model": "gpt-5.6-luna",
        "effort": "high",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
}

EXPECTED_POLICY_ROUTES = {
    "quick": {
        "low": {
            "allowed_profiles": ["devflow-verifier-low"],
            "selected": [
                {
                    "role": "verifier",
                    "profile": "devflow-verifier-low",
                    "agent_type": "devflow-verifier-low",
                }
            ],
            "max_agent_calls": 3,
            "max_concurrency": 1,
            "max_depth": 1,
            "max_retries": 0,
        },
        "standard": {
            "allowed_profiles": ["devflow-reviewer", "devflow-verifier"],
            "selected": [
                {
                    "role": "reviewer",
                    "profile": "devflow-reviewer",
                    "agent_type": "devflow-reviewer",
                },
                {
                    "role": "verifier",
                    "profile": "devflow-verifier",
                    "agent_type": "devflow-verifier",
                },
            ],
            "max_agent_calls": 5,
            "max_concurrency": 2,
            "max_depth": 1,
            "max_retries": 1,
        },
        "high": {
            "allowed_profiles": ["devflow-critical-reviewer", "devflow-verifier"],
            "selected": [
                {
                    "role": "critical-reviewer",
                    "profile": "devflow-critical-reviewer",
                    "agent_type": "devflow-critical-reviewer",
                },
                {
                    "role": "verifier",
                    "profile": "devflow-verifier",
                    "agent_type": "devflow-verifier",
                },
            ],
            "max_agent_calls": 5,
            "max_concurrency": 2,
            "max_depth": 1,
            "max_retries": 1,
        },
    },
    "full": {
        "standard": {
            "allowed_profiles": [
                "devflow-explorer",
                "devflow-implementer",
                "devflow-reviewer",
                "devflow-verifier",
                "devflow-test-engineer",
            ],
            "selected": [
                {
                    "role": "explorer",
                    "profile": "devflow-explorer",
                    "agent_type": "devflow-explorer",
                },
                {
                    "role": "implementer",
                    "profile": "devflow-implementer",
                    "agent_type": "devflow-implementer",
                },
                {
                    "role": "reviewer",
                    "profile": "devflow-reviewer",
                    "agent_type": "devflow-reviewer",
                },
                {
                    "role": "verifier",
                    "profile": "devflow-verifier",
                    "agent_type": "devflow-verifier",
                },
                {
                    "role": "test-engineer",
                    "profile": "devflow-test-engineer",
                    "agent_type": "devflow-test-engineer",
                },
            ],
            "max_agent_calls": 25,
            "max_concurrency": 3,
            "max_depth": 1,
            "max_retries": 1,
            "max_elapsed_ms": 7200000,
        },
        "high": {
            "allowed_profiles": [
                "devflow-explorer",
                "devflow-implementer-high",
                "devflow-reviewer",
                "devflow-verifier",
                "devflow-test-engineer",
                "devflow-critical-reviewer",
            ],
            "selected": [
                {
                    "role": "explorer",
                    "profile": "devflow-explorer",
                    "agent_type": "devflow-explorer",
                },
                {
                    "role": "implementer",
                    "profile": "devflow-implementer-high",
                    "agent_type": "devflow-implementer-high",
                },
                {
                    "role": "reviewer",
                    "profile": "devflow-reviewer",
                    "agent_type": "devflow-reviewer",
                },
                {
                    "role": "verifier",
                    "profile": "devflow-verifier",
                    "agent_type": "devflow-verifier",
                },
                {
                    "role": "test-engineer",
                    "profile": "devflow-test-engineer",
                    "agent_type": "devflow-test-engineer",
                },
                {
                    "role": "critical-reviewer",
                    "profile": "devflow-critical-reviewer",
                    "agent_type": "devflow-critical-reviewer",
                },
            ],
            "max_agent_calls": 25,
            "max_concurrency": 3,
            "max_depth": 1,
            "max_retries": 1,
            "max_elapsed_ms": 7200000,
        },
    },
}


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _is_symlink(path: Path) -> bool:
    metadata = _lstat(path)
    return metadata is not None and stat.S_ISLNK(metadata.st_mode)


def _symlink_component(root: Path, relative: str) -> Path | None:
    current = root
    if _is_symlink(current):
        return current
    for component in Path(relative).parts:
        current /= component
        if _is_symlink(current):
            return current
    return None


def _validate_plugin_root(plugin_root: Path, errors: list[str]) -> bool:
    if _is_symlink(plugin_root):
        errors.append(f"plugin root must not be a symlink: {plugin_root}")
        return False
    metadata = _lstat(plugin_root)
    if metadata is None:
        errors.append(f"plugin directory is missing: {plugin_root}")
        return False
    if not stat.S_ISDIR(metadata.st_mode):
        errors.append(f"plugin root must be a directory: {plugin_root}")
        return False
    try:
        resolved = plugin_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        errors.append(f"plugin root cannot be resolved: {plugin_root}: {error}")
        return False
    if resolved != plugin_root:
        errors.append(f"plugin root resolves outside its lexical path: {plugin_root}")
        return False
    return True


def _required_package_path(
    plugin_root: Path,
    relative: str,
    label: str,
    kind: str,
    errors: list[str],
    missing_label: str | None = None,
) -> Path | None:
    path = plugin_root / relative
    symlink = _symlink_component(plugin_root, relative)
    if symlink is not None:
        errors.append(f"{label} contains a symlink: {symlink}")
        return None
    try:
        canonical_root = plugin_root.resolve(strict=True)
        resolved = path.resolve(strict=False)
        resolved.relative_to(canonical_root)
    except ValueError:
        errors.append(f"{label} resolves outside the plugin root: {path}")
        return None
    except (OSError, RuntimeError) as error:
        errors.append(f"{label} cannot be resolved: {path}: {error}")
        return None
    metadata = _lstat(path)
    if metadata is None:
        errors.append(f"{missing_label or label} is missing: {path}")
        return None
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        errors.append(f"{label} must be a directory: {path}")
        return None
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        errors.append(f"{label} must be a regular file: {path}")
        return None
    return path


def _lexical_package_entries(plugin_root: Path) -> list[tuple[Path, os.stat_result]]:
    """Enumerate package entries without traversing symlink directories."""

    pending = [plugin_root]
    entries: list[tuple[Path, os.stat_result]] = []
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
                for child in children:
                    try:
                        metadata = child.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    child_path = Path(child.path)
                    entries.append((child_path, metadata))
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(child_path)
        except OSError:
            continue
    return entries


def validate_repository(root: Path) -> tuple[str, ...]:
    repository_root = Path(root).expanduser()
    try:
        repository_root = repository_root.resolve(strict=True)
    except (OSError, RuntimeError):
        repository_root = repository_root.resolve(strict=False)
    errors: list[str] = []
    marketplace_path = repository_root / ".agents" / "plugins" / "marketplace.json"
    marketplace = _load_json_object(marketplace_path, "marketplace.json", errors)
    if marketplace is not None:
        _validate_marketplace(marketplace, repository_root, errors)

    plugin_root = repository_root / "plugins" / PLUGIN_NAME
    if _validate_plugin_root(plugin_root, errors):
        manifest_path = _required_package_path(
            plugin_root,
            ".codex-plugin/plugin.json",
            "plugin manifest",
            "file",
            errors,
        )
        manifest = (
            _load_json_object(manifest_path, "plugin.json", errors)
            if manifest_path is not None
            else None
        )
        if manifest is not None:
            _validate_plugin_manifest(manifest, plugin_root, errors)

        _validate_agents(plugin_root, errors)
        _validate_policy(plugin_root, errors)
        _validate_helper_and_package_layout(plugin_root, errors)
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
    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 120:
        errors.append("plugin description must be a non-empty string of at most 120 characters")
    else:
        normalized_description = description.lower()
        for phrase in ("seven", "independent", "development phase", "optional", "orchestrator", "$design"):
            if phrase not in normalized_description:
                errors.append(f"plugin description must advertise {phrase!r}")
        if PUBLIC_METADATA_JARGON.search(description):
            errors.append("plugin description exposes private implementation or scaffold jargon")
    if manifest.get("author") != {"name": PLUGIN_AUTHOR_NAME}:
        errors.append(
            f"plugin author must identify {PLUGIN_AUTHOR_NAME!r}, got {manifest.get('author')!r}"
        )

    forbidden_fields = {"hooks", "mcpServers", "apps", "icons", "authentication"}
    for field in sorted(forbidden_fields.intersection(manifest)):
        errors.append(f"plugin manifest must not define {field!r}")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin interface must be an object")
    else:
        missing = sorted(PLUGIN_INTERFACE_FIELDS - set(interface))
        unexpected = sorted(set(interface) - PLUGIN_INTERFACE_FIELDS)
        if missing:
            errors.append(f"plugin interface is missing fields: {missing!r}")
        if unexpected:
            errors.append(f"plugin interface has unexpected fields: {unexpected!r}")
        if interface.get("displayName") != "Codex Dev Flow":
            errors.append("plugin interface displayName must preserve the product identity")
        if interface.get("developerName") != PLUGIN_AUTHOR_NAME:
            errors.append("plugin interface developerName must match the plugin author")
        if interface.get("category") != PLUGIN_CATEGORY:
            errors.append(
                f"plugin interface category must be {PLUGIN_CATEGORY!r}, got {interface.get('category')!r}"
            )
        if interface.get("capabilities") != []:
            errors.append("plugin interface capabilities must be an empty list for this skills-only plugin")

        short_description = interface.get("shortDescription")
        if (
            not isinstance(short_description, str)
            or not short_description.strip()
            or len(short_description) > 80
            or "phase" not in short_description.lower()
            or "routing" not in short_description.lower()
            or PUBLIC_METADATA_JARGON.search(short_description) is not None
        ):
            errors.append(
                "plugin interface shortDescription must concisely advertise phases and optional routing "
                "without private implementation or scaffold jargon"
            )

        long_description = interface.get("longDescription")
        if not isinstance(long_description, str) or not long_description.strip() or len(long_description) > 320:
            errors.append("plugin interface longDescription must be a non-empty string of at most 320 characters")
        else:
            if ("$" + "acceptance") in long_description:
                errors.append("plugin interface longDescription contains removed public token " + "$" + "acceptance")
            for token in sorted(PUBLIC_PHASE_TOKENS):
                if token not in long_description:
                    errors.append(f"plugin interface longDescription must advertise {token}")
            for phrase in ("directly", "one next phase", "required gates"):
                if phrase not in long_description.lower():
                    errors.append(f"plugin interface longDescription must explain {phrase!r}")
            if PUBLIC_METADATA_JARGON.search(long_description):
                errors.append("plugin interface longDescription exposes private implementation or scaffold jargon")

        default_prompt = interface.get("defaultPrompt")
        if (
            not isinstance(default_prompt, str)
            or not default_prompt.strip()
            or len(default_prompt) > 160
            or "$use-expand" not in default_prompt
            or "next development phase" not in default_prompt.lower()
        ):
            errors.append(
                "plugin interface defaultPrompt must explicitly invoke $use-expand for the next development phase"
            )
        elif PUBLIC_METADATA_JARGON.search(default_prompt):
            errors.append("plugin interface defaultPrompt exposes private implementation or scaffold jargon")

    _validate_skills(plugin_root / "skills", errors)


def _validate_skills(skills_root: Path, errors: list[str]) -> None:
    plugin_root = skills_root.parent
    if _required_package_path(
        plugin_root,
        "skills",
        "skills path",
        "directory",
        errors,
        missing_label="skills directory",
    ) is None:
        return
    entries = {path.name: path for path in skills_root.iterdir()}
    for name in sorted(EXPECTED_SKILLS - entries.keys()):
        errors.append(f"required skill {name!r} is missing")
    for name in sorted(entries.keys() - EXPECTED_SKILLS):
        errors.append(f"unexpected skill entry {name!r}")
    names: list[str] = []
    for skill_root in sorted(entries.values(), key=lambda path: path.name):
        relative_skill = f"skills/{skill_root.name}"
        if _required_package_path(
            plugin_root,
            relative_skill,
            f"skill {skill_root.name!r}",
            "directory",
            errors,
        ) is None:
            continue
        expected_files = {"SKILL.md", "agents/openai.yaml"}
        if skill_root.name == "brainstorm":
            expected_files.add("references/brainstorm-techniques.csv")
        if skill_root.name == "design":
            expected_files.update({
                "references/rules-index.md", "references/geometry.md", "references/typography.md",
                "references/interaction.md", "references/forms.md", "references/responsive.md",
                "references/accessibility.md", "references/motion.md", "references/data-display.md",
            })
        expected_directories = {"agents"}
        if skill_root.name == "brainstorm":
            expected_directories.add("references")
        if skill_root.name == "design":
            expected_directories.add("references")
        actual_files = {
            path.relative_to(skill_root).as_posix()
            for path in skill_root.rglob("*")
            if path.is_file()
        }
        actual_directories = {
            path.relative_to(skill_root).as_posix()
            for path in skill_root.rglob("*")
            if path.is_dir()
        }
        for relative in sorted(actual_directories - expected_directories):
            errors.append(f"skill {skill_root.name!r} contains unexpected directory {relative!r}")
        for relative in sorted(actual_files - expected_files):
            errors.append(f"skill {skill_root.name!r} contains unexpected file {relative!r}")
        for path in skill_root.rglob("*"):
            if path.is_symlink():
                errors.append(f"skill {skill_root.name!r} contains a symlink: {path}")
        skill_path = _required_package_path(
            plugin_root,
            f"{relative_skill}/SKILL.md",
            f"skill {skill_root.name!r} entrypoint",
            "file",
            errors,
        )
        if skill_path is None:
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
        if set(frontmatter) != {"name", "description"}:
            errors.append(
                f"skill {skill_root.name!r} frontmatter keys must be exactly name and description"
            )
        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"skill {skill_root.name!r} has no frontmatter name")
        else:
            names.append(name)
            if name != skill_root.name:
                errors.append(
                    f"skill {skill_root.name!r} frontmatter name must match directory"
                )
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
                errors.append(f"skill {skill_root.name!r} has an invalid frontmatter name")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"skill {skill_root.name!r} has no frontmatter description")
        elif not 20 <= len(description) <= 300:
            errors.append(f"skill {skill_root.name!r} description must be 20-300 characters")
        elif any(character in description for character in "<>\r\n"):
            errors.append(f"skill {skill_root.name!r} description contains forbidden characters")
        if len(contents.splitlines()) >= 500:
            errors.append(f"skill {skill_root.name!r} SKILL.md body is overlong")
        normalized_contents = contents.lower()
        if any(retired in normalized_contents for retired in RETIRED_SKILLS):
            errors.append(f"skill {skill_root.name!r} references a retired skill")
        if PUBLIC_SKILL_JARGON.search(contents):
            errors.append(f"skill {skill_root.name!r} contains private policy vocabulary")
        _validate_skill_metadata(skill_root, errors)
        if skill_root.name == "brainstorm":
            _validate_brainstorm_catalog(skill_root, errors)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        errors.append(f"skill name {name!r} is duplicated")


def _validate_brainstorm_catalog(skill_root: Path, errors: list[str]) -> None:
    plugin_root = skill_root.parent.parent
    catalog_path = _required_package_path(
        plugin_root,
        BRAINSTORM_CATALOG_RELATIVE,
        "brainstorm catalog",
        "file",
        errors,
    )
    if catalog_path is None:
        return
    try:
        contents = catalog_path.read_bytes()
    except OSError as error:
        errors.append(f"brainstorm catalog could not be read: {error}")
        return
    preamble = BRAINSTORM_CATALOG_PREAMBLE.encode("utf-8")
    if not contents.startswith(preamble):
        errors.append("brainstorm catalog has an unexpected attribution preamble")
        return
    payload = contents[len(preamble) :]
    if hashlib.sha256(payload).hexdigest() != BRAINSTORM_CATALOG_SHA256:
        errors.append("brainstorm catalog payload does not match the pinned upstream digest")


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
            if line.strip():
                errors.append(f"skill {skill_directory!r} frontmatter contains an invalid line")
            continue
        key = key.strip()
        value = raw_value.strip()
        if not key:
            errors.append(f"skill {skill_directory!r} frontmatter contains an empty key")
            continue
        if key in values:
            errors.append(f"skill {skill_directory!r} frontmatter key {key!r} is duplicated")
            continue
        if value.startswith(("[", "{")):
            errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a scalar")
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is invalid")
                continue
            if not isinstance(parsed, str):
                errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a string")
                continue
            value = parsed
        values[key] = value
    return values


def _validate_skill_metadata(skill_root: Path, errors: list[str]) -> None:
    plugin_root = skill_root.parent.parent
    agents_path = _required_package_path(
        plugin_root,
        f"skills/{skill_root.name}/agents",
        f"skill {skill_root.name!r} agents directory",
        "directory",
        errors,
    )
    if agents_path is None:
        return
    metadata_path = _required_package_path(
        plugin_root,
        f"skills/{skill_root.name}/agents/openai.yaml",
        f"skill {skill_root.name!r} metadata",
        "file",
        errors,
    )
    if metadata_path is None:
        return
    try:
        contents = metadata_path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"skill {skill_root.name!r} metadata could not be read: {error}")
        return
    metadata = _parse_skill_metadata(contents, skill_root.name, errors)
    if metadata is None:
        return
    if set(metadata) != {"interface", "policy"}:
        errors.append(f"skill {skill_root.name!r} metadata keys must be exactly interface and policy")
        return
    interface = metadata.get("interface")
    policy = metadata.get("policy")
    if not isinstance(interface, dict):
        errors.append(f"skill {skill_root.name!r} metadata interface must be a mapping")
        return
    if not isinstance(policy, dict):
        errors.append(f"skill {skill_root.name!r} metadata policy must be a mapping")
        return
    if set(interface) != {"display_name", "short_description", "default_prompt"}:
        errors.append(f"skill {skill_root.name!r} interface keys are not exact")
    if set(policy) != {"allow_implicit_invocation"}:
        errors.append(f"skill {skill_root.name!r} policy keys are not exact")
    for field in ("display_name", "short_description", "default_prompt"):
        value = interface.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"skill {skill_root.name!r} interface.{field} must be a non-empty string")
            continue
        if any(character in value for character in "<>\r\n"):
            errors.append(f"skill {skill_root.name!r} interface.{field} contains forbidden characters")
        if PUBLIC_SKILL_JARGON.search(value):
            errors.append(f"skill {skill_root.name!r} interface.{field} contains private policy vocabulary")
    short_description = interface.get("short_description")
    if isinstance(short_description, str) and not 25 <= len(short_description) <= 64:
        errors.append(f"skill {skill_root.name!r} short_description must be 25-64 characters")
    default_prompt = interface.get("default_prompt")
    if isinstance(default_prompt, str) and f"${skill_root.name}" not in default_prompt:
        errors.append(f"skill {skill_root.name!r} default_prompt must invoke the matching skill")
    implicit = policy.get("allow_implicit_invocation")
    if not isinstance(implicit, bool):
        errors.append(f"skill {skill_root.name!r} allow_implicit_invocation must be a boolean")
    elif implicit is not (skill_root.name == "use-expand"):
        errors.append(f"skill {skill_root.name!r} implicit invocation policy drift")


def _parse_skill_metadata(
    contents: str, skill_name: str, errors: list[str]
) -> dict[str, dict[str, object]] | None:
    values: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(contents.splitlines(), start=1):
        if not line.strip():
            continue
        if "\t" in line:
            errors.append(f"skill {skill_name!r} metadata line {line_number} contains a tab")
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key:
            errors.append(f"skill {skill_name!r} metadata line {line_number} is invalid")
            continue
        raw_value = raw_value.strip()
        if indentation == 0:
            if raw_value:
                errors.append(f"skill {skill_name!r} metadata root {key!r} must be a mapping")
                continue
            if key in values:
                errors.append(f"skill {skill_name!r} metadata key {key!r} is duplicated")
                continue
            values[key] = {}
            continue
        if indentation != 2:
            errors.append(f"skill {skill_name!r} metadata line {line_number} has invalid indentation")
            continue
        if not values:
            errors.append(f"skill {skill_name!r} metadata child appears before a root mapping")
            continue
        parent = next(reversed(values))
        mapping = values[parent]
        if key in mapping:
            errors.append(f"skill {skill_name!r} metadata key {parent}.{key!s} is duplicated")
            continue
        if parent == "interface":
            if len(raw_value) < 2 or raw_value[0] != raw_value[-1] or raw_value[0] not in {'"', "'"}:
                errors.append(f"skill {skill_name!r} interface.{key} must be a quoted string")
                continue
            try:
                value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError):
                errors.append(f"skill {skill_name!r} interface.{key} is invalid")
                continue
            if not isinstance(value, str):
                errors.append(f"skill {skill_name!r} interface.{key} must be a string")
                continue
            mapping[key] = value
        elif parent == "policy":
            if raw_value not in {"true", "false"}:
                errors.append(f"skill {skill_name!r} policy.{key} must be a YAML boolean literal")
                continue
            mapping[key] = raw_value == "true"
        else:
            mapping[key] = raw_value
    return values


def _validate_policy(plugin_root: Path, errors: list[str]) -> None:
    if _required_package_path(plugin_root, "assets", "asset directory", "directory", errors) is None:
        return
    policy_entries = [
        path.relative_to(plugin_root).as_posix()
        for path, _ in _lexical_package_entries(plugin_root)
        if path.name == "execution-policy.json"
    ]
    if policy_entries != [POLICY_PATH]:
        observed = ", ".join(sorted(policy_entries)) or "none"
        errors.append(
            f"execution policy artifacts must contain exactly {POLICY_PATH}; found {observed}"
        )
    policy_path = _required_package_path(
        plugin_root,
        POLICY_PATH,
        "execution policy",
        "file",
        errors,
    )
    if policy_path is None or policy_entries != [POLICY_PATH]:
        return
    policy = _load_json_object(policy_path, "execution policy", errors)
    if policy is None:
        return
    expected = {
        "policy_version": "execution-budget-policy.v1",
        "profiles": EXPECTED_POLICY_PROFILES,
        "routes": EXPECTED_POLICY_ROUTES,
    }
    if set(policy) != set(expected):
        errors.append("execution policy keys must be exactly policy_version, profiles, and routes")
        return
    if policy.get("policy_version") != expected["policy_version"]:
        errors.append("execution policy policy_version is not execution-budget-policy.v1")
    if policy.get("profiles") != EXPECTED_POLICY_PROFILES:
        errors.append("execution policy profiles do not match the exact validated roster")
    if policy.get("routes") != EXPECTED_POLICY_ROUTES:
        errors.append("execution policy routes do not match the exact validated plans")


def _validate_helper_and_package_layout(plugin_root: Path, errors: list[str]) -> None:
    if _required_package_path(plugin_root, "scripts", "plugin scripts directory", "directory", errors) is None:
        return
    helper = _required_package_path(
        plugin_root,
        HELPER_PATH,
        "worktree helper",
        "file",
        errors,
    )
    helpers = sorted(
        (
            path.relative_to(plugin_root).as_posix(),
            path,
        )
        for path in plugin_root.rglob("worktrees.py")
        if path.is_file() or path.is_symlink()
    )
    if [relative for relative, _ in helpers] != [HELPER_PATH]:
        observed = ", ".join(relative for relative, _ in helpers) or "none"
        errors.append(f"worktree helper must exist only at {HELPER_PATH}; found {observed}")
    plan_helper = _required_package_path(
        plugin_root,
        PLAN_GRAPH_HELPER_PATH,
        "plan graph helper",
        "file",
        errors,
    )
    plan_helpers = sorted(
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("plan_graph.py")
        if path.is_file() or path.is_symlink()
    )
    if plan_helpers != [PLAN_GRAPH_HELPER_PATH]:
        observed = ", ".join(plan_helpers) or "none"
        errors.append(
            f"plan graph helper must exist only at {PLAN_GRAPH_HELPER_PATH}; found {observed}"
        )
    for label, path in (("worktree", helper), ("plan graph", plan_helper)):
        if path is not None and (path.is_symlink() or not path.is_file() or path.stat().st_size == 0):
            errors.append(f"{label} helper must be a non-empty regular file")
    design_helper_path = "scripts/design_state.py"
    design_matches = sorted(
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("design_state.py")
        if path.is_file() or path.is_symlink()
    )
    if design_matches != [design_helper_path]:
        errors.append(f"design state helper must exist only at {design_helper_path}; found {', '.join(design_matches) or 'none'}")
    design_helper = plugin_root / design_helper_path
    if design_helper.is_symlink() or not design_helper.is_file() or design_helper.stat().st_size == 0:
        errors.append("design state helper must be a non-empty regular file")


def _validate_agents(plugin_root: Path, errors: list[str]) -> None:
    agents_root = plugin_root / AGENTS_PATH
    if _required_package_path(plugin_root, AGENTS_PATH, "agent directory", "directory", errors) is None:
        return

    expected_filenames = {f"{name}.toml" for name in EXPECTED_AGENTS}
    for path in sorted(agents_root.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            errors.append(f"agent profile {path.name!r} must not be a symlink")
            continue
        if not path.is_file():
            if path.name.startswith("devflow-"):
                errors.append(f"unexpected agent profile {path.name!r}")
            continue
        if path.suffix == ".toml" and path.name not in expected_filenames:
            errors.append(f"unexpected agent profile {path.stem!r}")
        elif path.name.startswith("devflow-") and path.name not in expected_filenames:
            errors.append(f"unexpected agent profile {path.name!r}")
    for expected_name in EXPECTED_AGENTS:
        path = _required_package_path(
            plugin_root,
            f"{AGENTS_PATH}/{expected_name}.toml",
            f"agent profile {expected_name!r}",
            "file",
            errors,
        )
        if path is None:
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
