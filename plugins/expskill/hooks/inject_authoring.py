from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(event, dict) or event.get("hook_event_name") != "SessionStart":
        return 0
    if event.get("source") not in {"startup", "resume", "clear", "compact"}:
        return 0
    script = Path(__file__).resolve()
    source_root = script.parents[2]
    default_root = source_root if (source_root / ".codex-plugin").is_dir() else script.parents[1]
    root = Path(os.environ.get("PLUGIN_ROOT", default_root)).resolve()
    policy_path = root / "content/policies/authoring-runtime.json"
    if not policy_path.is_file():
        policy_path = root / "assets/authoring-runtime.json"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        if not isinstance(policy, dict) or set(policy) != {"schema_version", "instructions"}:
            raise ValueError("Authoring runtime policy has unexpected fields")
        if policy["schema_version"] != "authoring-runtime.v1":
            raise ValueError("Authoring runtime policy has an unsupported schema")
        instructions = policy["instructions"]
        if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 1000:
            raise ValueError("Authoring runtime instructions must contain 1-1000 characters")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"Authoring hook could not load its policy: {error}", file=sys.stderr)
        return 1
    json.dump({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": instructions}}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
