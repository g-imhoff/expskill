---
name: file-pr
description: File a concise pull request when the user asks to open one. Only the user or another ExpSkill skill can load $file-pr.
---

## Check the branch first

Check whether a PR already exists for this branch. Diff the branch against its base, usually `origin/main`, and confirm the diff matches the goal before filing.

## Title

PR titles often become commit messages, so follow the repo title conventions. Look at recent merged PRs and Git history for examples. Prefer a short human title that states why the change matters.

BAD

> perf(server): negotiate permessage-deflate on the websocket

GOOD

> perf(server): cut websocket frame size by 70 percent with gzipping

## Description

Open with the problem in the user's own words, then state the fix in brief. Do not lead with an inventory of changed files or symbols.

BAD

> Removed implicit workspace carry-over from every new thread entry point. New threads inherit only the project from context. Branch, worktree, and env mode always come from the configured defaults. Deleted several thread setup helpers and seed context machinery.

GOOD

> My new worktree default was ignored when starting new threads on existing worktrees. Super unintuitive. Now your preferences always apply.

## Credit

End the description with the public system name and harness that made the changes, for example `Created with <name> in <harness>.` Include contributing subagent systems by their reported names. When identity is unavailable, name the harness without guessing.

## Submit

Check the title, description, quotes, and attachments for secrets and private details before sending.

Open a real PR rather than a draft so review bots run. When `gh pr create` fails on GraphQL rate limits, open through REST with `gh api repos/<owner>/<repo>/pulls -f title=... -f head=... -f base=... -F body=@<file>`.

Do not post a follow-up PR comment unless the user request needs it.
