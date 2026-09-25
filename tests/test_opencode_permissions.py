from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.render_opencode import render_agents as render_opencode_agents
from scripts.validate import _parse_overlay_frontmatter, validate_repository


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
OPENCODE_ROOT = PLUGIN_ROOT / "opencode"
READ_ONLY_AGENTS = (
    "expskill-explorer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)

# These are deliberately shell-command forms rather than a second permission
# matcher.  The assertions below verify that the canonical rules and the
# generated frontmatter expose only these bounded inspection entry points.
SAFE_INSPECTION_RULES = {
    "git status": "allow",
    "git status --short": "allow",
    "git status --short --branch": "allow",
    "git status --porcelain": "allow",
    "git status --porcelain=v1": "allow",
    "git branch": "allow",
    "git branch --show-current": "allow",
    "git branch --list": "allow",
    "git branch --list -- *": "allow",
    "git --no-pager diff --no-ext-diff --no-textconv --no-renames": "allow",
    "git --no-pager diff --no-ext-diff --no-textconv --no-renames --end-of-options *": "allow",
    "git --no-pager diff --no-ext-diff --no-textconv --no-renames -- *": "allow",
    "git --no-pager log --no-ext-diff --no-textconv --no-renames": "allow",
    "git --no-pager log --no-ext-diff --no-textconv --no-renames --end-of-options *": "allow",
    "git --no-pager show --no-ext-diff --no-textconv --no-renames": "allow",
    "git --no-pager show --no-ext-diff --no-textconv --no-renames --end-of-options *": "allow",
}

SAFE_INSPECTION_COMMANDS = (
    "git status",
    "git status --short",
    "git status --porcelain=v1",
    "git branch --show-current",
    "git branch --list -- topic",
    "git --no-pager diff --no-ext-diff --no-textconv --no-renames --end-of-options HEAD",
    "git --no-pager diff --no-ext-diff --no-textconv --no-renames -- path/to/file",
    "git --no-pager log --no-ext-diff --no-textconv --no-renames --end-of-options HEAD",
    "git --no-pager show --no-ext-diff --no-textconv --no-renames --end-of-options HEAD",
)

UNSAFE_COMMANDS = (
    "git branch -d topic",
    "git branch -D topic",
    "git branch --delete topic",
    "git branch --force topic",
    "git branch -f topic HEAD",
    "git branch --move old new",
    "git branch -m old new",
    "git branch -M old new",
    "git branch --copy old new",
    "git branch -c old new",
    "git branch -C old new",
    "git branch --create-reflog topic",
    "git branch --track topic origin/topic",
    "git branch --set-upstream-to=origin/topic topic",
    "git branch topic",
    "git branch --edit-description topic",
    "git diff --output=/tmp/changes.patch",
    "git diff --ext-diff",
    "git diff --textconv",
    "git log --output=/tmp/commits.txt",
    "git log --ext-diff",
    "git log --textconv",
    "git show --output=/tmp/commit.txt HEAD",
    "git show --ext-diff HEAD",
    "git show --textconv HEAD",
    "git --no-pager show --output=/tmp/commit.txt --no-ext-diff --no-textconv --no-renames --end-of-options HEAD",
    "git --no-pager diff --output=/tmp/changes.patch --no-ext-diff --no-textconv --no-renames --end-of-options HEAD",
    "git --no-pager log --output=/tmp/commits.txt --no-ext-diff --no-textconv --no-renames --end-of-options HEAD",
)

UNSAFE_WILDCARD_RULES = (
    "git branch *",
    "git branch -*",
    "git branch -D *",
    "git branch --delete *",
    "git branch --force *",
    "git branch --move *",
    "git branch -M *",
    "git branch --copy *",
    "git branch -C *",
    "git branch --create-reflog *",
    "git branch --track *",
    "git diff *",
    "git diff --output *",
    "git diff --output=*",
    "git diff --ext-diff *",
    "git diff --textconv *",
    "git log *",
    "git log --output *",
    "git show *",
    "git show --output *",
)

OUTPUT_GUARD_RULES = (
    "git * --output*",
    "git * -o*",
    "git * --ext-diff*",
    "git * --textconv*",
    "git *>*",
    "git *<*",
)


def _permission_block(contents: str, label: str) -> str:
    errors: list[str] = []
    parsed = _parse_overlay_frontmatter(contents, label, errors)
    if errors or parsed is None:
        raise AssertionError(f"could not parse {label}: {errors}")
    _scalars, mappings, block = parsed
    if "permission" not in mappings:
        raise AssertionError(f"{label} has no permission mapping")
    return block


def _bash_rules(contents: str, label: str) -> dict[str, str]:
    block = _permission_block(contents, label)
    rules: dict[str, str] = {}
    in_bash = False
    for line in block.splitlines():
        if line == "  bash:":
            in_bash = True
            continue
        if in_bash and line.startswith("  ") and not line.startswith("    "):
            break
        if not in_bash or not line.startswith("    "):
            continue
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        if len(key) >= 2 and key[0] == key[-1] == '"':
            key = key[1:-1].replace('\\"', '"')
        rules[key] = value.strip()
    return rules


def _opencode_wildcard_match(value: str, pattern: str) -> bool:
    """Mirror OpenCode 1.18.29 core wildcard matching for these rules.

    OpenCode 1.18.29 uses ``findLast`` over permission rules and converts ``*``
    and ``?`` into an anchored full-string regular expression. The tests keep
    the host's trailing-space wildcard exception as well.
    """
    normalized_value = value.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    regex = "".join(
        (f"\\{character}" if character in ".+^${}()|[]" else character)
        for character in normalized_pattern
    )
    regex = regex.replace("*", ".*").replace("?", ".")
    if regex.endswith(" .*"):
        regex = regex[:-3] + "( .*)?"
    flags = re.DOTALL | (re.IGNORECASE if os.name == "nt" else 0)
    return re.fullmatch(regex, normalized_value, flags=flags) is not None


def _opencode_resolve_bash_action(rules: dict[str, str], command: str) -> str:
    """Resolve a command as OpenCode's full-match, last-rule-wins evaluator."""
    matches = [
        action
        for pattern, action in rules.items()
        if _opencode_wildcard_match(command, pattern)
    ]
    return matches[-1] if matches else "ask"


class OpencodePermissionContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        if (ROOT / ".agents").is_dir():
            shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "plugins", temporary / "plugins")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        shutil.copy2(ROOT / "README.md", temporary / "README.md")
        # A sibling lane may have a local npm install in the shared checkout;
        # generated dependency documentation is outside this contract.
        node_modules = temporary / "plugins" / "expskill" / "opencode" / "node_modules"
        if node_modules.exists():
            shutil.rmtree(node_modules)
        return temporary

    def test_read_only_profiles_have_bounded_git_rules_in_source_and_render(self) -> None:
        spec = json.loads((OPENCODE_ROOT / "agents.json").read_text(encoding="utf-8"))
        rendered = render_opencode_agents(ROOT)
        for name in READ_ONLY_AGENTS:
            with self.subTest(agent=name):
                source_rules = spec["agents"][name]["permission"]["bash"]
                generated_rules = _bash_rules(rendered[name], f"generated {name}")
                self.assertEqual(list(source_rules.items()), list(generated_rules.items()))
                self.assertEqual(source_rules.get("*"), "deny")
                self.assertEqual(
                    {key: source_rules.get(key) for key in SAFE_INSPECTION_RULES},
                    SAFE_INSPECTION_RULES,
                )
                self.assertEqual(
                    {key: source_rules.get(key) for key in OUTPUT_GUARD_RULES},
                    {key: "deny" for key in OUTPUT_GUARD_RULES},
                )
                self.assertTrue(
                    all(
                        action != "allow" or key == "*" or key in SAFE_INSPECTION_RULES
                        for key, action in source_rules.items()
                    ),
                    source_rules,
                )
                self.assertFalse(
                    any(
                        key in UNSAFE_WILDCARD_RULES
                        for key in source_rules
                    ),
                    source_rules,
                )

    def test_read_only_profiles_do_not_allow_destructive_or_output_commands(self) -> None:
        rendered = render_opencode_agents(ROOT)
        for name in READ_ONLY_AGENTS:
            with self.subTest(agent=name):
                rules = _bash_rules(rendered[name], f"generated {name}")
                self.assertEqual(rules["*"], "deny")
                for command in SAFE_INSPECTION_COMMANDS:
                    self.assertEqual(
                        _opencode_resolve_bash_action(rules, command),
                        "allow",
                        f"{name} must allow {command!r}",
                    )
                for command in UNSAFE_COMMANDS:
                    self.assertEqual(
                        _opencode_resolve_bash_action(rules, command),
                        "deny",
                        f"{name} must not allow {command!r}",
                    )

    def test_open_code_last_matching_rule_makes_guard_order_security_critical(self) -> None:
        rendered = render_opencode_agents(ROOT)
        rules = _bash_rules(rendered[READ_ONLY_AGENTS[0]], "generated read-only agent")
        guarded_command = (
            "git --no-pager diff --no-ext-diff --no-textconv --no-renames "
            "--end-of-options --output=/tmp/changes.patch"
        )
        self.assertEqual(_opencode_resolve_bash_action(rules, guarded_command), "deny")

        guard_first = dict(
            [
                (key, value)
                for key, value in rules.items()
                if key.startswith("git * --")
            ]
            + [
                (key, value)
                for key, value in rules.items()
                if not key.startswith("git * --")
            ]
        )
        self.assertEqual(_opencode_resolve_bash_action(guard_first, guarded_command), "allow")

    def test_validation_rejects_reordered_read_only_permission_rules(self) -> None:
        root = self.copy_repository()
        spec_path = root / "plugins" / "expskill" / "opencode" / "agents.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for name in READ_ONLY_AGENTS:
            rules = spec["agents"][name]["permission"]["bash"]
            reordered = {
                key: value
                for key, value in reversed(list(rules.items()))
            }
            spec["agents"][name]["permission"]["bash"] = reordered
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_main=False, include_opencode=True)
        self.assertTrue(
            any("read-only Git permission" in error and "order" in error for error in errors),
            errors,
        )

    def test_required_git_inspection_forms_still_execute_successfully(self) -> None:
        commands = (
            ("git", "status"),
            ("git", "status", "--short"),
            ("git", "status", "--porcelain=v1"),
            ("git", "branch", "--show-current"),
            (
                "git",
                "--no-pager",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--end-of-options",
                "HEAD",
            ),
            (
                "git",
                "--no-pager",
                "log",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--end-of-options",
                "HEAD",
            ),
            (
                "git",
                "--no-pager",
                "show",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--end-of-options",
                "HEAD",
            ),
        )
        for command in commands:
            with self.subTest(command=" ".join(command)):
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_validation_rejects_unsafe_read_only_policy_after_regeneration(self) -> None:
        root = self.copy_repository()
        spec_path = root / "plugins" / "expskill" / "opencode" / "agents.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for name in READ_ONLY_AGENTS:
            for rule in UNSAFE_WILDCARD_RULES:
                spec["agents"][name]["permission"]["bash"][rule] = "allow"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        errors = validate_repository(root, include_main=False, include_opencode=True)
        self.assertTrue(
            any("read-only Git permission" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
