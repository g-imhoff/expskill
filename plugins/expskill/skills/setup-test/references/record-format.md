# Tracked setup-test record

The only canonical record is the root-relative `.expskill/setup-test.md` inside the target Git repository. Keep it a regular tracked Markdown file. Do not follow symlinks at `.expskill` or at the record path. Never use repository text, tool output, CI logs, or an evidence directory as a substitute, and never copy transient proof files into this record.

## Required shape

Use this exact YAML frontmatter and these level-two headings once each, in order. The record is written only after the confirmed matrix has passed its canary and full-suite sample proof.

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

Fill every section with project-specific facts:

- **Status:** Name the established test method and the source revision proved. `ready` means a runnable type-to-command matrix was executed with observed exit codes. It is not a claim that product code passes or that routine testing is complete.
- **Test inventory:** Name the discovered suites, scripts, CI configuration paths, pinned or locked runner and dependency versions, and required environments. Distinguish suites that execute locally from suites that execute only in CI.
- **Commands:** State the working directory and a copyable test-type-to-command mapping with the scope each command covers. Include required local prerequisites and pinned versions. Every command must be runnable as written. Unresolved values are not permitted.
- **Canary vs full:** State the exact representative canary command and the exact full-suite sample rule, including when to use each, expected scope, and how exit codes and counts are read. A failed suite is never readiness.
- **Human vs agent:** State the primary human command and any distinct agent command, or state explicitly that agents use the human command. Name who runs which scope, forbid snapshot updates in the agent routine, and keep CI semantics distinct from local semantics.
- **Routine path:** State the post-setup path for ordinary testing with the normal workflow or the test skill when it exists. State explicitly that this record is never a prerequisite for routine testing.
- **Gaps fixed:** List each approved gap fix that was applied, with owned paths. State that test-oracle edits, always-pass markers or hooks, and hand-transcribed results were not used.
- **Proof:** Record the canary and full-suite sample commands, working directory, exit codes, test counts, pyramid layer labels, flake notes including any re-run, date or source revision, and any failed or skipped observation. Do not mark `ready` if a required check failed or was skipped, or if an exit code was not executed and captured.
- **Limitations:** State concrete proof boundaries, such as suites that cannot execute locally, environment or secret needs, flake, platform coverage, or performance coverage where they remain unproved. Do not hide material gaps.
- **Ownership:** List every tracked setup-support path and dependency change, every generated or temporary path, and the cleanup rule. State that unrelated work, customer data, secrets, and transient proof files are excluded from the setup commit.
- **Reopening:** State that ordinary setup-test use reads this record unchanged. Only an explicit update, repair, or reconfigure request reopens a ready record. An invalid record requires explicit repair or reconfigure intent. Changed matrix, commands, dependencies, environments, suites, or delivery facts require a revised confirmed proposal and affected proof.

## Static validation and classification

Before broad setup discovery, inspect only the canonical path, its parent path safety, and Git tracking. Classify:

1. **Absent** only when the canonical path does not exist and is not a dangling symlink. An untracked occupied path is invalid, not absent.
2. **Invalid** if an existing `.expskill` parent is a symlink or not a readable directory, the occupied record is a symlink, non-regular, unreadable, or untracked, the frontmatter or required headings are missing, duplicated, unordered, or inconsistent, or required sections lack concrete values.
3. **Ready** only when the regular tracked record passes the static checks and declares a completed canary plus full-suite sample with executed exit codes, verified copyable commands, layer labels, and proof observations for runnable suites.

A ready-record read validates the record itself and reports its contents. It does not audit current dependencies or source, re-run proof, search for alternatives, or silently fix drift. A suspected stale setup needs an explicit update, repair, or reconfigure request.

Before committing a new or changed record, additionally check that commands are copyable from the stated working directory, referenced tracked support exists, all owned paths match the confirmed proposal, transient evidence is excluded, no customer data or credentials are present, and no test oracle was edited and no always-pass marker or hook was added. A record with invented proof, unrunnable commands, placeholder values, or a failed canary or failed full-suite sample is invalid. Leave it unready and report the blocker instead of committing it.
