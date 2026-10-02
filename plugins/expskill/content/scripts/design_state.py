"""Strict dependency-free private state for the route-neutral Design phase."""
from __future__ import annotations
import fcntl, hashlib, json, os, re, secrets, shlex, subprocess, sys, threading
from contextlib import contextmanager
from pathlib import Path

ID_RE = re.compile(r"^[0-9a-f]{32}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_GUARD = threading.Lock()
BASE_KEYS = {"schema_version","workflow_id","revision","lifecycle","identity","ui_contract","scope","selected_rules","seed_permission","questions","delivery","components","dependencies","evidence","approvals","invalidations","candidate_payload","review_evidence","manifest","brief","candidate","invocation_mode"}
LEGACY_KEYSETS = {
    frozenset(BASE_KEYS - {"invocation_mode"}),
    frozenset(BASE_KEYS - {"brief", "candidate"}),
    frozenset(BASE_KEYS - {"brief", "candidate", "invocation_mode"}),
}
TOOLING_GATES = {"format", "lint", "type", "build"}
BEHAVIOR_GATES = {"runtime", "responsive", "accessibility", "interaction", "reduced_motion"}
TECHNICAL_GATES = TOOLING_GATES | BEHAVIOR_GATES

def _thread_lock(key: str):
    with _THREAD_GUARD: return _THREAD_LOCKS.setdefault(key, threading.RLock())

def _reject_links(path: Path) -> None:
    cur = path
    while True:
        if cur.is_symlink(): raise ValueError("symlink in state path")
        if cur == cur.parent: break
        cur = cur.parent

def _root(home: Path) -> Path:
    home = Path(home)
    _reject_links(home)
    root = home / "design"
    if root.exists() and root.is_symlink(): raise ValueError("symlinked design root")
    if home.parent.joinpath("design").is_symlink(): raise ValueError("ambiguous sibling design root")
    return root

@contextmanager
def _locked(home: Path):
    root = _root(home); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / ".lock"
    if lock.is_symlink() or (lock.exists() and not lock.is_file()): raise ValueError("unsafe lock")
    if root.stat().st_mode & 0o077: raise ValueError("unsafe root mode")
    os.chmod(root, 0o700)
    with _thread_lock(str(root)):
        with lock.open("a+") as fh:
            os.chmod(lock, 0o600); fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield root
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()

def _dirty(path: Path) -> str:
    raw = _git(path, "status", "--porcelain=v1", "--untracked-files=all").encode()
    return hashlib.sha256(raw).hexdigest()

def canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def _identity(repository, branch, worktree, baseline, dirty_fingerprint, ui_contract, head=None):
    repo, wt = Path(repository).resolve(), Path(worktree).resolve()
    if not repo.is_dir() or not wt.is_dir(): raise ValueError("invalid repository/worktree")
    actual_repo = Path(_git(wt, "rev-parse", "--show-toplevel")).resolve()
    actual_branch, actual_head = _git(wt, "branch", "--show-current"), _git(wt, "rev-parse", "HEAD")
    expected_head = str(head or baseline)
    if actual_repo != repo or actual_branch != branch or expected_head != actual_head: raise ValueError("identity mismatch")
    digest = ui_contract.get("digest") if isinstance(ui_contract, dict) else None
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest): raise ValueError("invalid contract digest")
    actual_dirty = _dirty(wt)
    legacy_clean = hashlib.sha256(b"clean").hexdigest()
    if not isinstance(dirty_fingerprint, str) or (dirty_fingerprint != actual_dirty and not (dirty_fingerprint == legacy_clean and actual_dirty == hashlib.sha256(b"").hexdigest())): raise ValueError("invalid dirty fingerprint")
    return {"repository": str(repo), "branch": branch, "worktree": str(wt), "baseline": str(baseline), "head": actual_head, "dirty_fingerprint": actual_dirty, "ui_contract_digest": digest}

def _file(path: Path) -> None:
    _reject_links(path)
    if path.exists() and (not path.is_file() or path.stat().st_mode & 0o077): raise ValueError("unsafe state file")

def _validate(state: object, workflow: str) -> dict:
    if not isinstance(state, dict) or set(state) != BASE_KEYS: raise ValueError("closed state schema")
    if state["schema_version"] != 1 or state["workflow_id"] != workflow or not ID_RE.fullmatch(workflow) or not isinstance(state["revision"], int) or state["revision"] < 0: raise ValueError("invalid state")
    if state["lifecycle"] not in {"active","paused","delivered"}: raise ValueError("invalid lifecycle")
    if state["invocation_mode"] not in {"direct", "routed"}: raise ValueError("invalid invocation mode")
    for key in ("identity","ui_contract","scope","selected_rules","seed_permission","questions","delivery","components","dependencies","evidence","approvals","invalidations"):
        if not isinstance(state[key], dict): raise ValueError("invalid state mapping")
    identity = state["identity"]
    required = {"repository","branch","worktree","baseline","head","dirty_fingerprint","ui_contract_digest"}
    if set(identity) != required or not all(isinstance(v, str) and v for v in identity.values()): raise ValueError("invalid identity")
    if not DIGEST_RE.fullmatch(identity["ui_contract_digest"]) or not re.fullmatch(r"[0-9a-f]{40}", identity["head"]): raise ValueError("invalid contract")
    _validate_domains(state)
    return state

def _upgrade_legacy_state(state: object) -> object:
    if isinstance(state, dict) and frozenset(state) in LEGACY_KEYSETS and state.get("schema_version") == 1:
        upgraded = dict(state)
        upgraded.setdefault("brief", {"objective":"","requirements":[],"responsive_expectations":{},"non_goals":[],"source":None,"confirmed":False,"digest":None})
        upgraded.setdefault("candidate", None)
        upgraded.setdefault(
            "invocation_mode",
            "routed" if upgraded.get("candidate") is not None else "direct",
        )
        return upgraded
    return state

