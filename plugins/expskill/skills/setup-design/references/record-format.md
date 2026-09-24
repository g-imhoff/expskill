# Tracked setup-design record

The only canonical record is `<Git-root>/.expskill/setup-design.md`. Keep it a regular tracked Markdown file. Do not follow symlinks at `.expskill` or the record. Never use `.ui-harness/README.md` or an evidence directory as a substitute, and never copy them into this record.

## Required shape

Use this exact YAML frontmatter and these level-two headings once each, in order. The record is written only after the confirmed method has passed its canary and three-size runtime proof.

```markdown
---
schema_version: expskill.setup-design.v1
status: ready
---

# Setup Design

## Status

## Sketch location

## Seed and populate

## Commands

## Context and scenario

## Responsive inspection

## Approval rule

## Ownership

## Proof

## Limitations

## Reopening
```

Fill every section with project-specific facts:

- **Status:** Name the established method and the source revision or baseline proved. `ready` means a real target rendered and the commands were verified. It is not a claim that a feature sketch is approved.
- **Sketch location:** Give root-relative locations for the preview entry, scenario, and any generated sketch workspace. Distinguish tracked support from generated output.
- **Seed and populate:** Give literal steps from a clean checkout to prepare and populate a sketch from the existing production UI target. Name development dependencies with pinned or locked versions and the project-native source for them. Do not instruct copying production UI or using a fake substitute.
- **Commands:** State the working directory and a copyable primary human command, canary command, and any distinct agent command. If agents use the human command, say so explicitly. Include required local prerequisites. Avoid unresolved placeholders.
- **Context and scenario:** Name the real production component or screen import path, renderer, providers, theme, fonts, assets, data/fixture needs, and one stable realistic scenario with synthetic, non-sensitive content.
- **Responsive inspection:** Name the project evidence behind compact, intermediate, and wide dimensions. Give exact requested viewport or window width and height, the runtime-observed dimensions, and the inspection mechanism for all three using the same target and scenario. CSS widths alone do not count.
- **Approval rule:** State what a person must see and explicitly approve for a later feature sketch, including relevant states and all three sizes. Separate sketch approval from this setup's technical proof. Do not declare future sketches approved by default.
- **Ownership:** List every tracked setup-support path and development dependency change, generated or temporary path, and cleanup rule. State that production modules may not import preview or scenario modules.
- **Proof:** Record the canary, human command, distinct agent command if any, renderer or observer, observed real target/context/scenario, three runtime size observations, date or source revision, outcomes, and any failed or skipped observation. Do not mark `ready` if a required check failed or was skipped.
- **Limitations:** State concrete proof boundaries, such as backend, full application, accessibility, visual regression, or platform coverage where they remain unproved. Do not hide material gaps.
- **Reopening:** State that ordinary `$setup-design` use reads this record unchanged. Only an explicit update, repair, or reconfigure request reopens a ready record. An invalid record requires explicit repair or reconfigure intent. Changed target, context, commands, dependencies, sizes, method, or approval conditions require a revised confirmed proposal and affected proof.

## Static validation and classification

Before broad setup discovery, inspect only the canonical path, its parent path safety, and Git tracking. Classify:

1. **Absent** only when the canonical path does not exist and is not a dangling symlink. An untracked occupied path is invalid, not absent.
2. **Invalid** if an existing `.expskill` parent is a symlink or not a readable directory, the occupied record is a symlink, non-regular, unreadable, or untracked, the frontmatter or required headings are missing, duplicated, unordered, or inconsistent, or required sections lack concrete values.
3. **Ready** only when the regular tracked record passes the static checks and declares a completed canary, verified commands, and actual observed compact, intermediate, and wide sizes for a real production target with context.

A ready-record read validates the record itself and reports its contents. It does not audit current dependencies or source, re-run proof, search for alternatives, or silently fix drift. A suspected stale setup needs an explicit update, repair, or reconfigure request.

Before committing a new or changed record, additionally check that commands are copyable from the stated working directory, referenced tracked support exists, all owned paths match the confirmed proposal, transient evidence is excluded, no customer data or credentials are present, and production code has no preview/scenario imports. A record with invented proof, placeholder commands, or a failed canary is invalid. Leave it unready and report the blocker instead of committing it.
