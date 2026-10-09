"""Inject an instruction to load Unslop into SessionStart and PreCompact events.

SessionStart receives the documented JSON ``additionalContext`` envelope.
PreCompact receives the same payload as plain stdout text because the CLI
rejects a ``hookSpecificOutput`` envelope for that event. Compaction-adjacent
events (a ``compact`` source or any PreCompact trigger) carry the compaction
reminder; every other session start carries the session scope.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SESSION_START_SOURCES = {"startup", "resume", "clear", "compact", "fork"}
PRE_COMPACT_TRIGGERS = {"manual", "auto"}


def _event_name(event: object) -> object:
    if not isinstance(event, dict):
        return None
    name = event.get("hook_event_name")
    if name is None:
        name = event.get("hookEventName")
    return name


def _runtime_scope(path: Path, compaction: bool) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "unslop-runtime.v1":
        raise ValueError("Unslop runtime policy has an unsupported schema")
    if set(payload) != {"schema_version", "scope", "compaction_reminder"}:
        raise ValueError("Unslop runtime policy has unexpected fields")
    for field in ("scope", "compaction_reminder"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 5000:
            raise ValueError(f"Unslop runtime policy {field} must contain 1-5000 characters")
    return payload["compaction_reminder" if compaction else "scope"]


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(event, dict):
        return 0
    name = _event_name(event)
    if name == "SessionStart":
        if event.get("source") not in SESSION_START_SOURCES:
            return 0
        compaction = event.get("source") == "compact"
    elif name == "PreCompact":
        if event.get("trigger") not in PRE_COMPACT_TRIGGERS:
            return 0
        compaction = True
    else:
        return 0

    source_path = Path(__file__).resolve()
    source_package = source_path.parents[2]
    packaged_package = source_path.parents[1]
    default_root = (
        source_package
        if (source_package / ".claude-plugin").is_dir()
        else packaged_package
    )
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", default_root)).resolve()
    skill_path = plugin_root / "content" / "skills" / "unslop" / "SKILL.md"
    policy_path = plugin_root / "content" / "policies" / "unslop-runtime.json"
    if not skill_path.is_file():
        skill_path = plugin_root / "skills" / "unslop" / "SKILL.md"
    if not policy_path.is_file():
        policy_path = plugin_root / "assets" / "unslop-runtime.json"
    try:
        if not skill_path.is_file():
            raise ValueError("Unslop skill is unavailable")
        scope = _runtime_scope(policy_path, compaction)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"Unslop hook could not load its packaged skill: {error}", file=sys.stderr)
        return 1

    if name == "PreCompact":
        sys.stdout.write(scope.strip() + "\n")
        return 0
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
