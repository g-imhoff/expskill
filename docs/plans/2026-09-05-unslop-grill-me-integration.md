# Unslop and Grill Me Integration Plan

**Goal:** Add direct `$unslop` and `$grill-me` skills, apply Unslop to every eligible root conversation through a trusted plugin hook, and expose Grill Me only at a user-approved decision frontier.

## 1. Lock the contracts with failing tests

- Require both public skills and explicit-only metadata.
- Require the default `SessionStart` hook and verify its generated context and exclusions.
- Require one public Grill Me skill, with no separate public Grilling skill.
- Require pinned upstream snapshots, licenses, and digests.
- Reject em dashes and semicolons in every shipped or repository-local skill file.
- Require `$use-expskill` to offer Grill Me only when unresolved, connected, consequential user decisions block progress.

## 2. Vendor exact upstream material

- Store exact Unslop, Grill Me, and Grilling snapshots outside executable skill directories.
- Store both MIT licenses and a revision lock with source URLs and SHA-256 digests.
- Keep the public Codex skills as declared compatibility transforms of those snapshots.

## 3. Add Unslop

- Add a directly invokable `$unslop` skill with implicit invocation disabled.
- Add a read-only `SessionStart` hook that injects the Unslop policy for user-facing prose on startup, resume, clear, and compact.
- Exclude code, commands, machine-readable data, logs, identifiers, API names, quotations, citations, source excerpts, approved copy, and project-required terminology.
- Package and install the hook with the plugin.

## 4. Add Grill Me

- Merge the upstream wrapper and interview engine into one directly invokable `$grill-me` skill.
- Keep it explicit-only and preserve its fact-finding, decision-tree, recommendation, and user-confirmation behavior.
- Teach `$use-expskill` to offer it only at the exact decision-frontier blocker and resume the owning phase after confirmation.

## 5. Clean, package, and verify

- Remove em dashes and semicolons from every skill-owned text file without changing technical meaning.
- Update manifest, README, validator, and installer fixtures for the expanded package.
- Run focused tests, repository validation, plugin validation, the complete suite under the repository baseline umask, hook smoke tests, and final punctuation scans.
- Update the plugin cachebuster and reinstall the verified local plugin so both direct skills are discoverable in a new Codex session.
