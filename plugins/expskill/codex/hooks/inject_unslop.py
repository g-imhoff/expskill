#!/usr/bin/env python3
"""Inject the packaged Unslop body into a root SessionStart event."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unslop_body import runtime_scope, skill_body


ALLOWED_SOURCES = {"startup", "resume", "clear", "compact"}


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
        body = skill_body(skill_path.read_text(encoding="utf-8"))
        scope = runtime_scope(policy_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"Unslop hook could not load its packaged skill: {error}", file=sys.stderr)
        return 1

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": scope + body,
        }
    }
    json.dump(output, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
