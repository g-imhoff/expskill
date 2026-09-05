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
UNSLOP_HOOK_CONFIG_PATH = "hooks/hooks.json"
UNSLOP_HOOK_SCRIPT_PATH = "hooks/inject_unslop.py"
UNSLOP_HOOK_SCRIPT_SHA256 = "6eea44b9a2fcccfe685c5b93c7fd2b3e868bb9618f6764a7e97557dd4f8f6403"
THIRD_PARTY_LOCK_PATH = "third-party/upstream-lock.json"
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
    "$use-expand",
    "$design",
    "$grill-me",
    "$unslop",
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
    "grill-me",
    "unslop",
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

EXPECTED_THIRD_PARTY_SOURCES = {
    "pstack-unslop": {
        "repository": "https://github.com/cursor/plugins",
        "revision": "93b00b89ef425a9c1bac0d0b317dfc49c930ac99",
        "source_path": "pstack/skills/unslop/SKILL.md",
        "vendored_path": "sources/pstack/unslop/SKILL.md",
        "sha256": "2789ab80477b7e382292e4d7acca1057784df19713fffb74622ff0f83b2f3733",
        "license_path": "licenses/pstack-MIT.txt",
        "license_sha256": "bc957ca6bee02792566a1a028d105e02e247c6e77cf057061674273da77b200e",
    },
    "mattpocock-grill-me": {
        "repository": "https://github.com/mattpocock/skills",
        "revision": "3cca18b368ae95cdbdebbff572ccafa662551015",
        "source_path": "skills/productivity/grill-me/SKILL.md",
        "vendored_path": "sources/mattpocock/grill-me/SKILL.md",
        "sha256": "caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637",
        "license_path": "licenses/mattpocock-skills-MIT.txt",
        "license_sha256": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    },
    "mattpocock-grilling": {
        "repository": "https://github.com/mattpocock/skills",
        "revision": "3cca18b368ae95cdbdebbff572ccafa662551015",
        "source_path": "skills/productivity/grilling/SKILL.md",
        "vendored_path": "sources/mattpocock/grilling/SKILL.md",
        "sha256": "10ff989e7498b23b5acb49d5048f11dcd906757d2f79c5cdf8a00001381296f2",
        "license_path": "licenses/mattpocock-skills-MIT.txt",
        "license_sha256": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    },
}

EXPECTED_UNSLOP_HOOKS = {
    "description": "Apply Unslop to prose written by the root conversation.",
    "hooks": {
        "SessionStart": [
            {
                "matcher": "^(startup|resume|clear|compact)$",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 "${PLUGIN_ROOT}/hooks/inject_unslop.py"',
                        "timeout": 3,
                        "additionalContextLimit": 5000,
                    }
                ],
            }
        ]
    },
}

EXPECTED_AGENTS = {
    "devflow-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "devflow-test-engineer": ("gpt-5.6-luna", "max", "read-only"),
    "devflow-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "devflow-review": ("gpt-5.6-sol", "xhigh", "read-only"),
    "devflow-spec": ("gpt-5.6-sol", "xhigh", "read-only"),
}

REVIEW_HANDOFF_PATHS = (
    "skills/implement/SKILL.md",
    "skills/skill-builder/SKILL.md",
    "skills/skill-builder/references/evaluation-rubric.md",
)
REVIEW_HANDOFF_CLAUSES = (
    "the aggregate authored review handoff includes inline dispatch text, follow-up "
    "messages, and every generated context artifact regardless of carrier or extension.",
    "it is a locator, not a payload, and totals at most 300 physical lines.",
    "count the complete handoff before launch and stop before dispatch when it exceeds "
    "the limit.",
    "a real accepted specification file is referenced separately when it exists.",
    "the exception applies only to a specification file that existed before review "
    "dispatch.",
    "do not copy or embed diffs, source files, test logs, terminal output, transcripts, "
    "or other repository content.",
)

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
        "exactly one accepted node",
        "red-green-refactor",
        "one owned branch",
        "no delegation",
        "no scope expansion",
    ),
    "devflow-review": (
        "read-only",
        "300 physical lines",
        "self-inspect",
        "invalid handoff",
        "do not request a copied diff",
        "severity",
        "evidence",
        "impact",
        "correction",
        "ready",
        "not ready",
    ),
    "devflow-spec": (
        "every accepted behavior",
        "300 physical lines",
        "self-inspect",
        "invalid handoff",
        "do not request a copied diff",
        "criterion-by-criterion evidence",
        "no tracked-source edits",
        "pass or fail",
        "do not implement fixes",
        "do not expand scope",
        "do not delegate",
    ),
}

