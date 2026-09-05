#!/usr/bin/env python3
"""Inject the packaged Unslop body into a root SessionStart event."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ALLOWED_SOURCES = {"startup", "resume", "clear", "compact"}
SCOPE = """Apply the following Unslop rules to natural-language user-facing prose you author, including commentary and final messages. Preserve code, commands, machine-readable data, logs, identifiers, API names, quotations, citations, source excerpts, approved copy, and project-required terminology exactly. Higher-priority instructions and explicit user formatting or tone choices win. Before sending user-facing prose, perform the included self-audit.

"""


def _skill_body(contents: str) -> str:
    lines = contents.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("Unslop skill is missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("Unslop skill frontmatter is not closed") from error
    return "\n".join(lines[end + 1 :]).strip() + "\n"


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if event.get("hook_event_name") != "SessionStart":
        return 0
    if event.get("source") not in ALLOWED_SOURCES:
        return 0

    default_root = Path(__file__).resolve().parents[1]
    plugin_root = Path(os.environ.get("PLUGIN_ROOT", default_root)).resolve()
    skill_path = plugin_root / "skills" / "unslop" / "SKILL.md"
    try:
        body = _skill_body(skill_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Unslop hook could not load its packaged skill: {error}", file=sys.stderr)
        return 1

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": SCOPE + body,
        }
    }
    json.dump(output, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
