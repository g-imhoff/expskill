# UI rules index

Read this index before choosing references. Use the loading strategy below, then revisit it after the first isolated render because new behavior or states may reveal another category.

## Loading strategy

- **Narrow component:** when the work has one bounded responsibility and a small concern set, selectively load the category references that materially apply. Load another category as soon as the render exposes its trigger.
- **Complex composite:** when the work combines interacting regions, multiple behavior modes, state families, dense or structured data, breakpoint composition, or several dependent components, load the complete category catalog. Cross-category failures are easy to miss when the UI is judged one concern at a time.
- **Uncertain boundary:** load the broader relevant set. Reading a rule does not activate it. Its applicability still depends on the project, platform, confirmed intent, and stated trigger.

This strategy changes evidence coverage, not authority. Existing project convention wins over a heuristic, and activated normative obligations cannot be silently discarded.

## Precedence

1. Confirmed product requirements and activated normative obligations. Surface conflicts.
2. Existing project components, tokens, themes, content, interaction language, and platform conventions.
3. Conditional catalog rules whose activation criteria hold.
4. Heuristics where project evidence leaves room.

Never import a public system's exact values merely because its guidance is reputable. A project convention overrides heuristics. An activated normative requirement cannot be silently overridden.

## Always retain in the core skill

- inspect project UI evidence before invention.
- reuse project components and semantic tokens.
- bind exact platform and accessibility targets before treating numeric rules as obligations.
- render production-intended components in the project-compatible isolated surface.
- use realistic, risk-relevant states and content.
- require progressive user confirmation.
- record downstream proof when an isolated component cannot establish a page/process claim.

## Reference routing

| Reference | Load when the component involves |
|---|---|
| geometry | spacing, grids, alignment, nesting, radii, safe areas, density, hit-region geometry |
| typography | hierarchy, fonts, long text, localized scripts, truncation, scaling, text spacing |
| interaction | focus, keyboard, pointer, drag, gesture, hover/focus disclosure, status, custom controls |
| forms | editable input, labels, instructions, validation, errors, recovery, autofill, transactions |
| responsive | viewport/layout changes, orientation, reflow, visual/DOM reordering, overflow, zoom |
| accessibility | non-text content, semantics, sensory cues, contrast, language, assistive technology |
| motion | animation, transitions, auto-updates, flashing, parallax, reduced-motion behavior |
| data-display | charts, tables, dashboards, axes, encodings, provenance, data states, localization |
| yodea-preview | every Design run: required hosted preview, PR and handoff links, auth, labels, caps, proof, retention, cleanup |

## Source labels used by the drafts

- **Activated norm:** a normative requirement only when its documented platform, conformance level, trigger, and exceptions apply.
- **Project convention:** evidence from the current repository or an explicitly adopted design system.
- **Platform convention:** official guidance for the named platform. Never silently translated between CSS px, pt, dp, or another unit.
- **Conditional constraint:** a relationship that becomes binding only when its prerequisites hold.
- **Heuristic:** a useful default that project evidence or rendered evidence may reject.

## Rule loading is not a receipt

Do not emit capability JSON, selection traces, query results, or a declaration of every skipped category. Load what is useful, explain only consequential conflicts, and return to the index if the rendered artifact changes the concern set.
