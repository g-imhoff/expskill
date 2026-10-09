#!/usr/bin/env bash
# Run with Bash 3.2+ on macOS or Linux. Selected hosts install or update in order.
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

printf 'Install ExpSkill into:\n  1) Codex\n  2) OpenCode\n  3) Hermes\n  4) Claude Code\n  all) All providers\n'
while :; do
    printf 'Choose providers (e.g. 1 4, or all): '
    IFS= read -r selection || fail 'No provider selection received. Run again and choose 1, 2, 3, 4, or all.'
    hosts=()
    valid=true
    IFS=$' ,\t' read -r -a choices <<< "$selection"
    for choice in "${choices[@]-}"; do
        case "$choice" in
            1|[Cc][Oo][Dd][Ee][Xx]) add_host Codex ;;
            2|[Oo][Pp][Ee][Nn][Cc][Oo][Dd][Ee]) add_host OpenCode ;;
            3|[Hh][Ee][Rr][Mm][Ee][Ss]) add_host Hermes ;;
            4|[Cc][Ll][Aa][Uu][Dd][Ee]*) add_host Claude ;;
            [Aa][Ll][Ll]) add_host Codex; add_host OpenCode; add_host Hermes; add_host Claude ;;
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
        Claude) require claude; require git; require python3 ;;
    esac
done

for host in "${hosts[@]}"; do
    case "$host" in
    Codex)
        resolve_release codex-dist
        marketplaces=$(codex plugin marketplace list --json) ||
            fail 'Cannot inspect Codex marketplaces. Check the error above, then retry.'
        registered=$(python3 - "$marketplaces" <<'PY'
import json
import sys

try:
    result = json.loads(sys.argv[1])
    entries = result.get("marketplaces") if isinstance(result, dict) else None
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
        for entry in entries
    ):
        raise ValueError("expected a marketplaces list with named entries")
    print("true" if any(entry["name"] == "expskill" for entry in entries) else "false")
except ValueError as error:
    print("Cannot read Codex marketplace list: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
        ) || fail 'Cannot inspect Codex marketplaces. Update the Codex CLI, then retry.'
        # Adding the same marketplace with a new --ref conflicts. Keep the
        # installed plugin cache while replacing only its marketplace snapshot.
        if "$registered"; then
            printf 'Refreshing the ExpSkill marketplace for Codex...\n'
            codex plugin marketplace remove expskill ||
                fail 'Cannot refresh the Codex ExpSkill marketplace. Check the error above, then retry.'
        fi
        codex plugin marketplace add "$remote" --ref "$sha" ||
            fail 'Codex marketplace registration failed. Check the error above and network access, then retry this installer.'
        installed=$(codex plugin add expskill@expskill --json) ||
            fail 'Codex plugin installation failed. Check the error above, then retry this installer.'
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
    backup_root = target.parent / "agent-backups"
    roles = ("designer", "explorer", "implementer", "planner", "review", "spec", "test-engineer")
    names = ["expskill-" + role + ".toml" for role in roles]
    if target.is_symlink():
        raise ValueError("agents directory is a symlink: " + str(target))
    if backup_root.is_symlink() or (backup_root.exists() and not backup_root.is_dir()):
        raise ValueError("agent backup storage is not a regular directory: " + str(backup_root))
    for name in names:
        profile, destination = source / name, target / name
        if profile.is_symlink() or not profile.is_file():
            raise ValueError("missing regular packaged profile: " + str(profile))
        if destination.exists() and not destination.is_symlink() and not destination.is_file():
            raise ValueError("profile destination is not a regular file: " + str(destination))
    target.mkdir(parents=True, exist_ok=True)
    for legacy in sorted(target.glob("expskill-backup-*")):
        if legacy.is_symlink() or not legacy.is_dir():
            continue
        backup_root.mkdir(parents=True, exist_ok=True)
        archive = Path(tempfile.mkdtemp(prefix="expskill-backup-", dir=str(backup_root)))
        os.replace(str(legacy), str(archive / legacy.name))
        print("Existing backup moved to " + str(archive / legacy.name), flush=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".expskill-stage-", dir=str(target.parent)) as staging:
        for name in names:
            shutil.copy2(str(source / name), str(Path(staging) / name))
        for name in names:
            destination = target / name
            if not destination.is_symlink() and destination.is_file():
                if destination.read_bytes() == (source / name).read_bytes():
                    continue
            if os.path.lexists(str(destination)):
                if backup is None:
                    backup_root.mkdir(parents=True, exist_ok=True)
                    backup = Path(tempfile.mkdtemp(prefix="expskill-backup-", dir=str(backup_root)))
                    print("Existing profiles backed up in " + str(backup), flush=True)
                os.replace(str(destination), str(backup / name))
            os.replace(str(Path(staging) / name), str(destination))
    print("Installed seven agent profiles in " + str(target))
