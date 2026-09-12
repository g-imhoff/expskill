# Share a preview with yodea

Publish the isolated specimen as a hosted preview when the user asks for a
shareable link, or when routed mode permits an external preview. The local
specimen stays the default. A hosted preview never replaces the local gates.

Verified against yodea at revision `4691cef`. The issue thread guessed at a
`yodea deploy` command and a `credentials.json` file. Both are wrong. The
real commands are `push` and `delete`, and the session lives in
`session.json`. Use what follows, not the issue body.

## When this applies

An approval-ready candidate exists, all local gates pass, and one of these
holds: the user asked for a preview link, or routed mode explicitly allows an
external preview. Otherwise skip this page. Never auto-publish on every run.

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

Log in. The password comes from `$YODEA_PASSWORD` or a hidden prompt. It
never goes on the command line.

```sh
yodea login --email you@example.com
```

Deploy from the specimen folder. `push` packs `dist/` as a gzipped tar,
uploads it, and prints the preview URL with file and byte counts.

```sh
yodea push --dir ./specimen-preview
```

List your sites, and remove the preview when review ends.

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

## Proof and cleanup

Local gates still run against the exact candidate before any approval
request. The hosted page gets a light recheck at one width: it loads, it
shows the approved state, the console stays clean, nothing overflows.
That recheck is convenience evidence, not a gate result. Record it as such.

Bind the preview URL, the bundle digest, and the label into a short hosted
note (for example `hosted-preview.json`) filed with the delivery manifest's
review layer, next to the brief and approval digests. No state schema change
is needed. A re-push is a material change. It stales the affected evidence
and the approval bound to it, under the same invalidation rules as any other
candidate edit. Re-run the affected gates and ask for approval again.

Delete the preview when review ends or the candidate changes. `yodea list`
shows what is still live. Do not leave stale previews behind.

## Failure modes

- Not logged in, or the saved session is corrupt. Log in again.
- Expired token. Log in again.
- Folder is not a Vite React TS app. Stop, do not reshape the specimen.
- No `dist/index.html`. Run the build first.
- Bundle over a cap, or dotfiles or symlinks inside. Trim the bundle,
  never the gates.
- Label taken or project name invalid. Pick a valid project name.
- Server unreachable. The local specimen is still the proof. Stop blocked
  on the preview only, and say plainly that the push did not happen.
