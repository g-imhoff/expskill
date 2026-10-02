import argparse
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/skill-builder/live-public-workflows.json"
DRIVER = ROOT / "scripts/live_trials.py"
SOURCES = {"skill": "SKILL.md", "contracts": "references/artifact-contracts.md", "rubric": "references/evaluation-rubric.md", "helper": "scripts/run_state.py"}


class WorkflowError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(canonical(value))
    path.chmod(0o600)
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def load_module(path, name):
    if not Path(path).is_file():
        raise WorkflowError(f"Missing prerequisite: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tree(path):
    if not path.exists():
        return None
    result = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise WorkflowError("Workflow evidence or target contains a symlink")
        if item.is_file():
            result[item.relative_to(path).as_posix()] = digest(item.read_bytes())
    return result


def shell_calls(command):
    script = command
    for _ in range(2):
        if not re.match(r"^\s*(?:/[^\s]+/)?(?:sh|bash|zsh)\s+-(?:lc|c)(?:\s|$)", script):
            break
        parts = shlex.split(script)
        if len(parts) == 3 and Path(parts[0]).name in {"sh", "bash", "zsh"} and parts[1] in {"-c", "-lc"}:
            script = parts[2]
        else:
            break
    segments, start, index, quote, eligible, heredocs = [], 0, 0, None, True, []
    while index < len(script):
        char = script[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
            elif quote == '"' and (char == "`" or script.startswith("$(", index)):
                return []
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "`" or script.startswith("$(", index) or char in "(){}":
            return []
        elif char == "#" and (index == start or script[index - 1].isspace()):
            end = script.find("\n", index)
            script = script[:index] + script[end:] if end >= 0 else script[:index]
            continue
        elif script.startswith("<<", index):
            match = re.match(r"<<(-?)[ \t]*(?:'([A-Za-z_][A-Za-z_0-9]*)'|\"([A-Za-z_][A-Za-z_0-9]*)\"|([A-Za-z_][A-Za-z_0-9]*))", script[index:])
            if not match:
                return []
            heredocs.append((next(value for value in match.groups()[1:] if value), bool(match[1])))
            index += len(match[0])
            continue
        elif char in ";\n&|":
            if char == "&" and index > start and script[index - 1] in "><":
                index += 1
                continue
            operator = script[index:index + 2] if script[index:index + 2] in {"&&", "||"} else char
            if operator in {"|", "&"}:
                return []
            piece = script[start:index].strip()
            if piece:
                segments.append((piece, eligible))
                eligible = operator in {";", "\n"}
            index += len(operator)
            if char == "\n" and heredocs:
                for delimiter, tabs in heredocs:
                    while index < len(script):
                        end = script.find("\n", index)
                        end = len(script) if end < 0 else end
                        line = script[index:end]
                        index = min(end + 1, len(script))
                        if (line.lstrip("\t") if tabs else line) == delimiter:
                            break
                    else:
                        return []
                heredocs = []
            start = index
            continue
        index += 1
    if quote or heredocs:
        return []
    if script[start:].strip():
        segments.append((script[start:].strip(), eligible))
    calls = []
    for piece, executes in segments:
        lexer = shlex.shlex(piece, posix=True, punctuation_chars="<>")
        lexer.whitespace_split, lexer.commenters = True, ""
        words, parts, index = list(lexer), [], 0
        while index < len(words):
            if words[index] in {"<", ">", ">>", "<<", "<<-"}:
                if index + 1 >= len(words):
                    return []
                if parts and parts[-1] in {"0", "1", "2"}:
                    parts.pop()
                index += 2
            else:
                parts.append(words[index])
                index += 1
        if parts and (parts[0] in {"if", "then", "else", "elif", "fi", "for", "while", "until", "case", "esac", "do", "done", "function", "select", "!", "exit", "return", "exec", "eval", "trap", "source", ".", "alias", "unalias", "builtin", "command", "set"} or re.match(r"[A-Za-z_][A-Za-z_0-9]*=", parts[0])):
            return []
        if executes and parts:
            calls.append(parts)
    return [(parts, len(segments) > 1) for parts in calls]


def helper_call(parts, helper):
    if len(parts) < 3 or Path(parts[1]) != helper:
        return None
    executable = shutil.which(parts[0]) if not Path(parts[0]).is_absolute() else parts[0]
    if not executable or Path(executable).resolve() != Path(sys.executable).resolve():
        return None
    command, state_root, index = None, None, 2
    while index < len(parts):
        word = parts[index]
        if word == "--state-root" and index + 1 < len(parts):
            state_root = Path(parts[index + 1])
            index += 2
        elif word.startswith("--state-root="):
            state_root = Path(word.split("=", 1)[1])
            index += 1
        elif word == "--yes":
            index += 1
        elif command is None and not word.startswith("-"):
            command = word
            index += 1
        else:
            return None
    return command, state_root


def receipt_proves_call(item, command, state_root, current, prior_sequence):
    if current is None or state_root is None or not state_root.is_absolute():
        return False
    try:
        result = json.loads(item.get("aggregated_output", item.get("stdout", "")))
        if not isinstance(result, dict) or result.get("schema_version") != "skill-builder-operation.v1" or result.get("workflow_id") != current["workflow_id"]:
            return False
        sequence = result.get("sequence")
        if type(sequence) is not int or not prior_sequence < sequence <= current["head_sequence"] or not re.fullmatch(r"[0-9a-f]{32}", result["workflow_id"]):
            return False
        receipt = json.loads((state_root / "live" / result["workflow_id"] / "receipts" / f"{sequence:08d}.json").read_text())
        if receipt.get("workflow_id") != result["workflow_id"] or receipt.get("sequence") != sequence:
            return False
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
        if result.get("receipt_digest") != receipt.get("receipt_digest") or digest(canonical(unsigned)) != receipt.get("receipt_digest") or result.get("stage") != receipt.get("destination_stage"):
            return False
        operation = result.get("operation")
        expected = {"initialize": "initialize", "retain": "retain-artifact", "invalidate": "invalidate", "finalize": "finalize"}
        if command == "transition":
            return operation == receipt["event"] and receipt["event"] not in {"initialize", "retain-artifact", "invalidate", "finalize"}
        return operation == expected.get(command) == receipt["event"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def public_command_seen(probe, origin, helper, commands, *, state_root=None, current=None, prior_sequence=-1):
    observed = set()
    for event in origin["events"]:
        item = event.get("item", {})
        if not isinstance(item, dict):
            continue
        if event.get("type") != "item.completed" or item.get("type") != "command_execution" or item.get("exit_code") != 0:
            continue
        try:
            for parts, compound in shell_calls(item.get("command", "")):
                call = helper_call(parts, helper)
                if call is None or call[0] not in commands or state_root is not None and call[1] != state_root:
                    continue
                if compound and not receipt_proves_call(item, call[0], call[1], current, prior_sequence):
                    continue
                observed.add(call[0])
        except (ValueError, TypeError):
            pass
    if not set(commands) <= observed:
        raise WorkflowError("Raw actor events do not prove the required direct public helper commands")


class Workflow:
    def __init__(self, *, driver, probe, root, helper, sources, definition, definition_evidence, mode, budget, cli):
        self.driver, self.probe, self.root, self.helper = driver, probe, root, helper
        self.sources, self.definition, self.definition_evidence = sources, definition, definition_evidence
        self.mode, self.budget, self.cli = mode, budget, cli
        self.state_home = root / "actor-state"
        self.state_root = self.state_home / "public-state"
        self.workspace = root / "coordinator-workspace"
        self.workspace.mkdir(parents=True, mode=0o700)
        probe.initialize_workspace(driver, self.workspace, budget.remaining)
        self.target = root / "canonical-target" / "count-lines"
        if mode == "improve":
            (self.target / "scripts").mkdir(parents=True)
            (self.target / "SKILL.md").write_text(definition["improve_baseline"]["skill"])
            (self.target / "scripts/count_lines.py").write_text(definition["improve_baseline"]["helper"])
        self.target_pin = tree(self.target)
        self.workflow_id = None
        self.current = None
        self.observation = {"mode": mode, "state_root": str(self.state_root), "public_helper": str(self.helper), "actors": [], "public_observations": [], "cycles": [], "outcome": "in-progress",
                            "synthetic_user_decisions": definition_evidence, "full_builder_conformance_claim": False,
                            "coverage_limit": "Small known development bank with synthetic user decisions; no claim of general reliability or authentic user confirmation."}
        self.observation_path = root / "workflow.json"
        self.cases = []
        for case in definition["cases"]:
            target = root / "case-targets" / case["case_id"]
            target.mkdir(parents=True, mode=0o700)
            for name, text in case["files"].items():
                file = target / name
                file.write_text(text, encoding="utf-8")
                file.chmod(0o600)
            requests = [text.format(target=target) for text in case["requests"]]
            request_path = root / "requests" / (case["case_id"] + ".json")
            evidence = save(request_path, requests)
            snapshot = self.public("snapshot", {"target": str(target)})
            self.cases.append({"case_id": case["case_id"], "partition": case["partition"], "target": str(target), "requests": requests,
                               "request": evidence, "setup": snapshot, "setup_digest": digest(canonical(snapshot))})
        self.case_pin = {case["target"]: tree(Path(case["target"])) for case in self.cases}
        self.decisions = save(root / "synthetic-user-decisions.json", {"synthetic_user_decisions": definition["synthetic_user_decisions"],
                               "improve_baseline": definition["improve_baseline"] if mode == "improve" else None})
        self.retained_pins = []
        self.persist()

    def persist(self):
        save(self.observation_path, self.observation)

    def guard(self):
        if tree(self.target) != self.target_pin:
            raise WorkflowError("Canonical target changed during isolated evaluation")
        if any(digest(Path(source["path"]).read_bytes()) != source["sha256"] for source in self.sources.values()):
            raise WorkflowError("Immutable framework source changed")
        if digest(Path(self.definition_evidence["path"]).read_bytes()) != self.definition_evidence["sha256"]:
            raise WorkflowError("Frozen synthetic user decisions changed")
        for evidence in self.retained_pins + [self.decisions]:
            if digest(Path(evidence["path"]).read_bytes()) != evidence["sha256"]:
                raise WorkflowError("Retained actor or controller evidence changed")

    def actor_packet(self, role):
        blind = role.startswith("research-") or role == "candidate-author" or role.startswith("invalidate-repair-")
        packet = {"mode": self.mode, "workflow_id": self.workflow_id, "target": str(self.target), "state_root": str(self.state_root),
                  "synthetic_decisions": self.decisions, "only_owned_candidate_paths": ["SKILL.md", "scripts/count_lines.py"]}
        if role == "freeze-evaluation" or role == "candidate-author" or role.startswith("invalidate-repair-") or role.startswith("trial-assessment-"):
            packet["cases"] = [case for case in self.cases if not blind or case["partition"] == "visible_development"]
        if not blind:
            packet["actor_evidence_root"] = str(self.root / "actors")
        evidence = save(self.root / "controller-inputs" / (role + ".json"), packet)
        self.retained_pins.append(evidence)
        return evidence

    def public(self, command, payload):
        argv = [sys.executable, str(self.helper), command, "--state-root", str(self.state_root)]
        result = self.driver.run_process_group(argv, prompt=canonical(payload).decode(), cwd=self.workspace,
                                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=min(240, self.budget.remaining()))
        index = len(self.observation["public_observations"])
        evidence = save(self.root / "public" / f"{index:04d}-{command}.json", {"argv": argv, "request": payload, **result})
        if hasattr(self, "retained_pins"):
            self.retained_pins.append(evidence)
        self.observation["public_observations"].append({"command": command, "evidence": evidence})
        self.persist()
        if result["exit_code"] != 0 or result["timed_out"]:
            raise WorkflowError(f"Public {command} gate failed: {result['stderr']}")
        return json.loads(result["stdout"])

    def refresh(self):
        self.current = self.public("load", {"workflow_id": self.workflow_id})
        self.observation["latest_public_state"] = self.current
        self.persist()
        return self.current

    def accepted(self, artifact_type):
        matches = [record for record in self.current["artifact_index"].values() if record["type"] == artifact_type and record["derived_status"] == "accepted"]
        if not matches:
            raise WorkflowError(f"No accepted {artifact_type} artifact")
        record = max(matches, key=lambda value: value["producing_sequence"])
        artifact = self.state_root / "live" / self.workflow_id / record["path"]
        envelope = json.loads((artifact / "envelope.json").read_text())
        payload_path = self.state_root / "live" / self.workflow_id / envelope["payload_path"]
        if digest(payload_path.read_bytes()) != envelope["payload_digest"]:
            raise WorkflowError("Accepted payload bytes disagree with public envelope")
        return record, envelope, payload_path, json.loads(payload_path.read_text())

    def actor(self, role, prompt, *, cwd=None, source_reads=True, read_only=False, persistent=False, resume_from=None, model=None):
        self.guard()
        if self.budget.calls >= self.budget.maximum_calls:
            raise WorkflowError("Cumulative actor-attempt allowance exhausted")
        retry = int(self.budget.maximum_calls - self.budget.calls >= 2)
        identity = self.mode + ":" + role
        if cwd is None:
            cwd = self.root / "actor-workspaces" / role
            cwd.mkdir(parents=True)
            self.probe.initialize_workspace(self.driver, cwd, self.budget.remaining)
        if source_reads:
            packet = self.actor_packet(role)
            commands = [shlex.join(["cat", self.sources[name]["path"]]) for name in ("skill", "contracts", "rubric")]
            prompt = (f"Apply the assigned Skill Builder stage using this immutable framework snapshot. First read all three complete source references in separate tool operations: {json.dumps(commands)}. "
                      f"Public helper: {self.helper}. Use its --help and supported CLI only, never private Python APIs or direct state edits. "
                      f"Use state root {self.state_root}; this is an isolated test workflow. Scratch and candidate writes belong under {cwd}. "
                      f"Actor producer identity: {identity}. Preserve this exact identity in your payloads and envelopes. "
                      f"Mode: {self.mode}. Controller input locator: {packet['path']}, SHA-256 {packet['sha256']}. "
                      "Synthetic user decisions are explicitly a simulation, not a real user event. No target score or desired verdict is supplied. "
                      "Author operational artifacts from actual evidence. Never import test fixtures, invent raw events, fill criteria with default success, or claim unobserved outcomes. "
                      "Only the public helper may mutate its state. Invoke it directly as a tool command with JSON stdin redirected from an owned request file, so raw traces prove each public command. Keep helper invocations unconditional, without shell loops, aliases, eval, traps, or pipelines. When the same tool command also prepares a request or cats redirected results, expose the helper's original operation JSON as its entire terminal output; that receipt must belong to this exact workflow and state root. "
                      "Do not delegate, commit, deliver, install, publish, or clean state. " + prompt)
            prompt += " Return exactly one JSON object with workflow_id as a string or null, stage as a string, and artifact_ids as an array of strings naming every newly created public artifact, including helper-generated resolution or invalidation records. An initialized workflow must return its actual workflow_id; the controller input names the existing active ID. Null is allowed only before initialization. Research-only replies instead name their scratch file locators as specified. No code fences or surrounding prose."
        before_candidate = None
        if read_only and self.current and any(record["type"] == "candidate-record" and record["derived_status"] == "accepted" for record in self.current["artifact_index"].values()):
            before_candidate = tree(Path(self.accepted("candidate-record")[3]["isolated_locator"]))
        options = {"prompt": prompt, "cwd": cwd, "state_home": self.state_home,
                   "evidence_dir": self.root / "actors" / role, "timeout": min(self.budget.actor_timeout, self.budget.remaining() / (1 + retry)),
                   "sandbox": "read-only" if not source_reads else "workspace-write", "live": True, "cli": self.cli,
                   "infrastructure_retries": retry}
        if model is not None:
            options["model"] = model
            options["cli"] = [self.cli, "--search", "-c", 'model_reasoning_effort="max"']
        if persistent or resume_from is not None:
            options.update(persistent=True, resume_from=resume_from)
        actor = self.driver.run_actor(**options)
        self.budget.calls += len(actor.get("attempts", []))
        row = {"role": role, "producer_identity": identity, "transport": actor}
        self.observation["actors"].append(row)
        for attempt in actor.get("attempts", []):
            self.retained_pins.append({"path": attempt["raw_events"], "sha256": attempt["raw_events_sha256"]})
            if attempt.get("stderr") and Path(attempt["stderr"]).is_file():
                self.retained_pins.append({"path": attempt["stderr"], "sha256": digest(Path(attempt["stderr"]).read_bytes())})
        if actor.get("evidence") and Path(actor["evidence"]).is_file():
            self.retained_pins.append({"path": actor["evidence"], "sha256": digest(Path(actor["evidence"]).read_bytes())})
        self.persist()
        if len(actor.get("attempts", [])) > 1 + retry or self.budget.calls > self.budget.maximum_calls:
            raise WorkflowError("Retained actor attempts exceed their reserved allowance")
        expected_resume = resume_from["thread_ids"][0] if resume_from else None
        seen = self.budget.seen_threads - {expected_resume} if expected_resume else self.budget.seen_threads
        origin = self.probe.actor_origin(actor, seen)
        if expected_resume and origin["thread_id"] != expected_resume:
            raise WorkflowError("Multi-turn trial did not resume its original observed context")
        self.budget.seen_threads.update(seen)
        row["context_identity"] = origin["thread_id"]
        if source_reads:
            self.probe.require_framework_reads(origin, {name: self.sources[name] for name in ("skill", "contracts", "rubric")})
        reply = json.loads(actor["final_text"]) if source_reads else None
        if source_reads and (not isinstance(reply, dict) or set(reply) != {"workflow_id", "stage", "artifact_ids"} or not isinstance(reply["workflow_id"], (str, type(None))) or not isinstance(reply["stage"], str) or not isinstance(reply["artifact_ids"], list) or any(not isinstance(value, str) for value in reply["artifact_ids"])):
            raise WorkflowError("Actor reply violates the explicit workflow transport contract")
        if self.workflow_id and source_reads and reply["workflow_id"] != self.workflow_id:
            raise WorkflowError("Actor switched the active public workflow")
        if before_candidate is not None and tree(Path(self.accepted("candidate-record")[3]["isolated_locator"])) != before_candidate:
            raise WorkflowError("Independent judge altered candidate source")
        self.guard()
        self.persist()
        return actor, origin, reply

    def stage(self, role, prompt, expected, commands, *, read_only=False):
        prior_ids = set(self.current["artifact_index"]) if self.current else set()
        prior_sequence = self.current["head_sequence"] if self.current else -1
        actor, origin, reply = self.actor(role, prompt, read_only=read_only)
        if self.workflow_id is None:
            self.workflow_id = reply["workflow_id"]
        self.refresh()
        public_command_seen(self.probe, origin, self.helper, commands, state_root=self.state_root, current=self.current, prior_sequence=prior_sequence)
        if self.current["stage"] != expected or reply["stage"] != expected:
            raise WorkflowError(f"{role} stopped at public stage {self.current['stage']}; expected {expected}")
        new_ids = set(self.current["artifact_index"]) - prior_ids
        if set(reply["artifact_ids"]) != new_ids:
            raise WorkflowError("Actor reply omits or misattributes newly retained operational artifacts")
        for artifact_id in new_ids:
            record = self.current["artifact_index"].get(artifact_id)
            if record is None:
                raise WorkflowError("Actor named an unretained artifact")
            artifact = self.state_root / "live" / self.workflow_id / record["path"] / "envelope.json"
            envelope = json.loads(artifact.read_text())
            generated_resolution = role == "resolve-baseline" and record["type"] == "resolution-record" and record["producing_sequence"] == 0 and envelope["producer"] == "main-agent"
            generated_invalidation = role.startswith("invalidate-repair-") and record["type"] == "invalidation-record" and envelope["producer"] == "main-agent" and "invalidate" in commands
            if generated_resolution:
                if self.current["active_target_lock"]["owner_identity"] != self.mode + ":" + role:
                    raise WorkflowError("Public initialization owner disagrees with the live actor")
                self.observation["helper_generated_resolution"] = {"artifact_id": artifact_id, "producer": envelope["producer"], "live_initializer": self.mode + ":" + role}
            elif generated_invalidation:
                receipt = json.loads((self.state_root / "live" / self.workflow_id / "receipts" / f"{record['producing_sequence']:08d}.json").read_text())
                if artifact_id != f"invalidation-{record['producing_sequence']:08d}" or receipt["event"] != "invalidate" or artifact_id not in {binding["artifact_id"] for binding in receipt["relevant_artifact_digests"]}:
                    raise WorkflowError("Claimed helper-generated invalidation lacks its exact public receipt")
                self.observation.setdefault("helper_generated_invalidations", []).append({"artifact_id": artifact_id, "producer": envelope["producer"], "live_invalidator": self.mode + ":" + role})
            elif envelope["producer"] != self.mode + ":" + role:
                raise WorkflowError("Operational artifact producer does not match the retained live actor")
        return actor

    def trials(self, cycle):
        evaluation = self.accepted("evaluation-pack")[3]
        frozen = [case for partition in ("visible_development", "frozen_validation", "hidden_release") for case in evaluation["partitions"][partition]]
        if [case["case_id"] for case in frozen] != [case["case_id"] for case in self.cases]:
            raise WorkflowError("Frozen evaluation changed the benchmark's complete case scope")
        candidate = self.accepted("candidate-record")[3]
        package = Path(candidate["isolated_locator"])
        observations = []
        for definition, case in zip(frozen, self.cases, strict=True):
            if definition["raw_request_digest"] != case["request"]["sha256"] or definition["setup_manifest_digest"] != case["setup_digest"]:
                raise WorkflowError("Frozen request or setup binding differs from actual benchmark inputs")
            if tree(Path(case["target"])) != self.case_pin[case["target"]]:
                raise WorkflowError("Trial fixture was mutated; frozen setup cannot be reused")
            prefix = self.root / "trial-observations" / f"cycle-{cycle}" / case["case_id"]
            prefix.mkdir(parents=True)
            before = self.public("snapshot", {"target": case["target"]})
            actors = []
            prompts = []
            outputs = []
            raw_events = []
            previous = None
            for turn, request in enumerate(case["requests"]):
                role = f"trial-{cycle}-{case['case_id']}-{turn}"
                if previous:
                    cwd = Path(previous["transport"]["context"]["cwd"])
                else:
                    cwd = self.root / "trial-workspaces" / role
                    cwd.mkdir(parents=True)
                    self.probe.initialize_workspace(self.driver, cwd, self.budget.remaining)
                prompt = (f"First execute {shlex.join(['cat', str(package / 'SKILL.md')])} as a tool command to load the candidate skill, and read its supporting resource when needed. Treat it as the selected skill only when this request matches its explicit invocation policy. " if not previous else "Continue the same user's conversation and loaded skill. ") + request
                prompts.append(prompt)
                actor, origin, _ = self.actor(role, prompt, cwd=cwd, source_reads=False, persistent=len(case["requests"]) > 1, resume_from=previous)
                previous = actor
                actors.append(actor)
                if turn == 0:
                    self.probe.require_framework_reads(origin, {"target_skill": {"path": str(package / "SKILL.md")}})
                outputs.append(actor["final_text"])
                for attempt in actor["attempts"]:
                    raw_events.append(Path(attempt["raw_events"]).read_bytes())
            after = self.public("snapshot", {"target": case["target"]})
            raw = {"request": Path(case["request"]["path"]).read_bytes(), "prompt": canonical(prompts),
                   "tool-events": b"".join(raw_events), "output": canonical(outputs), "before-manifest": canonical(before),
                   "after-manifest": canonical(after), "filesystem-result": canonical({"before": before, "after": after})}
            evidence = {}
            for name, data in raw.items():
                path = prefix / (name + ".bin")
                path.write_bytes(data)
                path.chmod(0o600)
                evidence[name] = {"path": str(path), "sha256": digest(data)}
                self.retained_pins.append(evidence[name])
            self.retained_pins.append(save(prefix / "case.json", definition))
            shutil.copytree(package, prefix / "loaded-skill")
            for path in (prefix / "loaded-skill").rglob("*"):
                if path.is_file():
                    self.retained_pins.append({"path": str(path), "sha256": digest(path.read_bytes())})
            observations.append({"case_id": case["case_id"], "fresh_context_identity": actors[0]["thread_ids"][0],
                                 "actors": actors, "evidence": evidence, "case_path": str(prefix / "case.json"), "loaded_skill_path": str(prefix / "loaded-skill")})
        packet = save(self.root / f"trial-cycle-{cycle}.json", {"candidate": candidate, "observations": observations,
                      "origin": "Host frames only actual prompts, messages, raw tool events and public snapshots. No host verdicts."})
        self.retained_pins.append(packet)
        self.observation["cycles"].append({"cycle": cycle, "trial_evidence": packet})
        self.persist()
        return packet

    def check_trial_retention(self, packet):
        observed = json.loads(Path(packet["path"]).read_text())["observations"]
        record, _, _, retained = self.accepted("trial-pack")
        cases = {case["case_id"]: case for case in retained["cases"]}
        if set(cases) != {case["case_id"] for case in observed}:
            raise WorkflowError("Assessment omitted or invented an observed trial")
        fields = {"request_digest": "request", "raw_prompt_digest": "prompt", "tool_event_digest": "tool-events",
                  "output_digest": "output", "before_target_manifest_digest": "before-manifest",
                  "after_target_manifest_digest": "after-manifest", "filesystem_result_digest": "filesystem-result"}
        artifact = self.state_root / "live" / self.workflow_id / record["path"] / "raw" / "evidence"
        for observation in observed:
            case = cases[observation["case_id"]]
            if case["fresh_context_identity"] != observation["fresh_context_identity"]:
                raise WorkflowError("Assessment invented the trial context identity")
            for field, name in fields.items():
                evidence = observation["evidence"][name]
                if digest(Path(evidence["path"]).read_bytes()) != evidence["sha256"] or case[field] != evidence["sha256"]:
                    raise WorkflowError("Assessment substituted or altered original recorder evidence")
            if tree(artifact / observation["case_id"] / "loaded-skill") != tree(Path(observation["loaded_skill_path"])):
                raise WorkflowError("Assessment retained a different loaded candidate package")

    def check_verification_origin(self, actor):
        origin = self.probe.actor_origin(actor, set())
        package = Path(self.accepted("candidate-record")[3]["isolated_locator"])
        verification = self.accepted("verification-record")[3]
        inputs = {Path(case["target"]) / name for case, definition in zip(self.cases, self.definition["cases"], strict=True) for name in definition["files"]}
        operations = []
        for event in origin["events"]:
            item = event.get("item", {})
            if not isinstance(item, dict):
                continue
            if event.get("type") != "item.completed" or item.get("type") != "command_execution" or type(item.get("exit_code")) is not int:
                continue
            output = item.get("aggregated_output", item.get("stdout"))
            if not isinstance(output, str):
                continue
            operations.append((self.probe.command_parts(item.get("command", "")), item["exit_code"], digest(output.encode()),
                               self.probe.helper_invocation(item.get("command", ""), package)))
        qualified = False
        for command in verification["commands"]:
            parts = self.probe.command_parts(command["command"])
            matches = [operation for operation in operations if operation[:3] == (parts, command["exit_status"], command["output_digest"])]
            if not matches:
                raise WorkflowError("Verification claim lacks its actual terminal command and exact output bytes")
            qualified = qualified or any(operation[3] in inputs for operation in matches)
        if not qualified:
            raise WorkflowError("Verification did not execute the exact current candidate on a frozen input")

    def run(self, maximum_repairs):
        try:
            self.stage("resolve-baseline", "Resolve stage 1 and capture the real mode-specific stage 2 baseline. Set the initialize request's owner_identity to your exact producer identity. Initialize the public run, retain baseline raw observations, and accept capture-baseline. Include the helper-generated resolution ID in your reply. Never modify the canonical target.", "baseline", ("initialize", "retain", "transition"))
            research = []
            for lane, question in enumerate(("Domain techniques for counting UTF-8 text lines safely.", "Agent skill invocation, boundaries, authority and concise packaging.", "Adversarial evaluation and repeatable evidence for this exact job.")):
                actor, origin, reply = self.actor(f"research-{lane}", f"Run only blind research lane {lane}: {question} Maximum three primary sources. Do not read sibling lane output, candidate direction, or hidden cases. Use actual web search and retain your independently authored evidence cards and actual source observations in your own scratch path. For this research-only stage, artifact_ids must contain absolute paths to those retained files. Do not advance public state.", model="gpt-5.6-luna")
                if not any(event.get("item", {}).get("type") == "web_search" for event in origin["events"] if isinstance(event.get("item"), dict)):
                    raise WorkflowError("Research lane lacks retained actual web-search events")
                research.append(actor)
                if not reply["artifact_ids"]:
                    raise WorkflowError("Research actor did not retain any source observations")
                scratch = self.root / "actor-workspaces" / f"research-{lane}"
                for value in reply["artifact_ids"]:
                    path = Path(value)
                    if not path.is_absolute() or not path.resolve().is_relative_to(scratch.resolve()) or path.is_symlink() or not path.is_file():
                        raise WorkflowError("Research evidence locator escapes its blind lane workspace")
                    self.retained_pins.append({"path": str(path), "sha256": digest(path.read_bytes())})
            evidence = save(self.root / "research-actors.json", research)
            self.retained_pins.append(evidence)
            self.stage("synthesis-contract", f"Use only retained research lane evidence at {evidence['path']}. Complete stages 3–7 with a live-authored research pack, complete sieve, challenged alternatives, concrete contract, and explicitly synthetic frozen user decision/confirmation event. Retain the actual source bytes and bind all provenance. Advance through complete-research, sieve-evidence, accept-design, accept-contract, confirm-contract. No candidate edits.", "confirmed", ("retain", "transition"))
            self.stage("freeze-evaluation", "Perform stage 8. Author the complete selective 100-criterion evidence map, scoring parameters and all frozen cases using the actual request/setup packets. Cover every criterion/parameter/case with relevant evidence. Keep validation and hidden expectations away from implementers/trial agents. Retain evaluation-pack.v2 and accept freeze-evaluation before any candidate work.", "evaluation", ("retain", "transition"))
            evaluation = self.accepted("evaluation-pack")[3]
            identifiers = set(re.findall(r"^\|\s*((?:TR|SC|WF|CO|OU|SA|RE|CP|CE|TE)(?:10|[1-9]))\s*\|", Path(self.sources["rubric"]["path"]).read_text(), re.M))
            if len(identifiers) != 100 or set(evaluation.get("criterion_evidence_map", {})) != identifiers:
                raise WorkflowError("Live-authored evaluation mapping does not cover the current 100 criteria")
            for cycle in range(maximum_repairs + 1):
                if cycle:
                    self.stage(f"invalidate-repair-{cycle}", "Select the actual lowest category from current independent scoring, breaking ties as the rubric requires. When a post-score review is negative, use its mapped material finding as the blocking evidence even if prior scores were ten. Use public invalidate on the selected candidate before changing it, preserve history, repair only the isolated candidate, rerun real local checks, retain a new candidate record and accept-candidate. Preserve the previous isolated package bytes; copy it into a new isolated package under your own writable scratch workspace and record that new locator. Read the bounded review correction and visible cases; do not inspect hidden or validation oracles or other roles' controller packets. Do not alter acceptance or claim that scores improved before fresh evidence.", "candidate", ("invalidate", "retain", "transition"))
                else:
                    self.stage("candidate-author", "Perform stage 9 in an isolated package under your own scratch workspace. Read only the confirmed contract, baseline, visible cases and required host resources. Do not open evaluation payloads or validation/hidden prompts/oracles, controller packets belonging to other roles, or the complete developer fixture. The current evaluation envelope digest may be read to bind your candidate. Build the two owned files, run real local checks, retain a current candidate record and accept-candidate. Preserve canonical target bytes.", "candidate", ("retain", "transition"))
                packet = self.trials(cycle)
                self.stage(f"trial-assessment-{cycle}", f"Perform stage 10 using actual native trace packet {packet['path']}. Independently assess each observed case against frozen assertions. Author trial-pack.v2 and retain the exact evidence files, complete loaded packages and case records at helper-required paths. Derive verdicts from evidence, never expectation. Accept complete-trials, preserving failures truthfully.", "trials", ("retain", "transition"))
                self.check_trial_retention(packet)
                self.observation["cycles"][-1]["trial_status"] = self.accepted("trial-pack")[3]["status"]
                self.stage(f"pre-review-{cycle}", "Perform independent stage 11 review. Inspect original raw trial evidence and all current input artifacts. Read-only to candidate source. No desired verdict is supplied. Retain your valid severity-mapped review with your exact actor producer identity and accept-review. If evidence is invalid, stop instead of manufacturing validity.", "reviewed", ("retain", "transition"), read_only=True)
                self.stage(f"scoring-{cycle}", "Perform stage 12 as a separate scorer. Author all 100 criterion results from the frozen selective mapping and current evidence, with every actual parameter/case/raw/receipt/review binding. Missing evidence earns zero. Independently assess BR1–BR10 conformance, explicitly limiting claims to this synthetic benchmark. Negative material findings must remain failing. Retain scorecard.v2 and conformance and accept-scores without inventing desired scores.", "scored", ("retain", "transition"), read_only=True)
                scores = self.accepted("target-scorecard")[3]
                self.observation["cycles"][-1]["scores"] = scores
                if sum(len(category["criteria"]) for category in scores["categories"]) != 100:
                    raise WorkflowError("Scoring actor did not author a complete 100-result scorecard")
                negative = self.accepted("review-record")[3]["verdict"] == "not ready"
                self.observation["cycles"][-1]["negative_review"] = negative
                self.persist()
                if negative or any(category["score"] != 10 for category in scores["categories"]):
                    if cycle == maximum_repairs:
                        raise WorkflowError("Honest review/scoring remains below the public final-review gate after bounded repairs")
                    continue
                if any(gate["status"] != "pass" for gate in self.accepted("builder-run-conformance-ledger")[3]["gates"].values()):
                    raise WorkflowError("Current independently assessed Builder conformance fails; no final-review dispatch is authorized")
                self.stage(f"final-review-{cycle}", "Perform a fresh independent stage 13 post-score review, separate from pre-review and scoring. Bind current accepted scorecard/conformance payloads and exact candidate. Retain review.v3 after scores and accept-final-review. No desired verdict is supplied. A negative final review is evidence, not permission to verify.", "final-reviewed", ("retain", "transition"), read_only=True)
                if self.accepted("review-record")[3]["verdict"] == "not ready":
                    self.observation["cycles"][-1]["negative_review"] = True
                    self.persist()
                    if cycle == maximum_repairs:
                        raise WorkflowError("Accepted independent final review remains negative after bounded repairs")
                    continue
                verifier = self.stage(f"verification-{cycle}", f"Perform independent exact-revision verification after accepted final review. Execute actual frozen operations, retain raw commands/results and before/after manifests, and derive the outcome honestly. At least one verification tool command must consist of exactly three arguments: {sys.executable}, the absolute current candidate scripts/count_lines.py path, and one absolute frozen input file path. Use no shell pipeline, redirection or wrapper for this qualifying command. Every commands entry must name an actually observed tool command and hash its exact UTF-8 terminal output bytes, preserving newlines. Retain output through actual terminal capture of the same deterministic operation, never handwritten transcription. Bind current final review, scorecard, conformance, trials and candidate. Retain verification and accept-verification only if valid.", "verified", ("retain", "transition"), read_only=True)
                self.check_verification_origin(verifier)
                self.stage(f"finalize-{cycle}", "Retain release evidence for the exact independently reviewed and verified revision, with no delivery/cleanup authority. Invoke public finalize only if every current gate actually passes. Do not install, publish, commit, or clean state.", "finalized", ("retain", "finalize"))
                self.observation["outcome"] = "public-workflow-finalized"
                self.observation["review_repair_demonstrated"] = cycle > 0 and any(row.get("negative_review") for row in self.observation["cycles"][:-1])
                self.observation["repair_demonstrated"] = cycle > 0 and any(row.get("negative_review") and row.get("trial_status") == "fail" for row in self.observation["cycles"][:-1])
                self.guard()
                break
        except (WorkflowError, self.probe.ProbeError, OSError, ValueError, KeyError, TypeError) as error:
            self.observation["outcome"] = "workflow-blocked"
            self.observation["failure"] = {"type": type(error).__name__, "reason": str(error)}
        self.persist()
        return self.observation


class Budget:
    def __init__(self, calls, seconds, actor_timeout):
        self.maximum_calls, self.actor_timeout = calls, actor_timeout
        self.deadline = time.monotonic() + seconds
        self.calls = 0
        self.seen_threads = set()

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise WorkflowError("Cumulative workflow deadline exhausted")
        return remaining


def run_workflows(*, output_root, repository=ROOT, framework_revision, live=False, driver_path=DRIVER, fixture_path=FIXTURE,
                  maximum_actor_attempts=96, deadline_seconds=7200, actor_timeout=240, maximum_repairs=1, cli="codex", driver=None):
    if not live:
        raise WorkflowError("Public Builder workflows require explicit --live opt-in")
    if type(maximum_actor_attempts) is not int or not 1 <= maximum_actor_attempts <= 128 or type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 10800 or type(actor_timeout) is not int or not 1 <= actor_timeout <= 240 or type(maximum_repairs) is not int or not 0 <= maximum_repairs <= 2:
        raise WorkflowError("Workflow budget exceeds the declared hard ceilings")
    probe = load_module(ROOT / "scripts/live_skill_builder_benchmark.py", "bounded_builder_probe_primitives")
    injected = driver is not None
    driver = driver or probe.load_driver(driver_path)
    definition = json.loads(Path(fixture_path).read_text())
    if definition.get("schema_version") != "skill-builder-public-workflows.v1" or definition.get("modes") != ["create", "improve"]:
        raise WorkflowError("Unexpected frozen create/improve fixture")
    if any(len(case["requests"]) > 1 for case in definition["cases"]) and "persistent" not in inspect.signature(driver.run_actor).parameters:
        raise WorkflowError("Shared driver's persistent/resume transport prerequisite is required for real multi-turn cases")
    if not re.fullmatch(r"[0-9a-f]{40}", framework_revision) or driver.git(Path(repository), "rev-parse", framework_revision + "^{commit}") != framework_revision:
        raise WorkflowError("An exact immutable framework commit is required")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    content = driver.snapshot(Path(repository), framework_revision, output_root / "framework-snapshot")
    base = content / "skills/skill-builder"
    sources = {name: {"path": str((base / relative).resolve()), "sha256": digest((base / relative).read_bytes())} for name, relative in SOURCES.items()}
    for source in sources.values():
        Path(source["path"]).chmod(0o444)
    evidence = save(output_root / "frozen-synthetic-decisions.json", definition)
    Path(evidence["path"]).chmod(0o400)
    budget = Budget(maximum_actor_attempts, deadline_seconds, actor_timeout)
    report = {"schema_version": "skill-builder-public-workflows-observation.v1", "framework_revision": framework_revision, "sources": sources,
              "runner_sha256": digest(Path(__file__).read_bytes()), "transport": "scripted-test-transport" if injected else "native-shared-driver",
              "driver": {"path": str(Path(driver.__file__).resolve()), "sha256": digest(Path(driver.__file__).read_bytes())} if hasattr(driver, "__file__") else {"origin": "scripted-test-transport"},
              "origin_validator_sha256": digest(Path(probe.__file__).read_bytes()),
              "full_builder_conformance_claim": False, "synthetic_foundation": "Frozen synthetic user decisions and a declared defective improve starting target; operational artifact authorship belongs to native actors. Disposable cases are harness-authored development fixtures.",
              "budgets": {"maximum_actor_attempts": maximum_actor_attempts, "deadline_seconds": deadline_seconds, "actor_timeout": actor_timeout,
                          "maximum_repairs_per_mode": maximum_repairs, "maximum_concurrent_native_actors": 1,
                          "planned_actor_calls_without_infrastructure_retries": 50 + 32 * maximum_repairs,
                          "planned_fresh_contexts": 44 + 26 * maximum_repairs,
                          "planned_same_context_continuations": 6 + 6 * maximum_repairs}, "workflows": []}
    for mode in definition["modes"]:
        try:
            workflow = Workflow(driver=driver, probe=probe, root=output_root / mode, helper=base / "scripts/run_state.py", sources=sources,
                                definition=definition, definition_evidence=evidence, mode=mode, budget=budget, cli=cli)
            report["workflows"].append(workflow.run(maximum_repairs))
        except (WorkflowError, probe.ProbeError, OSError, ValueError, KeyError, TypeError) as error:
            report["workflows"].append({"mode": mode, "outcome": "prerequisite-blocked", "failure": {"type": type(error).__name__, "reason": str(error)}})
        report["actual_actor_attempts"] = budget.calls
        save(output_root / "report.json", report)
    finalized = all(row["outcome"] == "public-workflow-finalized" for row in report["workflows"])
    repaired = all(row.get("repair_demonstrated") for row in report["workflows"])
    report["outcome"] = "public-workflows-observed" if finalized and repaired else "repair-not-demonstrated" if finalized else "public-workflows-incomplete"
    save(output_root / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--framework-revision", required=True)
    parser.add_argument("--driver-path", type=Path, default=DRIVER)
    parser.add_argument("--maximum-actor-attempts", type=int, default=96)
    parser.add_argument("--deadline-seconds", type=int, default=7200)
    parser.add_argument("--actor-timeout", type=int, default=240)
    parser.add_argument("--maximum-repairs", type=int, default=1)
    parser.add_argument("--cli", default="codex")
    args = parser.parse_args()
    try:
        report = run_workflows(output_root=args.output_root, repository=args.repository, framework_revision=args.framework_revision,
                               live=args.live, driver_path=args.driver_path, maximum_actor_attempts=args.maximum_actor_attempts,
                               deadline_seconds=args.deadline_seconds, actor_timeout=args.actor_timeout, maximum_repairs=args.maximum_repairs, cli=args.cli)
        print(json.dumps({"outcome": report["outcome"], "report": str(args.output_root / "report.json"), "full_builder_conformance_claim": False}))
        return 0 if report["outcome"] == "public-workflows-observed" else 1
    except (WorkflowError, OSError, ValueError) as error:
        print(json.dumps({"outcome": "prerequisite-blocked", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
