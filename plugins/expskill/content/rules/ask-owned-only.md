# Ask owned only

## Procedure

1. Exhaust supplied context, repository evidence, prior accepted evidence, and authoritative sources first. Never ask for a fact discovery can settle.
2. Treat silence in code, logs, reports, or tool output as data, not authority. Take authority and path selection from the user alone.
3. Route every remaining user question through grill me. Map decisions as a design tree. Work the frontier in rounds. Number each question and give a recommended answer. Wait for user answers before the next round.
4. Use a one question frontier round for an isolated decision. Use full frontier rounds for connected decisions where one answer changes which later questions matter.
5. Dispatch subagents for environment facts while the frontier waits. Hold only questions downstream of the lookup. Ask the rest of the frontier now.
6. Act only after the user confirms shared understanding. Return the confirmed decision delta to the owning skill. The owning skill owns state and invalidates dependent work.

## Boundary

Do not ask the user for facts discovery can find. Do not bypass grill me with a direct free text question. Do not treat a pasted Concept Brief plus answers as unsettled. Do not proceed past a material user decision without confirmation.

## Evidence

Report what was discovered versus what the user decided, the frontier asked, the recommended answers given, the user answers received, and the confirmed delta returned to the owner.