def _validate_technical(technical: object) -> bool:
    if not isinstance(technical, dict) or set(technical) != {"status", "results", "gates"}:
        raise ValueError("invalid technical evidence")
    if technical["status"] not in {"pass", "fail"} or not isinstance(technical["results"], list) or not technical["results"]:
        raise ValueError("invalid technical status")
    output_digests = set()
    results_pass = True
    for result in technical["results"]:
        if not isinstance(result, dict) or set(result) not in ({"command", "exit", "output_digest"}, {"command", "exit", "output_digest", "record_id"}):
            raise ValueError("invalid technical result")
        if not isinstance(result["command"], str) or not result["command"] or isinstance(result["exit"], bool) or not isinstance(result["exit"], int) or result["exit"] < 0:
            raise ValueError("invalid technical result")
        if not isinstance(result["output_digest"], str) or not DIGEST_RE.fullmatch(result["output_digest"]):
            raise ValueError("invalid technical result digest")
        if "record_id" in result and not ID_RE.fullmatch(str(result["record_id"])): raise ValueError("invalid command record")
        output_digests.add(result["output_digest"])
        results_pass = results_pass and result["exit"] == 0
    gates = technical["gates"]
    if not isinstance(gates, dict) or set(gates) != TECHNICAL_GATES:
        raise ValueError("incomplete technical gates")
    gates_pass = True
    for name, gate in gates.items():
        if not isinstance(gate, dict) or set(gate) != {"status", "evidence_digest", "details"} or not isinstance(gate["details"], dict):
            raise ValueError("invalid technical gate")
        if gate["evidence_digest"] not in output_digests:
            raise ValueError("unbound technical gate")
        allowed = {"pass", "fail", "not-applicable"} if name in TOOLING_GATES else {"pass", "fail"}
        if gate["status"] not in allowed:
            raise ValueError("invalid technical gate status")
        if name == "responsive":
            expected_widths = {"compact", "intermediate", "wide"}
            overflow = gate["details"].get("page_overflow")
            if set(gate["details"]) != {"page_overflow"} or not isinstance(overflow, dict) or set(overflow) != expected_widths or any(not isinstance(value, bool) for value in overflow.values()):
                raise ValueError("invalid responsive gate")
            if (gate["status"] == "pass") != (not any(overflow.values())):
                raise ValueError("inconsistent responsive gate")
        elif name == "accessibility":
            details = gate["details"]
            if set(details) != {"serious", "critical"} or any(isinstance(details[key], bool) or not isinstance(details[key], int) or details[key] < 0 for key in ("serious", "critical")):
                raise ValueError("invalid accessibility gate")
            if (gate["status"] == "pass") != (details["serious"] == 0 and details["critical"] == 0):
                raise ValueError("inconsistent accessibility gate")
        elif gate["status"] == "not-applicable":
            if name not in TOOLING_GATES or set(gate["details"]) != {"reason"} or not isinstance(gate["details"]["reason"], str) or not gate["details"]["reason"].strip():
                raise ValueError("unjustified technical exception")
        elif gate["details"]:
            raise ValueError("unexpected technical gate details")
        gates_pass = gates_pass and gate["status"] in {"pass", "not-applicable"}
    derived_pass = results_pass and gates_pass
    if technical["status"] != ("pass" if derived_pass else "fail"):
        raise ValueError("inconsistent technical summary")
    return derived_pass

def _validate_brief(brief: object) -> None:
    if not isinstance(brief, dict) or set(brief) != {"objective", "requirements", "responsive_expectations", "non_goals", "source", "confirmed", "digest"}:
        raise ValueError("invalid design brief")
    if not isinstance(brief["confirmed"], bool): raise ValueError("invalid design brief confirmation")
    if not brief["confirmed"]:
        if brief != {"objective":"", "requirements":[], "responsive_expectations":{}, "non_goals":[], "source":None, "confirmed":False, "digest":None}:
            raise ValueError("invalid unconfirmed design brief")
        return
    if not isinstance(brief["objective"], str) or not brief["objective"].strip(): raise ValueError("design brief objective is required")
    for key in ("requirements", "non_goals"):
        if not isinstance(brief[key], list) or any(not isinstance(item, str) or not item.strip() for item in brief[key]):
            raise ValueError(f"invalid design brief {key}")
    if not brief["requirements"]: raise ValueError("design brief requirements are required")
    responsive = brief["responsive_expectations"]
    if not isinstance(responsive, dict) or set(responsive) != {"compact", "intermediate", "wide"} or any(not isinstance(value, str) or not value.strip() for value in responsive.values()):
        raise ValueError("invalid responsive expectations")
    source = brief["source"]
    if not isinstance(source, dict) or source.get("kind") not in {"plan-graph", "specification", "accepted-input"}: raise ValueError("invalid design brief source")
    if source["kind"] == "plan-graph":
        if set(source) != {"kind", "workflow_id", "revision", "digest"} or not ID_RE.fullmatch(str(source.get("workflow_id", ""))) or isinstance(source.get("revision"), bool) or not isinstance(source.get("revision"), int) or source["revision"] < 1:
            raise ValueError("invalid Plan Graph source")
    elif source["kind"] == "specification":
        if set(source) != {"kind", "path", "digest"} or not isinstance(source.get("path"), str) or not source["path"] or source["path"].startswith("/") or "\\" in source["path"] or any(part in {"", ".."} for part in source["path"].split("/")):
            raise ValueError("invalid specification source")
    if source["kind"] == "accepted-input":
        if set(source) != {"kind", "path", "digest", "decision_reference"} or not isinstance(source.get("path"), str) or not source["path"].startswith("/") or "\\" in source["path"] or any(part in {"", ".", ".."} for part in source["path"].split("/")[1:]) or not isinstance(source["decision_reference"], str) or not source["decision_reference"].strip(): raise ValueError("invalid accepted input source")
    if not DIGEST_RE.fullmatch(str(source.get("digest", ""))): raise ValueError("invalid design brief source digest")
    expected = canonical_digest({key: value for key, value in brief.items() if key != "digest"})
    if brief["digest"] != expected: raise ValueError("design brief digest mismatch")

def _revalidate_brief_source(brief: dict, worktree: Path) -> None:
    if not brief["confirmed"] or brief["source"]["kind"] == "plan-graph": return
    source = brief["source"]
    path = Path(source["path"]) if source["kind"] == "accepted-input" else worktree / source["path"]
    _reject_links(path)
    if not path.is_file(): raise ValueError("design brief source is unavailable")
    if source["kind"] == "accepted-input" and path.stat().st_mode & 0o277: raise ValueError("accepted input must be private and read-only")
    if hashlib.sha256(path.read_bytes()).hexdigest() != source["digest"]: raise ValueError("design brief source digest mismatch")


def _validate_candidate(candidate: object, state: dict) -> None:
    if candidate is None: return
    if not isinstance(candidate, dict) or set(candidate) != {"baseline", "branch", "commit", "brief_digest"}: raise ValueError("invalid Design candidate")
    identity = state["identity"]
    if candidate["baseline"] != identity["baseline"] or candidate["branch"] != identity["branch"] or candidate["commit"] != identity["head"]:
        raise ValueError("Design candidate identity mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", str(candidate["commit"])): raise ValueError("invalid Design candidate commit")
    if not state["brief"]["confirmed"] or candidate["brief_digest"] != state["brief"]["digest"]:
        raise ValueError("Design candidate brief mismatch")

