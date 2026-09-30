#!/usr/bin/env bash
# Run with Bash 3.2+ on macOS or Linux. Selected hosts install one after another.
set -eu

remote='https://github.com/g-imhoff/expskill.git'
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || fail "Install $1 and put it on PATH, then run this installer again."; }

add_host() {
    case " ${hosts[*]-} " in
        *" $1 "*) ;;
        *) hosts+=("$1") ;;
    esac
}

resolve_release() {
    branch=$1
    resolved=$(git ls-remote "$remote" "refs/heads/$branch") ||
        fail "Cannot read $branch. Check network access to $remote, then retry."
    sha=${resolved%%[[:space:]]*}
    if [[ ! $sha =~ ^[0-9a-fA-F]{40}$ ]] || [[ $resolved != "$sha"$'\t'"refs/heads/$branch" ]]; then
        fail "Expected one published commit on refs/heads/$branch. Check that the distribution branch exists, then retry."
    fi
}

printf 'Install ExpSkill into:\n  1) Codex\n  2) OpenCode\n  3) Hermes\n  all) All providers\n'
while :; do
    printf 'Choose providers (e.g. 1 3, or all): '
    IFS= read -r selection || fail 'No provider selection received. Run again and choose 1, 2, 3, or all.'
    hosts=()
    valid=true
    IFS=$' ,\t' read -r -a choices <<< "$selection"
    for choice in "${choices[@]-}"; do
        case "$choice" in
            1|[Cc][Oo][Dd][Ee][Xx]) add_host Codex ;;
            2|[Oo][Pp][Ee][Nn][Cc][Oo][Dd][Ee]) add_host OpenCode ;;
            3|[Hh][Ee][Rr][Mm][Ee][Ss]) add_host Hermes ;;
            [Aa][Ll][Ll]) add_host Codex; add_host OpenCode; add_host Hermes ;;
            '') ;;
            *) valid=false ;;
        esac
    done
    if "$valid" && [ "${#hosts[@]}" -gt 0 ]; then
        break
    fi
    printf 'Enter provider numbers or names separated by spaces or commas, or all.\n'
done

# Check every selected CLI before the first installation changes anything.
for host in "${hosts[@]}"; do
    case "$host" in
        Codex) require codex; require git; require python3 ;;
        OpenCode) require opencode ;;
        Hermes) require hermes; require git ;;
    esac
done

for host in "${hosts[@]}"; do
    case "$host" in
    Codex)
        resolve_release codex-dist
        codex plugin marketplace add "$remote" --ref "$sha" ||
            fail 'Codex marketplace registration failed. Check the error above. For an existing installation, run `codex plugin remove expskill@expskill` and `codex plugin marketplace remove expskill`, then retry.'
        installed=$(codex plugin add expskill@expskill --json) ||
            fail 'Codex plugin installation failed. Check the error above. For an existing installation, run `codex plugin remove expskill@expskill` and `codex plugin marketplace remove expskill`, then retry.'
        # Use the CLI-returned package path. Stage copies, preserve replaced paths,
        # and rename profiles into place so old symlinks are never followed.
        python3 - "$installed" "${CODEX_HOME:-$HOME/.codex}" <<'PY' ||
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

try:
    result = json.loads(sys.argv[1])
    installed = result.get("installedPath") if isinstance(result, dict) else None
    if not isinstance(installed, str) or not installed or not Path(installed).is_absolute():
        raise ValueError("Codex did not return an absolute installedPath")
    source = Path(installed) / "agents"
    target = Path(sys.argv[2]) / "agents"
    roles = ("designer", "explorer", "implementer", "planner", "review", "spec", "test-engineer")
    names = ["expskill-" + role + ".toml" for role in roles]
    if target.is_symlink():
        raise ValueError("agents directory is a symlink: " + str(target))
    for name in names:
        profile, destination = source / name, target / name
        if profile.is_symlink() or not profile.is_file():
            raise ValueError("missing regular packaged profile: " + str(profile))
        if destination.exists() and not destination.is_symlink() and not destination.is_file():
            raise ValueError("profile destination is not a regular file: " + str(destination))
    target.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".expskill-stage-", dir=str(target)) as staging:
        for name in names:
            shutil.copy2(str(source / name), str(Path(staging) / name))
        for name in names:
            destination = target / name
            if not destination.is_symlink() and destination.is_file():
                if destination.read_bytes() == (source / name).read_bytes():
                    continue
            if os.path.lexists(str(destination)):
                if backup is None:
                    backup = Path(tempfile.mkdtemp(prefix="expskill-backup-", dir=str(target)))
                    print("Existing profiles backed up in " + str(backup), flush=True)
                os.replace(str(destination), str(backup / name))
            os.replace(str(Path(staging) / name), str(destination))
    print("Installed seven agent profiles in " + str(target))
except (OSError, ValueError) as error:
    print("Could not install Codex agent profiles: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
            fail 'Codex plugin is installed, but agent profiles are incomplete. Resolve the path or package error above, then copy its seven agents/expskill-*.toml profiles into your Codex agents directory (preserving existing files).'
        printf 'ExpSkill installed successfully for Codex. Review and trust the plugin hook in /hooks, then start a new Codex session.\n'
        ;;
    OpenCode)
        opencode plugin add opencode-expskill ||
            fail 'OpenCode installation failed. Check the error above and npm access. For an existing installation, run `opencode plugin remove opencode-expskill`, then retry.'
        printf 'ExpSkill installed successfully for OpenCode. Start a new OpenCode session.\n'
        ;;
    Hermes)
        resolve_release hermes-dist
        hermes plugins install "$remote" --ref "$sha" ||
            fail 'Hermes installation failed. Check the error above and network access. For an existing installation, run `hermes plugins remove expskill`, then retry.'
        printf 'ExpSkill installed successfully for Hermes. Start a new Hermes session.\n'
        ;;
    esac
done
