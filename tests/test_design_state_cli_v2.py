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
CLI = ROOT / "plugins" / "expskill" / "scripts" / "design_state.py"
DIGEST = "a" * 64
BASE_RECEIPT_KEYS = {"schema_version", "operation", "workflow_id", "revision", "lifecycle", "identity", "state_digest"}
DELIVERY_RECEIPT_KEYS = BASE_RECEIPT_KEYS | {"candidate_digest", "candidate_inventory_digest", "review_evidence_digest", "manifest_digest", "evidence_digest", "approval_digest", "dependency_digest"}


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

    def _state_snapshot(self, xdg: Path) -> dict[str, bytes]:
        if not xdg.exists():
            return {}
        return {path.relative_to(xdg).as_posix(): path.read_bytes() for path in xdg.rglob("*") if path.is_file()}

    def _repo_snapshot(self, repo: Path) -> str:
        return subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"], text=True)

    def _run(self, command: str, payload: object | None, env: dict[str, str], *flags: str, raw: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), command, *flags],
            input=raw if raw is not None else (None if payload is None else json.dumps(payload)),
            text=True,
            capture_output=True,
            env=env,
        )

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
        receipt = self._receipt(result, "initialize")
        return receipt

    def test_cli_is_present_closed_and_exposes_every_lifecycle_operation(self) -> None:
        """Regression: absent or permissive CLI prevents deterministic external validation."""
        self.assertTrue(CLI.is_file(), CLI)
        with tempfile.TemporaryDirectory() as temporary:
            fixture_repo, _ = _repo(Path(temporary) / "fixture")
            env = os.environ.copy()
            env["XDG_STATE_HOME"] = str(Path(temporary) / "xdg")
            before_state = self._state_snapshot(Path(env["XDG_STATE_HOME"]))
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
                    self.assertEqual(self._state_snapshot(Path(env["XDG_STATE_HOME"])), before_state)
                    self.assertEqual(self._repo_snapshot(fixture_repo), before_repo)

    def test_cli_initialize_uses_normal_xdg_root_and_strict_json_input_output(self) -> None:
        """Regression: state must be discoverable under XDG_STATE_HOME and reject unknown JSON fields."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy()
            xdg = root / "xdg"
            env["XDG_STATE_HOME"] = str(xdg)
            payload = self._payload(repo, head)
            result = self._run("initialize", payload, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = self._receipt(result, "initialize")
            self.assertRegex(receipt["workflow_id"], r"^[0-9a-f]{32}$")
            self.assertTrue((xdg / "expskill" / "design").is_dir())
            unknown = dict(payload)
            unknown["unexpected"] = True
            before_state = self._state_snapshot(xdg)
            before_repo = self._repo_snapshot(repo)
            rejected = self._run("initialize", unknown, env)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(self._state_snapshot(xdg), before_state)
            self.assertEqual(self._repo_snapshot(repo), before_repo)

    def test_cli_initialize_discover_load_apply_pause_resume_are_successful_strict_receipts(self) -> None:
        """Regression: the core lifecycle must be executable through closed JSON receipts."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy()
            env["XDG_STATE_HOME"] = str(root / "xdg")
            receipt = self._initialize(env, repo, head)
            workflow = receipt["workflow_id"]
            discovered = self._run("discover", {"repository": str(repo), "branch": "feature/design"}, env)
            self.assertEqual(discovered.returncode, 0, discovered.stderr)
            self._receipt(discovered, "discover")
            loaded = self._run("load", {"workflow_id": workflow}, env)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self._receipt(loaded, "load")
            applied = self._run("apply", {"workflow_id": workflow, "expected_revision": receipt["revision"], "updates": self._records()}, env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_receipt = self._receipt(applied, "apply")
            paused = self._run("pause", {"workflow_id": workflow, "expected_revision": applied_receipt["revision"]}, env)
            self.assertEqual(paused.returncode, 0, paused.stderr)
            paused_receipt = self._receipt(paused, "pause")
            resumed = self._run("resume", {"workflow_id": workflow, "expected_revision": paused_receipt["revision"]}, env)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self._receipt(resumed, "resume")

    def test_cli_confirmed_discard_is_separate_and_durable(self) -> None:
        """Regression: discard must require explicit authority and return a durable terminal receipt."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy(); env["XDG_STATE_HOME"] = str(root / "xdg")
            receipt = self._initialize(env, repo, head)
            rejected = self._run("discard", {"workflow_id": receipt["workflow_id"], "expected_revision": receipt["revision"], "confirmed": False}, env)
            self.assertNotEqual(rejected.returncode, 0)
            discarded = self._run("discard", {"workflow_id": receipt["workflow_id"], "expected_revision": receipt["revision"], "confirmed": True}, env)
            self.assertEqual(discarded.returncode, 0, discarded.stderr)
            self._receipt(discarded, "discard")

    def test_cli_recovery_after_current_fault_and_full_delivery_are_successful(self) -> None:
        """Regression: recovery and delivery must be real closed operations, not labels in prose."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, head = _repo(root)
            env = os.environ.copy(); env["XDG_STATE_HOME"] = str(root / "xdg")
            receipt = self._initialize(env, repo, head)
            workflow = receipt["workflow_id"]
            applied = self._run("apply", {"workflow_id": workflow, "expected_revision": receipt["revision"], "updates": {"scope": {"components": ["CheckoutForm"]}}}, env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_receipt = self._receipt(applied, "apply")
            current = Path(env["XDG_STATE_HOME"]) / "expskill" / "design" / workflow
            current.write_text("{torn", encoding="utf-8")
            recovered = self._run("recover", {"workflow_id": workflow}, env)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self._receipt(recovered, "recover")

            delivery_repo, delivery_head = _repo(root / "delivery-repo")
            delivery_env = os.environ.copy(); delivery_env["XDG_STATE_HOME"] = str(root / "delivery-xdg")
            delivery_receipt = self._initialize(delivery_env, delivery_repo, delivery_head)
            workflow = delivery_receipt["workflow_id"]
            applied = self._run("apply", {"workflow_id": workflow, "expected_revision": delivery_receipt["revision"], "updates": self._records()}, delivery_env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            applied_receipt = self._receipt(applied, "delivery apply")
            candidate, review, manifest = self._layers()
            delivered = self._run("deliver", {"workflow_id": workflow, "expected_revision": applied_receipt["revision"], "candidate_payload": candidate, "review_evidence": review, "manifest": manifest}, delivery_env)
            self.assertEqual(delivered.returncode, 0, delivered.stderr)
            result = self._receipt(delivered, "deliver", DELIVERY_RECEIPT_KEYS)
            self.assertEqual(result["lifecycle"], "delivered")


if __name__ == "__main__":
    unittest.main()
