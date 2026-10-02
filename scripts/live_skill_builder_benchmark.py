import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE = ROOT / "tests/fixtures/skill-builder/live-repair-probe.json"
DEFAULT_DRIVER = ROOT / "scripts/live_trials.py"
REQUIRED_FILES = {"SKILL.md", "scripts/count_lines.py"}
ROLES = (
    "candidate-author", "trial-before", "reviewer-before", "repair-author",
    "trial-after", "independent-grader", "independent-verifier",
)
FRAMEWORK_STAGES = {
    "candidate-author": "9. Build one isolated candidate",
    "trial-before": "10. Run fresh-context trials",
    "reviewer-before": "11. Obtain independent review before scoring",
    "repair-author": "12. Score and repair the lowest category, repair behavior only",
    "trial-after": "10. Run fresh-context trials",
    "independent-grader": "12. Score and repair the lowest category, bounded evidence judgment only",
    "independent-verifier": "13. Review, verify, and finalize one exact revision, verification behavior only",
}
FRAMEWORK_RUBRIC = {
    "candidate-author": "Builder-run conformance gates BR6 and the frozen target scoring basis",
    "trial-before": "Builder-run conformance gates BR6 and BR7",
    "reviewer-before": "Reviewer evidence contract and builder-run gate BR8",
    "repair-author": "Target scoring method evidence principles and failure/repair conditions",
    "trial-after": "Builder-run conformance gates BR6 and BR7",
    "independent-grader": "Target scoring method evidence principles only, with no 100-criterion scorecard",
    "independent-verifier": "Reviewer evidence contract and builder-run gate BR8",
}
FAULT_SOURCE = (
    "import json\nfrom pathlib import Path\nimport sys\n"
    "text = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
    "print(json.dumps({'line_count': len(text.splitlines()) + 1}))\n"
)


class ProbeError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def retain_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def load_driver(path):
    path = Path(path)
    if not path.is_file():
        raise ProbeError(f"Missing shared live driver: {path}. Integrate the empirical benchmark prerequisite first.")
    spec = importlib.util.spec_from_file_location("skill_builder_shared_live_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if any(not callable(getattr(module, name, None)) for name in ("run_actor", "run_process_group", "snapshot", "git")):
        raise ProbeError("Shared live driver does not expose actor, process, snapshot, and Git primitives")
    return module


def initialize_workspace(driver, workspace, remaining):
    workspace = Path(workspace).resolve()
    if (workspace / ".git").exists():
        raise ProbeError("Disposable actor workspace already contains Git metadata")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    commands = (
        ["git", "init", "--quiet", "--template=", "."],
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "rev-parse", "--verify", "HEAD"],
        ["git", "remote"],
    )
    checks = []
    for argv in commands:
        result = driver.run_process_group(argv, prompt="", cwd=workspace, env=env, timeout=min(15, remaining()))
        checks.append({"argv": argv, **result})
    initialized, top_level, head, remotes = checks
    if any(check["timed_out"] for check in checks) or initialized["exit_code"] != 0 or top_level["exit_code"] != 0:
        raise ProbeError("Could not initialize and verify the disposable Git workspace")
    if not (workspace / ".git").is_dir() or Path(top_level["stdout"].strip()).resolve() != workspace:
        raise ProbeError("Actor workspace did not receive its own local Git repository")
    if head["exit_code"] != 128 or head["stdout"].strip() or remotes["exit_code"] != 0 or remotes["stdout"].strip():
        raise ProbeError("Disposable Git workspace unexpectedly has history or remotes")
    return {"repository": str(workspace), "history_present": False, "remotes": [], "checks": checks,
            "origin": "Host-created empty local Git repository; no commits or remotes"}


def manifest(package, *, require_files=True):
    files = {}
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise ProbeError("Candidate contains a symlink")
        if path.is_file():
            data = path.read_bytes()
            files[path.relative_to(package).as_posix()] = {"sha256": digest(data), "bytes": len(data)}
    if sum(item["bytes"] for item in files.values()) > 1024 * 1024:
        raise ProbeError("Candidate exceeds the one MiB probe ceiling")
    if set(files) - REQUIRED_FILES or (require_files and set(files) != REQUIRED_FILES):
        raise ProbeError("Candidate changed outside the two owned files or omitted a required file")
    return {"files": files, "digest": digest(canonical(files))}


