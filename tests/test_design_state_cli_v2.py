from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.design_state_test_support import passing_technical


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "plugins" / "expskill" / "content" / "scripts" / "design_state.py"
DIGEST = "a" * 64
BASE_RECEIPT_KEYS = {"revision"}
INIT_KEYS = {"workflow_id", "revision"}
DISCOVER_KEYS = {"workflow_id", "revision", "lifecycle"}
LOAD_KEYS = {"revision", "lifecycle"}
RECOVER_KEYS = {"revision", "lifecycle"}
DISCARD_KEYS: set[str] = set()
DELIVERY_RECEIPT_KEYS = {"schema_version", "operation", "workflow_id", "revision", "lifecycle", "identity", "state_digest", "candidate_digest", "candidate_inventory_digest", "review_evidence_digest", "manifest_digest", "evidence_digest", "approval_digest", "dependency_digest", "brief_digest", "candidate_commit"}
DELIVERY_UNBRIEFED_KEYS = DELIVERY_RECEIPT_KEYS - {"brief_digest", "candidate_commit"}


def _repo(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "fixture"], check=True)
    subprocess.run(["git", "-C", str(repo), "switch", "--quiet", "-c", "feature/design"], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return repo, head


class DesignStateCliContractV2Tests(unittest.TestCase):
    def _json(self, result: subprocess.CompletedProcess[str], label: str) -> dict:
        self.assertTrue(result.stdout.strip(), f"{label} emitted no JSON: {result.stderr}")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"{label} emitted invalid JSON: {error}")
        self.assertIsInstance(value, dict, label)
        return value

    def _receipt(self, result: subprocess.CompletedProcess[str], label: str, keys: set[str] = BASE_RECEIPT_KEYS) -> dict:
        value = self._json(result, label)
        self.assertEqual(set(value), keys, label)
        return value

    def _state_snapshot(self, root: Path) -> dict[str, bytes]:
        if not root.exists():
            return {}
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    def _repo_snapshot(self, repo: Path) -> str:
        return subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"], text=True)

    _SCALAR_COMMANDS = ("load", "pause", "resume", "recover", "discard", "preflight")

    def _run(self, command: str, payload: object | None, env: dict[str, str], *flags: str, raw: str | None = None) -> subprocess.CompletedProcess[str]:
        argv: list[str] = [command]
        body: str | None = None
        if raw is not None:
            body = raw
        elif isinstance(payload, dict) and command == "initialize":
            ui_contract = payload.get("ui_contract", {})
            scope = payload.get("scope", {})
            argv += ["--contract-digest", str(ui_contract.get("digest")),
                     "--contract-outcome", str(ui_contract.get("outcome", ""))]
            argv += ["--components", *[str(item) for item in scope.get("components", [])]]
            argv += ["--exclusions", *[str(item) for item in scope.get("exclusions", [])]]
            for key in payload:
                if key not in ("ui_contract", "scope", "invocation_mode", "state_home",
                               "repository", "branch", "worktree", "baseline", "dirty_fingerprint"):
                    argv.append(f"--{key}")
            if payload.get("invocation_mode") == "routed":
                argv.append("--routed")
        elif isinstance(payload, dict) and command in self._SCALAR_COMMANDS:
            if command == "preflight":
                argv.append(str(payload["workflow_id"]))
                if payload.get("plan_baseline") is not None:
                    argv += ["--plan-baseline", str(payload["plan_baseline"])]
                if payload.get("plan_target_branch") is not None:
                    argv += ["--plan-target-branch", str(payload["plan_target_branch"])]
            elif command == "discard":
                argv += [str(payload["workflow_id"]), str(payload["expected_revision"])]
                if payload.get("confirmed"):
                    argv.append("--confirmed")
            elif command in ("pause", "resume"):
                argv += [str(payload["workflow_id"]), str(payload["expected_revision"])]
            else:
                argv.append(str(payload["workflow_id"]))
        elif payload is not None:
            body = json.dumps(payload)
        result = subprocess.run(
            [sys.executable, str(CLI), *argv, *flags],
            input=body,
            text=True,
            capture_output=True,
            env=env,
            cwd=(payload.get("repository") if isinstance(payload, dict) and command == "initialize" and payload.get("repository") else None),
        )
        decoy = env.get("XDG_STATE_HOME")
        if decoy:
            self.assertFalse(Path(decoy).exists(), f"XDG decoy must stay untouched: {decoy}")
        return result

    def _payload(self, repo: Path, head: str) -> dict:
        return {
            "repository": str(repo),
            "branch": "feature/design",
            "worktree": str(repo),
            "baseline": head,
            "dirty_fingerprint": hashlib.sha256(b"").hexdigest(),
            "ui_contract": {"digest": DIGEST, "outcome": "checkout"},
            "scope": {"components": ["CheckoutForm"], "exclusions": ["route"]},
        }

    def _records(self) -> dict:
        records = {
            "selected_rules": {"responsive": {"id": "responsive", "reason": "actual pressure"}},
            "seed_permission": {"source": "project-owned", "version": "current", "approved": True},
            "questions": {},
            "delivery": {"classifications": ["component", "review", "manifest"]},
            "components": {"CheckoutForm": {"id": "CheckoutForm", "code_digest": DIGEST, "contract_digest": DIGEST, "evidence_ids": ["E1"], "dependency_ids": ["D1"], "approval_id": "A1"}},
            "dependencies": {"D1": {"id": "D1", "digest": DIGEST, "component_ids": ["CheckoutForm"]}},
            "evidence": {"E1": {"id": "E1", "component_id": "CheckoutForm", "digest": DIGEST, "code_digest": DIGEST, "contract_digest": DIGEST, "widths": ["compact", "intermediate", "wide"], "themes": ["light", "dark"], "states": ["default", "error", "loading"], "technical": passing_technical(DIGEST, "component-check")}},
            "approvals": {"A1": {"id": "A1", "component_id": "CheckoutForm", "code_digest": DIGEST, "contract_digest": DIGEST, "evidence_ids": ["E1"], "dependency_ids": ["D1"], "decision": "approved"}},
        }
        def inventory_digest(value: dict) -> str:
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        candidate, review, manifest = self._layers()
        records["delivery"] = {
            "classifications": {"candidate": "component", "review": "review", "manifest": "manifest"},
            "candidate": {"inventory_digest": inventory_digest(candidate), "files": candidate["files"]},
            "review": {"inventory_digest": inventory_digest(review), "files": review["files"]},
            "manifest": {"inventory_digest": inventory_digest(manifest), "files": manifest["files"]},
        }
        return records

    def _layers(self) -> tuple[dict, dict, dict]:
        return (
            {"files": [{"path": "CheckoutForm.tsx", "digest": DIGEST, "classification": "component"}]},
            {"files": [{"path": "review.json", "digest": DIGEST, "classification": "review"}]},
            {"files": [{"path": "manifest.json", "digest": DIGEST, "classification": "manifest"}]},
        )

    def _initialize(self, env: dict[str, str], repo: Path, head: str, *flags: str) -> dict:
        result = self._run("initialize", self._payload(repo, head), env, *flags)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = self._receipt(result, "initialize", INIT_KEYS)
        return receipt

    def test_cli_is_present_closed_and_exposes_every_lifecycle_operation(self) -> None:
        """Regression: absent or permissive CLI prevents deterministic external validation."""
        self.assertTrue(CLI.is_file(), CLI)
        with tempfile.TemporaryDirectory() as temporary:
            fixture_repo, _ = _repo(Path(temporary) / "fixture")
            env = os.environ.copy()
            env["HOME"] = str(Path(temporary) / "home")
            env["XDG_STATE_HOME"] = str(Path(env["HOME"]).parent / "xdg-decoy")
            before_state = self._state_snapshot(Path(env["HOME"]) / ".expskill")
            before_repo = self._repo_snapshot(fixture_repo)
            result = self._run("--help", None, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            for command in ("initialize", "discover", "load", "apply", "pause", "resume", "recover", "discard", "deliver"):
                self.assertIn(command, result.stdout)
            for command, payload, flags, raw in (
                ("unknown", {}, (), None),
                ("initialize", {}, ("--unknown",), None),
                ("initialize", [], (), None),
                ("initialize", None, (), "{}\n{}\n"),
                ("initialize", None, (), "x" * (1024 * 1024 + 1)),
            ):
                with self.subTest(command=command, flags=flags):
                    rejected = self._run(command, payload, env, *flags, raw=raw)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertEqual(self._state_snapshot(Path(env["HOME"]) / ".expskill"), before_state)
                    self.assertEqual(self._repo_snapshot(fixture_repo), before_repo)

    def test_cli_initialize_uses_normal_home_root_and_strict_json_input_output(self) -> None:
        """Regression: state must be discoverable under ~/.expskill and reject unknown JSON fields."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy()
            home = root / "home"
            env["HOME"] = str(home)
            env["XDG_STATE_HOME"] = str(Path(env["HOME"]).parent / "xdg-decoy")
            state_root = home / ".expskill"
            payload = self._payload(repo, head)
            result = self._run("initialize", payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = self._receipt(result, "initialize", INIT_KEYS)
            self.assertRegex(receipt["workflow_id"], r"^[0-9a-f]{32}$")
            self.assertTrue((state_root / "design").is_dir())
            unknown = dict(payload)
            unknown["unexpected"] = True
            before_state = self._state_snapshot(state_root)
            before_repo = self._repo_snapshot(repo)
            rejected = self._run("initialize", unknown, env)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(self._state_snapshot(state_root), before_state)
            self.assertEqual(self._repo_snapshot(repo), before_repo)

    def test_cli_initialize_discover_load_apply_pause_resume_are_successful_strict_receipts(self) -> None:
        """Regression: the core lifecycle must be executable through closed JSON receipts."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy()
            env["HOME"] = str(root / "home")
            env["XDG_STATE_HOME"] = str(Path(env["HOME"]).parent / "xdg-decoy")
            receipt = self._initialize(env, repo, head)
            workflow = receipt["workflow_id"]
            discovered = self._run("discover", {"repository": str(repo), "branch": "feature/design"}, env)
            self.assertEqual(discovered.returncode, 0, discovered.stderr)
            self._receipt(discovered, "discover", DISCOVER_KEYS)
            loaded = self._run("load", {"workflow_id": workflow}, env)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self._receipt(loaded, "load", LOAD_KEYS)
            applied = self._run("apply", {"workflow_id": workflow, "expected_revision": receipt["revision"], "updates": self._records()}, env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_receipt = self._receipt(applied, "apply", BASE_RECEIPT_KEYS)
            paused = self._run("pause", {"workflow_id": workflow, "expected_revision": applied_receipt["revision"]}, env)
            self.assertEqual(paused.returncode, 0, paused.stderr)
            paused_receipt = self._receipt(paused, "pause", BASE_RECEIPT_KEYS)
            resumed = self._run("resume", {"workflow_id": workflow, "expected_revision": paused_receipt["revision"]}, env)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self._receipt(resumed, "resume", BASE_RECEIPT_KEYS)

    def test_cli_confirmed_discard_is_separate_and_durable(self) -> None:
        """Regression: discard must require explicit authority and return a durable terminal receipt."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy(); env["HOME"] = str(root / "home"); env["XDG_STATE_HOME"] = str(Path(env["HOME"]).parent / "xdg-decoy")
            receipt = self._initialize(env, repo, head)
            rejected = self._run("discard", {"workflow_id": receipt["workflow_id"], "expected_revision": receipt["revision"], "confirmed": False}, env)
            self.assertNotEqual(rejected.returncode, 0)
            discarded = self._run("discard", {"workflow_id": receipt["workflow_id"], "expected_revision": receipt["revision"], "confirmed": True}, env)
            self.assertEqual(discarded.returncode, 0, discarded.stderr)
            self._receipt(discarded, "discard", DISCARD_KEYS)

    def test_cli_recovery_after_current_fault_and_full_delivery_are_successful(self) -> None:
        """Regression: recovery and delivery must be real closed operations, not labels in prose."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy(); env["HOME"] = str(root / "home"); env["XDG_STATE_HOME"] = str(Path(env["HOME"]).parent / "xdg-decoy")
            receipt = self._initialize(env, repo, head)
            workflow = receipt["workflow_id"]
            applied = self._run("apply", {"workflow_id": workflow, "expected_revision": receipt["revision"], "updates": {"scope": {"components": ["CheckoutForm"]}}}, env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_receipt = self._receipt(applied, "apply", BASE_RECEIPT_KEYS)
            current = Path(env["HOME"]) / ".expskill" / "design" / workflow
            current.write_text("{torn", encoding="utf-8")
            recovered = self._run("recover", {"workflow_id": workflow}, env)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self._receipt(recovered, "recover", RECOVER_KEYS)

            delivery_repo, delivery_head = _repo(root / "delivery-repo")
            delivery_env = os.environ.copy(); delivery_env["HOME"] = str(root / "delivery-home"); delivery_env["XDG_STATE_HOME"] = str(Path(env["HOME"]).parent / "xdg-decoy")
            delivery_receipt = self._initialize(delivery_env, delivery_repo, delivery_head)
            workflow = delivery_receipt["workflow_id"]
            applied = self._run("apply", {"workflow_id": workflow, "expected_revision": delivery_receipt["revision"], "updates": self._records()}, delivery_env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_receipt = self._receipt(applied, "delivery apply")
            candidate, review, manifest = self._layers()
            delivered = self._run("deliver", {"workflow_id": workflow, "expected_revision": applied_receipt["revision"], "candidate_payload": candidate, "review_evidence": review, "manifest": manifest}, delivery_env)
            self.assertEqual(delivered.returncode, 0, delivered.stderr)
            result = self._receipt(delivered, "deliver", DELIVERY_UNBRIEFED_KEYS)
            self.assertEqual(result["lifecycle"], "delivered")


if __name__ == "__main__":
    unittest.main()
