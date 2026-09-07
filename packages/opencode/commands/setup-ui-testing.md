---
description: Explicit-only UI setup, never production redesign
---

Load the `setup-ui-testing` skill through the skill tool and follow that skill exactly. Apply it to the following request.

$ARGUMENTS

When $ARGUMENTS is empty, ask the user which project setup to establish, retrieve, repair, or update before doing anything else. This skill never redesigns production UI. Resolve helper scripts relative to the real path of the loaded SKILL.md file after resolving any symlinks, then follow the skill relative script paths from there. Never push, merge, approve, or deliver remotely.