def actor_origin(actor, seen_threads):
    if actor.get("outcome") != "completed-ungraded":
        raise ProbeError(f"Actor transport did not complete: {actor.get('outcome')}")
    attempts = actor.get("attempts")
    selected = actor.get("selected_attempt")
    if not isinstance(attempts, list) or not attempts or type(selected) is not int or not 1 <= selected <= len(attempts):
        raise ProbeError("Actor has no selected retained attempt")
    observed_threads = []
    selected_events = None
    for index, attempt in enumerate(attempts, start=1):
        raw = Path(attempt["raw_events"]).read_bytes()
        if digest(raw) != attempt["raw_events_sha256"]:
            raise ProbeError("Actor raw-event digest does not match retained bytes")
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if any(not isinstance(event, dict) for event in events):
            raise ProbeError("Actor event stream contains a non-object")
        threads = [event["thread_id"] for event in events if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str)]
        if threads != attempt["thread_ids"] or any(thread in seen_threads or thread in observed_threads for thread in threads):
            raise ProbeError("Actor context identity is reused or disagrees with raw events")
        observed_threads.extend(threads)
        if index == selected:
            selected_events = events
            if attempt["exit_code"] != 0 or attempt["timed_out"] or len(threads) != 1 or actor.get("thread_ids") != threads:
                raise ProbeError("Selected actor attempt has no fresh successful transport")
    messages = [event["item"].get("text") for event in selected_events if event.get("type") == "item.completed" and isinstance(event.get("item"), dict) and event["item"].get("type") == "agent_message"]
    if not messages or messages[-1] != actor.get("final_text"):
        raise ProbeError("Actor final reply is not the retained raw agent message")
    seen_threads.update(observed_threads)
    models = sorted({event["model"] for event in selected_events if isinstance(event.get("model"), str)})
    return {"thread_id": actor["thread_ids"][0], "observed_model_fields": models,
            "model_identity_established": bool(models), "events": selected_events}


def payload(actor):
    value = json.loads(actor["final_text"])
    if not isinstance(value, dict):
        raise ProbeError("Actor reply must be one JSON object")
    return value


def validate_judgment(actor, pin, observations):
    value = payload(actor)
    expected = {item["case_id"]: item["passed"] for item in observations["checks"]}
    results = value.get("criterion_results")
    if value.get("candidate_digest") != pin or results != expected or not isinstance(results, dict) or any(type(item) is not bool for item in results.values()):
        raise ProbeError("Independent judgment is stale or disagrees with recorder observations")
    grade = "pass" if all(expected.values()) else "fail"
    if value.get("grade") != grade or not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ProbeError("Independent judgment lacks a supported bounded grade")
    evidence = value.get("evidence_digests")
    if not isinstance(evidence, list) or not evidence or any(item != observations["evidence"]["sha256"] for item in evidence):
        raise ProbeError("Independent judgment does not bind current recorder evidence")
    return value


def trial_origin(actor, origin, pin, cases, fixture_root, package):
    value = payload(actor)
    if value.get("candidate_digest") != pin or value.get("executed_case_ids") != [case["case_id"] for case in cases]:
        raise ProbeError("Trial reply lacks the current candidate and frozen operations")
    require_executed_cases(origin, cases, fixture_root, package)


def command_parts(command):
    parts = shlex.split(command)
    for _ in range(2):
        if len(parts) == 3 and Path(parts[0]).name in {"sh", "bash", "zsh"} and parts[1] in {"-c", "-lc"}:
            parts = shlex.split(parts[2])
        else:
            break
    return parts


def require_framework_reads(origin, sources):
    read_paths = set()
    for event in origin["events"]:
        item = event.get("item")
        if event.get("type") != "item.completed" or not isinstance(item, dict) or item.get("type") != "command_execution" or item.get("exit_code") != 0:
            continue
        try:
            parts = command_parts(item.get("command", ""))
            if parts and Path(parts[0]).name == "cat" and len(parts) > 1 and all(Path(value).is_absolute() for value in parts[1:]):
                read_paths.update(str(Path(value).resolve()) for value in parts[1:])
        except (ValueError, TypeError):
            pass
    if any(source["path"] not in read_paths for source in sources.values()):
        raise ProbeError("Actor raw events do not show required immutable framework source reads")


