# Publish a Yodea preview

Publish the isolated specimen through Yodea on every Design run, in both direct
and routed mode. The hosted preview lets the user inspect the work throughout
approval, handoff, and PR review. It never replaces the local gates.

Verified against yodea at revision `4691cef`. The issue thread guessed at a
`yodea deploy` command and a `credentials.json` file. Both are wrong. The
real commands are `push` and `delete`, and the session lives in
`session.json`. Use what follows, not the issue body.

## When this applies

Read this reference on every Design run. Publish each candidate that is ready
for visual review after all local gates pass and before requesting approval.
Do not wait for a separate request for a preview link. If an explicit user
instruction forbids publication or a prerequisite is unavailable, report the
blocker and keep preview delivery incomplete. Never claim a local-only result
completed the required hosted preview.

## What you upload

The built static bundle only. Build the specimen (`npm run build`), upload
`dist/`. The bundle holds deterministic synthetic fixture content. It never
holds secrets, credentials, or customer data. Check before you push.

`yodea push` only accepts a Vite React TS folder: `package.json` with react,
react-dom, and vite, a `vite.config`, a tsconfig, and at least one `.tsx`
file. If the specimen does not meet that, stop. Do not reshape the specimen
to fit the host.

## Commands

Install the CLI once. Go 1.27 or newer is required.

```sh
go install github.com/g-imhoff/yodea/cmd/yodea@latest
```

Reuse a valid session or log in. The password comes from `$YODEA_PASSWORD` or a
hidden prompt. It never goes on the command line.

```sh
yodea login --email you@example.com
```

Deploy from the specimen folder. `push` packs `dist/` as a gzipped tar,
uploads it, and prints the preview URL with file and byte counts.

```sh
yodea push --dir ./specimen-preview
```

List your sites. Remove a preview only under the retention rules below.

```sh
yodea list
yodea delete <project>
```

Server choice is `--server URL`, else `$YODEA_SERVER`, else localhost:8093
when `$YODEA_DEV=1`, else the production default
`https://previews.example.com`.

## Auth and session

Login writes the OS config dir file `yodea/session.json` with mode 0600,
for example `~/.config/yodea/session.json`. It holds the server URL, the
bearer token, and the user id. `$YODEA_SESSION_FILE` overrides the path for
tests and throwaway logins. The server accepts the token as a Bearer header
or as the `yodea_session` cookie. Supabase signs production tokens and the
server checks them against the project JWKS. Tokens expire. When a call
fails on auth, log in again. Never copy the session file into the repo,
the bundle, or chat.

## Labels and limits

The preview address is `<userpart>-<project>` on the base domain. The user
part is a lowercased DNS-safe handle of the user id, capped at 20 chars.
The project name is one path segment, 1 to 40 chars. The full label keeps
the 63-char DNS limit. Long project names get trimmed, the user part wins.

The server enforces hard caps. A packed `dist/` over 30MB is refused.
Expanded content over 150MB is refused. More than 20,000 files is refused.
A single file over 25MB is refused. The archive must hold a top-level
`index.html`. Absolute paths, `..` escapes, symlinks and other special
files, and dotfiles are all refused. Dotfiles are rejected rather than
skipped, so a stray `.env` in `dist/` fails the push instead of leaking.

## Proof and traceability

Local gates still run against the exact candidate before any approval
request. The hosted page gets a light recheck at one width: it loads, it
shows the candidate state, the console stays clean, nothing overflows.
That recheck is convenience evidence, not a gate result. Record it as such.

Bind the preview URL, the bundle digest, the project label, and the represented
candidate revision or digest into a short hosted note such as
`hosted-preview.json`. File it with the delivery manifest's review layer beside
the brief digest, then add the approval digests once approval exists. Pass that
note to the next coordinator before any local evidence cleanup. No state schema
change is needed. A re-push is a material change. It stales the affected evidence
and the approval bound to it, under the same invalidation rules as any other
candidate edit. Re-run the affected gates and ask for approval again.

Include a clickable preview link in visual approval requests, preview progress
updates, delivery summaries, and downstream handoffs. In routed mode, the router
relays the link to the user and passes the hosted note onward with the manifest.
Whenever a PR or merge request for this UI work is created or updated, its
description must include the current Yodea link, the represented candidate
revision or digest, and what the preview shows. The final user response for that
PR must include both the PR link and the preview link. Identify it as the Design
specimen so the user knows which component states they can inspect.

Refresh these links and their candidate identities when the preview changes.
Check the link before sharing it again. If it is unavailable or represents an
older candidate, report that status explicitly. Do not present it as current.
Passing the link onward does not authorize a phase to create or update a PR
outside that phase's existing permissions.

## Retention and cleanup

Keep the preview available during Design approval, downstream handoff, and the
related PR review. Design approval, phase completion, and local worktree cleanup
do not end its lifetime. Remove it only after the related PR is merged or closed
and it is no longer needed for review, or when the user requests removal. If no
PR is planned, keep it until the user confirms that review is finished and the
preview is no longer needed.

Before removing a superseded preview, publish and check its replacement, update
the handoff and any authorized PR description, and confirm that no open review
still needs the old link. Use `yodea list` to identify the exact project before
`yodea delete <project>`. Retain its URL and digest in the hosted note and mark
it retired so the record still identifies what the user reviewed.

## Failure modes

- Not logged in, or the saved session is corrupt. Log in again.
- Expired token. Log in again.
- Folder is not a Vite React TS app. Stop, do not reshape the specimen.
- No `dist/index.html`. Run the build first.
- Bundle over a cap, or dotfiles or symlinks inside. Trim the bundle,
  never the gates.
- Label taken or project name invalid. Pick a valid project name.
- Server unreachable. The local specimen is still the proof. Stop blocked
  on preview delivery and say plainly that publication could not be verified.
  Do not report Design delivery complete until a working hosted link exists.
- Hosted page fails its load-and-look check. Retain the local evidence, correct
  the preview, and recheck it before sharing it as ready for review.
