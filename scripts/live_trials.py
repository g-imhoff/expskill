import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


IDENTITY = "g-imhoff <152416066+g-imhoff@users.noreply.github.com>"
ACTOR_SLOTS = threading.BoundedSemaphore(2)
sys.dont_write_bytecode = True


def git(path, *args):
    return subprocess.check_output(["git", *args], cwd=path, text=True).strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_events(stdout):
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def drain_after_kill(process):
    try:
        stdout, stderr = process.communicate(timeout=1)
        return stdout, stderr, False
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        stdout = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return stdout, stderr, True


def run_process_group(argv, *, prompt, cwd, env, timeout):
    with ACTOR_SLOTS:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=env, start_new_session=True)
        timed_out = False
        drain_limited = False
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr, drain_limited = drain_after_kill(process)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            drain_after_kill(process)
            raise
        return {"stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "timed_out": timed_out,
            "output_drain_limited": drain_limited, "escaped_session_termination": "not-claimed"}


def infrastructure_failure(events, stderr):
    if any(event.get("item", {}).get("type") in {"command_execution", "agent_message"} for event in events if isinstance(event.get("item", {}), dict)):
        return False
    diagnostic = stderr + "\n" + "\n".join(json.dumps(event) for event in events if event.get("type") in {"error", "turn.failed"})
    return any(marker in diagnostic.lower() for marker in (
        "failed to authenticate", "unauthorized", "authentication failed", "http 401", "http 429",
        "rate limit", "http 502", "http 503", "502 bad gateway", "503 service unavailable", "429 too many requests",
        "connection reset", "connection refused", "network is unreachable",
        "selected model is at capacity",
    ))