def _validate_domains(state: dict) -> None:
    _validate_brief(state["brief"])
    _validate_candidate(state["candidate"], state)
    if state["invocation_mode"] == "direct" and state["candidate"] is not None: raise ValueError("direct Design workflow cannot contain a candidate checkpoint")
    if set(state["ui_contract"]) - {"digest", "outcome"} or not DIGEST_RE.fullmatch(str(state["ui_contract"].get("digest", ""))): raise ValueError("invalid ui contract")
    scope=state["scope"]
    if scope and (set(scope) - {"components", "exclusions", "owned_paths", "protected_digest"} or not isinstance(scope.get("components", []), list) or not isinstance(scope.get("exclusions", []), list)): raise ValueError("invalid scope")
    owned_paths = _owned_paths(scope, Path(state["identity"]["worktree"]), inspect_workspace=state["lifecycle"] != "delivered")
    if owned_paths and not DIGEST_RE.fullmatch(str(scope.get("protected_digest", ""))): raise ValueError("missing protected workspace digest")
    for item in state["selected_rules"].values():
        if not isinstance(item, dict) or set(item) != {"id", "reason"} or not all(isinstance(v, str) and v for v in item.values()): raise ValueError("invalid selected rule")
    if state["seed_permission"] and (set(state["seed_permission"]) != {"source", "version", "approved"} or not isinstance(state["seed_permission"].get("approved"), bool)): raise ValueError("invalid seed permission")
    for item in state["questions"].values():
        if not isinstance(item, dict) or set(item) not in ({"id"}, {"id", "material", "resolved", "decision_reference"}) or not isinstance(item.get("id"), str) or not item["id"]: raise ValueError("invalid question")
        if "resolved" in item and (not isinstance(item["resolved"], bool) or not isinstance(item["material"], bool) or not isinstance(item["decision_reference"], str) or item["resolved"] and not item["decision_reference"].strip()): raise ValueError("invalid question resolution")
    for item in state["invalidations"].values():
        if not isinstance(item, dict) or set(item) != {"reason", "component_id", "approval_id"}: raise ValueError("invalid invalidation")
    delivery=state["delivery"]
    if not isinstance(delivery, dict) or set(delivery) != {"classifications", "candidate", "review", "manifest"} or not isinstance(delivery["classifications"], dict):
        raise ValueError("invalid delivery shape")
    if set(delivery["classifications"]) != {"candidate", "review", "manifest"} or delivery["classifications"] != {"candidate":"component","review":"review","manifest":"manifest"}:
        raise ValueError("invalid delivery classifications")
    for layer in ("candidate", "review", "manifest"):
            value=delivery[layer]
            if value is not None and (not isinstance(value, dict) or set(value) != {"inventory_digest", "files"}): raise ValueError("invalid delivery layer")
    for collection, allowed in ((state["components"], {"id","code_digest","contract_digest","brief_digest","evidence_ids","dependency_ids","approval_id","digest","eligible","approved","files"}), (state["dependencies"], {"id","digest","component_ids"}), (state["evidence"], {"id","component_id","digest","code_digest","contract_digest","brief_digest","widths","themes","states","technical"}), (state["approvals"], {"id","component_id","code_digest","contract_digest","brief_digest","evidence_ids","dependency_ids","decision","provenance"})):
        if not isinstance(collection, dict): raise ValueError("invalid binding collection")
        for key, item in collection.items():
            if not isinstance(key, str) or not isinstance(item, dict) or set(item) - allowed: raise ValueError("unknown binding field")
            if item.get("id") != key: raise ValueError("binding id mismatch")
            if collection is state["components"] and "files" in item: _component_inventory(item)
            if collection is state["components"] and "eligible" in item and not isinstance(item["eligible"], bool): raise ValueError("invalid component eligibility")
            for field in ("digest", "code_digest", "contract_digest"):
                if field in item and not DIGEST_RE.fullmatch(str(item[field])): raise ValueError("invalid binding digest")
            if collection is state["evidence"] and not DIGEST_RE.fullmatch(str(item.get("digest", ""))): raise ValueError("missing evidence digest")
    for evidence in state["evidence"].values():
        if "technical" in evidence: _validate_technical(evidence["technical"])
    if state["brief"]["confirmed"]:
        expected_brief = state["brief"]["digest"]
        for collection in (state["components"], state["evidence"], state["approvals"]):
            if any(item.get("brief_digest") != expected_brief for item in collection.values()):
                raise ValueError("binding does not match confirmed design brief")
    for approval_id, approval in state["approvals"].items():
        if approval.get("decision") == "approved" and approval_id not in state["invalidations"] and approval.get("component_id") not in state["invalidations"]:
            if not state["brief"]["confirmed"]: raise ValueError("confirmed design brief required before approval")
            evidence_ids = approval.get("evidence_ids", [])
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ValueError("technical evidence required before approval")
            for evidence_id in evidence_ids:
                evidence = state["evidence"].get(evidence_id)
                if not isinstance(evidence, dict) or not _validate_technical(evidence.get("technical")):
                    raise ValueError("passing technical gates required before approval")
    for payload_key, classification in (("candidate_payload", "component"), ("review_evidence", "review"), ("manifest", "manifest")):
        payload = state[payload_key]
        if payload is not None:
            if not isinstance(payload, dict) or set(payload) != {"files"} or not isinstance(payload["files"], list): raise ValueError("invalid persisted payload")
            for item in payload["files"]:
                if not isinstance(item, dict) or set(item) != {"path", "digest", "classification"} or item["classification"] != classification or not isinstance(item["path"], str) or not DIGEST_RE.fullmatch(str(item["digest"])): raise ValueError("invalid persisted inventory")
    if state["candidate_payload"] is not None:
        for key, payload_key in (("candidate", "candidate_payload"), ("review", "review_evidence"), ("manifest", "manifest")):
            payload = state[payload_key]
            expected = {"inventory_digest": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "files": payload["files"]}
            if state["delivery"][key] != expected: raise ValueError("delivery mismatch")

def _load(root: Path, workflow: str) -> dict:
    if not ID_RE.fullmatch(workflow): raise ValueError("invalid workflow id")
    target = root / workflow; _file(target)
    try:
        fd=os.open(target, os.O_RDONLY|os.O_NOFOLLOW); data=json.loads(os.read(fd, 8*1024*1024).decode()); os.close(fd)
    except Exception as exc: raise ValueError("malformed state") from exc
    return _validate(_upgrade_legacy_state(data), workflow)

