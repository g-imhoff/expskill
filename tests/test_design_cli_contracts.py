import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins/expskill/content/scripts/design_state.py"
IDENTITY = "g-imhoff <152416066+g-imhoff@users.noreply.github.com>"


def load_helper():
    spec = importlib.util.spec_from_file_location("design_cli_contracts", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnreadableInput:
    def read(self, *args):
        raise AssertionError("help read operation input")


class DesignCliContractsTests(unittest.TestCase):
    def invoke(self, *arguments, payload=None, env=None):
        return subprocess.run(
            [sys.executable, "-B", str(HELPER), *arguments],
            input=json.dumps(payload) if payload is not None else "malformed operation input",
            text=True, capture_output=True, env=env,
        )

    def test_focused_help_and_schema_descriptions_never_open_state_or_input(self):
        module = load_helper()
        arguments = [("describe",), ("describe", "initialize"), ("initialize", "--help"),
                     ("describe-schema",)]
        arguments.extend(("describe-schema", name) for name in module._schema_definitions())
        with patch.object(module, "_root", side_effect=AssertionError("help opened state")), \
             patch.object(module, "_git", side_effect=AssertionError("help inspected target")), \
             patch.object(module.sys, "stdin", UnreadableInput()), \
             patch.object(module.Path, "home", side_effect=AssertionError("help resolved state home")):
            for args in arguments:
                with self.subTest(args=args), patch.object(module.sys, "argv", [str(HELPER), *args]), \
                     patch.object(module.sys, "stdout", io.StringIO()) as output:
                    self.assertEqual(module._cli(), 0)
                    self.assertIsInstance(json.loads(output.getvalue()), dict)

    def test_descriptions_follow_the_shared_dispatch_signature(self):
        module = load_helper()
        result = self.invoke("describe")
        self.assertEqual(result.returncode, 0, result.stderr)
        operations = json.loads(result.stdout)["operations"]
        self.assertEqual(set(operations), set(module._cli_operations()))
        for operation in operations:
            with self.subTest(operation=operation):
                result = self.invoke("describe", operation)
                self.assertEqual(result.returncode, 0, result.stderr)
                contract = json.loads(result.stdout)
                self.assertNotIn("state_home", contract["required_fields"])
                self.assertNotIn("state_home", contract["optional_fields"])
                self.assertEqual(contract["operation"], operation)
        initialize = json.loads(self.invoke("initialize", "--help").stdout)
        self.assertIn("ui_contract", initialize["required_fields"])
        self.assertEqual(initialize["optional_fields"], {"invocation_mode": "direct"})
        self.assertEqual(initialize["schemas"]["ui-contract"]["required_fields"], ["digest"])
        self.assertEqual(initialize["schemas"]["ui-contract"]["optional_fields"], ["outcome"])

    def test_unknown_or_malformed_descriptions_fail_without_creating_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "absent-state"
            env = dict(os.environ, XDG_STATE_HOME=str(state))
            for args in (("describe", "unknown"), ("describe-schema", "unknown"),
                         ("describe", "initialize", "extra"), ("initialize", "--state-home")):
                with self.subTest(args=args):
                    result = self.invoke(*args, env=env)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(state.exists())

    def test_public_initialization_and_brief_accept_described_fields_and_reject_guesses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "feature/design", str(repo)], check=True)
            for key, value in (("user.name", "g-imhoff"),
                               ("user.email", "152416066+g-imhoff@users.noreply.github.com")):
                subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
            (repo / "brief.txt").write_text("Retain the native preference control.\n")
            subprocess.run(["git", "-C", str(repo), "add", "brief.txt"], check=True)
            for role in ("AUTHOR", "COMMITTER"):
                value = subprocess.check_output(["git", "-C", str(repo), "var", "GIT_" + role + "_IDENT"], text=True)
                self.assertTrue(value.startswith(IDENTITY + " "), value)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "Create isolated Design contract fixture"], check=True)
            value = subprocess.check_output(["git", "-C", str(repo), "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD"], text=True).strip()
            self.assertEqual(value, IDENTITY + " | " + IDENTITY)
            head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            home = root / "state"
            payload = {"repository": str(repo), "branch": "feature/design", "worktree": str(repo),
                       "baseline": head, "dirty_fingerprint": hashlib.sha256(b"").hexdigest(),
                       "ui_contract": {"digest": "a" * 64, "requirements": ["Preserve the native control"]},
                       "scope": {"owned_paths": ["index.html"]}}
            rejected = self.invoke("initialize", "--state-home", str(home), payload=payload)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("invalid ui contract", rejected.stderr)
            self.assertEqual([p for p in (home / "design").glob("*") if len(p.name) == 32], [])
            ui = json.loads(self.invoke("describe-schema", "ui-contract").stdout)
            payload["ui_contract"] = {ui["required_fields"][0]: "a" * 64, "outcome": "Preserve the native control"}
            initialized = self.invoke("initialize", "--state-home", str(home), payload=payload)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            receipt = json.loads(initialized.stdout)
            brief = {"objective": "Retain the native preference control", "requirements": ["Preserve native behavior"],
                     "responsive_expectations": {width: "No horizontal overflow" for width in ("compact", "intermediate", "wide")},
                     "non_goals": ["Backend changes"], "source": {"kind": "specification", "path": "brief.txt",
                     "digest": hashlib.sha256((repo / "brief.txt").read_bytes()).hexdigest()}}
            description = json.loads(self.invoke("describe-schema", "brief").stdout)
            self.assertEqual(set(brief), set(description["required_fields"]))
            confirmation = {"workflow_id": receipt["workflow_id"], "expected_revision": receipt["revision"],
                            "brief": brief, "confirmed": True}
            invalid = self.invoke("confirm-brief", "--state-home", str(home), payload={**confirmation, "brief": {**brief, "digest": "b" * 64}})
            self.assertEqual(invalid.returncode, 1)
            confirmed = self.invoke("confirm-brief", "--state-home", str(home), payload=confirmation)
            self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
            self.assertEqual(json.loads(confirmed.stdout)["revision"], receipt["revision"] + 1)
            loaded = self.invoke("load", "--state-home", str(home), payload={"workflow_id": receipt["workflow_id"]})
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(json.loads(loaded.stdout)["revision"], receipt["revision"] + 1)