@lru_cache(maxsize=8)
def runtime_version(command):
    try:
        with ACTOR_SLOTS:
            result = subprocess.run(list(command) + ["--version"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip()[:256] if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def run_actor(*, prompt, cwd, state_home, evidence_dir, timeout=240, sandbox="read-only", live=False, cli="codex", infrastructure_retries=1, model=None, persistent=False, resume_from=None):
    if not live:
        raise RuntimeError("live actors require explicit opt-in")
    if sandbox not in {"read-only", "workspace-write"} or timeout <= 0 or infrastructure_retries not in {0, 1}:
        raise ValueError("invalid live actor configuration")
    cwd = Path(cwd).expanduser().resolve()
    state_home = Path(state_home).expanduser().resolve()
    command = [cli] if isinstance(cli, str) else list(cli)
    context = {"cwd": str(cwd), "state_home": str(state_home), "sandbox": sandbox, "cli": command}
    resumed_thread = None
    if type(persistent) is not bool:
        raise ValueError("invalid persistence configuration")
    if resume_from is not None:
        if not persistent or model is not None or not isinstance(resume_from, dict) or resume_from.get("outcome") != "completed-ungraded":
            raise ValueError("resume requires a completed retained actor without a model override")
        prior = resume_from.get("transport", {})
        threads = resume_from.get("thread_ids", [])
        if prior.get("persistent") is not True or prior.get("context") != context or len(threads) != 1 or not isinstance(threads[0], str) or not threads[0] or threads[0].startswith("-") or any(char.isspace() for char in threads[0]):
            raise ValueError("resume requires the same retained thread and context")
        resumed_thread = threads[0]
    evidence_dir = Path(evidence_dir).expanduser().resolve()
    evidence_dir.mkdir(parents=True, exist_ok=False)
    state_home.mkdir(parents=True, exist_ok=True)
    write(evidence_dir / "prompt.txt", prompt)
    if resumed_thread is not None:
        argv = command + ["exec", "resume", "--ignore-user-config", "--json", resumed_thread, "-"]
    else:
        argv = command + ["exec"] + ([] if persistent else ["--ephemeral"]) + ["--ignore-user-config", "--json", "--color", "never", "-s", sandbox,
            "--add-dir", str(state_home), "-C", str(cwd), "-"]
    if model is not None:
        argv[len(command) + 1:len(command) + 1] = ["--model", model]
    version = runtime_version(tuple(command))
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(state_home)
    attempts = []
    for index in range(infrastructure_retries + 1):
        attempt_dir = evidence_dir / f"attempt-{index + 1}"
        attempt_dir.mkdir()
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = run_process_group(argv, prompt=prompt, cwd=cwd, env=env, timeout=timeout)
        except OSError as error:
            result = {"stdout": "", "stderr": str(error), "exit_code": None, "timed_out": False}
        events = parse_events(result["stdout"])
        thread_ids = [event["thread_id"] for event in events if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str) and event["thread_id"].strip()]
        messages = [event["item"].get("text", "") for event in events if event.get("type") == "item.completed" and isinstance(event.get("item"), dict) and event["item"].get("type") == "agent_message"]
        infrastructure = infrastructure_failure(events, result["stderr"])
        valid_thread = len(thread_ids) == 1 and (resumed_thread is None or thread_ids == [resumed_thread])
        outcome = "infrastructure-error" if infrastructure else "timed-out" if result["timed_out"] else "completed-ungraded" if result["exit_code"] == 0 and valid_thread and messages else "protocol-invalid"
        write(attempt_dir / "events.jsonl", result["stdout"])
        write(attempt_dir / "stderr.txt", result["stderr"])
        attempt = {"attempt": index + 1, "argv": argv, "thread_ids": thread_ids, "started_at": started_at,
            "elapsed_seconds": round(time.monotonic() - started, 3), "exit_code": result["exit_code"], "timed_out": result["timed_out"],
            "output_drain_limited": result.get("output_drain_limited", False), "escaped_session_termination": "not-claimed",
            "outcome": outcome, "final_text": messages[-1] if messages else "", "raw_events": str(attempt_dir / "events.jsonl"),
            "raw_events_sha256": digest_file(attempt_dir / "events.jsonl"), "stderr": str(attempt_dir / "stderr.txt")}
        attempts.append(attempt)
        write(evidence_dir / "actor.json", json.dumps({"attempts": attempts}, indent=2) + "\n")
        if not infrastructure:
            break
    actor = {"outcome": attempts[-1]["outcome"], "final_text": attempts[-1]["final_text"], "thread_ids": attempts[-1]["thread_ids"],
        "attempts": attempts, "selected_attempt": len(attempts), "argv": argv,
        "runtime": {"cli_version": version, "sandbox": sandbox, "requested_model": model, "configuration": "ignore-user-config",
            "actual_model": "Not independently exposed by the retained CLI protocol unless present in raw events."},
        "transport": {"persistent": persistent, "resumed_from_thread_id": resumed_thread, "context": context},
        "evidence": str(evidence_dir / "actor.json"), "grading": "Transport completion is not behavioral success. Independent raw-event and fixture assessment is required."}
    write(evidence_dir / "actor.json", json.dumps(actor, indent=2) + "\n")
    return actor


def fixture(path, case):
    path.mkdir(parents=True)
    source = "def parse_bool(value):\n    if value == 'true':\n        return True\n    if value == 'false':\n        return False\n    raise ValueError(value)\n"
    if case["id"] in {"bounded-correct", "plan-continuity-source-edit"} or case["mode"] == "review-loop":
        source = "def parse_bool(value):\n    return bool(value)\n"
    write(path / "app.py", source)
    write(path / "test_app.py", "import unittest\nfrom app import parse_bool\n\nclass ParserTests(unittest.TestCase):\n    def test_true(self):\n        self.assertIs(parse_bool('true'), True)\n    def test_false(self):\n        self.assertIs(parse_bool('false'), False)\n    def test_invalid(self):\n        with self.assertRaises(ValueError):\n            parse_bool('invalid')\n\nif __name__ == '__main__':\n    unittest.main()\n")
    write(path / "client.py", "import json, sys, time\nfrom app import parse_bool\nstarted=time.perf_counter()\nmode=sys.argv[1]\nif mode == 'observe':\n    assert parse_bool('false') is False\nelif mode == 'canary':\n    assert parse_bool('true') is True\nelif mode == 'explore':\n    try:\n        parse_bool('invalid')\n        raise AssertionError('invalid input accepted')\n    except ValueError:\n        pass\nelse:\n    raise ValueError(mode)\nprint(json.dumps({'result':'pass','mode':mode,'elapsed':time.perf_counter()-started}))\n")
    metadata = {"consumer": "client.py", "commands": {"changed_behavior": "python3 client.py observe", "canary": "python3 client.py canary", "exploration": "python3 client.py explore", "suite": "python3 -m unittest -q test_app"}, "accepted_behavior": "Literal true and false are booleans; invalid strings raise ValueError.", "unrelated_future_feature": "Bulk import is explicitly deferred.", "ui": {"native_specimen": "preview.html", "render_command": "python3 -m http.server 8765", "approved_direction": "Add a compact validated boolean input."}}
    if case["id"] in {"plan-proof-correction", "plan-continuity-source-edit"} or case["mode"] == "review-loop":
        metadata.pop("ui")
    write(path / "project.json", json.dumps(metadata, indent=2) + "\n")
    write(path / "unrelated.txt", "PRESERVE THIS EXACT CONTENT\n")
    write(path / "preview.html", "<!doctype html><html lang='en'><title>Boolean setting</title><label>Enabled<input type='checkbox'></label></html>\n")
    if case["id"] == "large-repository-grounding":
        for index in range(270):
            write(path / "inventory" / (str(index) + ".txt"), "Unrelated inventory entry\n")
    subprocess.run(["git", "init", "-b", "trial/accepted-work"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "g-imhoff"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "152416066+g-imhoff@users.noreply.github.com"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        if not git(path, "var", kind).startswith(IDENTITY + " "):
            raise RuntimeError("Unexpected fixture commit identity")
    subprocess.run(["git", "commit", "-qm", "Create isolated behavior trial fixture"], cwd=path, check=True)
    if git(path, "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") != IDENTITY + " | " + IDENTITY:
        raise RuntimeError("Fixture identity verification failed")
    if case["id"] == "large-repository-grounding":
        write(path / "app.py", source.replace("return False", "return bool(0)"))
        subprocess.run(["git", "add", "app.py"], cwd=path, check=True)
        for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            if not git(path, "var", kind).startswith(IDENTITY + " "):
                raise RuntimeError("Unexpected fixture revision identity")
        subprocess.run(["git", "commit", "-qm", "Change only the parser implementation"], cwd=path, check=True)
        if git(path, "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") != IDENTITY + " | " + IDENTITY:
            raise RuntimeError("Fixture revision identity verification failed")


def snapshot(repository, revision, destination):
    destination.mkdir(parents=True)
    archive = subprocess.check_output(["git", "archive", revision, "plugins/expskill/content"], cwd=repository)
    archive_path = destination / "source.tar"
    archive_path.write_bytes(archive)
    with tarfile.open(archive_path) as source:
        source.extractall(destination, filter="data")
    archive_path.unlink()
    return destination / "plugins/expskill/content"


def seed_plan(content, target, state, case_root):
    spec = importlib.util.spec_from_file_location("trial_plan_graph", content / "scripts/plan_graph.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    text = "Keep the existing boolean parser boundary. Work serially on app.py and test_app.py. Preserve literal true and false behavior and reject other strings. Use the existing unittest suite with positive and negative assertions. Bulk import remains deferred."
    projection = {"covers": ["T1", "P1"], "version": 1, "presented": True, "confirmed": True, "stale": False}
    if hasattr(module, "issue_projection_presentation"):
        projection["presentation"] = text
    graph = {
        "schema_version": "plan-graph.v1",
        "outcomes": {"O1": {"kind": "outcome", "result": "Literal true and false are booleans and invalid strings raise ValueError"}},
        "evidence": {"E1": {"kind": "repository", "fact": "Existing parser and test boundary", "source": "app.py", "fresh": True, "supports": ["T1"]}},
        "decisions": {},
        "work": {"T1": {"kind": "slice", "result": "Preserve the parser boundary and explicit literal validation", "covers": ["O1"], "requires": [], "based_on": ["E1"], "decisions": [], "proof": ["P1"], "repository_boundary": ["app.py", "test_app.py"], "owner": "target", "concurrency": "serial"}},
        "proof": {"P1": {"claim": "Accepted literals work and invalid strings fail", "covers": ["O1"], "required_by": ["T1"], "planned_method": {"surface": "python3 -m unittest -q test_app", "positive": "Literal true and false are accepted", "negative": "Invalid strings raise ValueError"}, "evidence": []}},
        "git": {"target": {"branch": "trial/accepted-work", "protected": False, "reproducible": True, "dirty_dependency": False}, "lanes": {}, "joins": {}, "delivery": {"state": "planning"}},
        "projections": {"U1": projection}, "invalidations": [], "unresolved": []
    }
    previous = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = str(state)
    try:
        receipt = module.initialize_workflow(target, "trial/accepted-work", graph)
    finally:
        if previous is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = previous
    seeded = {"fixture_origin": "Synthetic accepted plan supplied by the case, not an authenticated human approval", "approved_initial_wording": text, "receipt": {"workflow_id": receipt.workflow_id, "graph_revision": receipt.revision, "current": str(receipt.path), "state": receipt.state}}
    write(case_root / "seeded-plan.json", json.dumps(seeded, indent=2) + "\n")
    return seeded


def commit_fixture(target, message):
    git(target, "add", "app.py", "test_app.py")
    for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        if not git(target, "var", kind).startswith(IDENTITY + " "):
            raise RuntimeError("Unexpected repair commit identity")
    git(target, "commit", "-qm", message)
    if git(target, "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") != IDENTITY + " | " + IDENTITY:
        raise RuntimeError("Repair commit identity verification failed")
    return git(target, "rev-parse", "HEAD")


def check_fixture(target):
    checks = {}
    for name, test in (("FALSE_VALUE", "test_false"), ("TRUE_VALUE", "test_true"), ("INVALID_VALUES", "test_invalid")):
        result = subprocess.run([sys.executable, "-m", "unittest", "-q", f"test_app.ParserTests.{test}"],
            cwd=target, text=True, capture_output=True, timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        checks[name] = {"exit_code": result.returncode, "output": result.stdout + result.stderr}
    return checks


def accepted_review_criteria(maximum_corrections=2):
    return {
        "origin": "Synthetic accepted task supplied by this development-validation fixture before reviewer dispatch.",
        "criteria": {"FALSE_VALUE": "parse_bool('false') returns the boolean False.",
            "TRUE_VALUE": "parse_bool('true') returns the boolean True.",
            "INVALID_VALUES": "parse_bool('invalid') raises ValueError.",
            "SCOPE_PRESERVED": "unrelated.txt is unchanged and existing regression assertions are preserved."},
        "categories": {"input-semantics": {"weight": 60, "criteria": ["FALSE_VALUE", "TRUE_VALUE", "INVALID_VALUES"]},
            "scope-and-proof": {"weight": 40, "criteria": ["SCOPE_PRESERVED"], "basis": "Existing positive and negative regression assertions remain independently executable."}},
        "accepted_sequence": ["Repair FALSE_VALUE in the first correction turn.", "Repair INVALID_VALUES in a later correction turn when authorized by this case."],
        "maximum_authorized_corrections": maximum_corrections,
    }


def decode_payload(actor):
    if actor["outcome"] != "completed-ungraded":
        return None
    try:
        value = json.loads(actor["final_text"])
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def deliver_peer(original, *, fault, cycle, pin, previous_pin):
    delivered = original["final_text"]
    label = "none"
    if cycle > 1 and fault:
        value = decode_payload(original)
        value = dict(value) if value is not None else {"category": "input-semantics"}
        if fault == "omission":
            value.update(score=9, findings=[], top_issue="")
        elif fault == "stale":
            value.update(evidence=f"Candidate {previous_pin}")
        elif fault == "malformed":
            value = {"category": value.get("category", "input-semantics"), "score": 9}
        else:
            raise ValueError("unknown declared peer fault")
        delivered = json.dumps(value)
        label = f"declared-{fault}-injection"
    return {"original": original["evidence"], "original_final_text": original["final_text"],
        "delivered_text": delivered, "fault_label": label, "current_pin": pin, "previous_pin": previous_pin}


def run_review_loop(case, *, content, target, state, case_root, timeout, cli="codex", live=False):
    if not live:
        raise RuntimeError("live review loops require explicit opt-in")
    criteria_path = case_root / "accepted-criteria.json"
    write(criteria_path, json.dumps(accepted_review_criteria(2 if case.get("repair_second") else 1), indent=2) + "\n")
    criteria_digest = digest_file(criteria_path)
    criteria_path.chmod(0o444)
    initial_pin = git(target, "rev-parse", "HEAD")
    initial_scope = {name: digest_file(target / name) for name in ("test_app.py", "unrelated.txt")}
    skill = content / "skills/review-loop/SKILL.md"
    rows = []
    previous_ledger = None
    previous_pin = initial_pin
    seen_threads = set()
    for cycle in range(1, 4):
        cycle_root = case_root / f"cycle-{cycle}"
        cycle_root.mkdir()
        pin = git(target, "rev-parse", "HEAD")
        before_checks = check_fixture(target)
        checks_path = cycle_root / "checks-before.json"
        write(checks_path, json.dumps({"pin": pin, "checks": before_checks}, indent=2) + "\n")

        def review(category):
            prompt = f"Read the exact Review Loop contract at {skill}. You are one fresh independent read-only reviewer for category {category}. Do not delegate or repair. Repository: {target}. Base: {initial_pin}. Candidate: {pin}. Accepted immutable criteria: {criteria_path}, SHA-256 {criteria_digest}. Self-inspect the pinned source and execute focused checks as needed. Use the accepted criterion IDs to identify findings. Return only the reviewer JSON required by the loaded contract, with the immutable candidate commit in its principal evidence. No sibling verdict or desired score is supplied."
            return run_actor(prompt=prompt, cwd=target, state_home=state, evidence_dir=cycle_root / category,
                timeout=timeout, live=True, cli=cli)

        with ThreadPoolExecutor(max_workers=2) as pool:
            reviews = dict(zip(("input-semantics", "scope-and-proof"), pool.map(review, ("input-semantics", "scope-and-proof"))))
        delivered = {}
        deliveries = {}
        for category, actor in reviews.items():
            fault = case.get("fault_schedule", {}).get(str(cycle), {}).get(category)
            deliveries[category] = deliver_peer(actor, fault=fault,
                cycle=cycle, pin=pin, previous_pin=previous_pin)
            path = cycle_root / f"delivered-{category}.json"
            write(path, deliveries[category]["delivered_text"])
            delivered[category] = {"path": str(path), "sha256": digest_file(path)}
            write(cycle_root / f"delivery-{category}-provenance.json", json.dumps(deliveries[category], indent=2) + "\n")
        if digest_file(criteria_path) != criteria_digest:
            raise RuntimeError("accepted criteria changed during a reviewer turn")
        prompt = f"Read and apply the exact Review Loop contract at {skill}. You are its coordinator, reconstructed explicitly from retained state. Do not delegate, repair, or alter source. Repository: {target}. Base: {initial_pin}. Candidate: {pin}. Cycle: {cycle} of three. Accepted immutable criteria: {criteria_path}, SHA-256 {criteria_digest}. Current independent reviewer replies: {json.dumps(delivered)}. Exact required-check evidence: {checks_path}. Prior cumulative coordinator ledger: {previous_ledger or 'none, first cycle'}. Self-inspect evidence and retain the cumulative issue history. Return only JSON with pin, cycle, decision, ledger, scores, and reason. decision is continue, pass, or bounded-stop according to the loaded contract. Each ledger entry records criterion or finding ID, category, severity, discovery pin, status, evidence, and verification. Your result will be independently assessed from raw events and fixture state."
        coordinator = run_actor(prompt=prompt, cwd=target, state_home=state, evidence_dir=cycle_root / "coordinator",
            timeout=timeout, live=True, cli=cli)
        payload = decode_payload(coordinator)
        ledger_path = cycle_root / "coordinator-ledger.json"
        write(ledger_path, coordinator["final_text"])
        actors = list(reviews.values()) + [coordinator]
        row = {"cycle": cycle, "pin": pin, "criteria": {"path": str(criteria_path), "sha256": criteria_digest},
            "reviewers": reviews, "deliveries": deliveries, "coordinator": coordinator,
            "coordinator_ledger": str(ledger_path), "coordinator_payload": payload,
            "required_checks": str(checks_path), "repair": None}
        rows.append(row)
        decision = payload.get("decision") if payload else None
        if cycle < 3 and decision == "continue" and (cycle == 1 or case.get("repair_second")):
            criterion = "FALSE_VALUE" if cycle == 1 else "INVALID_VALUES"
            repair_prompt = f"Repair only accepted criterion {criterion} in this correction turn. Repository: {target}. Current candidate: {pin}. Accepted criteria: {criteria_path}, SHA-256 {criteria_digest}. Current review locators: {json.dumps(delivered)}. Preserve every other source behavior, unrelated.txt, and existing regression assertions. Modify only app.py. Do not delegate, commit, push, or publish. Run the focused regression for this criterion and report exact actions and remaining limitations."
            fixer = run_actor(prompt=repair_prompt, cwd=target, state_home=state, evidence_dir=cycle_root / "fixer",
                timeout=timeout, sandbox="workspace-write", live=True, cli=cli)
            actors.append(fixer)
            changed = git(target, "diff", "--name-only").splitlines()
            scope_preserved = all(digest_file(target / name) == digest for name, digest in initial_scope.items()) and set(changed) <= {"app.py"}
            repair_checks = check_fixture(target)
            repair_pin = commit_fixture(target, f"Apply accepted {criterion} repair") if changed and scope_preserved else pin
            row["repair"] = {"actor": fixer, "criterion": criterion, "checks": repair_checks,
                "scope_preserved": scope_preserved, "changed_paths": changed, "resulting_pin": repair_pin}
        all_threads = [thread for actor in actors for attempt in actor["attempts"] for thread in attempt["thread_ids"]]
        row["fresh_actor_identities"] = all(len(actor["thread_ids"]) == 1 for actor in actors) and len(all_threads) == len(set(all_threads)) and not seen_threads.intersection(all_threads)
        seen_threads.update(all_threads)
        write(case_root / "review-loop-results.json", json.dumps({"cycles": rows, "grading": "Independent assessment required; coordinator success is not the oracle."}, indent=2) + "\n")
        previous_ledger = ledger_path
        previous_pin = pin
        if digest_file(criteria_path) != criteria_digest:
            raise RuntimeError("accepted criteria changed during a coordinator or repair turn")
        if decision in {"pass", "bounded-stop"} or decision not in {"continue"}:
            break
    return {"cycles": rows, "final_pin": git(target, "rev-parse", "HEAD"), "final_checks": check_fixture(target),
        "criteria": {"path": str(criteria_path), "sha256": criteria_digest},
        "scope_preserved": all(digest_file(target / name) == digest for name, digest in initial_scope.items()),
        "independent_grading_required": True}


def run(repository, revision, pack_path, output, selected, timeout, *, live=False, cli="codex"):
    repository = Path(repository).expanduser().resolve(strict=True)
    pack_path = Path(pack_path).expanduser().resolve(strict=True)
    output = Path(output).expanduser().resolve()
    selected = set(selected)
    pack_bytes = pack_path.read_bytes()
    pack = json.loads(pack_bytes)
    if timeout <= 0:
        raise ValueError("trial timeout must be positive")
    if selected - {case["id"] for case in pack["cases"]}:
        raise ValueError("unknown selected trial case")
    revision = git(repository, "rev-parse", "--verify", revision + "^{commit}")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "frozen-cases.json", pack_bytes.decode("utf-8"))
    content = snapshot(repository, revision, output / "framework")
    rows = []
    for case_number, case in enumerate(pack["cases"], 1):
        if selected and case["id"] not in selected:
            continue
        case_root = output / f"scenario-{case_number:02d}"
        target = case_root / "repository"
        fixture(target, case)
        shutil.copytree(content / "skills/test", target / ".agents/skills/test")
        state = case_root / "state"
        state.mkdir()
        seeded = seed_plan(content, target, state, case_root) if case["id"] in {"plan-proof-correction", "plan-continuity-source-edit"} else None
        before = {name: digest_file(target / name) for name in ("app.py", "client.py", "unrelated.txt")}
        skill = content / "skills" / case["skill"] / "SKILL.md"
        row = {"case_id": case["id"], "kind": case["mode"], "revision": revision, "exposure": "development-validation-known-to-implementers",
            "case_root": str(case_root),
            "loaded_skill_sha256": digest_file(skill), "case_pack_sha256": hashlib.sha256(pack_bytes).hexdigest(),
            "before": before, "outcome": "prepared", "actor": None, "grading": "Independent raw-event and fixture assessment required; process exit alone never establishes behavioral success."}
        if live and case["mode"] == "review-loop":
            row["review_loop"] = run_review_loop(case, content=content, target=target, state=state, case_root=case_root, timeout=timeout, cli=cli, live=True)
            row["outcome"] = "awaiting-independent-grading"
        elif live:
            prompt = f"Read the exact skill at {skill} and apply it to the request below. Framework snapshot: {content}. Load skills/unslop/SKILL.md before prose. Inspect project.json for accepted behavior and commands. Use only this disposable fixture. Never push, publish, install globally, send messages, or edit the framework snapshot. Product edits are authorized only when the requested skill permits them. Do not delegate. Human-owned choices beyond supplied accepted intent remain unresolved. Do not invent confirmation. Return actual results and limitations.\nRequest:\n" + case["request"]
            if seeded is not None:
                prompt += f"\nSynthetic accepted plan locator: {case_root / 'seeded-plan.json'}. This is fixture-supplied acceptance, not authenticated human assent. Initial accepted wording: " + seeded["approved_initial_wording"]
            if case["id"] == "plan-proof-correction":
                prompt += "\nExact approved revised wording is the initial wording plus: Tests must reject coercion from integers, booleans, None, and nonliteral strings. Apply and reconfirm this exact correction through the helper. No other choices are approved. Do not execute product tests."
            row["actor"] = run_actor(prompt=prompt, cwd=target, state_home=state, evidence_dir=case_root / "actor",
                timeout=timeout, sandbox="workspace-write", live=True, cli=cli)
            row["outcome"] = "awaiting-independent-grading" if row["actor"]["outcome"] == "completed-ungraded" else row["actor"]["outcome"]
            if case["id"] == "bounded-correct":
                row["direct_checks"] = check_fixture(target)
        row["after"] = {name: digest_file(target / name) for name in before}
        row["seeded_plan"] = seeded
        rows.append(row)
        write(output / "results.json", json.dumps({"schema_version": "expskill-live-trials.v2", "revision": revision,
            "runner": {"path": str(Path(__file__).resolve()), "sha256": digest_file(Path(__file__))},
            "live_opt_in": live, "max_concurrent_actors": 2, "case_pack_sha256": hashlib.sha256(pack_bytes).hexdigest(), "cases": rows}, indent=2) + "\n")
        print(json.dumps({"case": case["id"], "outcome": row["outcome"]}), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--pack", type=Path, default=Path(__file__).resolve().parents[1] / "tests/fixtures/live/frozen-live-cases-v2.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--live", action="store_true")
    arguments = parser.parse_args()
    run(arguments.repository, arguments.revision, arguments.pack, arguments.output, set(arguments.case), arguments.timeout, live=arguments.live)


if __name__ == "__main__":
    main()