def _durable_write(root: Path, workflow: str, state: dict) -> None:
    target, previous, temp = root/workflow, root/(workflow+".previous"), root/(workflow+".tmp")
    for p in (target, previous, temp):
        if p.exists() and p.is_symlink(): raise ValueError("symlinked generation")
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    fd = os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded); os.fsync(fd)
    finally: os.close(fd)
    if target.exists():
        backup = root/(workflow+".backup")
        if backup.exists(): backup.unlink()
        backup.write_bytes(target.read_bytes()); os.chmod(backup, 0o600)
        with backup.open("rb") as fh: os.fsync(fh.fileno())
        os.replace(backup, previous)
    os.replace(temp, target)
    with target.open("rb") as fh: os.fsync(fh.fileno())
    dfd = os.open(root, os.O_RDONLY); os.fsync(dfd); os.close(dfd)

def _workflow_paths(root):
    for path in root.iterdir():
        if path.name == ".lock" or path.name.endswith((".previous", ".tmp", ".backup")): continue
        if path.name == "records":
            _reject_links(path)
            if not path.is_dir() or path.stat().st_mode & 0o077: raise ValueError("unsafe command records")
            continue
        if not path.is_file(): raise ValueError("ambiguous state")
        yield path


def initialize_workflow(*, repository, branch, worktree, baseline, dirty_fingerprint, ui_contract, scope, state_home, invocation_mode="direct"):
    if invocation_mode not in {"direct", "routed"}: raise ValueError("invalid invocation mode")
    identity = _identity(repository, branch, worktree, baseline, dirty_fingerprint, ui_contract)
    scope = dict(scope)
    owned_paths = _owned_paths(scope, Path(identity["worktree"]))
    if owned_paths:
        scope["protected_digest"] = _protected_workspace_digest(Path(identity["worktree"]), owned_paths)
    elif "protected_digest" in scope:
        raise ValueError("protected digest requires an owned path scope")
    with _locked(Path(state_home)) as root:
        for p in _workflow_paths(root):
            try:
                old = _load(root, p.name)
                if old["lifecycle"] != "delivered" and old["identity"]["repository"] == identity["repository"] and old["identity"]["branch"] == branch: raise ValueError("active workflow exists")
            except ValueError as exc:
                if "active workflow" in str(exc): raise
                raise ValueError("ambiguous state") from exc
        workflow = secrets.token_hex(16)
        state = {"schema_version":1,"workflow_id":workflow,"revision":0,"lifecycle":"active","identity":identity,"ui_contract":ui_contract,"scope":scope,"selected_rules":{},"seed_permission":{},"questions":{},"delivery":{"classifications":{"candidate":"component","review":"review","manifest":"manifest"},"candidate":None,"review":None,"manifest":None},"components":{},"dependencies":{},"evidence":{},"approvals":{},"invalidations":{},"candidate_payload":None,"review_evidence":None,"manifest":None,"brief":{"objective":"","requirements":[],"responsive_expectations":{},"non_goals":[],"source":None,"confirmed":False,"digest":None},"candidate":None,"invocation_mode":invocation_mode}
        _validate_domains(state)
        _durable_write(root, workflow, state)
        return {"workflow_id":workflow,"revision":0,"baseline":baseline,"identity":identity}

def load_workflow(*, workflow_id, state_home, repository=None, branch=None, worktree=None, baseline=None, dirty_fingerprint=None, ui_contract=None):
    with _locked(Path(state_home)) as root:
        raw = json.loads((root / workflow_id).read_text(encoding="utf-8"))
        if (not isinstance(raw.get("delivery"), dict) or set(raw["delivery"]) != {"classifications", "candidate", "review", "manifest"} or raw["delivery"].get("classifications") != {"candidate":"component","review":"review","manifest":"manifest"}): raise ValueError("legacy delivery shape")
        state = _load(root, workflow_id)
        if state["lifecycle"] != "delivered": _revalidate(state)
        if any(x is not None for x in (repository,branch,worktree,baseline,dirty_fingerprint,ui_contract)):
            expected = _identity(repository or state["identity"]["repository"], branch or state["identity"]["branch"], worktree or state["identity"]["worktree"], baseline or state["identity"]["baseline"], dirty_fingerprint or state["identity"]["dirty_fingerprint"], ui_contract or {"digest":state["identity"]["ui_contract_digest"]}, head=state["identity"]["head"])
            if expected != state["identity"]: raise ValueError("identity revalidation failed")
        return state

def _owned_paths(scope: dict, worktree: Path, *, inspect_workspace: bool = True) -> list[str]:
    paths = scope.get("owned_paths", [])
    if not isinstance(paths, list) or any(not isinstance(item, str) or not item or item.startswith("/") or "\\" in item or any(part in {"", ".", "..", ".git"} for part in item.split("/")) or any(character in item for character in "*?[]{}") for item in paths):
        raise ValueError("invalid owned path scope")
    if len({item.casefold() for item in paths}) != len(paths):
        raise ValueError("duplicate owned path scope")
    if inspect_workspace:
        for item in paths:
            target = worktree / item
            _reject_links(target)
            if target.exists() and not target.is_file(): raise ValueError("owned paths must name exact files")
    return paths


