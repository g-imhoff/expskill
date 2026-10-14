#!/usr/bin/env python3
"""Shared Unslop rule-text loader for the Expskill host hooks.

The canonical rule text lives in ``content/skills/unslop/SKILL.md``
(frontmatter-stripped body) prefixed with the ``scope`` value from
``content/policies/unslop-runtime.json``.  Every host hook (Codex
SessionStart, Hermes ``pre_llm_call``, Opencode system injection) must
reuse this exact text, never a paraphrase.

Pure stdlib; the only I/O is reading the two canonical files.
"""

from __future__ import annotations

import json
from pathlib import Path


RUNTIME_SCHEMA_VERSION = "unslop-runtime.v1"


def skill_body(contents: str) -> str:
    """Return the SKILL.md body with its YAML frontmatter removed."""
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("Unslop skill is missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("Unslop skill frontmatter is not closed") from error
    return "\n".join(lines[end + 1 :]).strip() + "\n"


def runtime_scope_text(policy_text: str) -> str:
    """Return the scope prefix from unslop-runtime.json text."""
    payload = json.loads(policy_text)
    if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_SCHEMA_VERSION:
        raise ValueError("Unslop runtime policy has an unsupported schema")
    if set(payload) != {"schema_version", "scope", "compaction_reminder"}:
        raise ValueError("Unslop runtime policy has unexpected fields")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("Unslop runtime policy has no scope")
    return scope


def runtime_scope(policy_path: Path) -> str:
    """Return the scope prefix from an unslop-runtime.json file."""
    return runtime_scope_text(policy_path.read_text(encoding="utf-8"))


def resolve_content_paths(plugin_root: Path) -> tuple[Path, Path]:
    """Resolve the canonical skill and runtime-policy files under a plugin root.

    The packaged layouts differ per host (``content/...`` in the source
    tree, ``skills/...`` and ``assets/...`` in some built artifacts), so
    each location falls back to its packaged counterpart.
    """
    skill_path = plugin_root / "content" / "skills" / "unslop" / "SKILL.md"
    policy_path = plugin_root / "content" / "policies" / "unslop-runtime.json"
    if not skill_path.is_file():
        skill_path = plugin_root / "skills" / "unslop" / "SKILL.md"
    if not policy_path.is_file():
        policy_path = plugin_root / "assets" / "unslop-runtime.json"
    return skill_path, policy_path


def load_unslop_text(plugin_root: Path) -> str:
    """Return the exact shared rule text: scope prefix + skill body."""
    skill_path, policy_path = resolve_content_paths(plugin_root)
    body = skill_body(skill_path.read_text(encoding="utf-8"))
    scope = runtime_scope(policy_path)
    return scope + body
