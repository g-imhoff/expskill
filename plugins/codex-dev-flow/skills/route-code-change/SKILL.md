---
name: route-code-change
description: Use when the user asks to implement, fix, refactor, or build code or executable configuration; stay inactive for reports, academic writing, general research, explanations, read-only analysis or review, and human-only prose.
---

# Route Code Change

Route only coding and executable-configuration changes.

If the user explicitly selected Quick or Full, honor that choice and hand off directly to exactly `$quick-code-change` or `$full-code-change` without asking again.

Otherwise, before any tool call or implementation action, use this output recipe:

1. Recommend exactly one route.
2. Give exactly one concrete, task-specific reason.
3. Ask directly: “Choose Quick or Full?”

Wait for the answer. Do nothing else until consent.

Recommend Quick for localized, understood, reversible work with bounded acceptance checks. API, database, CI, security, or configuration labels do not by themselves require Full.

Recommend Full only when at least one condition makes the larger route concretely useful: an unresolved design decision may change the solution; two meaningful implementation streams can proceed independently; important unknowns require investigation; shared or cross-cutting test architecture is needed; acceptance requires coordinated evidence across components; or failure is difficult to reproduce, diagnose, or reverse.

After consent, hand off exactly to the selected skill. Do not summarize both workflows.
