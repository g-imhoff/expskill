#!/usr/bin/env python3
"""Inject an instruction to load Unslop into a root SessionStart event."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ALLOWED_SOURCES = {"startup", "resume", "clear", "compact"}


def _runtime_scope(path: Path, source: str) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "unslop-runtime.v1":
        raise ValueError("Unslop runtime policy has an unsupported schema")
    if set(payload) != {"schema_version", "scope", "compaction_reminder"}:
        raise ValueError("Unslop runtime policy has unexpected fields")
    for field in ("scope", "compaction_reminder"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Unslop runtime policy has no {field}")
    return payload["compaction_reminder" if source == "compact" else "scope"]


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if event.get("hook_event_name") != "SessionStart":
        return 0
    if event.get("source") not in ALLOWED_SOURCES:
        return 0

    source_path = Path(__file__).resolve()
    source_package = source_path.parents[2]
    packaged_package = source_path.parents[1]
    default_root = (
        source_package
        if (source_package / ".codex-plugin").is_dir()
        else packaged_package
    )
    plugin_root = Path(os.environ.get("PLUGIN_ROOT", default_root)).resolve()
    skill_path = plugin_root / "content" / "skills" / "unslop" / "SKILL.md"
    policy_path = plugin_root / "content" / "policies" / "unslop-runtime.json"
    if not skill_path.is_file():
        skill_path = plugin_root / "skills" / "unslop" / "SKILL.md"
    if not policy_path.is_file():
        policy_path = plugin_root / "assets" / "unslop-runtime.json"
    try:
        if not skill_path.is_file():
            raise ValueError("Unslop skill is unavailable")
        scope = _runtime_scope(policy_path, event["source"])
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"Unslop hook could not load its packaged skill: {error}", file=sys.stderr)
        return 1

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": scope,
        }
    }
    json.dump(output, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
