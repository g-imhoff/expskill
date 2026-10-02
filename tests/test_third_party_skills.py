from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
SKILLS_ROOT = PLUGIN_ROOT / "content" / "skills"
HOOKS_PATH = PLUGIN_ROOT / "codex" / "hooks" / "hooks.json"
HOOK_SCRIPT = PLUGIN_ROOT / "codex" / "hooks" / "inject_unslop.py"
LOCK_PATH = PLUGIN_ROOT / "content" / "third-party" / "upstream-lock.json"

EXPECTED_PUBLIC_SKILLS = {
    "autonomous-run",
    "brainstorm",
    "design",
    "grill-me",
    "implement",
    "correct",
    "review",
    "review-loop",
    "plan",
    "setup-design",
    "setup-test",
    "skill-builder",
    "test",
    "unslop",
    "use-expskill",
}

EXPECTED_UPSTREAM = {
    "pstack-unslop": {
        "repository": "https://github.com/cursor/plugins",
        "revision": "93b00b89ef425a9c1bac0d0b317dfc49c930ac99",
        "source_path": "pstack/skills/unslop/SKILL.md",
        "vendored_path": "sources/pstack/unslop/SKILL.md",
        "sha256": "2789ab80477b7e382292e4d7acca1057784df19713fffb74622ff0f83b2f3733",
        "license_path": "licenses/pstack-MIT.txt",
        "license_sha256": "bc957ca6bee02792566a1a028d105e02e247c6e77cf057061674273da77b200e",
    },
    "mattpocock-grill-me": {
        "repository": "https://github.com/mattpocock/skills",
        "revision": "3cca18b368ae95cdbdebbff572ccafa662551015",
        "source_path": "skills/productivity/grill-me/SKILL.md",
        "vendored_path": "sources/mattpocock/grill-me/SKILL.md",
        "sha256": "caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637",
        "license_path": "licenses/mattpocock-skills-MIT.txt",
        "license_sha256": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    },
    "mattpocock-grilling": {
        "repository": "https://github.com/mattpocock/skills",
        "revision": "3cca18b368ae95cdbdebbff572ccafa662551015",
        "source_path": "skills/productivity/grilling/SKILL.md",
        "vendored_path": "sources/mattpocock/grilling/SKILL.md",
        "sha256": "10ff989e7498b23b5acb49d5048f11dcd906757d2f79c5cdf8a00001381296f2",
        "license_path": "licenses/mattpocock-skills-MIT.txt",
        "license_sha256": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    },
}


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"missing frontmatter: {path}")
    end = lines.index("---", 1)
    result: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"invalid frontmatter line: {line!r}")
        result[key.strip()] = value.strip().strip('"')
    return result


