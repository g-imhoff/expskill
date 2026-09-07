---
description: Classify a request for its next phase
---

Load the `use-expskill` skill through the skill tool and follow that skill exactly. Apply it to the following request.

$ARGUMENTS

When $ARGUMENTS is empty, ask the user for the code or executable configuration request to classify before doing anything else. This is the only skill that may activate without an explicit invocation. Open only the selected skill for the current transition and explain the choice in plain language. Resolve helper scripts relative to the real path of the loaded SKILL.md file after resolving any symlinks, then follow the skill relative script paths from there. Never push, merge, approve, or deliver remotely.