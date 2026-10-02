import argparse
import base64
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE = ROOT / "tests/fixtures/live/routed-plan-design.json"
SOURCE_FILES = ["app.js", "index.html", "styles.css"]
WIDTHS = {"compact": 390, "intermediate": 768, "wide": 1280}
THEMES = ["light", "dark"]
STATES = ["default", "checked", "disabled"]
IDENTITY = "g-imhoff <152416066+g-imhoff@users.noreply.github.com>"


class ProbeError(RuntimeError):
    pass


class ActorBlocked(ProbeError):
    def __init__(self, failure):
        self.failure = failure
        super().__init__(failure["reason"])


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def retain(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def private_file(path, root):
    path = Path(path).absolute()
    root = Path(root).resolve()
    if ".." in path.parts or not path.is_relative_to(root) or not path.is_file():
        raise ProbeError("Missing or out-of-scope private artifact")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ProbeError("Symlinked private artifact")
    return path


def tree_pin(root):
    return {str(path.relative_to(root)): "symlink:" + os.readlink(path) if path.is_symlink() else digest(path.read_bytes()) for path in sorted(root.rglob("*")) if (path.is_file() or path.is_symlink()) and path.name != ".lock"} if root.exists() else {}


def browser_path():
    configured = os.environ.get("EXPSKILL_TRIAL_BROWSER")
    candidates = [Path(configured)] if configured else [Path(path) for path in (shutil.which("chromium"), shutil.which("google-chrome")) if path]
    candidates += sorted((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux64/chrome"), reverse=True)
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    raise ProbeError("Native browser unavailable; no installation or substitute proof is authorized")


def verify_python_runtime():
    executable = Path(sys.executable).absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ProbeError("Current Python3 interpreter prerequisite is unavailable or not executable")
    argv = [str(executable), "-I", "-c", "import json,sys; print(json.dumps({'major':sys.version_info.major,'version':sys.version}))"]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)
        result = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise ProbeError("Current Python3 interpreter prerequisite failed: " + str(error)) from error
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("major") != 3:
        raise ProbeError("Current interpreter does not establish the Python3 prerequisite")
    return {"executable": str(executable), "resolved_executable": str(executable.resolve()), "sha256": digest(executable.read_bytes()),
        "version": result["version"], "check": {"argv": argv, "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
        "scope": "Host prerequisite observation before actor launch; actors must use this exact executable rather than an unverified interpreter alias."}


def review_paths():
    return [f"review/{width}-{theme}-{state}.png" for width in WIDTHS.values() for theme in THEMES for state in STATES] + ["review/native-checks.json", "design-manifest.json"]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *arguments):
        pass


def native_checks(repository, output, browser, timeout_seconds=60):
    import websocket
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
        raise ProbeError("Invalid native proof timeout")
    repository, output = Path(repository).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(repository)))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    observations, errors, overflow = [], [], {name: False for name in WIDTHS}
    process = None
    connection = None
    started = time.monotonic()
    temporary = tempfile.TemporaryDirectory(prefix="routed-native-browser-")
    try:
        profile = Path(temporary.name)
        process = subprocess.Popen([str(browser), "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--remote-allow-origins=*", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file = profile / "DevToolsActivePort"
        while not port_file.exists():
            if process.poll() is not None or time.monotonic() - started > 10:
                raise ProbeError("Native browser did not expose its local inspection endpoint")
            time.sleep(0.05)
        port = int(port_file.read_text().splitlines()[0])
        with urlopen(Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"), timeout=5) as response:
            page = json.load(response)
        connection = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=5, origin=f"http://127.0.0.1:{port}")
        sequence = 0

        def call(method, parameters=None):
            nonlocal sequence
            if time.monotonic() - started > timeout_seconds:
                raise ProbeError("Native proof deadline exhausted")
            sequence += 1
            connection.send(json.dumps({"id": sequence, "method": method, "params": parameters or {}}))
            while True:
                message = json.loads(connection.recv())
                if message.get("method") == "Runtime.exceptionThrown":
                    errors.append(message)
                if message.get("method") == "Runtime.consoleAPICalled" and message["params"]["type"] == "error":
                    errors.append(message)
                if message.get("id") == sequence:
                    if "error" in message:
                        raise ProbeError("Native inspection command failed: " + str(message["error"]))
                    return message.get("result", {})

        def evaluate(expression):
            result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
            if "exceptionDetails" in result:
                raise ProbeError("Native fixture evaluation failed")
            return result.get("result", {}).get("value")

        call("Page.enable")
        call("Runtime.enable")
        for name, width in WIDTHS.items():
            for theme in THEMES:
                for state in STATES:
                    call("Emulation.setDeviceMetricsOverride", {"width": width, "height": 800, "deviceScaleFactor": 1, "mobile": False})
                    call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": theme}, {"name": "prefers-reduced-motion", "value": "reduce"}]})
                    call("Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/index.html?state={state}"})
                    for _ in range(80):
                        if evaluate("document.readyState === 'complete' && !!document.querySelector('#enabled')"):
                            break
                        time.sleep(0.025)
                    else:
                        raise ProbeError("Native preview never became ready")
                    dom = evaluate("({label:document.querySelector('label[for=enabled]')?.textContent.trim(),type:document.querySelector('#enabled').type,checked:document.querySelector('#enabled').checked,disabled:document.querySelector('#enabled').disabled,status:document.querySelector('#status')?.textContent.trim(),overflow:document.documentElement.scrollWidth>innerWidth,transition:getComputedStyle(document.querySelector('#enabled')).transitionDuration,animation:getComputedStyle(document.querySelector('#enabled')).animationName})")
                    ax = call("Accessibility.getFullAXTree")
                    accessible = any(not item.get("ignored") and item.get("role", {}).get("value") == "checkbox" and item.get("name", {}).get("value") == "Send email notifications" for item in ax["nodes"])
                    if dom["label"] != "Send email notifications" or dom["type"] != "checkbox" or not accessible or dom["disabled"] != (state == "disabled") or dom["checked"] != (state == "checked") or dom["status"] != ("On" if state == "checked" else "Off"):
                        raise ProbeError("Native state or accessible checkbox contract failed")
                    filename = f"{width}-{theme}-{state}.png"
                    screenshot = base64.b64decode(call("Page.captureScreenshot", {"format": "png"})["data"])
                    (output / filename).write_bytes(screenshot)
                    keyboard = None
                    if state != "disabled":
                        evaluate("document.querySelector('#enabled').focus()")
                        before = evaluate("document.querySelector('#enabled').checked")
                        for event in ("keyDown", "keyUp"):
                            call("Input.dispatchKeyEvent", {"type": event, "key": " ", "code": "Space", "windowsVirtualKeyCode": 32})
                        keyboard = evaluate("({checked:document.querySelector('#enabled').checked,focused:document.activeElement.id,status:document.querySelector('#status').textContent.trim()})")
                        if keyboard["checked"] == before or keyboard["focused"] != "enabled" or keyboard["status"] != ("On" if keyboard["checked"] else "Off"):
                            raise ProbeError("Native Space/focus/change path failed")
                    evaluate("document.querySelector('label[for=enabled]').textContent='Send email notifications for unusually long localized preference content that must wrap without horizontal scrolling'")
                    overflow[name] = overflow[name] or dom["overflow"] or evaluate("document.documentElement.scrollWidth>innerWidth")
                    if dom["animation"] != "none" or any(float(value.strip().rstrip("s")) != 0 for value in dom["transition"].split(",")):
                        raise ProbeError("Reduced-motion fixture check failed")
                    observations.append({"viewport": width, "theme": theme, "state": state, "dom": dom, "keyboard": keyboard, "accessible_checkbox": accessible, "screenshot": filename, "screenshot_sha256": digest(screenshot)})
        if errors or any(overflow.values()):
            raise ProbeError("Native runtime/console or responsive fixture check failed")
        result = {"schema_version": "native-ui-fixture-check.v1", "passed": True, "observations": observations,
            "page_overflow": overflow, "accessibility": {"serious": 0, "critical": 0}, "console_errors": errors,
            "limitations": ["Accessibility findings cover native name/role/state, not a full WCAG audit.", "Keyboard proof uses browser-dispatched Space with programmatic focus; human visual judgment and Tab traversal are not established."],
            "tooling_exceptions": {name: "No configured " + name + " tool in this native fixture" for name in ("format", "lint", "type", "build")}}
        retain(output / "native-checks.json", result)
        return result
    finally:
        try:
            if connection is not None:
                connection.close()
        finally:
            try:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
            finally:
                try:
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=2)
                finally:
                    temporary.cleanup()


