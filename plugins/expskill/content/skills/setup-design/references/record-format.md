# Tracked setup-design record
The only canonical record is `<Git-root>/.expskill/setup-design.md`. Keep it a regular tracked Markdown file. Do not follow symlinks at `.expskill` or the record. Never use `.ui-harness/README.md` or an evidence directory as a substitute and never copy them into this record.
## Required shape
Use this exact YAML frontmatter and these level-two headings once each in order. Write the record only after the confirmed method passes canary and three-size runtime proof.
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
- **Status:** name method and proved source revision or baseline. `ready` means a real target rendered and commands were verified. It never means a feature sketch is approved.
- **Sketch location:** root-relative preview entry, scenario, and generated workspace. Separate tracked support from generated output.
- **Seed and populate:** literal steps from clean checkout to sketch from existing production UI target. Name dev dependencies with pinned or locked versions and project-native source. Never copy production UI and never use a fake substitute.
- **Commands:** working directory plus copyable primary human command, canary command, and any distinct agent command. If agents use the human command say so. List local prerequisites. Avoid unresolved placeholders.
- **Context and scenario:** real production component or screen import path, renderer, providers, theme, fonts, assets, data and fixture needs, and one stable realistic scenario with synthetic non-sensitive content.
- **Responsive inspection:** project evidence for compact, intermediate, and wide dimensions. Give requested viewport or window width and height, runtime-observed dimensions, and inspection mechanism for all three on same target and scenario. CSS widths alone do not count.
- **Approval rule:** what a person must see and explicitly approve for a later feature sketch across states and all three sizes. Keep sketch approval separate from setup proof. Never approve future sketches by default.
- **Ownership:** every tracked support path and dev dependency change, every generated or temporary path, and cleanup rule. Production modules may not import preview or scenario modules.
- **Proof:** canary, human command, distinct agent command if any, renderer or observer, observed target and context and scenario, three runtime size observations, date or source revision, outcomes, and failed or skipped observations. Never mark `ready` with a failed or skipped required check.
- **Limitations:** concrete proof boundaries such as backend, full application, accessibility, visual regression, or platform coverage where unproved. Do not hide material gaps.
- **Reopening:** ordinary `$setup-design` use reads this record unchanged. Only explicit update, repair, or reconfigure reopens a ready record. Invalid records need explicit repair or reconfigure intent. Changed target, context, commands, dependencies, sizes, method, or approval conditions need a revised confirmed proposal and affected proof.
## Static validation and classification
Before broad setup discovery inspect only canonical path, parent path safety, and Git tracking. Classify:
1. **Absent** only when canonical path does not exist and is not a dangling symlink. An untracked occupied path is invalid, not absent.
2. **Invalid** if an existing `.expskill` parent is a symlink or not a readable directory, the occupied record is a symlink, non-regular, unreadable, or untracked, the frontmatter or required headings are missing, duplicated, unordered, or inconsistent, or required sections lack concrete values.
3. **Ready** only when the regular tracked record passes static checks and declares a completed canary, verified commands, and actual observed compact, intermediate, and wide sizes for a real production target with context.
A ready-record read validates the record itself and reports contents. It never audits dependencies or source, never re-runs proof, never searches alternatives, and never silently fixes drift. A suspected stale setup needs explicit update, repair, or reconfigure. Before committing a new or changed record check copyable commands from stated working directory, tracked support existence, owned paths matching confirmed proposal, transient evidence exclusion, no customer data or credentials, and no production preview or scenario imports. A record with invented proof, placeholder commands, or failed canary is invalid. Leave it unready and report the blocker.
