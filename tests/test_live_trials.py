from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from scripts import live_trials as trials


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "tests/fixtures/live/frozen-live-cases-v2.json"


def events(thread="offline-thread", message="actual result", *, command=False):
    rows = [{"type": "thread.started", "thread_id": thread}]
    if command:
        rows.append({"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1}})
    rows.append({"type": "item.completed", "item": {"type": "agent_message", "text": message}})
    return "\n".join(json.dumps(row) for row in rows) + "\n"


class LiveActorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        version = mock.patch.object(trials, "runtime_version", return_value="offline-scripted-runtime")
        version.start()
        self.addCleanup(version.stop)

    def actor(self, **kwargs):
        return trials.run_actor(prompt="Inspect this fixture.", cwd=self.root, state_home=self.root / "state",
            evidence_dir=self.root / "evidence", **kwargs)

    def test_default_requires_opt_in_before_any_process(self):
        with mock.patch.object(trials, "run_process_group") as process:
            with self.assertRaisesRegex(RuntimeError, "explicit opt-in"):
                self.actor()
            process.assert_not_called()

    def test_exit_zero_without_thread_and_message_is_not_success(self):
        with mock.patch.object(trials, "run_process_group", return_value={"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}):
            actor = self.actor(live=True)
        self.assertEqual(actor["outcome"], "protocol-invalid")
        self.assertEqual(len(actor["attempts"]), 1)

    def test_empty_thread_identity_is_not_a_fresh_actor(self):
        with mock.patch.object(trials, "run_process_group", return_value={"stdout": events(thread=""), "stderr": "", "exit_code": 0, "timed_out": False}):
            actor = self.actor(live=True)
        self.assertEqual(actor["outcome"], "protocol-invalid")
        self.assertEqual(actor["thread_ids"], [])

    def test_completed_transport_remains_ungraded(self):
        with mock.patch.object(trials, "run_process_group", return_value={"stdout": events(), "stderr": "", "exit_code": 0, "timed_out": False}):
            actor = self.actor(live=True, sandbox="workspace-write")
        self.assertEqual(actor["outcome"], "completed-ungraded")
        self.assertEqual(actor["thread_ids"], ["offline-thread"])
        self.assertEqual(actor["final_text"], "actual result")
        self.assertEqual(trials.digest_file(Path(actor["attempts"][0]["raw_events"])), actor["attempts"][0]["raw_events_sha256"])

    def test_requested_runtime_model_and_version_are_retained(self):
        with mock.patch.object(trials, "run_process_group", return_value={"stdout": events(), "stderr": "", "exit_code": 0, "timed_out": False}):
            actor = self.actor(live=True, model="configured-model")
        self.assertEqual(actor["argv"][:4], ["codex", "exec", "--model", "configured-model"])
        self.assertEqual(actor["runtime"]["cli_version"], "offline-scripted-runtime")
        self.assertEqual(actor["runtime"]["requested_model"], "configured-model")
        self.assertIn("started_at", actor["attempts"][0])

    def test_infrastructure_retry_retains_original_and_retry_raw_evidence(self):
        first = {"stdout": json.dumps({"type": "error", "message": "HTTP 502 upstream failed"}) + "\n", "stderr": "", "exit_code": 1, "timed_out": False}
        second = {"stdout": events("offline-retry"), "stderr": "", "exit_code": 0, "timed_out": False}
        with mock.patch.object(trials, "run_process_group", side_effect=[first, second]) as process:
            actor = self.actor(live=True)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(actor["selected_attempt"], 2)
        self.assertEqual(actor["attempts"][0]["outcome"], "infrastructure-error")
        self.assertIn("HTTP 502", Path(actor["attempts"][0]["raw_events"]).read_text())
        self.assertEqual(actor["attempts"][1]["thread_ids"], ["offline-retry"])

    def test_model_capacity_retry_retains_failed_attempt(self):
        first = {"stdout": json.dumps({"type": "turn.failed", "error": {"message": "selected model is at capacity"}}), "stderr": "", "exit_code": 1, "timed_out": False}
        second = {"stdout": events("offline-capacity-retry"), "stderr": "", "exit_code": 0, "timed_out": False}
        with mock.patch.object(trials, "run_process_group", side_effect=[first, second]):
            actor = self.actor(live=True)
        self.assertEqual(len(actor["attempts"]), 2)
        self.assertEqual(actor["attempts"][0]["outcome"], "infrastructure-error")
        self.assertIn("at capacity", Path(actor["attempts"][0]["raw_events"]).read_text())

    def test_behavior_failure_and_malformed_final_reply_are_never_retried(self):
        for name, stdout, stderr, exit_code in (("behavior", events(command=True), "HTTP 502 from product check", 1),
            ("malformed", events(message="{malformed"), "", 0)):
            with self.subTest(name=name), mock.patch.object(trials, "run_process_group", return_value={"stdout": stdout, "stderr": stderr, "exit_code": exit_code, "timed_out": False}) as process:
                actor = trials.run_actor(prompt="Review.", cwd=self.root, state_home=self.root / "state",
                    evidence_dir=self.root / name, live=True)
                self.assertEqual(process.call_count, 1)
                self.assertNotEqual(actor["outcome"], "infrastructure-error")

    def test_timeout_kills_descendants_that_ignore_term(self):
        program = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
        result = trials.run_process_group([sys.executable, "-c", program], prompt="", cwd=self.root, env=os.environ.copy(), timeout=0.3)
        self.assertTrue(result["timed_out"])
        child = int(result["stdout"].strip())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = Path(f"/proc/{child}/stat")
            try:
                status = state.read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                break
            if status == "Z":
                break
            time.sleep(0.02)
        else:
            self.fail("timeout left a running child process")

    def test_interruption_cleans_up_process_group_before_propagating(self):
        process = mock.Mock(pid=3344)
        process.communicate.side_effect = [KeyboardInterrupt, ("", "")]
        with mock.patch.object(trials.subprocess, "Popen", return_value=process), mock.patch.object(trials.os, "killpg") as kill:
            with self.assertRaises(KeyboardInterrupt):
                trials.run_process_group(["offline"], prompt="", cwd=self.root, env={}, timeout=1)
        kill.assert_called_once_with(3344, trials.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_escaped_session_cannot_hold_output_drain_open_forever(self):
        program = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); print(p.pid,flush=True); print('partial evidence',flush=True); time.sleep(60)"
        started = time.monotonic()
        result = trials.run_process_group([sys.executable, "-c", program], prompt="", cwd=self.root, env=os.environ.copy(), timeout=0.3)
        child = int(result["stdout"].splitlines()[0])
        try:
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(result["timed_out"])
            self.assertTrue(result["output_drain_limited"])
            self.assertIn("partial evidence", result["stdout"])
            self.assertEqual(result["escaped_session_termination"], "not-claimed")
            os.kill(child, 0)
        finally:
            try:
                os.killpg(child, trials.signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_global_child_limit_is_two_even_with_four_callers(self):
        log = self.root / "activity.jsonl"
        program = "import json,os,time; p=os.environ['TRIAL_ACTIVITY_LOG']; f=open(p,'a'); f.write(json.dumps([time.monotonic_ns(),1])+'\\n'); f.flush(); time.sleep(.15); f.write(json.dumps([time.monotonic_ns(),-1])+'\\n'); f.close()"
        def child(_):
            return trials.run_process_group([sys.executable, "-c", program], prompt="", cwd=self.root,
                env={**os.environ, "TRIAL_ACTIVITY_LOG": str(log)}, timeout=3)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(child, range(6)))
        self.assertTrue(all(result["exit_code"] == 0 for result in results))
        active = 0
        maximum = 0
        for _, delta in sorted(json.loads(line) for line in log.read_text().splitlines()):
            active += delta
            maximum = max(maximum, active)
        self.assertEqual(maximum, 2)
        self.assertEqual(active, 0)


class ReviewLoopTrialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case_root = self.root / "case"
        self.target = self.case_root / "repository"
        self.cases = {case["id"]: case for case in json.loads(PACK.read_text())["cases"]}
        self.case = self.cases["review-loop-real-repair-success"]
        trials.fixture(self.target, self.case)
        self.lock = threading.Lock()
        self.actor_count = 0
        self.prompts = []

    def fake_actor(self, *, prompt, cwd, state_home, evidence_dir, **kwargs):
        with self.lock:
            self.actor_count += 1
            thread = f"offline-scripted-actor-{self.actor_count}"
            self.prompts.append(prompt)
        evidence_dir.mkdir(parents=True)
        cycle = int(evidence_dir.parent.name.split("-")[1])
        role = evidence_dir.name
        pin = trials.git(self.target, "rev-parse", "HEAD")
        checks = trials.check_fixture(self.target)
        if role == "fixer":
            if "FALSE_VALUE" in prompt.split("in this correction turn")[0]:
                self.target.joinpath("app.py").write_text("def parse_bool(value):\n    if value == 'false':\n        return False\n    return bool(value)\n")
            else:
                self.target.joinpath("app.py").write_text("def parse_bool(value):\n    if value == 'false':\n        return False\n    if value == 'true':\n        return True\n    raise ValueError(value)\n")
            payload = {"actions": "offline scripted fixture repair"}
        elif role == "coordinator":
            open_ids = [criterion for criterion, result in checks.items() if result["exit_code"]]
            ledger = [{"id": criterion, "category": "input-semantics", "severity": "High", "discovery_pin": "offline-initial-pin", "status": "open" if criterion in open_ids else "fixed", "evidence": criterion, "verification": checks[criterion]} for criterion in ("FALSE_VALUE", "INVALID_VALUES")]
            payload = {"pin": pin, "cycle": cycle, "ledger": ledger, "scores": {}, "reason": "offline scripted coordinator",
                "decision": "continue" if open_ids and cycle < 3 else "bounded-stop" if open_ids else "pass"}
        else:
            findings = [{"id": criterion, "severity": "High", "evidence": "app.py:parse_bool", "impact": "accepted behavior fails", "correction": "repair criterion"} for criterion, result in checks.items() if result["exit_code"]] if role == "input-semantics" else []
            payload = {"category": role, "score": 4 if findings else 9, "reason": "offline scripted review", "findings": findings,
                "coverage": "fixture checks", "top_issue": findings[0]["id"] if findings else "", "evidence": pin}
        final_text = json.dumps(payload)
        trials.write(evidence_dir / "events.jsonl", events(thread, final_text))
        actor = {"outcome": "completed-ungraded", "thread_ids": [thread], "final_text": final_text,
            "attempts": [{"thread_ids": [thread], "raw_events": str(evidence_dir / "events.jsonl")}], "evidence": str(evidence_dir / "actor.json")}
        trials.write(evidence_dir / "actor.json", json.dumps(actor))
        return actor

    def run_loop(self, case):
        with mock.patch.object(trials, "run_actor", side_effect=self.fake_actor):
            return trials.run_review_loop(case, content=ROOT / "plugins/expskill/content", target=self.target,
                state=self.case_root / "state", case_root=self.case_root, timeout=1, live=True)

    def test_review_loop_entry_requires_explicit_live_opt_in(self):
        with mock.patch.object(trials, "run_actor") as actor:
            with self.assertRaisesRegex(RuntimeError, "explicit opt-in"):
                trials.run_review_loop(self.case, content=ROOT / "plugins/expskill/content", target=self.target,
                    state=self.case_root / "state", case_root=self.case_root, timeout=1)
        actor.assert_not_called()

    def test_success_has_three_pins_two_distinct_repairs_and_retained_ledgers(self):
        result = self.run_loop(self.case)
        self.assertEqual(self.actor_count, 11)
        self.assertEqual(len(result["cycles"]), 3)
        self.assertEqual(len({row["pin"] for row in result["cycles"]}), 3)
        self.assertTrue(all(row["fresh_actor_identities"] for row in result["cycles"]))
        self.assertNotEqual(result["cycles"][0]["repair"]["checks"]["INVALID_VALUES"]["exit_code"], 0)
        self.assertTrue(all(check["exit_code"] == 0 for check in result["final_checks"].values()))
        self.assertTrue(result["independent_grading_required"])
        self.assertTrue(all(Path(row["coordinator_ledger"]).is_file() for row in result["cycles"]))
        reviewer_prompts = [prompt for prompt in self.prompts if "independent read-only reviewer" in prompt]
        self.assertEqual(len(reviewer_prompts), 6)
        self.assertTrue(all("SHA-256" in prompt and "accepted-criteria.json" in prompt for prompt in reviewer_prompts))
        self.assertTrue(all("bounded-stop" not in prompt and "score 9" not in prompt for prompt in reviewer_prompts))

    def test_combined_fault_stop_preserves_original_delivery_and_open_blocker(self):
        result = self.run_loop(self.cases["review-loop-retained-blocker-stop"])
        self.assertEqual(self.actor_count, 10)
        self.assertEqual(len(result["cycles"]), 3)
        self.assertNotEqual(result["final_checks"]["INVALID_VALUES"]["exit_code"], 0)
        second = result["cycles"][1]
        self.assertEqual(second["deliveries"]["input-semantics"]["fault_label"], "declared-omission-injection")
        self.assertIn("INVALID_VALUES", second["deliveries"]["input-semantics"]["original_final_text"])
        self.assertEqual(json.loads(second["deliveries"]["input-semantics"]["delivered_text"])["findings"], [])
        stale = json.loads(second["deliveries"]["scope-and-proof"]["delivered_text"])
        self.assertNotEqual(stale["pin"], second["pin"])
        third = result["cycles"][2]
        self.assertEqual(set(json.loads(third["deliveries"]["scope-and-proof"]["delivered_text"])), {"category", "score"})
        self.assertEqual(third["coordinator_payload"]["decision"], "bounded-stop")
        self.assertTrue(any(item["id"] == "INVALID_VALUES" and item["status"] == "open" for item in third["coordinator_payload"]["ledger"]))

    def test_premature_coordinator_pass_cannot_become_benchmark_success(self):
        original = self.fake_actor
        def premature(**kwargs):
            actor = original(**kwargs)
            if kwargs["evidence_dir"].name == "coordinator":
                actor["final_text"] = json.dumps({"decision": "pass", "ledger": []})
                trials.write(Path(actor["attempts"][0]["raw_events"]), events(actor["thread_ids"][0], actor["final_text"]))
                trials.write(Path(actor["evidence"]), json.dumps(actor))
            return actor
        with mock.patch.object(trials, "run_actor", side_effect=premature):
            result = trials.run_review_loop(self.case, content=ROOT / "plugins/expskill/content", target=self.target,
                state=self.case_root / "state", case_root=self.case_root, timeout=1, live=True)
        self.assertEqual(len(result["cycles"]), 1)
        self.assertTrue(result["independent_grading_required"])
        self.assertNotEqual(result["final_checks"]["INVALID_VALUES"]["exit_code"], 0)
        self.assertNotIn("pass", result)

    def test_default_bank_preparation_launches_no_actor(self):
        output = self.root / "prepared"
        with mock.patch.object(trials, "run_actor") as actor:
            rows = trials.run(ROOT, trials.git(ROOT, "rev-parse", "HEAD"), PACK,
                Path(os.path.relpath(output, Path.cwd())), {"bounded-correct"}, 1)
        actor.assert_not_called()
        self.assertEqual(rows[0]["outcome"], "prepared")
        metadata = json.loads((output / "results.json").read_text())
        self.assertFalse(metadata["live_opt_in"])
        self.assertEqual(metadata["cases"][0]["exposure"], "development-validation-known-to-implementers")
        self.assertTrue(Path(rows[0]["case_root"]).is_absolute())
        self.assertNotIn("bounded-correct", rows[0]["case_root"])


if __name__ == "__main__":
    unittest.main()