def _protected_workspace_digest(worktree: Path, owned_paths: list[str]) -> str:
    raw = subprocess.check_output(["git", "-C", str(worktree), "ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    records = []
    for relative in sorted(set(os.fsdecode(item) for item in raw.split(b"\0") if item)):
        if relative in owned_paths: continue
        target = worktree / relative
        if target.is_symlink():
            records.append([relative, "symlink", os.readlink(target)])
        elif target.is_file():
            records.append([relative, "file", hashlib.sha256(target.read_bytes()).hexdigest()])
        elif target.exists():
            records.append([relative, "other", target.stat().st_mode])
        else:
            records.append([relative, "missing"])
    return canonical_digest(records)


def _revalidate(state: dict) -> None:
    i = state["identity"]
    worktree = Path(i["worktree"])
    owned_paths = _owned_paths(state["scope"], worktree)
    expected_dirty = _dirty(worktree) if owned_paths else i["dirty_fingerprint"]
    actual = _identity(i["repository"], i["branch"], i["worktree"], i["baseline"], expected_dirty, {"digest": i["ui_contract_digest"]}, head=i["head"])
    if owned_paths:
        if _protected_workspace_digest(worktree, owned_paths) != state["scope"].get("protected_digest"):
            raise ValueError("unrelated workspace bytes changed")
        actual["dirty_fingerprint"] = i["dirty_fingerprint"]
    if actual != i: raise ValueError("external workspace change")
    _revalidate_brief_source(state["brief"], worktree)

def confirm_brief(*, workflow_id, expected_revision, brief, confirmed, state_home):
    if confirmed is not True: raise PermissionError("explicit design brief confirmation required")
    if not isinstance(brief, dict) or set(brief) != {"objective", "requirements", "responsive_expectations", "non_goals", "source"}:
        raise ValueError("invalid design brief")
    candidate = dict(brief)
    candidate["confirmed"] = True
    candidate["digest"] = canonical_digest(candidate)
    _validate_brief(candidate)
    with _locked(Path(state_home)) as root:
        state = _load(root, workflow_id)
        _revalidate(state)
        if state["lifecycle"] == "delivered" or state["revision"] != expected_revision: raise ValueError("immutable or stale workflow")
        _revalidate_brief_source(candidate, Path(state["identity"]["worktree"]))
        changed = state["brief"]["confirmed"] and state["brief"]["digest"] != candidate["digest"]
        if changed and state["candidate"] is not None: raise ValueError("cannot change design brief after candidate checkpoint")
        if state["brief"]["digest"] != candidate["digest"]:
            for name, component in state["components"].items():
                state["invalidations"][name] = {"reason":"design brief changed","component_id":name,"approval_id":component.get("approval_id","")}
            state["components"].clear()
            state["dependencies"].clear()
            state["evidence"].clear()
            state["approvals"].clear()
            state["candidate_payload"] = state["review_evidence"] = state["manifest"] = None
            state["delivery"] = {"classifications":{"candidate":"component","review":"review","manifest":"manifest"},"candidate":None,"review":None,"manifest":None}
        state["brief"] = candidate
        state["revision"] += 1
        _validate_domains(state)
        _durable_write(root, workflow_id, state)
        return {"workflow_id":workflow_id,"revision":state["revision"],"brief_digest":candidate["digest"]}

def checkpoint_candidate(*, workflow_id, expected_revision, candidate_commit, state_home):
    with _locked(Path(state_home)) as root:
        state = _load(root, workflow_id)
        if state["lifecycle"] == "delivered" or state["revision"] != expected_revision: raise ValueError("immutable or stale workflow")
        if state["invocation_mode"] != "routed": raise PermissionError("candidate checkpoint requires routed Design invocation")
        if not state["brief"]["confirmed"]: raise ValueError("confirmed design brief required")
        identity = state["identity"]
        worktree = Path(identity["worktree"])
        if Path(_git(worktree, "rev-parse", "--show-toplevel")).resolve() != Path(identity["repository"]): raise ValueError("candidate repository mismatch")
        if _git(worktree, "branch", "--show-current") != identity["branch"]: raise ValueError("candidate branch mismatch")
        actual_head = _git(worktree, "rev-parse", "HEAD")
        if candidate_commit != actual_head or not re.fullmatch(r"[0-9a-f]{40}", str(candidate_commit)): raise ValueError("candidate HEAD mismatch")
        if _dirty(worktree) != hashlib.sha256(b"").hexdigest(): raise ValueError("candidate worktree is dirty")
        parents = _git(worktree, "rev-list", "--parents", "-n", "1", candidate_commit).split()
        if len(parents) != 2 or parents[1] != identity["baseline"]: raise ValueError("candidate must be one commit above baseline")
        if _git(worktree, "rev-list", "--count", f"{identity['baseline']}..{candidate_commit}") != "1": raise ValueError("candidate history is not atomic")
        owned_paths = _owned_paths(state["scope"], worktree)
        if owned_paths and _protected_workspace_digest(worktree, owned_paths) != state["scope"].get("protected_digest"):
            raise ValueError("unrelated workspace bytes changed")
        previous = state["candidate"]
        changed = previous is not None and previous["commit"] != candidate_commit
        if changed:
            for name, component in state["components"].items():
                state["invalidations"][name] = {"reason":"candidate commit changed","component_id":name,"approval_id":component.get("approval_id","")}
            state["components"].clear()
            state["dependencies"].clear()
            state["evidence"].clear()
            state["approvals"].clear()
            state["candidate_payload"] = state["review_evidence"] = state["manifest"] = None
            state["delivery"] = {"classifications":{"candidate":"component","review":"review","manifest":"manifest"},"candidate":None,"review":None,"manifest":None}
        state["identity"]["head"] = candidate_commit
        state["identity"]["dirty_fingerprint"] = hashlib.sha256(b"").hexdigest()
        state["candidate"] = {"baseline":identity["baseline"],"branch":identity["branch"],"commit":candidate_commit,"brief_digest":state["brief"]["digest"]}
        state["revision"] += 1
        _validate_domains(state)
        _durable_write(root, workflow_id, state)
        return {"workflow_id":workflow_id,"revision":state["revision"],"candidate_commit":candidate_commit,"brief_digest":state["brief"]["digest"]}

def discover_workflow(*, repository, branch, state_home):
    with _locked(Path(state_home)) as root:
        matches=[]
        for p in _workflow_paths(root):
            state=_load(root,p.name)
            if state["lifecycle"] != "delivered" and state["identity"]["repository"]==str(Path(repository).resolve()) and state["identity"]["branch"]==branch: matches.append(state)
        if len(matches)!=1: raise ValueError("ambiguous workflow")
        s=matches[0]; return {"workflow_id":s["workflow_id"],"revision":s["revision"],"lifecycle":s["lifecycle"]}

def apply_updates(*, workflow_id, expected_revision, updates, state_home):
    with _locked(Path(state_home)) as root:
        s=_load(root,workflow_id)
        _revalidate(s)
        if s["lifecycle"]=="delivered" or s["revision"]!=expected_revision: raise ValueError("immutable or stale workflow")
        old_components=dict(s["components"])
        old_evidence=dict(s["evidence"])
        old_dependencies=dict(s["dependencies"])
        if not isinstance(updates,dict) or not set(updates)<= {"components","dependencies","evidence","approvals","scope","ui_contract","selected_rules","seed_permission","questions","delivery"}: raise ValueError("closed update")
        if "scope" in updates and (not isinstance(updates["scope"], dict) or updates["scope"].get("owned_paths") != s["scope"].get("owned_paths") or updates["scope"].get("protected_digest") != s["scope"].get("protected_digest")):
            raise ValueError("owned path scope cannot change silently")
        for k,v in updates.items():
            if not isinstance(v,dict): raise ValueError("typed update required")
            if k=="ui_contract" and (set(v)-{"digest","outcome"} or not isinstance(v.get("digest"),str) or not DIGEST_RE.fullmatch(v["digest"])): raise ValueError("invalid ui contract")
            if k=="scope" and set(v)-{"components","exclusions","owned_paths","protected_digest"}: raise ValueError("invalid scope")
            if k in {"components","dependencies","evidence","approvals"} and any(not isinstance(n,str) or not isinstance(x,dict) for n,x in v.items()): raise ValueError("typed binding required")
            allowed = {"components":{"id","code_digest","contract_digest","brief_digest","evidence_ids","dependency_ids","approval_id","digest","eligible","approved","files"},"dependencies":{"id","digest","component_ids"},"evidence":{"id","component_id","digest","code_digest","contract_digest","brief_digest","widths","themes","states","technical"},"approvals":{"id","component_id","code_digest","contract_digest","brief_digest","evidence_ids","dependency_ids","decision","provenance"}}
            if k in allowed:
                for x in v.values():
                    if set(x) - allowed[k]: raise ValueError("unknown nested key")
                    if k in {"components","dependencies","evidence","approvals"} and "id" not in x and not (k=="components" and "digest" in x): raise ValueError("missing nested id")
                    for field in ("digest","code_digest","contract_digest"):
                        if field in x and not DIGEST_RE.fullmatch(str(x[field])): raise ValueError("invalid digest")
            if k=="components":
                for n,x in v.items():
                    if "id" in x and x["id"]!=n: raise ValueError("component id mismatch")
                    if "evidence_ids" in x and not isinstance(x["evidence_ids"],list): raise ValueError("invalid evidence binding")
                    if any(eid not in s["evidence"] and eid not in updates.get("evidence", {}) for eid in x.get("evidence_ids", [])): raise ValueError("missing evidence binding")
            if k in {"components","dependencies","evidence","approvals"}: s[k].update(v)
            else: s[k]=v
        affected=set()
        coherent_initial = all(k in updates for k in ("components", "dependencies", "evidence", "approvals")) and not old_components
        full_refresh = all(k in updates for k in ("components", "dependencies", "evidence", "approvals")) and "delivery" in updates
        if "components" in updates and not coherent_initial: affected.update(n for n,x in updates["components"].items() if n not in old_components or old_components.get(n) != x)
        if "ui_contract" in updates: affected=set(s["components"])
        for key in ("dependencies","evidence"):
            if key in updates:
                for n,x in updates[key].items():
                    refs=x.get("component_ids",[]) or ([x.get("component_id")] if x.get("component_id") else [])
                    if (key == "evidence" and n not in old_evidence) or (key == "dependencies" and n not in old_dependencies): refs=[]
                    if not coherent_initial: affected.update(refs)
        for n in affected: s["invalidations"][n]={"reason":"causal change","component_id":n,"approval_id":s["components"].get(n,{}).get("approval_id","")}
        for aid, approval in s["approvals"].items():
            if approval.get("component_id") in affected or ("ui_contract" in updates and approval.get("component_id")):
                s["invalidations"][aid] = {"reason":"causal approval change","component_id":approval.get("component_id",""),"approval_id":aid}
        if full_refresh:
            for n in list(s["invalidations"]):
                if n in updates.get("components", {}) or n in updates.get("approvals", {}): s["invalidations"].pop(n, None)
        for n, comp in s["components"].items():
            for eid in comp.get("evidence_ids", []):
                if eid not in s["evidence"] and eid not in updates.get("evidence", {}): raise ValueError("missing evidence binding")
        _validate_domains(s)
        s["revision"]+=1; _durable_write(root,workflow_id,s); return {"workflow_id":workflow_id,"revision":s["revision"]}

def _transition(*,workflow_id,expected_revision,state_home,lifecycle):
    with _locked(Path(state_home)) as root:
        s=_load(root,workflow_id)
        _revalidate(s)
        if s["lifecycle"]=="delivered" or s["revision"]!=expected_revision: raise ValueError("immutable or stale workflow")
        s["lifecycle"]=lifecycle; s["revision"]+=1; _durable_write(root,workflow_id,s); return {"workflow_id":workflow_id,"revision":s["revision"],"lifecycle":lifecycle}
def pause_workflow(*,workflow_id,expected_revision,state_home): return _transition(workflow_id=workflow_id,expected_revision=expected_revision,state_home=state_home,lifecycle="paused")
def resume_workflow(*,workflow_id,expected_revision,state_home): return _transition(workflow_id=workflow_id,expected_revision=expected_revision,state_home=state_home,lifecycle="active")

def discard_workflow(*,workflow_id,expected_revision,confirmed,state_home):
    if not confirmed: raise PermissionError("explicit discard confirmation required")
    with _locked(Path(state_home)) as root:
        s=_load(root,workflow_id)
        _revalidate(s)
        if s["lifecycle"]=="delivered" or s["revision"]!=expected_revision: raise ValueError("immutable or stale workflow")
        (root/workflow_id).unlink(); (root/(workflow_id+".previous")).unlink(missing_ok=True); return {"workflow_id":workflow_id,"revision":expected_revision+1,"lifecycle":"discarded"}

def recover_workflow(*,workflow_id,state_home):
    with _locked(Path(state_home)) as root:
        target=root/workflow_id
        if target.is_symlink() or target.is_dir() or (target.exists() and target.stat().st_mode & 0o077):
            raise ValueError("unsafe current generation")
        if target.is_file() and not target.is_symlink():
            try:
                current=_load(root, workflow_id)
                if current["lifecycle"] != "delivered": _revalidate(current)
                raise ValueError("current generation is valid")
            except ValueError as exc:
                if "current generation is valid" in str(exc): raise
        torn_revision=0
        if target.is_file():
            try: torn_revision=int(json.loads(target.read_text(encoding="utf-8")).get("revision", 0))
            except Exception: torn_revision=0
        previous=root/(workflow_id+".previous")
        if previous.exists() and (previous.is_symlink() or not previous.is_file() or previous.stat().st_mode & 0o077): raise ValueError("unsafe predecessor")
        if not previous.is_file() or previous.is_symlink(): raise ValueError("no valid predecessor")
        try: state=_validate(_upgrade_legacy_state(json.loads(previous.read_text(encoding="utf-8"))),workflow_id)
        except Exception as exc: raise ValueError("invalid predecessor") from exc
        _revalidate(state); state["revision"]=max(state["revision"], torn_revision)+1
        if target.exists(): target.unlink()
        _durable_write(root,workflow_id,state)
        return {"workflow_id":workflow_id,"revision":state["revision"],"lifecycle":state["lifecycle"]}

def _inventory(value):
    if not isinstance(value,dict) or set(value) != {"files"} or not isinstance(value.get("files"),list) or not value["files"]: raise ValueError("exact inventory required")
    for item in value["files"]:
        if not isinstance(item,dict) or set(item)!={"path","digest","classification"} or not isinstance(item["path"],str) or not DIGEST_RE.fullmatch(str(item["digest"])): raise ValueError("invalid inventory")
        if item["path"].startswith("/") or "\\" in item["path"] or any(part in {"", ".", "..", ".git"} for part in item["path"].split("/")): raise ValueError("invalid inventory path")
    paths=[x["path"] for x in value["files"]]
    if len(paths)!=len(set(paths)) or len({p.casefold() for p in paths})!=len(paths): raise ValueError("duplicate inventory path")

def component_digest(files: list) -> str:
    inventory = {"files": files}
    _inventory(inventory)
    if any(item["classification"] != "component" for item in files): raise ValueError("component file classification")
    return canonical_digest({"files": sorted(files, key=lambda item: item["path"])})


def _component_inventory(component: dict) -> list:
    files = component.get("files")
    if files is None: raise ValueError("legacy component requires exact file inventory")
    if component.get("code_digest") != component_digest(files): raise ValueError("component inventory digest mismatch")
    return files


def _artifact_files(inventory: dict, worktree: Path) -> None:
    _inventory(inventory)
    for item in inventory["files"]:
        target = worktree / item["path"]
        _reject_links(target)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != item["digest"]:
            raise ValueError("artifact bytes do not match inventory")


def record_check(*, workflow_id, expected_revision, argv, candidate_payload, state_home, timeout_seconds=300):
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv): raise ValueError("invalid check argv")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 300: raise ValueError("invalid check timeout")
    with _locked(Path(state_home)) as root:
        state = _load(root, workflow_id)
        _revalidate(state)
        if state["lifecycle"] != "active" or state["revision"] != expected_revision: raise ValueError("inactive or stale check")
        worktree = Path(state["identity"]["worktree"])
        _artifact_files(candidate_payload, worktree)
        completed = subprocess.run(argv, cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_seconds, check=False)
        _revalidate(state)
        _artifact_files(candidate_payload, worktree)
        records = root / "records"
        _reject_links(records)
        records.mkdir(mode=0o700, exist_ok=True)
        if records.stat().st_mode & 0o077: raise ValueError("unsafe command records")
        record_id = secrets.token_hex(16)
        output_digest = hashlib.sha256(completed.stdout).hexdigest()
        record = {"record_id": record_id, "workflow_id": workflow_id, "baseline": state["identity"]["baseline"], "brief_digest": state["brief"]["digest"], "contract_digest": state["ui_contract"]["digest"], "candidate_digest": canonical_digest(candidate_payload), "command": shlex.join(argv), "exit": completed.returncode if completed.returncode >= 0 else 128 - completed.returncode, "output_digest": output_digest}
        for suffix, data in ((".output", completed.stdout), (".json", json.dumps(record, sort_keys=True).encode())):
            fd = os.open(records / (record_id + suffix), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        return {key: record[key] for key in ("record_id", "command", "exit", "output_digest")}


def _recorded_results(root: Path, state: dict, technical: dict, candidate_payload: dict) -> None:
    for result in technical["results"]:
        record_id = result.get("record_id")
        if not ID_RE.fullmatch(str(record_id)): raise ValueError("legacy technical evidence requires recorded checks")
        records = root / "records"
        if not records.is_dir() or records.stat().st_mode & 0o077: raise ValueError("unsafe command records")
        record_path = records / (record_id + ".json")
        output_path = root / "records" / (record_id + ".output")
        _reject_links(record_path)
        _reject_links(output_path)
        _file(record_path)
        _file(output_path)
        try: record = json.loads(record_path.read_bytes())
        except Exception as exc: raise ValueError("missing command record") from exc
        expected = {"record_id": record_id, "workflow_id": state["workflow_id"], "baseline": state["identity"]["baseline"], "brief_digest": state["brief"]["digest"], "contract_digest": state["ui_contract"]["digest"], "candidate_digest": canonical_digest(candidate_payload), "command": result["command"], "exit": result["exit"], "output_digest": result["output_digest"]}
        if record != expected or not output_path.is_file() or hashlib.sha256(output_path.read_bytes()).hexdigest() != result["output_digest"]:
            raise ValueError("stale or altered command record")


def _approval_attestation(approval: dict) -> None:
    provenance = approval.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {"kind", "actor", "authority_reference", "decision_reference"} or provenance["kind"] != "trusted-attestation" or provenance["actor"] not in {"human", "authorized-agent"} or any(not isinstance(provenance[key], str) or not provenance[key].strip() for key in ("authority_reference", "decision_reference")):
        raise ValueError("explicit approval attestation provenance required")


def deliver_workflow(*,workflow_id,expected_revision,candidate_payload,review_evidence,manifest,state_home):
    with _locked(Path(state_home)) as root:
        s=_load(root,workflow_id)
        _revalidate(s)
        if s["lifecycle"]=="delivered" or s["revision"]!=expected_revision: raise ValueError("immutable or stale workflow")
        if not s["brief"]["confirmed"]: raise ValueError("confirmed design brief required for delivery")
        if any(question.get("material", True) and not question.get("resolved", False) for question in s["questions"].values()): raise ValueError("unresolved material Design questions")
        if any(component.get("eligible") is False for component in s["components"].values()): raise ValueError("ineligible Design component")
        if s["invocation_mode"] == "routed" and s["candidate"] is None: raise ValueError("routed delivery requires candidate checkpoint")
        if not s["components"]: raise ValueError("current approvals required")
        for x in (candidate_payload,review_evidence,manifest): _inventory(x)
        union = {}
        for component in s["components"].values():
            for item in _component_inventory(component):
                if item["path"] in union and union[item["path"]] != item: raise ValueError("conflicting component file ownership")
                union[item["path"]] = item
        expected_inventory = {"files": sorted(union.values(), key=lambda item: item["path"])}
        _inventory(expected_inventory)
        if sorted(candidate_payload["files"], key=lambda item: item["path"]) != expected_inventory["files"]: raise ValueError("candidate binding requires complete component file union")
        for x in (candidate_payload,review_evidence,manifest): _artifact_files(x, Path(s["identity"]["worktree"]))
        for name, comp in s["components"].items():
            aid=comp.get("approval_id"); approval=s["approvals"].get(aid, {})
            if not aid or approval.get("decision")!="approved" or approval.get("component_id")!=name or name in s["invalidations"]: raise ValueError("current approvals required")
            _approval_attestation(approval)
            if approval.get("code_digest") != comp.get("code_digest") or approval.get("contract_digest") != comp.get("contract_digest"): raise ValueError("stale approval")
            if set(approval.get("evidence_ids", [])) != set(comp.get("evidence_ids", [])) or set(approval.get("dependency_ids", [])) != set(comp.get("dependency_ids", [])): raise ValueError("stale approval")
            for eid in comp.get("evidence_ids", []):
                ev=s["evidence"].get(eid, {})
                if ev.get("id") != eid or ev.get("component_id") != name or ev.get("code_digest") != comp.get("code_digest") or ev.get("contract_digest") != comp.get("contract_digest") or set(ev.get("widths", [])) != {"compact", "intermediate", "wide"} or not ev.get("themes") or not ev.get("states"): raise ValueError("incomplete evidence")
                if not _validate_technical(ev.get("technical")): raise ValueError("technical evidence failed")
                _recorded_results(root, s, ev["technical"], candidate_payload)
            for did in comp.get("dependency_ids", []):
                dep=s["dependencies"].get(did, {})
                if dep.get("id") != did or name not in dep.get("component_ids", []) or not DIGEST_RE.fullmatch(str(dep.get("digest", ""))): raise ValueError("invalid dependency")
        if any(x["classification"]!="component" for x in candidate_payload["files"]): raise ValueError("candidate classification")
        if any(x["classification"]!="review" for x in review_evidence["files"]): raise ValueError("review classification")
        if any(x["classification"]!="manifest" for x in manifest["files"]): raise ValueError("manifest classification")
        evidence_digests={s["evidence"][eid].get("digest") for comp in s["components"].values() for eid in comp.get("evidence_ids", []) if s["evidence"].get(eid, {}).get("digest")}
        if evidence_digests and not {x["digest"] for x in review_evidence["files"]} <= evidence_digests: raise ValueError("review binding")
        def inv(x): return {"inventory_digest": hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "files": x["files"]}
        expected_delivery={"classifications":{"candidate":"component","review":"review","manifest":"manifest"},"candidate":inv(candidate_payload),"review":inv(review_evidence),"manifest":inv(manifest)}
        if isinstance(s["delivery"], dict) and s["delivery"].get("candidate") is not None and s["delivery"] != expected_delivery: raise ValueError("delivery does not match predeclared inventories")
        s["candidate_payload"],s["review_evidence"],s["manifest"]=candidate_payload,review_evidence,manifest
        s["lifecycle"]="delivered"; s["revision"]+=1; _durable_write(root,workflow_id,s)
        digest=hashlib.sha256(json.dumps(candidate_payload,sort_keys=True).encode()).hexdigest()
        def dg(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        result={"schema_version":1,"workflow_id":workflow_id,"revision":s["revision"],"lifecycle":"delivered","identity":s["identity"],"ui_contract":s["ui_contract"],"candidate_digest":digest,"candidate_inventory_digest":dg(candidate_payload),"review_evidence_digest":dg(review_evidence),"manifest_digest":dg(manifest),"evidence_digest":dg(s["evidence"]),"approval_digest":dg(s["approvals"]),"dependency_digest":dg(s["dependencies"])}
        if s["brief"]["confirmed"]:
            result["brief_digest"]=s["brief"]["digest"]
            if s["candidate"] is not None:
                result["candidate_commit"]=s["candidate"]["commit"]
        return result

def _cli():
    if len(sys.argv)>1 and sys.argv[1] == "--help":
        print("initialize discover load confirm-brief checkpoint-candidate record-check apply pause resume recover discard deliver")
        return 0
    if len(sys.argv)<2 or len(sys.argv)>4 or (len(sys.argv)>2 and sys.argv[2] != "--state-home"): return 2
    command=sys.argv[1]
    allowed={"initialize","discover","load","confirm-brief","checkpoint-candidate","apply","pause","resume","recover","discard","record-check","deliver"}
    if command not in allowed: return 2
    raw=sys.stdin.read(1024*1024+1)
    if len(raw)>1024*1024: return 2
    try:
        payload=json.loads(raw); 
        if not isinstance(payload,dict): raise ValueError()
        home=Path(sys.argv[3]) if len(sys.argv)==4 else Path(os.environ.get("XDG_STATE_HOME",str(Path.home()/".local/state"))) / "expskill"
        if len(sys.argv)==4: payload.pop("state_home",None)
        if command=="initialize": result=initialize_workflow(state_home=home,**payload)
        elif command=="discover": result=discover_workflow(state_home=home,**payload)
        elif command=="load": result=load_workflow(state_home=home,**payload)
        elif command=="confirm-brief": result=confirm_brief(state_home=home,**payload)
        elif command=="checkpoint-candidate": result=checkpoint_candidate(state_home=home,**payload)
        elif command=="record-check": result=record_check(state_home=home,**payload)
        elif command=="apply": result=apply_updates(state_home=home,**payload)
        elif command=="pause": result=pause_workflow(state_home=home,**payload)
        elif command=="resume": result=resume_workflow(state_home=home,**payload)
        elif command=="recover": result=recover_workflow(state_home=home,**payload)
        elif command=="discard": result=discard_workflow(state_home=home,**payload)
        else: result=deliver_workflow(state_home=home,**payload)
        digest=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
        base={"schema_version":1,"operation":command,"workflow_id":result.get("workflow_id"),"revision":result.get("revision",0),"lifecycle":result.get("lifecycle","active"),"identity":result.get("identity",{}),"state_digest":digest}
        if command=="record-check":
            base["workflow_id"]=payload["workflow_id"]
            base["revision"]=payload["expected_revision"]
            base["record"]=result
        if command=="deliver":
            for key in ("candidate_digest","candidate_inventory_digest","review_evidence_digest","manifest_digest","evidence_digest","approval_digest","dependency_digest","brief_digest","candidate_commit"):
                if key in result: base[key]=result[key]
        print(json.dumps(base,sort_keys=True)); return 0
    except Exception as exc:
        print(str(exc),file=sys.stderr); return 1

if __name__ == "__main__": raise SystemExit(_cli())