def helper_invocation(command, package):
    try:
        parts = command_parts(command)
        if parts and parts[0] == "env":
            parts = parts[1:]
        if parts and parts[0] == "PYTHONDONTWRITEBYTECODE=1":
            parts = parts[1:]
        if len(parts) != 3:
            return None
        executable = shutil.which(parts[0]) if not Path(parts[0]).is_absolute() else parts[0]
        if not executable or Path(executable).resolve() != Path(sys.executable).resolve():
            return None
        arguments = [(Path(value) if Path(value).is_absolute() else package.parent / value).resolve() for value in parts[1:]]
        if arguments[0] != (package / "scripts/count_lines.py").resolve():
            return None
        return arguments[1]
    except (ValueError, OSError, TypeError):
        return None


def require_executed_cases(origin, cases, fixture_root, package):
    operations = [event["item"] for event in origin["events"] if event.get("type") == "item.completed" and isinstance(event.get("item"), dict) and event["item"].get("type") == "command_execution" and type(event["item"].get("exit_code")) is int]
    inputs = {helper_invocation(item.get("command", ""), package) for item in operations}
    if any((fixture_root / f"{case['case_id']}.txt").resolve() not in inputs for case in cases):
        raise ProbeError("Trial raw events do not show the required helper executions")


def record_checks(driver, package, fixture_root, cases, destination, remaining):
    destination.mkdir(parents=True, exist_ok=False)
    before = manifest(package)
    checks = []
    for case in cases:
        argv = [sys.executable, str(package / "scripts/count_lines.py"), str(fixture_root / f"{case['case_id']}.txt")]
        started = time.monotonic()
        result = driver.run_process_group(argv, prompt="", cwd=package.parent,
                                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=min(15, remaining()))
        stdout = destination / f"{case['case_id']}.stdout"
        stderr = destination / f"{case['case_id']}.stderr"
        stdout.write_text(result["stdout"], encoding="utf-8")
        stderr.write_text(result["stderr"], encoding="utf-8")
        observed = None
        if case["expected_line_count"] is not None:
            try:
                observed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                pass
            passed = result["exit_code"] == 0 and isinstance(observed, dict) and type(observed.get("line_count")) is int and observed["line_count"] == case["expected_line_count"]
        else:
            passed = type(result["exit_code"]) is int and result["exit_code"] != 0
        checks.append({"case_id": case["case_id"], "argv": argv, "exit_code": result["exit_code"],
                       "timed_out": result["timed_out"], "elapsed_seconds": time.monotonic() - started,
                       "stdout": str(stdout), "stdout_sha256": digest(stdout.read_bytes()),
                       "stderr": str(stderr), "stderr_sha256": digest(stderr.read_bytes()),
                       "observed_json": observed, "passed": bool(passed and not result["timed_out"])})
    after = manifest(package)
    if before != after:
        raise ProbeError("Executing the oracle changed candidate source")
    value = {"candidate_digest": before["digest"], "checks": checks, "all_passed": all(item["passed"] for item in checks),
             "before_manifest": before, "after_manifest": after, "origin": "recorder-executed commands, not model output transcription"}
    evidence = retain_json(destination / "observations.json", value)
    return {**value, "evidence": evidence}