except (OSError, ValueError) as error:
    print("Could not install Codex agent profiles: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
            fail 'Codex plugin is installed, but agent profiles are incomplete. Resolve the path or package error above, then copy its seven agents/expskill-*.toml profiles into your Codex agents directory (preserving existing files).'
        printf 'ExpSkill installed or updated successfully for Codex. Review and trust the plugin hook in /hooks, then start a new Codex session.\n'
        ;;
    OpenCode)
        plugins=$(opencode plugin list) ||
            fail 'Cannot inspect OpenCode plugins. Check the error above, then retry.'
        target=''
        # OpenCode lists ID, VERSION, SOURCE. Retain a configured version spec
        # as the update target and match the package name exactly.
        while IFS=$' \t' read -r plugin_id version source rest; do
            case "$source" in
                opencode-expskill|opencode-expskill@*) target=$source; break ;;
            esac
        done <<< "$plugins"
        if [ -n "$target" ]; then
            opencode plugin update "$target" ||
                fail 'OpenCode update failed. Check the error above and npm access, then retry.'
        else
            opencode plugin add opencode-expskill ||
                fail 'OpenCode installation failed. Check the error above and npm access, then retry.'
        fi
        printf 'ExpSkill installed or updated successfully for OpenCode. Start a new OpenCode session.\n'
        ;;
    Hermes)
        resolve_release hermes-dist
        # Hermes update refuses pinned installs. --force replaces an existing
        # package with the latest release and also works for a first install.
        hermes plugins install "$remote" --ref "$sha" --force ||
            fail 'Hermes installation or update failed. Check the error above and network access, then retry.'
        printf 'ExpSkill installed or updated successfully for Hermes. Start a new Hermes session.\n'
        ;;
    Claude)
        resolve_release claude-dist
        # Claude Code marketplace sources pin a branch with a #ref suffix
        # (the CLI has no --ref flag). The claude-dist branch moves on each
        # release, so installs track the latest published marketplace.
        source="$remote#claude-dist"
        marketplaces=$(claude plugin marketplace list --json) ||
            fail 'Cannot inspect Claude Code marketplaces. Check the error above, then retry.'
        registered=$(python3 - "$marketplaces" <<'PY'
import json
import sys

try:
    entries = json.loads(sys.argv[1])
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
        for entry in entries
    ):
        raise ValueError("expected a marketplaces list with named entries")
    print("true" if any(entry["name"] == "expskill" for entry in entries) else "false")
except ValueError as error:
    print("Cannot read Claude Code marketplace list: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
        ) || fail 'Cannot inspect Claude Code marketplaces. Update the Claude Code CLI, then retry.'
        if "$registered"; then
            printf 'Refreshing the ExpSkill marketplace for Claude Code...\n'
            claude plugin marketplace update expskill ||
                fail 'Cannot refresh the Claude Code ExpSkill marketplace. Check the error above, then retry.'
        else
            claude plugin marketplace add "$source" ||
                fail 'Claude Code marketplace registration failed. Check the error above and network access, then retry this installer.'
        fi
        installed=$(claude plugin list --json) ||
            fail 'Cannot inspect Claude Code plugins. Check the error above, then retry.'
        present=$(python3 - "$installed" <<'PY'
import json
import sys

try:
    entries = json.loads(sys.argv[1])
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or not isinstance(entry.get("id"), str)
        for entry in entries
    ):
        raise ValueError("expected a plugins list with identified entries")
    print("true" if any(entry["id"] == "expskill@expskill" for entry in entries) else "false")
except ValueError as error:
    print("Cannot read Claude Code plugin list: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
        ) || fail 'Cannot inspect Claude Code plugins. Update the Claude Code CLI, then retry.'
        if "$present"; then
            claude plugin update expskill@expskill ||
                fail 'Claude Code update failed. Check the error above and network access, then retry.'
        else
            claude plugin install expskill@expskill ||
                fail 'Claude Code installation failed. Check the error above and network access, then retry.'
        fi
        printf 'ExpSkill installed or updated successfully for Claude Code. Start a new Claude Code session.\n'
        ;;
    esac
done
