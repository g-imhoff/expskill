#!/usr/bin/env python3
"""Classify a project's local UI inspection guide without changing anything."""
import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
SCHEMA_VERSION = "setup-ui-testing-inspection.v1"
MARKER = "<!-- expskill:setup-ui-testing:v1 -->"
REQUIRED_HEADINGS = (
    "Status", "Established Method", "Prerequisites", "Commands",
    "Specimens and Scenarios", "Project Context", "Responsive Inspection",
    "Canary", "Owned Files", "Agent-Only Support", "Limitations", "Updating",
)
IGNORE_PROBES = (
    ".ui-harness/README.md", ".ui-harness/.expskill-ignore-probe",
    ".ui-harness/agent/.expskill-ignore-probe", ".ui-harness/evidence/.expskill-ignore-probe",
)
def run_git(cwd, *arguments, input_text=None):
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    return subprocess.run(
        ["git", "-C", os.fspath(cwd), *arguments],
        check=False, capture_output=True, input=input_text, text=True,
        encoding="utf-8", errors="surrogateescape", env=environment, timeout=10,
    )
def is_not_git_repository(result):
    return result.returncode == 128 and not result.stdout.strip() and (
        result.stderr.strip().lower().startswith("fatal: not a git repository"))
def path_kind(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"
def resolve_root(explicit):
    candidate = Path(explicit).expanduser() if explicit else Path.cwd()
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {resolved}")
    if explicit:
        return resolved, "explicit"
    try:
        result = run_git(resolved, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Git worktree root detection failed: {error}") from error
    if is_not_git_repository(result):
        return resolved, "non-git-current-directory"
    if result.returncode or not result.stdout.strip():
        detail = git_error("Git worktree root detection failed", result)
        raise RuntimeError(detail if result.returncode else detail + ": empty output")
    return Path(result.stdout.strip()).resolve(strict=True), "git-worktree"
def markdown_lines_outside_fences(contents):
    active = None
    for line in contents.splitlines():
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        fence = None
        if indent <= 3 and stripped[:1] in {"`", "~"}:
            character = stripped[0]
            length = len(stripped) - len(stripped.lstrip(character))
            if length >= 3:
                fence = character, length, stripped[length:]
        if fence and active is None:
            active = fence[:2]
            continue
        if fence and fence[0] == active[0] and fence[1] >= active[1]:
            if not fence[2].strip():
                active = None
                continue
        if active is None:
            yield line
def parse_ignore_matches(output):
    fields = output.split("\x00")
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 4:
        return None
    matches = {}
    for index in range(0, len(fields), 4):
        source, line, pattern, path = fields[index:index + 4]
        matches[path] = {
            "source": source, "line": line, "pattern": pattern, "path": path,
        }
    return matches
def git_error(label, result):
    detail = result.stderr.strip().replace("\x00", "\\0")
    return f"{label}: {detail or 'Git command failed'}"
def inspect_git(project_root):
    facts = {
        "repository": None, "worktree_root": None, "exclude_path": None,
        "tracked_under_harness": None, "tracked_paths": [],
        "harness_root_ignored": None, "ignore_checks": [], "errors": [],
    }
    try:
        inside = run_git(project_root, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        facts["errors"].append("git executable not found")
        return facts
    except (OSError, subprocess.TimeoutExpired) as error:
        facts["errors"].append(f"Git repository detection failed: {error}")
        return facts
    if is_not_git_repository(inside):
        facts["repository"] = False
        return facts
    if inside.returncode or inside.stdout.strip() != "true":
        detail = git_error("Git repository detection failed", inside)
        if not inside.returncode:
            detail = f"Git repository detection returned {inside.stdout.strip() or 'empty output'!r}"
        facts["errors"].append(detail)
        return facts
    facts["repository"] = True
    root = run_git(project_root, "rev-parse", "--show-toplevel")
    if root.returncode or not root.stdout.strip():
        facts["errors"].append(git_error("cannot resolve Git worktree root", root))
        return facts
    facts["worktree_root"] = os.fspath(Path(root.stdout.strip()).resolve(strict=True))
    exclude = run_git(project_root, "rev-parse", "--git-path", "info/exclude")
    if exclude.returncode or not exclude.stdout.strip():
        facts["errors"].append(git_error("cannot resolve local exclude", exclude))
    else:
        exclude_path = Path(exclude.stdout.strip())
        if not exclude_path.is_absolute():
            exclude_path = project_root / exclude_path
        facts["exclude_path"] = os.fspath(exclude_path.resolve(strict=False))
    tracked = run_git(project_root, "ls-files", "-z", "--", ".ui-harness")
    if tracked.returncode:
        facts["errors"].append(git_error("cannot inspect tracked harness paths", tracked))
    else:
        paths = sorted(item for item in tracked.stdout.split("\x00") if item)
        facts["tracked_paths"] = paths
        facts["tracked_under_harness"] = bool(paths)
    probe_input = "\x00".join(IGNORE_PROBES) + "\x00"
    ignored = run_git(
        project_root, "check-ignore", "-v", "-z", "--no-index", "--stdin",
        input_text=probe_input,
    )
    if ignored.returncode not in {0, 1}:
        facts["errors"].append(git_error("cannot inspect harness ignore coverage", ignored))
        return facts
    matches = parse_ignore_matches(ignored.stdout)
    if matches is None:
        facts["errors"].append("cannot parse Git ignore matches")
        return facts
    facts["ignore_checks"] = [
        {"path": path, "ignored": path in matches, "match": matches.get(path)}
        for path in IGNORE_PROBES
    ]
    facts["harness_root_ignored"] = all(check["ignored"] for check in facts["ignore_checks"])
    return facts
def inspect_guide(project_root):
    harness_path = project_root / ".ui-harness"
    guide_path = harness_path / "README.md"
    harness_kind = path_kind(harness_path)
    guide_kind = "absent"
    issues = []
    marker_count = 0
    heading_counts = {heading: 0 for heading in REQUIRED_HEADINGS}
    headings_in_order = False
    if harness_kind in {"symlink", "file", "other", "unreadable"}:
        issues.append(f"canonical harness path has unsafe type: {harness_kind}")
    elif harness_kind == "directory":
        guide_kind = path_kind(guide_path)
        if guide_kind == "symlink":
            issues.append("canonical guide is a symlink")
        elif guide_kind not in {"absent", "file"}:
            issues.append(f"canonical guide has unsafe type: {guide_kind}")
        elif guide_kind == "file":
            try:
                contents = guide_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                issues.append(f"canonical guide is not readable UTF-8: {error}")
            else:
                lines = list(markdown_lines_outside_fences(contents))
                marker_count = lines.count(MARKER)
                if marker_count != 1:
                    issues.append(f"exact ready marker count is {marker_count}, expected 1")
                positions = []
                for heading in REQUIRED_HEADINGS:
                    exact = f"## {heading}"
                    count = lines.count(exact)
                    heading_counts[heading] = count
                    if count == 1:
                        positions.append(lines.index(exact))
                    else:
                        issues.append(f"heading {exact!r} count is {count}, expected 1")
                headings_in_order = len(positions) == len(REQUIRED_HEADINGS) and (
                    positions == sorted(positions)
                )
                if len(positions) == len(REQUIRED_HEADINGS) and not headings_in_order:
                    issues.append("required headings are out of order")
    git = inspect_git(project_root)
    issues.extend(f"Git safety check failed: {item}" for item in git["errors"])
    if git["tracked_under_harness"] is True:
        issues.append("one or more .ui-harness paths are tracked by Git")
    if guide_kind == "file" and git["repository"]:
        if git["harness_root_ignored"] is not True:
            missing = [
                item["path"] for item in git["ignore_checks"] if not item["ignored"]
            ]
            issues.append("canonical harness root is not fully ignored: " + ", ".join(missing))
    if guide_kind == "absent" and not issues:
        classification = "absent"
    elif guide_kind == "file" and not issues:
        classification = "ready"
    else:
        classification = "invalid"
    return {
        "classification": classification,
        "project_root": os.fspath(project_root),
        "canonical_root": os.fspath(harness_path),
        "guide": {
            "path": os.fspath(guide_path),
            "harness_path_type": harness_kind,
            "path_type": guide_kind,
            "expected_marker": MARKER,
            "marker_count": marker_count,
            "required_heading_counts": heading_counts,
            "headings_in_order": headings_in_order,
        },
        "git": git,
        "issues": issues,
    }
def emit(payload):
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))

def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only .ui-harness classifier")
    parser.add_argument("--project-root", help="exact project root, defaults to Git root")
    args = parser.parse_args(argv)
    try:
        project_root, source = resolve_root(args.project_root)
        result = inspect_guide(project_root)
        result["schema_version"] = SCHEMA_VERSION
        result["project_root_source"] = source
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        source = "explicit" if args.project_root else "automatic"
        emit({
            "schema_version": SCHEMA_VERSION,
            "classification": "invalid",
            "project_root": None,
            "project_root_source": source,
            "issues": [f"cannot resolve project root: {error}"],
        })
        return 2
    emit(result)
    return 0 if result["classification"] in {"absent", "ready"} else 1

if __name__ == "__main__":
    sys.exit(main())