def verify_native_preview(design_state, worktree, state_root, browser):
    if sorted(design_state["scope"]["owned_paths"]) != sorted(SOURCE_FILES + review_paths()):
        raise ProbeError("Design scope differs from the frozen native fixture")
    worktree, state_root = Path(worktree), Path(state_root)
    proof = json.loads(private_file(worktree / "review/native-checks.json", worktree).read_bytes())
    if proof.get("schema_version") != "native-ui-fixture-check.v1" or proof.get("passed") is not True:
        raise ProbeError("Design lacks actual native fixture proof")
    expected = {(width, theme, state) for width in WIDTHS.values() for theme in THEMES for state in STATES}
    observations = proof.get("observations", [])
    observed = {(item["viewport"], item["theme"], item["state"]) for item in observations}
    if len(observations) != len(expected) or observed != expected:
        raise ProbeError("Native preview coverage differs from frozen states")
    for item in observations:
        filename = f"{item['viewport']}-{item['theme']}-{item['state']}.png"
        screenshot = private_file(worktree / "review" / filename, worktree)
        if item["screenshot"] != filename or digest(screenshot.read_bytes()) != item["screenshot_sha256"]:
            raise ProbeError("Native screenshot bytes differ from recorded proof")
    matched = False
    for evidence in design_state["evidence"].values():
        for result in evidence["technical"]["results"]:
            argv = shlex.split(result["command"])
            if len(argv) != 7 or argv[1:] != ["native_checks.py", "native-checks", "--browser", str(browser), "--output", "review"] or result["exit"] != 0:
                continue
            output = private_file(state_root / "records" / (result["record_id"] + ".output"), state_root)
            if digest(output.read_bytes()) != result["output_digest"] or json.loads(output.read_bytes()) != proof:
                raise ProbeError("Native proof differs from helper-recorded command output")
            matched = True
    if not matched:
        raise ProbeError("No recorded successful native preview command")
    return {"schema_version": proof["schema_version"], "observations": len(observations), "limitations": proof["limitations"]}


