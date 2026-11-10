# Publish a Yodea preview

Publish the isolated specimen through Yodea on every Design run, in direct and routed mode. The hosted preview lets the user inspect the work through approval, handoff, and PR review. Local gates still decide, and the preview supplements them.

Verified against yodea at revision `4691cef`. The issue thread wrongly guessed a `yodea deploy` command and a `credentials.json` file. The real commands are `push` and `delete`, and the session lives in `session.json`. Use what follows instead of the issue body.

## When this applies

Read this reference on every Design run. Publish each candidate ready for visual review after all local gates pass and before requesting approval, even without a link request. If an explicit user instruction forbids publication or a prerequisite is unavailable, report the blocker and keep preview delivery incomplete. Report a local-only result as leaving the required hosted preview incomplete.

## What you upload

The built static bundle only. Build the specimen (`npm run build`), upload `dist/`. The bundle holds only deterministic synthetic fixture content. Check before you push.

`yodea push` only accepts a Vite React TS folder: `package.json` with react, react-dom, and vite, a `vite.config`, a tsconfig, and at least one `.tsx` file. If the specimen does not meet that, stop and leave its structure as built.

## Commands

Install the CLI once with Go 1.27 or newer.

```sh
go install github.com/g-imhoff/yodea/cmd/yodea@latest
```

Reuse a valid session or log in. The password comes only from a hidden prompt or a `$YODEA_PASSWORD` set outside your commands.

```sh
yodea login --email you@example.com
yodea push --dir ./specimen-preview
yodea list
yodea delete <project>
```

`push` packs `dist/` as a gzipped tar, uploads it, and prints the preview URL with file and byte counts. List sites with `list`. Server choice is `--server URL`, else `$YODEA_SERVER`, else localhost:8093 when `$YODEA_DEV=1`, else the production default `https://previews.example.com`.

## Auth and session

Login writes the OS config dir file `yodea/session.json` with mode 0600, for example `~/.config/yodea/session.json`. It holds the server URL, bearer token, and user id. `$YODEA_SESSION_FILE` overrides the path for tests and throwaway logins. The server accepts the token as a Bearer header or the `yodea_session` cookie. Tokens expire, when a call fails on auth, log in again. Keep the session file and its contents only at its login path.

## Labels and limits

The preview address is `<userpart>-<project>` on the base domain: a lowercased DNS-safe user handle capped at 20 chars, one project path segment of 1 to 40 chars, 63-char DNS limit total. Long project names get trimmed, the user part wins.

Hard caps: packed `dist/` over 30MB, expanded content over 150MB, more than 20,000 files, or any file over 25MB is refused. The archive must hold a top-level `index.html`. Absolute paths, `..` escapes, symlinks and other special files, and dotfiles are refused, a stray `.env` in `dist/` fails the push instead of leaking.

## Proof and traceability

Local gates still run against the exact candidate before any approval request. The hosted page gets a light recheck at one width: it loads, shows the candidate state, the console stays clean, nothing overflows. Record that recheck as convenience evidence only.

Bind the preview URL, bundle digest, project label, and represented candidate revision or digest into a short hosted note such as `hosted-preview.json`. File it with the delivery manifest's review layer beside the brief digest, then add approval digests once approval exists. Pass that note to the next coordinator before any local evidence cleanup. No state schema change is needed. A re-push is a material change: it stales affected evidence and the approval bound to it. Re-run affected gates and ask for approval again.

Include a clickable preview link in visual approval requests, preview progress updates, delivery summaries, and downstream handoffs. In routed mode, the router relays the link with the manifest. Whenever a PR or merge request for this UI work is created or updated, its description must include the current Yodea link, the represented candidate revision or digest, and what the preview shows. The final user response for that PR must include the PR link and the preview link. Identify it as the Design specimen so the user knows which component states they can inspect. Refresh links and candidate identities when the preview changes. Check a link before sharing it again, and present it as current only when it represents the current candidate. Report explicitly when a link is unavailable or older. Each phase's existing permissions alone govern creating or updating a PR, whatever links it receives.

## Retention and cleanup

Keep the preview available during Design approval, downstream handoff, and the related PR review. Its lifetime continues past Design approval, phase completion, and local worktree cleanup. Remove it only after the related PR is merged or closed and it is no longer needed for review, or when the user requests removal. If no PR is planned, keep it until the user confirms review is finished.

Before removing a superseded preview, publish and check its replacement, update the handoff and any authorized PR description, and confirm no open review still needs the old link. Use `yodea list` to identify the exact project before `yodea delete <project>`. Retain its URL and digest in the hosted note and mark it retired.

## Failure modes

- Not logged in, expired token, or corrupt saved session. Log in again.
- Folder is not a Vite React TS app. Stop, and leave the specimen as built.
- No `dist/index.html`. Run the build first.
- Bundle over a cap, or dotfiles or symlinks inside. Trim the bundle and keep every gate.
- Label taken or project name invalid. Pick a valid project name.
- Server unreachable. The local specimen is still the proof. Stop blocked on preview delivery and report publication as unverified. Report Design delivery complete only once a working hosted link exists.
- Hosted page fails its load-and-look check. Retain local evidence, correct the preview, and recheck it before sharing it as ready for review.
