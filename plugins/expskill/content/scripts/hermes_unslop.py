#!/usr/bin/env python3
"""Hermes native plugin hook that auto-injects the Unslop rule text.

Install: copy this file into a Hermes plugin directory (for example
``~/.hermes/plugins/expskill-unslop/hermes_unslop.py``) so the plugin
loader calls ``register(ctx)``.  Point it at the Expskill sources with
``EXPSKILL_HOME`` (a checkout root or an installed ``hermes-dist``
artifact root); when this file ships inside such a tree it resolves the
canonical files relative to itself.

The injected text is byte-for-byte the shared rule text: the ``scope``
prefix from ``content/policies/unslop-runtime.json`` plus the
frontmatter-stripped body of ``content/skills/unslop/SKILL.md``.  It is
loaded from disk at runtime, never copied into this file, so skill edits
apply without touching the hook.

Event Hooks reference:
https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
(``pre_llm_call`` per-turn context injection, ``on_session_start``
observer; reviewed 2026-10-01).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


RUNTIME_SCHEMA_VERSION = "unslop-runtime.v1"

_cached_text: str | None = None


def _skill_body(contents: str) -> str:
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("Unslop skill is missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("Unslop skill frontmatter is not closed") from error
    return "\n".join(lines[end + 1 :]).strip() + "\n"


def _runtime_scope(policy_text: str) -> str:
    payload = json.loads(policy_text)
    if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_SCHEMA_VERSION:
        raise ValueError("Unslop runtime policy has an unsupported schema")
    if set(payload) != {"schema_version", "scope", "compaction_reminder"}:
        raise ValueError("Unslop runtime policy has unexpected fields")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("Unslop runtime policy has no scope")
    return scope


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    home = os.environ.get("EXPSKILL_HOME")
    if home:
        roots.append(Path(home))
    try:
        here = Path(__file__).resolve()
    except NameError:
        return roots
    # Source tree: <root>/content/scripts/hermes_unslop.py.
    source_root = here.parents[2]
    if (source_root / "content" / "skills" / "unslop" / "SKILL.md").is_file():
        roots.append(source_root)
    # Built artifact: <root>/scripts/hermes_unslop.py.
    packaged_root = here.parents[1]
    if packaged_root != source_root:
        roots.append(packaged_root)
    return roots


def _resolve_paths() -> tuple[Path, Path]:
    for root in _candidate_roots():
        skill_path = root / "content" / "skills" / "unslop" / "SKILL.md"
        policy_path = root / "content" / "policies" / "unslop-runtime.json"
        if not skill_path.is_file():
            skill_path = root / "skills" / "unslop" / "SKILL.md"
        if not policy_path.is_file():
            policy_path = root / "assets" / "unslop-runtime.json"
        if skill_path.is_file() and policy_path.is_file():
            return skill_path, policy_path
    raise FileNotFoundError("Unslop canonical content is unavailable")


def load_unslop_text() -> str:
    """Load (and cache) the exact shared Unslop rule text."""
    global _cached_text
    if _cached_text is None:
        skill_path, policy_path = _resolve_paths()
        body = _skill_body(skill_path.read_text(encoding="utf-8"))
        scope = _runtime_scope(policy_path.read_text(encoding="utf-8"))
        _cached_text = scope + body
    return _cached_text


def reset_cache() -> None:
    """Forget the cached rule text (tests only)."""
    global _cached_text
    _cached_text = None


def pre_llm_call(session_id: str = "", **kwargs: object) -> dict[str, str] | None:
    """Inject the Unslop rules as per-turn context.  Never raises."""
    try:
        return {"context": load_unslop_text()}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"expskill-unslop: canonical content unavailable: {error}", file=sys.stderr)
        return None


def on_session_start(session_id: str = "", **kwargs: object) -> None:
    """Observer-only start hook: warm the rule-text cache.  Never raises."""
    try:
        load_unslop_text()
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"expskill-unslop: canonical content unavailable: {error}", file=sys.stderr)
    return None


def register(ctx: object) -> None:
    """Hermes plugin entry point: register the Unslop hooks."""
    register_hook = getattr(ctx, "register_hook")
    register_hook("pre_llm_call", pre_llm_call)
    register_hook("on_session_start", on_session_start)