def run_probe(*, output_root, live=False, inject_defect=False, driver_path=DEFAULT_DRIVER,
              case_path=DEFAULT_CASE, actor_timeout=180, deadline_seconds=1500, cli="codex", driver=None,
              repository=ROOT, framework_revision=None):
    if not live:
        raise ProbeError("Live Skill Builder repair probe requires explicit --live opt-in")
    if type(actor_timeout) is not int or not 1 <= actor_timeout <= 240 or type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 1800:
        raise ProbeError("Probe requires actor timeout 1..240 seconds and deadline 1..1800 seconds")
    injected_transport = driver is not None
    driver = driver or load_driver(driver_path)
    if not isinstance(framework_revision, str) or len(framework_revision) != 40 or any(char not in "0123456789abcdef" for char in framework_revision):
        raise ProbeError("An immutable full framework commit revision is required")
    if driver.git(Path(repository), "rev-parse", framework_revision + "^{commit}") != framework_revision:
        raise ProbeError("Framework revision does not identify the requested commit")
    definition = json.loads(Path(case_path).read_text())
    if definition.get("schema_version") != "skill-builder-live-repair-probe.v1" or definition.get("required_files") != sorted(REQUIRED_FILES):
        raise ProbeError("Unexpected bounded probe definition")
    cases = definition["cases"]
    if [case["case_id"] for case in cases] != ["two-lines", "empty-file", "missing-file"]:
        raise ProbeError("Probe requires exactly its three frozen command cases")
    for case in cases:
        if set(case) != {"case_id", "text", "expected_line_count"}:
            raise ProbeError("Probe case has unexpected fields")
        if case["case_id"] == "missing-file":
            valid = case["text"] is None and case["expected_line_count"] is None
        else:
            valid = isinstance(case["text"], str) and type(case["expected_line_count"]) is int and case["expected_line_count"] == len(case["text"].splitlines())
        if not valid:
            raise ProbeError("Frozen line-count oracle contradicts its fixture")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    content = driver.snapshot(Path(repository), framework_revision, output_root / "framework-snapshot")
    framework_sources = {}
    for name, relative in (("skill", "skills/skill-builder/SKILL.md"), ("rubric", "skills/skill-builder/references/evaluation-rubric.md")):
        source = (content / relative).resolve()
        framework_sources[name] = {"path": str(source), "sha256": digest(source.read_bytes())}
        source.chmod(0o444)
    contract = retain_json(output_root / "frozen-probe.json", definition)
    Path(contract["path"]).chmod(0o444)
    fixture_root = output_root / "fixtures"
    fixture_root.mkdir()
    for case in cases:
        if case["text"] is not None:
            (fixture_root / f"{case['case_id']}.txt").write_text(case["text"], encoding="utf-8")
    fixture_pin = {path.name: digest(path.read_bytes()) for path in fixture_root.iterdir()}
    report = {"schema_version": "skill-builder-live-repair-observation.v1", "scope": definition["scope"],
              "synthetic_foundation": definition["synthetic_foundation"], "frozen_probe": contract,
              "full_builder_completion": False, "scorecard_created": False, "live_public_state_transitions": [],
              "separate_mechanical_gate_suite": "tests/test_skill_builder_state.py; not executed by this probe",
              "context_claim": "Fresh observed CLI contexts, not authenticated actors or established model diversity",
              "transport_mode": "injected-test-transport" if injected_transport else "shared-live-CLI-driver",
              "probe_runner": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__).read_bytes())},
              "framework": {"repository": str(Path(repository).resolve()), "revision": framework_revision, "sources": framework_sources,
                            "orchestration": "harness-owned bounded stages; synthetic foundation, no full 100-criterion scoring or finalization"},
              "driver_source": None if injected_transport else {"path": str(Path(driver_path).resolve()), "sha256": digest(Path(driver_path).read_bytes())},
              "budgets": {"maximum_actor_calls": 7, "maximum_infrastructure_attempts_per_actor": 2,
                          "actor_timeout_seconds": actor_timeout, "deadline_seconds": deadline_seconds,
                          "retry_reservation": "Each dispatch reserves two attempt timeouts within its remaining deadline."},
              "fault_injection": {"requested": bool(inject_defect), "applied": False}, "actors": {}, "observations": {},
              "workspace_prerequisites": {},
              "outcome": "in-progress"}
    deadline = time.monotonic() + deadline_seconds
    seen_threads = set()

    def persist():
        retain_json(output_root / "report.json", report)

    def validate_retained_evidence():
        remaining()
        replayed_threads = set()
        for row in report["actors"].values():
            actor_origin(row["transport"], replayed_threads)
        for observations in report["observations"].values():
            evidence = observations["evidence"]
            if digest(Path(evidence["path"]).read_bytes()) != evidence["sha256"]:
                raise ProbeError("Retained recorder observations changed after grading")
            for check in observations["checks"]:
                for field in ("stdout", "stderr"):
                    if digest(Path(check[field]).read_bytes()) != check[f"{field}_sha256"]:
                        raise ProbeError("Retained command output changed after grading")
        guard()

    def guard():
        if digest(Path(contract["path"]).read_bytes()) != contract["sha256"] or fixture_pin != {path.name: digest(path.read_bytes()) for path in fixture_root.iterdir()}:
            raise ProbeError("Frozen benchmark definition or input fixtures changed")
        if any(digest(Path(source["path"]).read_bytes()) != source["sha256"] for source in framework_sources.values()):
            raise ProbeError("Immutable framework source snapshot changed")

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ProbeError("Probe deadline exhausted")
        return seconds

    def call(role, prompt, package, *, writer=False):
        guard()
        if role not in ROLES or role in report["actors"] or len(report["actors"]) >= 7:
            raise ProbeError("Probe actor-call ceiling or role ownership violated")
        before = manifest(package, require_files=role != "candidate-author")
        read_commands = [shlex.join(["cat", source["path"]]) for source in framework_sources.values()]
        framework_context = (
            f"Framework commit: {framework_revision}. Framework sources and SHA-256 digests: {json.dumps(framework_sources)}. "
            f"First read both complete framework files with these exact commands in separate tool operations: {json.dumps(read_commands)}. "
            f"Apply Skill Builder stage {FRAMEWORK_STAGES[role]}. Relevant rubric sections: {FRAMEWORK_RUBRIC[role]}. "
            "The target count-lines skill is distinct from the framework Skill Builder source. "
            "This is an explicitly bounded post-evaluation probe with harness-owned orchestration. Target selection, contract, and frozen oracle are synthetic accepted foundation. "
            "Research, design, user-confirmation, full 100-criterion scoring, public state transitions, finalization, delivery, and cleanup are outside the live probe. "
            "Do not invoke routing or begin the complete lifecycle. Follow only this assigned stage and report its actual evidence. "
        )
        prompt = framework_context + prompt
        actor = driver.run_actor(prompt=prompt, cwd=package.parent, state_home=output_root / "state" / role,
                                 evidence_dir=output_root / "actors" / role, timeout=min(actor_timeout, remaining() / 2),
                                 sandbox="workspace-write" if writer else "read-only", live=True, cli=cli,
                                 infrastructure_retries=1)
        report["actors"][role] = {"transport": actor}
        persist()
        origin = actor_origin(actor, seen_threads)
        require_framework_reads(origin, framework_sources)
        report["actors"][role]["origin"] = {key: value for key, value in origin.items() if key != "events"}
        report["actors"][role]["framework_stage"] = FRAMEWORK_STAGES[role]
        report["actors"][role]["framework_rubric"] = FRAMEWORK_RUBRIC[role]
        after = manifest(package)
        if not writer and before != after:
            raise ProbeError("Read-only actor changed the pinned candidate")
        guard()
        persist()
        return actor, origin

    def trial(role, package):
        pin = manifest(package)["digest"]
        commands = [shlex.join([sys.executable, str(package / "scripts/count_lines.py"), str(fixture_root / f"{case['case_id']}.txt")]) for case in cases]
        prompt = f"Read the target skill at {package / 'SKILL.md'} and perform this frozen request: {contract['path']}, SHA-256 {contract['sha256']}. Candidate digest: {pin}. Execute each of these exact helper commands in a separate tool operation without editing candidate source: {json.dumps(commands)}. Avoid loops, substitutions, batches, or echoing commands. The harness captures outputs directly. Return only JSON with candidate_digest and executed_case_ids in frozen order. Do not transcribe outputs or start additional lifecycle stages."
        actor, origin = call(role, prompt, package)
        trial_origin(actor, origin, pin, cases, fixture_root, package)
        result = record_checks(driver, package, fixture_root, cases, output_root / "checks" / role, remaining)
        report["observations"][role] = result
        persist()
        return result

    def judge(role, package, observations):
        pin = manifest(package)["digest"]
        prompt = f"You are a fresh independent read-only bounded {role}. Inspect the candidate at {package}, digest {pin}. Frozen functional criteria: {contract['path']}, SHA-256 {contract['sha256']}. Recorder observations: {observations['evidence']['path']}, SHA-256 {observations['evidence']['sha256']}. Self-inspect source and original raw actor events under {output_root / 'actors'} and execute checks as needed. No desired grade is supplied. Grade only the three named command criteria, not all 100 Skill Builder criteria or full Builder completion. Return only JSON with candidate_digest, criterion_results (each frozen case ID maps to a boolean), grade (pass or fail), evidence_digests, and reason. Do not edit source or delegate."
        if role == "independent-verifier":
            commands = [shlex.join([sys.executable, str(package / "scripts/count_lines.py"), str(fixture_root / f"{case['case_id']}.txt")]) for case in cases]
            prompt += f" Independently execute each exact verification command in a separate tool operation without loops or batches: {json.dumps(commands)}."
        actor, origin = call(role, prompt, package)
        if role == "independent-verifier":
            require_executed_cases(origin, cases, fixture_root, package)
        judgment = validate_judgment(actor, pin, observations)
        report["actors"][role]["bounded_judgment"] = judgment
        persist()
        return judgment

    persist()
    try:
        original = output_root / "workspaces" / "original" / "candidate"
        original.mkdir(parents=True)
        report["workspace_prerequisites"]["original"] = initialize_workspace(driver, original.parent, remaining)
        persist()
        prompt = f"Create exactly the two-file skill candidate in {original} for this frozen contract: {contract['path']}, SHA-256 {contract['sha256']}. Read the contract before editing. Write SKILL.md with appropriate frontmatter and scripts/count_lines.py. Use only standard-library dependencies. Add no code comments, other documentation, commits, or delegation. Do not begin the complete lifecycle workflow. This is a bounded candidate-author stage and does not ask for full Skill Builder completion."
        call("candidate-author", prompt, original, writer=True)
        report["candidate_authored"] = manifest(original)
        initial = record_checks(driver, original, fixture_root, cases, output_root / "checks" / "authored", remaining)
        report["observations"]["authored"] = initial
        if initial["all_passed"] and inject_defect:
            before = manifest(original)
            original_snapshot = output_root / "authored-package"
            shutil.copytree(original, original_snapshot)
            (original / "scripts/count_lines.py").write_text(FAULT_SOURCE, encoding="utf-8")
            report["fault_injection"].update({"applied": True, "origin": "declared harness replacement, not a naturally occurring model error",
                                             "kind": "line-count-plus-one", "before": before, "after": manifest(original),
                                             "original_package": str(original_snapshot), "injected_source_sha256": digest(FAULT_SOURCE.encode())})
        first = trial("trial-before", original)
        if first["all_passed"]:
            validate_retained_evidence()
            report["outcome"] = "repair-not-demonstrated"
            persist()
            return report
        judge("reviewer-before", original, first)
        repaired = output_root / "workspaces" / "repaired" / "candidate"
        shutil.copytree(original, repaired)
        report["workspace_prerequisites"]["repaired"] = initialize_workspace(driver, repaired.parent, remaining)
        persist()
        skill_pin = manifest(repaired)["files"]["SKILL.md"]
        prompt = f"Repair only scripts/count_lines.py in {repaired}. Preserve SKILL.md exactly. Frozen contract: {contract['path']}, SHA-256 {contract['sha256']}. Actual first failing observations: {first['evidence']['path']}, SHA-256 {first['evidence']['sha256']}. Independent pre-repair review: {output_root / 'actors/reviewer-before/actor.json'}. Inspect those artifacts and correct the observed failure. Do not modify frozen inputs, add files, delegate, commit, or claim full Builder completion."
        call("repair-author", prompt, repaired, writer=True)
        if manifest(repaired)["digest"] == manifest(original)["digest"]:
            raise ProbeError("Repair actor did not change candidate source")
        if manifest(repaired)["files"]["SKILL.md"] != skill_pin:
            raise ProbeError("Repair changed SKILL.md outside its owned Python file")
        second = trial("trial-after", repaired)
        judge("independent-grader", repaired, second)
        verified = record_checks(driver, repaired, fixture_root, cases, output_root / "checks" / "verification", remaining)
        report["observations"]["verification"] = verified
        judge("independent-verifier", repaired, verified)
        validate_retained_evidence()
        if manifest(original) != first["before_manifest"]:
            raise ProbeError("Original failing candidate changed during repair")
        report["candidate_repaired"] = manifest(repaired)
        report["outcome"] = "repair-observed" if second["all_passed"] and verified["all_passed"] else "repair-not-successful"
    except (ProbeError, OSError, ValueError, KeyError, TypeError) as error:
        report["outcome"] = "probe-blocked"
        report["failure"] = {"type": type(error).__name__, "reason": str(error)}
    persist()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--inject-defect", action="store_true")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--driver-path", type=Path, default=DEFAULT_DRIVER)
    parser.add_argument("--actor-timeout", type=int, default=180)
    parser.add_argument("--deadline-seconds", type=int, default=1500)
    parser.add_argument("--cli", default="codex")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--framework-revision", required=True)
    args = parser.parse_args()
    try:
        report = run_probe(output_root=args.output_root, live=args.live, inject_defect=args.inject_defect,
                           driver_path=args.driver_path, actor_timeout=args.actor_timeout,
                           deadline_seconds=args.deadline_seconds, cli=args.cli,
                           repository=args.repository, framework_revision=args.framework_revision)
        print(json.dumps({"outcome": report["outcome"], "report": str(args.output_root / "report.json"),
                          "full_builder_completion": False}))
        return 0 if report["outcome"] == "repair-observed" else 1
    except (ProbeError, OSError, ValueError) as error:
        print(json.dumps({"outcome": "prerequisite-blocked", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
