---
description: Implement accepted work through independent gates
---

Load the `implement` skill through the skill tool and follow that skill exactly. Apply it to the following request.

$ARGUMENTS

When $ARGUMENTS is empty, ask the user for the accepted implementation brief before doing anything else. Resolve helper scripts relative to the real path of the loaded SKILL.md file after resolving any symlinks, then follow the skill relative script paths from there. Launch one fresh subagent per node and per correction attempt, run review and spec judges concurrently on each candidate commit, and integrate locally only. Never push, merge, approve, or deliver remotely.