EXPECTED_POLICY_PROFILES = {
    "devflow-explorer": {
        "agent_type": "devflow-explorer",
        "role": "explorer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "devflow-test-engineer": {
        "agent_type": "devflow-test-engineer",
        "role": "test-engineer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "devflow-implementer": {
        "agent_type": "devflow-implementer",
        "role": "implementer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "devflow-review": {
        "agent_type": "devflow-review",
        "role": "review",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "devflow-spec": {
        "agent_type": "devflow-spec",
        "role": "spec",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
}

EXPECTED_POLICY_ROUTES = {
    "implement": {
        "standard": {
            "allowed_profiles": [
                "devflow-implementer",
                "devflow-review",
                "devflow-spec",
            ],
            "selected": [
                {
                    "role": "implementer",
                    "profile": "devflow-implementer",
                    "agent_type": "devflow-implementer",
                },
                {
                    "role": "review",
                    "profile": "devflow-review",
                    "agent_type": "devflow-review",
                },
                {
                    "role": "spec",
                    "profile": "devflow-spec",
                    "agent_type": "devflow-spec",
                },
            ],
            "max_agent_calls": 30,
            "max_concurrency": 6,
            "max_depth": 1,
            "max_retries": 3,
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
        _validate_review_handoff_contract(plugin_root, errors)
        _validate_policy(plugin_root, errors)
        _validate_unslop_hook(plugin_root, errors)
        _validate_third_party_sources(plugin_root, errors)
        _validate_helper_and_package_layout(plugin_root, errors)
    _validate_skill_punctuation(repository_root, errors)
    return tuple(errors)


def _validate_review_handoff_contract(plugin_root: Path, errors: list[str]) -> None:
    for relative in REVIEW_HANDOFF_PATHS:
        path = plugin_root / relative
        try:
            contents = path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"review handoff contract could not be read at {relative}: {error}")
            continue
        normalized = " ".join(contents.lower().split())
        for clause in REVIEW_HANDOFF_CLAUSES:
            if clause not in normalized:
                errors.append(
                    f"review handoff contract at {relative} must include {clause!r}"
                )


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
        for phrase in ("six", "independent", "skills", "optional", "lifecycle router"):
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
            or "skills" not in short_description.lower()
            or "routing" not in short_description.lower()
            or PUBLIC_METADATA_JARGON.search(short_description) is not None
        ):
            errors.append(
                "plugin interface shortDescription must concisely advertise skills and optional routing "
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
            for phrase in ("directly", "next lifecycle step", "implementation review", "specification gates"):
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
            or "next lifecycle step" not in default_prompt.lower()
        ):
            errors.append(
                "plugin interface defaultPrompt must explicitly invoke $use-expand for the next lifecycle step"
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


def _validate_unslop_hook(plugin_root: Path, errors: list[str]) -> None:
    hooks_root = _required_package_path(
        plugin_root,
        "hooks",
        "Unslop hook directory",
        "directory",
        errors,
    )
    if hooks_root is None:
        return

    expected_files = {"hooks.json", "inject_unslop.py"}
    actual_files = {
        path.relative_to(hooks_root).as_posix()
        for path in hooks_root.rglob("*")
        if path.is_file() or path.is_symlink()
        if "__pycache__" not in path.relative_to(hooks_root).parts
    }
    if actual_files != expected_files:
        errors.append(
            "Unslop hook files must be exactly hooks.json and inject_unslop.py"
        )

    config_path = _required_package_path(
        plugin_root,
        UNSLOP_HOOK_CONFIG_PATH,
        "Unslop hook configuration",
        "file",
        errors,
    )
    script_path = _required_package_path(
        plugin_root,
        UNSLOP_HOOK_SCRIPT_PATH,
        "Unslop hook script",
        "file",
        errors,
    )
    if config_path is not None:
        config = _load_json_object(config_path, "Unslop hook configuration", errors)
        if config is not None and config != EXPECTED_UNSLOP_HOOKS:
            errors.append("Unslop hook configuration does not match the root SessionStart contract")
    if script_path is None:
        return
    try:
        script_bytes = script_path.read_bytes()
        script = script_bytes.decode("utf-8")
    except OSError as error:
        errors.append(f"Unslop hook script could not be read: {error}")
        return
    except UnicodeDecodeError:
        errors.append("Unslop hook script must be UTF-8 text")
        return
    if not script.strip():
        errors.append("Unslop hook script must be non-empty")
        return
    if hashlib.sha256(script_bytes).hexdigest() != UNSLOP_HOOK_SCRIPT_SHA256:
        errors.append("Unslop hook script digest does not match the reviewed implementation")
    try:
        ast.parse(script, filename=str(script_path))
    except SyntaxError as error:
        errors.append(f"Unslop hook script is not valid Python: {error.msg}")
    normalized_script = script.lower()
    for marker in (
        "sessionstart",
        '"unslop"',
        "additionalcontext",
        "user-facing prose",
        "machine-readable data",
        "higher-priority instructions",
    ):
        if marker not in normalized_script:
            errors.append(f"Unslop hook script is missing required marker {marker!r}")


def _validate_third_party_sources(plugin_root: Path, errors: list[str]) -> None:
    third_party_root = _required_package_path(
        plugin_root,
        "third-party",
        "third-party source directory",
        "directory",
        errors,
    )
    if third_party_root is None:
        return
    lock_path = _required_package_path(
        plugin_root,
        THIRD_PARTY_LOCK_PATH,
        "third-party upstream lock",
        "file",
        errors,
    )
    if lock_path is None:
        return
    lock = _load_json_object(lock_path, "third-party upstream lock", errors)
    if lock is None:
        return
    expected_lock = {
        "schema_version": "third-party-sources.v1",
        "sources": EXPECTED_THIRD_PARTY_SOURCES,
    }
    if lock != expected_lock:
        errors.append("third-party upstream lock does not match the pinned source contract")

    expected_files = {"upstream-lock.json"}
    for source in EXPECTED_THIRD_PARTY_SOURCES.values():
        expected_files.add(source["vendored_path"])
        expected_files.add(source["license_path"])
    actual_files = {
        path.relative_to(third_party_root).as_posix()
        for path in third_party_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != expected_files:
        errors.append("third-party source package does not contain the exact pinned file set")

    for name, source in EXPECTED_THIRD_PARTY_SOURCES.items():
        for path_field, digest_field, label in (
            ("vendored_path", "sha256", "upstream source"),
            ("license_path", "license_sha256", "upstream license"),
        ):
            relative = source[path_field]
            path = _required_package_path(
                plugin_root,
                f"third-party/{relative}",
                f"{name} {label}",
                "file",
                errors,
            )
            if path is None:
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                errors.append(f"{name} {label} could not be read: {error}")
                continue
            if digest != source[digest_field]:
                errors.append(f"{name} {label} digest does not match the pinned upstream digest")

    _validate_public_third_party_derivations(plugin_root, third_party_root, errors)


def _validate_public_third_party_derivations(
    plugin_root: Path,
    third_party_root: Path,
    errors: list[str],
) -> None:
    try:
        unslop_source = (
            third_party_root / "sources" / "pstack" / "unslop" / "SKILL.md"
        ).read_bytes()
        public_unslop = (plugin_root / "skills" / "unslop" / "SKILL.md").read_bytes()
    except OSError as error:
        errors.append(f"public Unslop derived-copy validation failed: {error}")
    else:
        expected_unslop = unslop_source.replace(
            b"disable-model-invocation: true\n",
            b"",
            1,
        )
        if public_unslop != expected_unslop:
            errors.append("public skill 'unslop' does not match its declared derived upstream copy")

    try:
        wrapper = (
            third_party_root / "sources" / "mattpocock" / "grill-me" / "SKILL.md"
        ).read_bytes()
        engine = (
            third_party_root / "sources" / "mattpocock" / "grilling" / "SKILL.md"
        ).read_bytes()
        public_grill = (plugin_root / "skills" / "grill-me" / "SKILL.md").read_bytes()
        wrapper_end = wrapper.index(b"\n---\n", 4) + len(b"\n---\n")
        engine_end = engine.index(b"\n---\n", 4) + len(b"\n---\n")
    except (OSError, ValueError) as error:
        errors.append(f"public Grill Me derived-copy validation failed: {error}")
    else:
        frontmatter = wrapper[:wrapper_end].replace(
            b"disable-model-invocation: true\n",
            b"",
            1,
        )
        body = engine[engine_end:].lstrip(b"\n")
        body = body.replace(b"it; don't", b"it. Don't").replace(
            b"report; ask",
            b"report. Ask",
        )
        expected_grill = frontmatter + b"\n" + body
        if public_grill != expected_grill:
            errors.append("public skill 'grill-me' does not match its declared derived upstream copy")


def _validate_skill_punctuation(repository_root: Path, errors: list[str]) -> None:
    roots = (
        repository_root / "plugins" / PLUGIN_NAME / "skills",
        repository_root / ".agents" / "skills",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                contents = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(repository_root)
            if "\N{EM DASH}" in contents:
                errors.append(f"skill text {relative} contains an em dash")
            if ";" in contents:
                errors.append(f"skill text {relative} contains a semicolon")


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
