from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.sync_opencode_agents import sync as sync_opencode_agents
from scripts.sync_opencode_agents import render_all as render_opencode_agents
from scripts.validate import _parse_overlay_frontmatter, validate_repository


ROOT = Path(__file__).resolve().parents[1]
OPENCODE_ROOT = ROOT / "packages" / "opencode"
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


class OpencodePermissionContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        shutil.copytree(ROOT / ".agents", temporary / ".agents")
        shutil.copytree(ROOT / "packages", temporary / "packages")
        shutil.copytree(ROOT / "scripts", temporary / "scripts")
        shutil.copy2(ROOT / "README.md", temporary / "README.md")
        # A sibling lane may have a local npm install in the shared checkout;
        # generated dependency documentation is outside this contract.
        node_modules = temporary / "packages" / "opencode" / "node_modules"
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
                self.assertEqual(source_rules, generated_rules)
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
                for command in UNSAFE_COMMANDS:
                    self.assertNotEqual(
                        rules.get(command),
                        "allow",
                        f"{name} must not allow {command!r}",
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
        spec_path = root / "packages" / "opencode" / "agents.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for name in READ_ONLY_AGENTS:
            for rule in UNSAFE_WILDCARD_RULES:
                spec["agents"][name]["permission"]["bash"][rule] = "allow"
        spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        sync_opencode_agents(root)
        errors = validate_repository(root)
        self.assertTrue(
            any("read-only Git permission" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