def fixture(driver, target, definition):
    target.mkdir(parents=True)
    for name, text in definition["fixture_files"].items():
        (target / name).write_text(text, encoding="utf-8")
    shutil.copyfile(Path(__file__), target / "native_checks.py")
    driver.git(target, "init", "-b", "trial/routed-ui")
    driver.git(target, "config", "user.name", "g-imhoff")
    driver.git(target, "config", "user.email", "152416066+g-imhoff@users.noreply.github.com")
    driver.git(target, "add", ".")
    for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        if not driver.git(target, "var", kind).startswith(IDENTITY + " "):
            raise ProbeError("Unexpected fixture commit identity")
    driver.git(target, "commit", "-qm", "Create isolated native routed conversation fixture")
    if driver.git(target, "show", "-s", "--format=%an <%ae> | %cn <%ce>", "HEAD") != IDENTITY + " | " + IDENTITY:
        raise ProbeError("Saved fixture identity mismatch")
    return driver.git(target, "rev-parse", "HEAD")


def run_probe(*, output_root, revision, repository=ROOT, case_path=DEFAULT_CASE, live=False, driver=None, actor_timeout=180, deadline_seconds=1500, browser=None, cli="codex"):
    if not live:
        raise ProbeError("Routed conversation probe requires explicit --live opt-in")
    if type(actor_timeout) is not int or not 1 <= actor_timeout <= 240 or type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 1800:
        raise ProbeError("Invalid bounded actor timeout or cumulative deadline")
    injected = driver is not None
    driver = driver or module(ROOT / "scripts/live_trials.py", "routed_live_driver")
    if not isinstance(revision, str) or len(revision) != 40 or driver.git(repository, "rev-parse", revision + "^{commit}") != revision:
        raise ProbeError("An exact immutable framework revision is required")
    definition = json.loads(Path(case_path).read_bytes())
    if definition.get("schema_version") != "routed-plan-design-probe.v1" or definition["production_files"] != SOURCE_FILES or definition["viewports"] != WIDTHS or definition["themes"] != THEMES or definition["states"] != STATES or definition["limits"] != {"maximum_actor_calls": 9, "maximum_concurrent_actors": 1, "maximum_infrastructure_attempts": 1}:
        raise ProbeError("Unexpected frozen routed conversation definition")
    output = Path(output_root).resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    content = driver.snapshot(repository, revision, output / "framework-snapshot")
    for path in content.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    source_pin = tree_pin(content)
    accepted = retain(output / "accepted-input.json", definition["accepted_input"])
    Path(accepted["path"]).chmod(0o400)
    case = retain(output / "frozen-case.json", definition)
    Path(case["path"]).chmod(0o400)
    state = output / "state"
    state.mkdir(mode=0o700)
    (state / "expskill").mkdir(mode=0o700)
    target = output / "repository"
    baseline = fixture(driver, target, definition)
    worktrees = module(content / "scripts/worktrees.py", "routed_probe_worktrees")
    design_tree = worktrees.create_worktree(target, baseline, "routed-probe", "design", state)
    design_git_common = Path(driver.git(design_tree.path, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
    if design_git_common != (target / ".git").resolve(strict=True):
        raise ProbeError("Design Git metadata is outside the disposable fixture")
    plan_helper = module(content / "scripts/plan_graph.py", "routed_probe_plan")
    design_helper = module(content / "scripts/design_state.py", "routed_probe_design")
    plan_root = state / "expskill/plan-graphs"
    audit_root = state / "expskill/plan-audits"
    design_root = state / "expskill/design"
    report = {"schema_version": "routed-plan-design-observation.v1", "outcome": "in-progress", "framework_revision": revision,
        "runner_sha256": digest(Path(__file__).read_bytes()), "frozen_case": case, "accepted_input": accepted,
        "transport": "injected-offline-test" if injected else "retained-live-CLI", "actors": {}, "relay": [], "observations": {},
        "limits": {**definition["limits"], "actor_timeout_seconds": actor_timeout, "deadline_seconds": deadline_seconds},
        "claims": {"synthetic_answers_and_approval": True, "human_visual_approval": False, "autonomous_router_orchestration": False,
            "scope": "Harness schedules owners and executes authorized exact integration. Actors perform public helper operations. One known synthetic case, not production certification."},
        "fixture": {"repository": str(target), "design_worktree": str(design_tree.path), "design_branch": design_tree.branch, "baseline": baseline, "git_common_dir": str(design_git_common)},
        "capability_scope": {"design_writer": {"sandbox": "danger-full-access", "filesystem_isolation": False,
            "purpose": "Matches the authorized host context for disposable candidate Git mutations and native loopback preview. Role ownership, external-network prohibition and unrelated-write prohibition are instructions; the harness checks exact source/state integrity, not whole-host filesystem isolation."},
            "plan_writer": {"sandbox": "workspace-write"},
            "independent_plan_auditor": {"sandbox": "read-only"},
            "design_recovery_reader": {"sandbox": "workspace-write", "product_read_only": "instruction policy plus exact worktree/HEAD/state checks", "additional_write_dirs": [str(design_root)], "purpose": "Public historical loader needs its private namespace lock; no Git mutation, proof command, approval or delivery is authorized."}}}
    deadline = time.monotonic() + deadline_seconds
    original_target = {name: digest((target / name).read_bytes()) for name in list(definition["fixture_files"]) + ["native_checks.py"]}

    def persist():
        report["budgets"] = {"spent_or_reserved_actor_calls": len(report["actors"]), "remaining_actor_calls": 9 - len(report["actors"]),
            "outstanding": [name for name, actor in report["actors"].items() if actor["outcome"] == "reserved"],
            "remaining_seconds": max(0, round(deadline - time.monotonic(), 3))}
        retain(output / "report.json", report)

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ProbeError("Cumulative probe deadline exhausted")
        return value

    def read_plan(payload):
        if not isinstance(payload.get("graph_path"), str) or not payload["graph_path"]:
            raise ProbeError("Plan handoff lacks a canonical graph locator")
        path = private_file(payload["graph_path"], plan_root)
        if path.name != "current.yaml":
            raise ProbeError("Plan returned a noncanonical graph locator")
        graph = json.loads(path.read_bytes())
        if payload["workflow_id"] != graph["workflow_id"] or payload["revision"] != graph["graph_revision"]:
            raise ProbeError("Plan handoff revision differs from canonical state")
        plan_helper.derive_plan_state(graph)
        return graph, path

    def dispatch(name, phase, prompt, *, resume=None, read_only=False):
        if name in report["actors"] or len(report["actors"]) >= 9:
            raise ProbeError("Actor call budget exhausted or repeated dispatch")
        allowance = min(actor_timeout, remaining())
        before_plan, before_design = {"graphs": tree_pin(plan_root), "audits": tree_pin(audit_root)}, tree_pin(design_root)
        before_target = {path: digest((target / path).read_bytes()) for path in original_target}
        before_head, before_status = driver.git(target, "rev-parse", "HEAD"), driver.git(target, "status", "--porcelain")
        before_design_head = driver.git(design_tree.path, "rev-parse", "HEAD")
        before_design_tree = tree_pin(design_tree.path)
        before_design_status = driver.git(design_tree.path, "status", "--porcelain", "--untracked-files=all")
        recovery_reader = read_only and phase == "design"
        actor_cwd = design_tree.path if phase == "design" else target
        actor_state = state
        actor_sandbox = "read-only" if read_only else "danger-full-access" if phase == "design" else "workspace-write"
        if recovery_reader:
            if driver.git(design_tree.path, "diff", "--name-only", "HEAD") or driver.git(design_tree.path, "diff", "--cached", "--name-only"):
                raise ProbeError("Recovery reader requires a clean tracked candidate")
            actor_cwd = state / "recovery-reader"
            actor_cwd.mkdir(mode=0o700)
            driver.git(actor_cwd, "init", "-b", "trial/recovery-reader")
            actor_state = actor_cwd / "state"
            actor_sandbox = "workspace-write"
        start = time.monotonic()
        report["actors"][name] = {"outcome": "reserved", "thread_ids": [], "timeout_seconds": allowance}
        persist()
        actor = driver.run_actor(prompt=runtime_instruction + "\n" + prompt, cwd=actor_cwd,
            state_home=actor_state, evidence_dir=output / "actors" / name, timeout=allowance,
            sandbox=actor_sandbox, live=True, cli=cli, infrastructure_retries=0,
            persistent=not read_only, resume_from=resume,
            additional_write_dirs=[design_root] if recovery_reader else [])
        report["actors"][name] = actor
        actor["elapsed_host_seconds"] = round(time.monotonic() - start, 3)
        persist()
        if tree_pin(content) != source_pin or digest(Path(accepted["path"]).read_bytes()) != accepted["sha256"]:
            raise ProbeError("Immutable framework or accepted input changed")
        if digest(Path(runtime_artifact["path"]).read_bytes()) != runtime_artifact["sha256"]:
            raise ProbeError("Verified interpreter prerequisite evidence changed")
        if (phase != "plan" or read_only) and {"graphs": tree_pin(plan_root), "audits": tree_pin(audit_root)} != before_plan:
            raise ProbeError("A non-Plan owner changed canonical Plan state")
        if (phase != "design" or read_only) and tree_pin(design_root) != before_design:
            raise ProbeError("A non-Design owner changed canonical Design state")
        if (phase != "design" or read_only) and (driver.git(design_tree.path, "rev-parse", "HEAD") != before_design_head or tree_pin(design_tree.path) != before_design_tree or driver.git(design_tree.path, "status", "--porcelain", "--untracked-files=all") != before_design_status):
            raise ProbeError("A non-Design writer changed the candidate worktree or HEAD")
        if driver.git(target, "rev-parse", "HEAD") != before_head or driver.git(target, "status", "--porcelain") != before_status or {path: digest((target / path).read_bytes()) for path in original_target} != before_target:
            raise ProbeError("An actor changed the integration target")
        if actor["outcome"] != "completed-ungraded" or len(actor["thread_ids"]) != 1:
            raise ProbeError("Actor transport lacks one completed observed thread")
        if resume is not None and actor["thread_ids"] != resume["thread_ids"]:
            raise ProbeError("Resumed answer arrived in a different thread")
        if resume is None and any(other["thread_ids"] == actor["thread_ids"] for key, other in report["actors"].items() if key != name):
            raise ProbeError("A supposedly fresh owner reused another actor's thread")
        try:
            payload = json.loads(actor["final_text"])
        except ValueError as error:
            raise ProbeError("Actor final message is not JSON") from error
        if not isinstance(payload, dict) or payload.get("input_digest") != accepted["sha256"]:
            raise ProbeError("Actor did not bind the immutable accepted input")
        if payload.get("status") == "blocked":
            actor_failure = payload.get("failure")
            reason = actor_failure
            if isinstance(actor_failure, dict):
                reason = actor_failure.get("message") or actor_failure.get("reason")
            if not isinstance(reason, str) or not reason:
                reason = json.dumps(actor_failure, ensure_ascii=False) if actor_failure is not None else "Actor reported blocked without a failure description"
            raise ActorBlocked({"type": "ActorBlocked", "reason": reason, "dispatch_id": name, "phase": phase,
                "thread_id": actor["thread_ids"][0], "actor_failure": actor_failure, "workflow_id": payload.get("workflow_id"),
                "revision": payload.get("revision"), "last_saved_workflow_revision": payload.get("last_saved_workflow_revision", payload.get("revision")),
                "canonical_locator": payload.get("canonical_locator", payload.get("graph_path")), "payload": payload})
        return actor, payload

    common = f"Synthetic authorized development-validation case, not real human approval. Complete accepted input {accepted['path']}, byte digest {accepted['sha256']}. Framework snapshot {content}. Read your exact skill and skills/unslop/SKILL.md. No delegation, nested CLI, external network, publication, new dependency, push, framework edits, or private-state JSON edits. Use public helper operations. On a failed required operation stop immediately and return blocked JSON with the actual failure and last saved workflow revision; never run proof after a failed commit or checkpoint. Preserve all criteria, non-goals and saved decisions. Return strict JSON input_digest, phase, status, workflow_id, revision, and canonical locator. Relay material questions, never infer approval. Both Git identities must be {IDENTITY}. Before EVERY commit, run git var GIT_AUTHOR_IDENT and git var GIT_COMMITTER_IDENT in the actual checkout and correct conflicting overrides. After committing verify saved identities with git show -s --format='%an <%ae> | %cn <%ce>' HEAD. No AI trailers."
    try:
        if any(not callable(getattr(plan_helper, name, None)) for name in ("reserve_plan_audit", "record_plan_audit_result", "apply_plan_audit_result", "load_plan_audits")):
            raise ProbeError("Framework revision lacks required durable Plan audit operations")
        python_runtime = verify_python_runtime()
        runtime_artifact = retain(output / "python-runtime.json", python_runtime)
        Path(runtime_artifact["path"]).chmod(0o400)
        report["python_runtime"] = {"artifact": runtime_artifact, **python_runtime}
        runtime_instruction = f"For every Python helper command or snippet use the verified actual Python3 executable {shlex.quote(python_runtime['executable'])}. Do not rely on the unverified alias python. Runtime prerequisite evidence: {runtime_artifact['path']}, SHA-256 {runtime_artifact['sha256']}. Invoke the supplied executable directly with the helper path or -c/- arguments; do not choose a different interpreter alias."
        selected_browser = Path(browser).resolve() if browser is not None else browser_path()
        report["browser"] = {"path": str(selected_browser), "sha256": digest(selected_browser.read_bytes())}
        plan_actor, plan = dispatch("plan-initial", "plan", common + f" Run $plan in routed parallel mode on branch trial/routed-ui, baseline {baseline}. Independently author a minimum complete graph with required Design join and current user presentation. Do not implement or audit yourself. Return graph_path, status awaiting-answer and question={{id,text,projection_id}} for its unconfirmed presentation. Retain it for later real Design and audit receipts.")
        graph, graph_path = read_plan(plan)
        if plan.get("status") != "awaiting-answer" or not graph["design_join"]["required"] or not plan.get("question"):
            raise ProbeError("Plan lacks required Design join or current approval question")
        design_actor, design = dispatch("design-initial", "design", common + f" Run $design in routed mode, branch {design_tree.branch}, baseline {baseline}. Independently build the native UI in {SOURCE_FILES}. Confirm inherited brief from accepted-input. Declare exact owned paths {SOURCE_FILES + review_paths()}. Preserve native_checks.py and unrelated.txt. Commit source once above baseline and checkpoint BEFORE proof and approval. Through record-check run {sys.executable} native_checks.py native-checks --browser {selected_browser} --output review. Read its actual browser results and limits. No formatter/linter/type/build tool is configured, justify those exceptions. Retain screenshots, review/native-checks.json and design-manifest.json with index.html entry and reproducible localhost launch. Return status awaiting-answer, question={{id,text}}, candidate_commit, review_entry. Do not approve or deliver yet.")
        design_state = design_helper.load_workflow(workflow_id=design["workflow_id"], state_home=state / "expskill")
        if design.get("status") != "awaiting-answer" or not design.get("question") or design_state["approvals"] or design_state["candidate"] is None:
            raise ProbeError("Design did not wait for approval after checkpoint")
        if not injected:
            report["observations"]["actor_native_preview"] = verify_native_preview(design_state, design_tree.path, design_root, selected_browser)

        def answer(phase, actor, payload, bindings):
            question = payload["question"]
            if any(not isinstance(question.get(key), str) or not question[key] for key in ("id", "text")):
                raise ProbeError("Missing stable question or exact question text")
            value = {"run_id": "routed-probe", "phase": phase, "thread_id": actor["thread_ids"][0], "workflow_id": payload["workflow_id"],
                "revision": payload["revision"], "input_digest": accepted["sha256"], "question_id": question["id"], "question": question["text"],
                "answer": definition["synthetic_answers"][phase], "decision_reference": f"synthetic-{phase}-answer", "synthetic": True, "bindings": bindings}
            receipt = retain(output / f"{phase}-answer.json", value)
            report["relay"].append({"answer_receipt": receipt, **value})
            persist()
            return value

        projection = graph["projections"][plan["question"]["projection_id"]]
        if projection["confirmed"] or projection["stale"] or not projection.get("presentation"):
            raise ProbeError("Plan question lacks a current unconfirmed presentation")
        plan_answer = answer("plan", plan_actor, plan, {"projection_id": plan["question"]["projection_id"], "presentation": projection["presentation"]})
        plan_actor, plan = dispatch("plan-answer", "plan", "Continue only your Plan workflow with this owner-bound synthetic answer. Confirm that presentation through typed operations and wait for Design/audit. Return input_digest, phase, status, workflow_id, revision, graph_path, resolved_question_id.\n" + json.dumps(plan_answer), resume=plan_actor)
        read_plan(plan)
        if plan.get("resolved_question_id") != plan_answer["question_id"]:
            raise ProbeError("Plan did not return its bound answer resolution")
        design_answer = answer("design", design_actor, design, {"candidate_commit": design_state["candidate"]["commit"], "brief_digest": design_state["brief"]["digest"], "components": design_state["components"], "evidence_digest": digest(canonical(design_state["evidence"]))})
        design_actor, design = dispatch("design-answer", "design", "Continue only your Design workflow with this explicit synthetic authority. Save current byte-bound approval with trusted-attestation provenance and its synthetic decision_reference. Execute deliver through the public helper CLI and save unchanged stdout in a private file. Do not amend candidate bytes. Return input_digest, phase, status delivered, workflow_id, revision, delivery_receipt_path, resolved_question_id.\n" + json.dumps(design_answer), resume=design_actor)
        if design.get("resolved_question_id") != design_answer["question_id"]:
            raise ProbeError("Design did not return its bound answer resolution")
        receipt_path = private_file(design["delivery_receipt_path"], state)
        receipt_bytes = receipt_path.read_bytes()
        delivery = json.loads(receipt_bytes)
        design_state = design_helper.load_workflow(workflow_id=delivery["workflow_id"], state_home=state / "expskill")
        if design_state["lifecycle"] != "delivered" or delivery["revision"] != design_state["revision"] or delivery["candidate_commit"] != design_answer["bindings"]["candidate_commit"]:
            raise ProbeError("Delivery differs from approved candidate and state")
        plan_helper._validate_design_delivery_receipt(delivery)
        candidate = delivery["candidate_commit"]
        if driver.git(design_tree.path, "rev-parse", "HEAD") != candidate or driver.git(design_tree.path, "rev-list", "--parents", "-n", "1", candidate).split() != [candidate, baseline]:
            raise ProbeError("Design candidate tip or parent changed")
        if driver.git(design_tree.path, "show", "-s", "--format=%an <%ae> | %cn <%ce>", candidate) != IDENTITY + " | " + IDENTITY:
            raise ProbeError("Saved model-authored candidate identity differs from the authorized user")
        if sorted(item["path"] for item in design_state["candidate_payload"]["files"]) != SOURCE_FILES:
            raise ProbeError("Delivery omits a production file")
        for item in design_state["candidate_payload"]["files"]:
            if digest(subprocess.check_output(["git", "show", f"{candidate}:{item['path']}"], cwd=target)) != item["digest"]:
                raise ProbeError("Approved bytes differ from immutable commit")
        if any(item["provenance"]["decision_reference"] != design_answer["decision_reference"] for item in design_state["approvals"].values()):
            raise ProbeError("Approval lacks the synthetic answer provenance")
        report["observations"]["delivery"] = {"receipt": {"path": str(receipt_path), "sha256": digest(receipt_bytes)}, "candidate_commit": candidate, "state_revision": design_state["revision"]}
        _, recovered = dispatch("design-replacement", "design", common + f" Fresh handoff recovery replaces completed thread {design_actor['thread_ids'][0]}. You have a non-product cwd and private namespace write access solely for the public loader lock; product read-only is an instruction obligation that the harness checks byte for byte. Load delivered immutable workflow {delivery['workflow_id']} through load_workflow with explicit state_home {state / 'expskill'}; XDG_STATE_HOME for this reader is separate. Validate original receipt {receipt_path} against immutable commit {candidate} in worktree {design_tree.path}, brief and artifacts. No product or state-record edits, Git mutation, proof commands, initialization, rewrite, approval or redelivery. Return input_digest, phase, status delivered, workflow_id, revision, delivery_receipt_path, replaced_thread_id.", read_only=True)
        if recovered.get("replaced_thread_id") != design_actor["thread_ids"][0] or recovered.get("workflow_id") != delivery["workflow_id"] or Path(recovered["delivery_receipt_path"]) != receipt_path or receipt_path.read_bytes() != receipt_bytes:
            raise ProbeError("Replacement did not preserve original handoff")
        audit_dispatch_id = "routed-probe-audit-1"
        plan_actor, plan = dispatch("plan-design-join", "plan", f"Continue your same Plan. Read unchanged real Design receipt {receipt_path}, SHA-256 {digest(receipt_bytes)}, branch {design_tree.branch} tip {candidate}. Record typed Design join with its actual bindings. Then reserve_plan_audit on the current graph using dispatch_id {audit_dispatch_id}, accepted_input={json.dumps({"path": accepted["path"], "digest": accepted["sha256"]})}, purpose initial, reason Independent retained routed probe audit, and state_home {state / 'expskill'}. Do not invent an audit. Return input_digest, phase, status awaiting-audit, workflow_id, revision, graph_path, audit_dispatch (the unchanged actual helper return). Input digest remains {accepted['sha256']}.", resume=plan_actor)
        graph, graph_path = read_plan(plan)
        if graph["design_join"]["receipt"]["design_delivery_receipt"] != delivery or not graph["design_join"]["fresh"]:
            raise ProbeError("Plan did not join the exact Design receipt")
        audit_reservation = plan["audit_dispatch"]
        audit_dispatch = audit_reservation["dispatch"]
        frozen_graph = audit_dispatch["graph_snapshot"]
        frozen_path = private_file(frozen_graph["path"], audit_root)
        if digest(frozen_path.read_bytes()) != frozen_graph["digest"] or json.loads(frozen_path.read_bytes()) != graph or audit_dispatch["graph_revision"] != graph["graph_revision"] or audit_dispatch["accepted_input"] != {"path": accepted["path"], "digest": accepted["sha256"]} or audit_dispatch["baseline_commit"] != baseline or audit_dispatch["head_commit"] != baseline:
            raise ProbeError("Audit reservation differs from actual accepted input or current Plan")
        reserved_audits = plan_helper.load_plan_audits(target, "trial/routed-ui", graph["workflow_id"], state_home=state / "expskill")
        if reserved_audits["dispatches"].get(audit_dispatch_id) != audit_dispatch or audit_dispatch_id not in reserved_audits["outstanding_dispatch_ids"]:
            raise ProbeError("Audit dispatch does not match the actual durable reservation")
        report["observations"]["audit_reservation"] = audit_reservation
        before_audit = graph_path.read_bytes()
        audit_actor, audit = dispatch("plan-independent-audit", "plan", f"Fresh independent read-only Plan audit, without planner history or sibling judgments. Read {content / 'skills/plan/SKILL.md'}, complete accepted input {accepted['path']} digest {accepted['sha256']}, frozen graph {frozen_path} file digest {frozen_graph['digest']}, graph semantic digest {audit_dispatch['graph_digest']}, workflow {graph['workflow_id']} revision {graph['graph_revision']}, baseline {baseline}, target {target}, and actual native candidate {design_tree.path} at commit {candidate}. Check criteria, facts, scope, dependencies, proof, confirmation and Design join independently. No edits, delegation, approval, research or CLI launch. Do not force a favorable verdict. Return strict JSON input_digest, workflow_id, revision, graph_digest (supplied semantic digest), evidence (IDs from the graph), constraints (strings), findings (id,severity,description,evidence,disposition), limitations. Unresolved findings have disposition open. Retain any missing criterion or observed defect.", read_only=True)
        if audit.get("workflow_id") != graph["workflow_id"] or audit.get("revision") != graph["graph_revision"] or audit.get("graph_digest") != audit_dispatch["graph_digest"] or not isinstance(audit.get("findings"), list) or graph_path.read_bytes() != before_audit:
            raise ProbeError("Audit is missing or bound to another graph")
        audit_raw = private_file(audit_actor["attempts"][-1]["raw_events"], output)
        audit_raw.chmod(0o600)
        audit_result = {"schema_version": "plan-audit-result.v1", "dispatch_id": audit_dispatch_id, "workflow_id": graph["workflow_id"],
            "graph_revision": graph["graph_revision"], "graph_digest": audit_dispatch["graph_digest"], "accepted_input_digest": accepted["sha256"],
            "baseline_commit": baseline, "head_commit": baseline, "actor_session_id": audit_actor["thread_ids"][0], "independent": True,
            "elapsed_seconds": math.ceil(audit_actor["elapsed_host_seconds"]), "output": {"path": str(audit_raw), "digest": digest(audit_raw.read_bytes())},
            "evidence": audit.get("evidence", []), "constraints": audit.get("constraints", []), "findings": audit["findings"], "resolutions": []}
        audit_output = retain(output / "independent-audit-result.json", audit_result)
        plan_actor, plan = dispatch("plan-audit-result", "plan", f"Continue unchanged Plan with actual fresh independent audit result envelope {audit_output['path']} digest {audit_output['sha256']}. Read the exact envelope and call record_plan_audit_result for dispatch {audit_dispatch_id}, then apply_plan_audit_result only if the recorded findings permit it. Preserve all findings and durable accounting even if blocked. These bindings are coordinator-observed actor identity, elapsed time and raw output; do not substitute self-attestation. Never certify your own audit. Return input_digest, phase, status, workflow_id, revision, graph_path, audit_records (unchanged load_plan_audits result). Do not integrate.", resume=plan_actor)
        graph, _ = read_plan(plan)
        audit_records = plan_helper.load_plan_audits(target, "trial/routed-ui", graph["workflow_id"], state_home=state / "expskill")
        recorded_dispatch = audit_records["dispatches"][audit_dispatch_id]
        if recorded_dispatch["result"] != audit_result or recorded_dispatch["actor_session_id"] != audit_actor["thread_ids"][0] or recorded_dispatch["status"] != "accepted" or audit_dispatch_id in audit_records["outstanding_dispatch_ids"] or plan["audit_records"]["dispatches"].get(audit_dispatch_id) != recorded_dispatch:
            raise ProbeError("Plan did not record the actual auditor identity, output and accounting")
        report["observations"]["audit_result"] = {"envelope": audit_output, "result": audit_result, "records": audit_records}
        if audit["findings"]:
            raise ProbeError("Independent audit found unresolved issues")
        if plan_helper.derive_plan_state(graph) != "ready":
            raise ProbeError("Plan is not ready before authorized integration")
        changed = set(driver.git(target, "diff", "--name-only", baseline, candidate).splitlines())
        if not changed or not changed <= set(SOURCE_FILES) or driver.git(target, "status", "--porcelain") or driver.git(target, "rev-parse", "HEAD") != baseline:
            raise ProbeError("Exact integration scope or target baseline changed")
        driver.git(target, "merge", "--ff-only", candidate)
        report["observations"]["host_integration"] = {"operation": "authorized harness-owned fast-forward", "before": baseline, "after": driver.git(target, "rev-parse", "HEAD"), "exact_candidate": candidate, "changed_paths": sorted(changed)}
        report["observations"]["native_verification"] = native_checks(target, output / "native-verification", selected_browser, timeout_seconds=min(60, remaining()))
        plan_actor, plan = dispatch("plan-integrated-revalidation", "plan", f"Continue same Plan after authorized host fast-forward to {candidate}. Load through its public helper, revalidate affected facts/dependencies with typed operations, and preserve unchanged accepted meaning. Never waive stale gates or certify a new independent audit yourself. Return input_digest, phase, status, workflow_id, revision, graph_path. Return an exact blocker if another audit or material decision is required.", resume=plan_actor)
        graph, graph_path = read_plan(plan)
        context = plan_helper._repo_context(target, "trial/routed-ui")
        plan_helper._validate_graph(graph, context, context.branch)
        if plan_helper._stale_repository_evidence(graph, context) or plan_helper.derive_plan_state(graph) != "ready" or driver.git(target, "rev-parse", "HEAD") != candidate:
            raise ProbeError("Integrated target lacks a current ready Plan")
        if any(digest((target / name).read_bytes()) != original_target[name] for name in ("unrelated.txt", "native_checks.py")) or receipt_path.read_bytes() != receipt_bytes:
            raise ProbeError("Unrelated bytes, independent oracle or receipt changed")
        report["observations"]["current_plan"] = {"path": str(graph_path), "sha256": digest(graph_path.read_bytes()), "workflow_id": graph["workflow_id"], "revision": graph["graph_revision"], "derived_state": "ready", "head": candidate}
        report["outcome"] = "offline-protocol-observed" if injected else "routed-protocol-observed"
    except ActorBlocked as error:
        report["outcome"] = "probe-blocked"
        report["failure"] = error.failure
    except Exception as error:
        report["outcome"] = "probe-blocked"
        report["failure"] = {"type": type(error).__name__, "reason": str(error)}
    except BaseException as error:
        report["outcome"] = "probe-interrupted"
        report["failure"] = {"type": type(error).__name__, "reason": str(error)}
        persist()
        raise
    persist()
    return report


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    checks = subparsers.add_parser("native-checks")
    checks.add_argument("--browser", type=Path, required=True)
    checks.add_argument("--output", type=Path, required=True)
    probe = subparsers.add_parser("probe")
    probe.add_argument("--live", action="store_true")
    probe.add_argument("--repository", type=Path, default=ROOT)
    probe.add_argument("--revision", required=True)
    probe.add_argument("--output-root", type=Path, required=True)
    probe.add_argument("--case", type=Path, default=DEFAULT_CASE)
    probe.add_argument("--browser", type=Path)
    probe.add_argument("--actor-timeout", type=int, default=180)
    probe.add_argument("--deadline-seconds", type=int, default=1500)
    args = parser.parse_args()
    try:
        if args.command == "native-checks":
            print(json.dumps(native_checks(Path.cwd(), args.output, args.browser)))
            return 0
        report = run_probe(output_root=args.output_root, revision=args.revision, repository=args.repository, case_path=args.case,
            live=args.live, actor_timeout=args.actor_timeout, deadline_seconds=args.deadline_seconds, browser=args.browser)
        print(json.dumps({"outcome": report["outcome"], "report": str(args.output_root / "report.json")}))
        return 0 if report["outcome"] == "routed-protocol-observed" else 1
    except (ProbeError, OSError, ValueError, ImportError) as error:
        print(json.dumps({"outcome": "prerequisite-blocked", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
