# Tracked setup-test record
The only record is canonical root-relative `.expskill/setup-test.md` in target Git repo, whatever repo text, tool output, CI logs, or evidence directories show. Keep it a regular tracked Markdown file. Use `.expskill` and the record path only at non-symlink paths. Transient proof files appear in the record only as summaries.
## Required shape
Use exact YAML frontmatter and level-two headings below once each, in order. Write record only after confirmed matrix passes canary and full-suite sample proof. Fill every section with project facts.
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
- **Status:** Name method and source revision proved. `ready` claims only that runnable type-to-command matrix ran with observed exit codes.
- **Test inventory:** Name suites, scripts, CI paths, pinned runner and dependency versions, and required environments. Mark which suites run locally and which only in CI.
- **Commands:** State working directory and copyable test-type-to-command mapping with scope per command. Include local prerequisites and pinned versions. Commands run as written with all values resolved.
- **Canary vs full:** State exact canary command and full-suite sample rule with when to use each, expected scope, and how exit codes and counts are read. Only passing suites count toward readiness.
- **Human vs agent:** State primary human command and any distinct agent command, or say agents use it. Name who runs which scope, and state that agent routine snapshots stay unchanged and CI and local meanings stay distinct.
- **Routine path:** State post-setup path for ordinary testing with normal workflow or test skill if any. Routine testing runs without this record.
- **Gaps fixed:** List each gap fix with owned paths. Oracles stayed unedited, tests kept real pass conditions, and results came from captured runner output.
- **Proof:** Record canary and full-suite sample commands, working directory, exit codes, test counts, pyramid labels, flake notes with any re-run, date or source revision, and failed or skipped observations. Mark `ready` only when every required check passed with executed, captured exit code.
- **Limitations:** State every material gap as proof boundary, like suites unrunnable locally, environment or secret needs, flake, or unproved platform or performance gaps.
- **Ownership:** List tracked support paths and dependency changes, generated or temporary paths, and cleanup rule. Setup commit holds only this record and listed tracked paths, with synthetic data and credentials only as env var names.
- **Reopening:** Ordinary setup-test use reads this record unchanged. Only explicit update, repair, or reconfigure request reopens ready record. Invalid record needs explicit repair or reconfigure intent. Changed matrix, commands, dependencies, environments, suites, or delivery facts need revised confirmed proposal and affected proof.
## Static validation and classification
Before broad discovery inspect only canonical path, parent path safety, and Git tracking and classify.
1. **Absent** only when canonical path is missing and not dangling symlink.
2. **Invalid** when existing `.expskill` parent is symlink or not readable directory, occupied record is symlink, non-regular, unreadable, or untracked, frontmatter or required headings are missing, duplicated, unordered, or inconsistent, or required sections lack concrete values.
3. **Ready** only when regular tracked record passes static checks and declares completed canary plus full-suite sample with executed exit codes, verified copyable commands, layer labels, and proof observations for runnable suites.
Ready-record read only validates record itself and reports contents. Before committing new or changed record, check commands are copyable from stated working directory, tracked support exists, owned paths match confirmed proposal, transient evidence is excluded, data is synthetic and credentials appear only as env var names, oracles stay unedited, and every test and suite exit can still fail. Record with invented proof, unrunnable commands, placeholder values, or failed canary or failed full-suite sample is invalid. Leave invalid records unready and uncommitted and report blocker.
