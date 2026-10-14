from __future__ import annotations

import importlib.util
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


HELPER = Path(__file__).resolve().parents[1] / "plugins/expskill/content/scripts/research_budget.py"


class ResearchBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        subprocess.run(["git", "init", "-b", "feature/research"], cwd=self.repository, check=True, capture_output=True)
        self.state_home = self.root / "private"
        self.assertTrue(HELPER.is_file(), "durable research helper is missing")
        spec = importlib.util.spec_from_file_location("research_budget_candidate", HELPER)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.identity = (self.repository, "feature/research", "run-1")
        self.clock = mock.patch.object(self.helper, "_now_ms", return_value=1000)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def initialize(self, **options):
        return self.helper.initialize(*self.identity, state_home=self.state_home, **options)

    def load(self, locator):
        return self.helper.load(*self.identity, locator=locator)

    def apply(self, state, operation):
        return self.helper.apply(*self.identity, state["revision"], operation, locator=state["locator"])

    def test_interruption_and_replacement_restore_spent_outstanding_and_union_time(self) -> None:
        state = self.initialize()
        state = self.apply(state, {"op": "reserve", "dispatch_id": "first", "question": "Resolve protocol semantics"})
        self.now.return_value = 2000
        state = self.apply(state, {"op": "reserve", "dispatch_id": "second", "question": "Check contrary primary evidence"})
        self.now.return_value = 3000
        state = self.apply(state, {"op": "interrupt", "dispatch_id": "first"})
        self.now.return_value = 6000
        restored = self.load(state["locator"])
        self.assertEqual(restored["spent_turns"], 2)
        self.assertEqual(restored["outstanding_dispatch_ids"], ["first", "second"])
        self.assertEqual(restored["active_seconds"], 5)
        self.assertEqual(restored["remaining"]["turns"], 4)
        self.assertEqual(restored["remaining"]["in_flight"], 1)
        self.assertEqual(restored["locator"], state["locator"])
        self.assertEqual(self.initialize()["spent_turns"], 2)

    def test_unique_dispatch_and_reconcile_do_not_double_count_or_refund(self) -> None:
        state = self.initialize()
        state = self.apply(state, {"op": "reserve", "dispatch_id": "one", "question": "Named external investigation"})
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(state, {"op": "reserve", "dispatch_id": "one", "question": "Repeat"})
        self.now.return_value = 4000
        operation = {"op": "reconcile", "dispatch_id": "one", "outcome": "failed", "evidence_reference": "receipt:one"}
        state = self.apply(state, operation)
        repeated = self.apply(state, operation)
        self.assertEqual(repeated["revision"], state["revision"])
        self.assertEqual(repeated["spent_turns"], 1)
        self.assertEqual(repeated["outstanding_dispatch_ids"], [])
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(state, dict(operation, outcome="completed"))
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.helper.apply(*self.identity, 0, {"op": "interrupt", "dispatch_id": "one"}, locator=state["locator"])

    def test_turn_and_concurrency_limits_survive_reopen_and_authorized_extension(self) -> None:
        state = self.initialize(limits={"turns": 2, "active_seconds": 10, "in_flight": 1}, limit_source="parent:bounded-gap")
        state = self.apply(state, {"op": "reserve", "dispatch_id": "one", "question": "First gap"})
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(state, {"op": "reserve", "dispatch_id": "two", "question": "Second gap"})
        for dispatch_id in ("one", "two"):
            if dispatch_id == "two":
                state = self.apply(state, {"op": "reserve", "dispatch_id": dispatch_id, "question": "Second gap"})
            state = self.apply(state, {"op": "reconcile", "dispatch_id": dispatch_id, "outcome": "completed", "evidence_reference": "receipt:" + dispatch_id})
        state = self.load(state["locator"])
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(state, {"op": "reserve", "dispatch_id": "three", "question": "Named missing gap"})
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.initialize(limits={"turns": 3, "active_seconds": 10, "in_flight": 1}, limit_source="reset")
        state = self.apply(state, {"op": "extend", "limits": {"turns": 3, "active_seconds": 10, "in_flight": 1}, "authorization_reference": "user:one-named-gap"})
        self.assertEqual(state["spent_turns"], 2)
        self.assertEqual(state["remaining"]["turns"], 1)
        self.assertEqual(state["limit_source"], "parent:bounded-gap")
        state = self.apply(state, {"op": "reserve", "dispatch_id": "three", "question": "Named missing gap"})
        self.assertEqual(state["spent_turns"], 3)

    def test_active_time_exhaustion_and_pure_user_wait(self) -> None:
        state = self.initialize(limits={"turns": 6, "active_seconds": 2, "in_flight": 3})
        state = self.apply(state, {"op": "reserve", "dispatch_id": "one", "question": "Resolve fact"})
        self.now.return_value = 4000
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(self.load(state["locator"]), {"op": "reserve", "dispatch_id": "two", "question": "Further fact"})
        state = self.apply(state, {"op": "reconcile", "dispatch_id": "one", "outcome": "completed", "evidence_reference": "receipt:ended-before-user-wait", "ended_at_ms": 2000})
        state = self.apply(state, {"op": "activity-start"})
        self.now.return_value = 4500
        state = self.apply(state, {"op": "activity-stop"})
        self.now.return_value = 100000
        state = self.load(state["locator"])
        self.assertEqual(state["active_seconds"], 1.5)
        self.assertEqual(state["remaining"]["active_seconds"], 0.5)

    def test_locator_identity_and_private_storage_reject_substitution(self) -> None:
        state = self.initialize()
        locator = Path(state["locator"])
        self.assertFalse(locator.is_relative_to(self.repository))
        self.assertEqual(locator.stat().st_mode & 0o777, 0o600)
        self.assertEqual(locator.parent.stat().st_mode & 0o777, 0o700)
        for branch, run_id in (("feature/other", "run-1"), ("feature/research", "run-2")):
            with self.assertRaises(self.helper.ResearchBudgetError):
                self.helper.load(self.repository, branch, run_id, locator=locator)
        raw = locator.read_bytes()
        locator.unlink()
        target = self.root / "substitute.json"
        target.write_bytes(raw)
        target.chmod(0o600)
        locator.symlink_to(target)
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.load(locator)

    def test_unsafe_roots_corrupt_state_and_clock_rollback_never_reset(self) -> None:
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.helper.initialize(*self.identity, state_home=self.repository / "private")
        state = self.initialize()
        state = self.apply(state, {"op": "reserve", "dispatch_id": "one", "question": "Named gap"})
        self.now.return_value = 999
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.load(state["locator"])
        self.now.return_value = 2000
        locator = Path(state["locator"])
        value = json.loads(locator.read_text())
        value["revision"] = True
        locator.write_text(json.dumps(value))
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.load(locator)
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.initialize()

    def test_cli_reopen_retains_state_and_uses_returned_locator(self) -> None:
        arguments = [sys.executable, str(HELPER), "initialize", "--repo", str(self.repository), "--branch", "feature/research", "--run-id", "cli-run", "--state-home", str(self.state_home)]
        created = subprocess.run(arguments, check=True, capture_output=True, text=True)
        state = json.loads(created.stdout)
        operation = {"op": "reserve", "dispatch_id": "cli-one", "question": "Named CLI gap"}
        changed = subprocess.run([sys.executable, str(HELPER), "apply", "--repo", str(self.repository), "--branch", "feature/research", "--run-id", "cli-run", "--locator", state["locator"], "--revision", str(state["revision"])], input=json.dumps(operation), check=True, capture_output=True, text=True)
        state = json.loads(changed.stdout)
        restored = subprocess.run([sys.executable, str(HELPER), "load", "--repo", str(self.repository), "--branch", "feature/research", "--run-id", "cli-run", "--locator", state["locator"]], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(restored.stdout)["spent_turns"], 1)
        self.assertEqual(json.loads(restored.stdout)["outstanding_dispatch_ids"], ["cli-one"])

    def test_protected_current_branch_and_detached_checkout_allow_read_only_accounting(self) -> None:
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.repository, check=True)
        state = self.helper.initialize(self.repository, "main", "main-run", state_home=self.state_home)
        state = self.helper.apply(self.repository, "main", "main-run", state["revision"], {"op": "reserve", "dispatch_id": "main-one", "question": "Early fact lookup"}, locator=state["locator"])
        self.assertEqual(state["identity"]["branch"], "main")
        self.assertEqual(state["spent_turns"], 1)
        empty_tree = subprocess.check_output(["git", "mktree"], cwd=self.repository, input=b"").strip().decode()
        environment = {**os.environ, "GIT_AUTHOR_NAME": "g-imhoff", "GIT_AUTHOR_EMAIL": "152416066+g-imhoff@users.noreply.github.com", "GIT_COMMITTER_NAME": "g-imhoff", "GIT_COMMITTER_EMAIL": "152416066+g-imhoff@users.noreply.github.com"}
        commit = subprocess.check_output(["git", "commit-tree", empty_tree, "-m", "fixture"], cwd=self.repository, env=environment).strip().decode()
        subprocess.run(["git", "update-ref", "--no-deref", "HEAD", commit], cwd=self.repository, check=True)
        self.assertEqual(subprocess.run(["git", "symbolic-ref", "--quiet", "HEAD"], cwd=self.repository).returncode, 1)
        restored = self.helper.load(self.repository, "main", "main-run", locator=state["locator"])
        self.assertEqual(restored["spent_turns"], 1)
        self.assertEqual(subprocess.run(["git", "symbolic-ref", "--quiet", "HEAD"], cwd=self.repository).returncode, 1)

    def test_non_repository_concept_retains_its_original_identity_across_phases(self) -> None:
        digest = "a" * 64
        state = self.helper.initialize(None, None, "concept-run", scope_digest=digest, state_home=self.state_home)
        state = self.helper.apply(None, None, "concept-run", state["revision"], {"op": "reserve", "dispatch_id": "concept-one", "question": "Named primary-source gap"}, scope_digest=digest, locator=state["locator"])
        restored = self.helper.load(None, None, "concept-run", scope_digest=digest, locator=state["locator"])
        self.assertEqual(restored["spent_turns"], 1)
        self.assertEqual(restored["identity"]["scope_digest"], digest)
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.helper.load(None, None, "concept-run", scope_digest="b" * 64, locator=state["locator"])

    def test_competing_replacement_writers_cannot_spend_the_same_revision(self) -> None:
        state = self.initialize()
        command = [sys.executable, str(HELPER), "apply", "--repo", str(self.repository), "--branch", "feature/research", "--run-id", "run-1", "--locator", state["locator"], "--revision", "0"]
        def reserve(index):
            return subprocess.run(command, input=json.dumps({"op": "reserve", "dispatch_id": f"competing-{index}", "question": "One contested gap"}), capture_output=True, text=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(reserve, range(6)))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1, [result.stderr for result in results])
        self.now.return_value = time.time_ns() // 1_000_000
        restored = self.load(state["locator"])
        self.assertEqual(restored["spent_turns"], 1)
        self.assertEqual(restored["revision"], 1)

    def test_actor_crash_after_reservation_restores_unknown_capacity_in_fresh_process(self) -> None:
        state = self.initialize()
        command = [sys.executable, str(HELPER), "apply", "--repo", str(self.repository), "--branch", "feature/research", "--run-id", "run-1", "--locator", state["locator"], "--revision", "0"]
        script = self.root / "interrupted_actor.py"
        script.write_text("""import json
import os
import signal
import subprocess
import sys
operation = {'op': 'reserve', 'dispatch_id': 'crashed', 'question': 'One interrupted primary-source gap'}
completed = subprocess.run(sys.argv[1:], input=json.dumps(operation), capture_output=True, text=True, check=True)
print(completed.stdout, flush=True)
os.kill(os.getpid(), signal.SIGKILL)
""")
        crashed = subprocess.run([sys.executable, str(script), *command], capture_output=True, text=True)
        self.assertEqual(crashed.returncode, -9, crashed.stderr)
        restored = subprocess.run([sys.executable, str(HELPER), "load", "--repo", str(self.repository), "--branch", "feature/research", "--run-id", "run-1", "--locator", state["locator"]], check=True, capture_output=True, text=True)
        receipt = json.loads(restored.stdout)
        self.assertEqual(receipt["spent_turns"], 1)
        self.assertEqual(receipt["outstanding_dispatch_ids"], ["crashed"])
        self.assertEqual(receipt["remaining"]["turns"], 5)
        self.assertEqual(receipt["remaining"]["in_flight"], 2)
        self.assertGreaterEqual(receipt["active_seconds"], 0)

    def test_branch_switch_and_fresh_discovery_restore_original_pool_without_locator(self) -> None:
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.repository, check=True)
        state = self.helper.initialize(self.repository, "main", "inherited-run", state_home=self.state_home)
        state = self.helper.apply(self.repository, "main", "inherited-run", 0, {"op": "reserve", "dispatch_id": "inherited-one", "question": "Earlier unresolved primary-source gap"}, locator=state["locator"])
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/feature/replacement"], cwd=self.repository, check=True)
        discovered = subprocess.run([sys.executable, str(HELPER), "discover", "--repo", str(self.repository), "--state-home", str(self.state_home)], check=True, capture_output=True, text=True)
        result = json.loads(discovered.stdout)
        self.assertEqual(result["status"], "found")
        self.assertEqual(len(result["pools"]), 1)
        pool = result["pools"][0]
        self.assertEqual(pool["locator"], state["locator"])
        self.assertEqual(pool["identity"]["branch"], "main")
        self.assertEqual(pool["spent_turns"], 1)
        self.assertEqual(pool["outstanding_dispatch_ids"], ["inherited-one"])
        self.assertEqual(pool["remaining"]["turns"], 5)
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.helper.initialize(self.repository, "feature/replacement", "inherited-run", state_home=self.state_home)
        self.assertEqual(subprocess.check_output(["git", "symbolic-ref", "--short", "HEAD"], cwd=self.repository, text=True).strip(), "feature/replacement")

    def test_discovery_blocks_ambiguity_and_closure_never_resets_spent_capacity(self) -> None:
        first = self.initialize()
        second = self.helper.initialize(self.repository, "main", "run-2", state_home=self.state_home)
        result = self.helper.discover(self.repository, state_home=self.state_home)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(len(result["pools"]), 2)
        only_first = self.helper.discover(self.repository, run_id="run-1", state_home=self.state_home)
        self.assertEqual(only_first["status"], "found")
        first = self.apply(first, {"op": "reserve", "dispatch_id": "unfinished", "question": "Named outstanding work"})
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(first, {"op": "close", "completion_reference": "ended:workflow"})
        first = self.apply(first, {"op": "reconcile", "dispatch_id": "unfinished", "outcome": "cancelled", "evidence_reference": "cancelled:known-stopped"})
        first = self.apply(first, {"op": "close", "completion_reference": "ended:workflow"})
        self.assertEqual(self.load(first["locator"])["spent_turns"], 1)
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.apply(first, {"op": "reserve", "dispatch_id": "restart", "question": "Reset attempt"})
        result = self.helper.discover(self.repository, state_home=self.state_home)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["pools"][0]["locator"], second["locator"])

    def test_discovery_reports_absence_and_refuses_corrupt_catalog_without_resetting(self) -> None:
        self.assertEqual(self.helper.discover(self.repository, state_home=self.state_home), {"status": "none", "pools": []})
        state = self.initialize()
        Path(state["locator"]).write_text("{}")
        with self.assertRaises(self.helper.ResearchBudgetError):
            self.helper.discover(self.repository, state_home=self.state_home)

    def test_cli_imports_shared_storage_without_writing_package_bytecode(self) -> None:
        package = self.root / "read-only-package"
        package.mkdir()
        for name in ("research_budget.py", "plan_graph.py"):
            shutil.copyfile(HELPER.parent / name, package / name)
        environment = dict(os.environ)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        completed = subprocess.run([sys.executable, str(package / "research_budget.py"), "--help"], env=environment, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(sorted(path.name for path in package.iterdir()), ["plan_graph.py", "research_budget.py"])


if __name__ == "__main__":
    unittest.main()
