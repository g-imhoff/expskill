# Tracked setup-test record
The only canonical record is root-relative `.expskill/setup-test.md` in target Git repo. Keep it a regular tracked Markdown file. Do not follow symlinks at `.expskill` or record path. Never substitute repo text, tool output, CI logs, or evidence directory, and never copy transient proof files into it.
## Required shape
Use exact YAML frontmatter and level-two headings below once each, in order. Record is written only after confirmed matrix passes canary and full-suite sample proof. Fill every section with project facts.
```markdown
---
schema_version: expskill.setup-test.v1
status: ready
---

# Setup Test

## Status

## Test inventory

## Commands

## Canary vs full

## Human vs agent

## Routine path

## Gaps fixed

## Proof

## Limitations

## Ownership

## Reopening
```
- **Status:** Name method and source revision proved. `ready` means runnable type-to-command matrix ran with observed exit codes. It never claims product code passes or routine testing is complete.
- **Test inventory:** Name suites, scripts, CI paths, pinned runner and dependency versions, and required environments. Mark which suites run locally and which run only in CI.
- **Commands:** State working directory and copyable test-type-to-command mapping with scope per command. Include local prerequisites and pinned versions. Every command must run as written with no unresolved values.
- **Canary vs full:** State exact canary command and full-suite sample rule with when to use each, expected scope, and how exit codes and counts are read. Failed suite is never readiness.
- **Human vs agent:** State primary human command and any distinct agent command, or state agents use human command. Name who runs which scope, forbid snapshot updates in agent routine, and keep CI distinct from local meaning.
- **Routine path:** State post-setup path for ordinary testing with normal workflow or test skill when it exists. This record is never a prerequisite for routine testing.
- **Gaps fixed:** List each applied gap fix with owned paths. Oracle edits, always-pass markers or hooks, and hand-transcribed results were not used.
- **Proof:** Record canary and full-suite sample commands, working directory, exit codes, test counts, pyramid labels, flake notes with any re-run, date or source revision, and failed or skipped observations. Do not mark `ready` when required check failed or was skipped, or when exit code was not executed and captured.
- **Limitations:** State proof boundaries like suites that cannot run locally, environment or secret needs, flake, and platform or performance gaps where still unproved. Do not hide material gaps.
- **Ownership:** List tracked support paths and dependency changes, generated or temporary paths, and cleanup rule. Unrelated work, customer data, secrets, and transient files stay out of setup commit.
- **Reopening:** Ordinary setup-test use reads this record unchanged. Only explicit update, repair, or reconfigure request reopens a ready record. Invalid record needs explicit repair or reconfigure intent. Changed matrix, commands, dependencies, environments, suites, or delivery facts need revised confirmed proposal and affected proof.
## Static validation and classification
Before broad discovery inspect only canonical path, parent path safety, and Git tracking and classify.
1. **Absent** only when canonical path does not exist and is not dangling symlink. Untracked occupied path is invalid, not absent.
2. **Invalid** when existing `.expskill` parent is symlink or not readable directory, occupied record is symlink, non-regular, unreadable, or untracked, frontmatter or required headings are missing, duplicated, unordered, or inconsistent, or required sections lack concrete values.
3. **Ready** only when regular tracked record passes static checks and declares completed canary plus full-suite sample with executed exit codes, verified copyable commands, layer labels, and proof observations for runnable suites.
Ready-record read validates record itself and reports contents. It does not audit dependencies or source, re-run proof, search alternatives, or silently fix drift. Suspected stale setup needs explicit update, repair, or reconfigure request. Before committing new or changed record check commands are copyable from stated working directory, tracked support exists, owned paths match confirmed proposal, transient evidence is excluded, no customer data or credentials are present, and no oracle was edited and no always-pass marker or hook was added. Record with invented proof, unrunnable commands, placeholder values, or failed canary or failed full-suite sample is invalid. Leave it unready and report blocker instead of committing it.