class ThirdPartySkillContractTests(unittest.TestCase):
    def test_public_roster_exposes_direct_unslop_and_one_grill_skill(self) -> None:
        observed = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
        self.assertEqual(observed, EXPECTED_PUBLIC_SKILLS)
        self.assertFalse((SKILLS_ROOT / "grilling").exists())

    def test_unslop_is_directly_invokable_but_not_implicitly_selected(self) -> None:
        root = SKILLS_ROOT / "unslop"
        frontmatter = _frontmatter(root / "SKILL.md")
        metadata = (PLUGIN_ROOT / "codex" / "skill-adapters" / "unslop" / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertEqual(frontmatter["name"], "unslop")
        self.assertIn('$unslop', metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_grill_me_is_directly_invokable_but_not_implicitly_selected(self) -> None:
        root = SKILLS_ROOT / "grill-me"
        frontmatter = _frontmatter(root / "SKILL.md")
        metadata = (PLUGIN_ROOT / "codex" / "skill-adapters" / "grill-me" / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertEqual(frontmatter["name"], "grill-me")
        self.assertIn('$grill-me', metadata)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_plugin_uses_only_a_root_session_start_hook_for_unslop(self) -> None:
        payload = json.loads(HOOKS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"description", "hooks"})
        self.assertEqual(set(payload["hooks"]), {"SessionStart"})
        groups = payload["hooks"]["SessionStart"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "^(startup|resume|clear|compact)$")
        handlers = groups[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]
        self.assertEqual(handler["type"], "command")
        self.assertIn("${PLUGIN_ROOT}/codex/hooks/inject_unslop.py", handler["command"])
        self.assertGreaterEqual(handler["additionalContextLimit"], 4000)
        self.assertFalse(handler.get("async", False))

    def test_session_start_hook_requests_loading_unslop_without_inlining_rules(self) -> None:
        event = {
            "session_id": "test-session",
            "transcript_path": None,
            "cwd": str(ROOT),
            "hook_event_name": "SessionStart",
            "source": "startup",
            "model": "test-model",
            "permission_mode": "default",
        }
        environment = dict(os.environ)
        environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "SessionStart")
        context = hook_output["additionalContext"]
        self.assertIn("Always load $expskill:unslop", context)
        self.assertIn("at the start of the conversation", context)
        self.assertIn("after each compaction", context)
        self.assertIn("before writing user-facing prose", context)
        self.assertNotIn("# Unslop", context)
        self.assertLess(len(context), 500)

    def test_unslop_hook_stays_silent_for_subagents_and_unknown_sources(self) -> None:
        environment = dict(os.environ)
        environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        events = (
            {"hook_event_name": "SubagentStart", "source": "startup"},
            {"hook_event_name": "SessionStart", "source": "unknown"},
        )
        for event in events:
            with self.subTest(event=event):
                result = subprocess.run(
                    [sys.executable, str(HOOK_SCRIPT)],
                    input=json.dumps(event),
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_unslop_hook_reinjects_for_every_supported_root_start_source(self) -> None:
        environment = dict(os.environ)
        environment["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        for source in ("startup", "resume", "clear", "compact"):
            with self.subTest(source=source):
                result = subprocess.run(
                    [sys.executable, str(HOOK_SCRIPT)],
                    input=json.dumps(
                        {"hook_event_name": "SessionStart", "source": source}
                    ),
                    text=True,
                    capture_output=True,
                    env=environment,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(
                    output["hookSpecificOutput"]["hookEventName"],
                    "SessionStart",
                )
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertIn("$expskill:unslop", context)
                self.assertIn("before writing user-facing prose", context)
                if source == "compact":
                    self.assertIn("After this compaction, reload", context)
                else:
                    self.assertIn("at the start of the conversation", context)
                self.assertNotIn("# Unslop", context)

    def test_upstream_snapshots_and_licenses_match_pinned_digests(self) -> None:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(lock["schema_version"], "third-party-sources.v1")
        self.assertEqual(lock["sources"], EXPECTED_UPSTREAM)
        third_party = LOCK_PATH.parent
        for name, source in EXPECTED_UPSTREAM.items():
            with self.subTest(source=name):
                vendored = third_party / source["vendored_path"]
                license_path = third_party / source["license_path"]
                self.assertEqual(hashlib.sha256(vendored.read_bytes()).hexdigest(), source["sha256"])
                self.assertEqual(
                    hashlib.sha256(license_path.read_bytes()).hexdigest(),
                    source["license_sha256"],
                )

    def test_public_unslop_preserves_upstream_with_first_party_scope(self) -> None:
        source = (
            PLUGIN_ROOT
            / "content"
            / "third-party"
            / "sources"
            / "pstack"
            / "unslop"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        expected = source.replace("disable-model-invocation: true\n", "", 1)
        expected = expected.replace(
            "## Process\n",
            "## Scope\n\n"
            "Apply these rules to natural-language user-facing prose you author, "
            "including commentary and final messages. Preserve code, commands, "
            "machine-readable data, logs, identifiers, API names, quotations, citations, "
            "source excerpts, approved copy, and project-required terminology exactly. "
            "Higher-priority instructions and explicit user formatting or tone choices win. "
            "Before sending user-facing prose, perform the included self-audit. "
            "Never create documentation files or add code comments unless the user asked for them.\n\n"
            "## Process\n",
            1,
        )
        actual = (SKILLS_ROOT / "unslop" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_public_grill_me_mechanically_merges_the_upstream_wrapper_and_engine(self) -> None:
        third_party = PLUGIN_ROOT / "content" / "third-party" / "sources" / "mattpocock"
        wrapper = (third_party / "grill-me" / "SKILL.md").read_text(encoding="utf-8")
        engine = (third_party / "grilling" / "SKILL.md").read_text(encoding="utf-8")
        wrapper_end = wrapper.index("\n---\n", 4) + len("\n---\n")
        engine_end = engine.index("\n---\n", 4) + len("\n---\n")
        frontmatter = wrapper[:wrapper_end].replace(
            "disable-model-invocation: true\n",
            "",
            1,
        )
        body = engine[engine_end:].lstrip("\n")
        body = body.replace("it; don't", "it. Don't").replace(
            "report; ask",
            "report. Ask",
        )
        expected = frontmatter + "\n" + body
        actual = (SKILLS_ROOT / "grill-me" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(actual, expected)

    def test_every_skill_owned_text_file_avoids_banned_punctuation(self) -> None:
        roots = (SKILLS_ROOT, ROOT / ".agents" / "skills")
        violations: list[str] = []
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    contents = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for character, label in (("\N{EM DASH}", "em dash"), (";", "semicolon")):
                    if character in contents:
                        violations.append(f"{path.relative_to(ROOT)}: {label}")
        self.assertEqual(violations, [])

    def test_use_expskill_only_uses_grill_me_at_a_user_decision_frontier(self) -> None:
        body = " ".join(
            (SKILLS_ROOT / "use-expskill" / "SKILL.md")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )
        for phrase in (
            "decision frontier",
            "facts are exhausted",
            "consequential",
            "connected",
            "only the user can decide",
            "use `$grill-me`",
            "confirmed shared understanding",
            "resume the owning skill",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)


if __name__ == "__main__":
    unittest.main()
