---
description: Exercise implemented behavior through real product paths
---

Load the `test` skill through the skill tool and follow that skill exactly. Apply it to the following request.

$ARGUMENTS

When $ARGUMENTS is empty, ask the user which already implemented behavior to exercise before doing anything else. This skill never edits production code and never repairs the product. Resolve helper scripts relative to the real path of the loaded SKILL.md file after resolving any symlinks, then follow the skill relative script paths from there. Never push, merge, approve, or deliver remotely.