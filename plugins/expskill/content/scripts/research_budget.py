#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_previous_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    import plan_graph as storage
finally:
    sys.dont_write_bytecode = _previous_bytecode


DEFAULT_LIMITS = {"turns": 6, "active_seconds": 1200, "in_flight": 3}
HARD_LIMITS = {"turns": 64, "active_seconds": 14400, "in_flight": 8}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
STATE_FIELDS = {"schema_version", "identity", "revision", "initial_limits", "limits", "limit_source", "extensions", "dispatches", "activity_intervals", "last_observed_at_ms", "lifecycle", "closure_reference"}
DISPATCH_FIELDS = {"question", "status", "started_at_ms", "ended_at_ms", "evidence_reference"}
OUTSTANDING = {"reserved", "interrupted"}
TERMINAL = {"completed", "failed", "cancelled"}


class ResearchBudgetError(ValueError):
    pass


class ResearchBudgetMissingState(ResearchBudgetError):
    pass


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _integer(value: object, minimum: int = 0, maximum: int = 2**53 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500 or "\x00" in value:
        raise ResearchBudgetError(f"invalid {label}")
    return value


def _limits(value: object, maximum: dict[str, int] = HARD_LIMITS) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(HARD_LIMITS) or any(not _integer(value[key], 1, maximum[key]) for key in HARD_LIMITS):
        raise ResearchBudgetError("invalid bounded research limits")
    return dict(value)


def _identity(repo: Path | None, branch: str | None, run_id: str, scope_digest: str | None) -> tuple[dict, Path | None]:
    if not isinstance(run_id, str) or IDENTIFIER.fullmatch(run_id) is None:
        raise ResearchBudgetError("invalid research run ID")
    if repo is None:
        if branch is not None or not isinstance(scope_digest, str) or re.fullmatch(r"[0-9a-f]{64}", scope_digest) is None:
            raise ResearchBudgetError("non-repository research needs its accepted initial scope digest")
        return {"git_common_dir": None, "branch": None, "run_id": run_id, "scope_digest": scope_digest}, None
    if scope_digest is not None:
        raise ResearchBudgetError("research identity cannot mix repository and concept modes")
    repository = storage._repository(Path(repo))
    branch = storage._check_branch_name(repository, branch, allow_protected=True)
    common = Path(storage._run_git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
    return {"git_common_dir": str(common), "branch": branch, "run_id": run_id, "scope_digest": None}, repository


def _validate(state: object, identity: dict[str, str], now: int) -> dict:
    if not isinstance(state, dict) or set(state) != STATE_FIELDS or state["schema_version"] != "research-budget.v1" or state["identity"] != identity:
        raise ResearchBudgetError("research ledger schema or identity mismatch")
    if not _integer(state["revision"]) or not _integer(state["last_observed_at_ms"]) or now < state["last_observed_at_ms"]:
        raise ResearchBudgetError("research ledger revision or clock is invalid")
    initial = _limits(state["initial_limits"], DEFAULT_LIMITS)
    current = _limits(state["limits"])
    _text(state["limit_source"], "limit source")
    extensions = state["extensions"]
    if not isinstance(extensions, list) or len(extensions) > 16:
        raise ResearchBudgetError("research extensions exceed the bounded ledger")
    previous = initial
    for extension in extensions:
        if not isinstance(extension, dict) or set(extension) != {"limits", "authorization_reference"}:
            raise ResearchBudgetError("invalid research extension")
        _text(extension["authorization_reference"], "extension authorization reference")
        following = _limits(extension["limits"])
        if following == previous or any(following[key] < previous[key] for key in previous):
            raise ResearchBudgetError("research extension cannot reset spent capacity")
        previous = following
    if current != previous:
        raise ResearchBudgetError("research limits lack their retained extension")
    dispatches = state["dispatches"]
    if not isinstance(dispatches, dict) or len(dispatches) > HARD_LIMITS["turns"] or len(dispatches) > current["turns"]:
        raise ResearchBudgetError("research dispatch count exceeds the ledger limit")
    for dispatch_id, dispatch in dispatches.items():
        if not isinstance(dispatch_id, str) or IDENTIFIER.fullmatch(dispatch_id) is None or not isinstance(dispatch, dict) or set(dispatch) != DISPATCH_FIELDS:
            raise ResearchBudgetError("invalid research dispatch")
        _text(dispatch["question"], "named research question")
        start, end = dispatch["started_at_ms"], dispatch["ended_at_ms"]
        if not _integer(start) or start > state["last_observed_at_ms"] or not isinstance(dispatch["status"], str) or dispatch["status"] not in OUTSTANDING | TERMINAL:
            raise ResearchBudgetError("invalid research dispatch timing or status")
        if dispatch["status"] in OUTSTANDING:
            if end is not None or dispatch["evidence_reference"] is not None:
                raise ResearchBudgetError("outstanding research cannot claim completion")
        elif not _integer(end, start, state["last_observed_at_ms"]):
            raise ResearchBudgetError("invalid reconciled research timing")
        else:
            _text(dispatch["evidence_reference"], "reconciliation evidence reference")
    intervals = state["activity_intervals"]
    if not isinstance(intervals, list) or len(intervals) > 128:
        raise ResearchBudgetError("research activity exceeds the bounded ledger")
    previous_end = 0
    for index, interval in enumerate(intervals):
        if not isinstance(interval, list) or len(interval) != 2 or not _integer(interval[0], previous_end, state["last_observed_at_ms"]):
            raise ResearchBudgetError("invalid coordinator activity interval")
        if interval[1] is None:
            if index != len(intervals) - 1:
                raise ResearchBudgetError("ambiguous open coordinator activity")
        elif not _integer(interval[1], interval[0], state["last_observed_at_ms"]):
            raise ResearchBudgetError("invalid coordinator activity end")
        previous_end = interval[1] if interval[1] is not None else interval[0]
    if state["lifecycle"] == "active":
        if state["closure_reference"] is not None:
            raise ResearchBudgetError("active research cannot claim workflow closure")
    elif state["lifecycle"] == "closed":
        _text(state["closure_reference"], "workflow closure reference")
        if any(dispatch["status"] in OUTSTANDING for dispatch in dispatches.values()) or (intervals and intervals[-1][1] is None):
            raise ResearchBudgetError("closed research retains unsettled activity")
    else:
        raise ResearchBudgetError("invalid research lifecycle")
    return state


def _receipt(state: dict, locator: Path, now: int) -> dict:
    intervals = [[entry["started_at_ms"], entry["ended_at_ms"] if entry["ended_at_ms"] is not None else now] for entry in state["dispatches"].values()]
    intervals.extend([[start, end if end is not None else now] for start, end in state["activity_intervals"]])
    elapsed, end = 0, 0
    for start, finish in sorted(intervals):
        elapsed += max(0, finish - max(start, end))
        end = max(end, finish)
    outstanding = sorted(key for key, value in state["dispatches"].items() if value["status"] in OUTSTANDING)
    return {
        **state, "locator": str(locator), "spent_turns": len(state["dispatches"]),
        "outstanding_dispatch_ids": outstanding, "active_seconds": elapsed / 1000,
        "remaining": {
            "turns": state["limits"]["turns"] - len(state["dispatches"]),
            "active_seconds": max(0, state["limits"]["active_seconds"] - elapsed / 1000),
            "in_flight": max(0, state["limits"]["in_flight"] - len(outstanding)),
        },
    }


@contextmanager
def _transaction(repo, branch, run_id, *, locator=None, state_home=None, create=False, scope_digest=None):
    home_fd = root_fd = catalog_fd = lock_fd = -1
    try:
        identity, repository = _identity(repo, branch, run_id, scope_digest)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if locator is not None:
            supplied = Path(locator)
            if not supplied.is_absolute() or ".." in supplied.parts or supplied.name != f"{key}.json" or supplied.parent.name != "research-budgets" or state_home is not None:
                raise ResearchBudgetError("research locator does not match the stable run identity")
            home = supplied.parent.parent
        else:
            home = storage._absolute_state_path(state_home)
        home_fd = storage._open_directory_path(home, create=create)
        actual_home = Path(os.readlink(f"/proc/self/fd/{home_fd}"))
        if repository is not None:
            forbidden_paths = (repository, Path(identity["git_common_dir"]))
        else:
            try:
                forbidden_paths = (storage._repository(Path.cwd()),)
            except storage.PlanGraphError:
                forbidden_paths = ()
        if any(actual_home == forbidden or actual_home.is_relative_to(forbidden) for forbidden in forbidden_paths):
            raise ResearchBudgetError("research state must remain outside the repository")
        root_fd = storage._open_private_child(home_fd, "research-budgets", create=create)
        actual_locator = actual_home / "research-budgets" / f"{key}.json"
        if locator is not None and supplied != actual_locator:
            raise ResearchBudgetError("research locator was substituted")
        catalog_fd = storage._open_lock(root_fd, "catalog")
        try:
            fcntl.flock(catalog_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ResearchBudgetError("research catalog has another writer, retry without resetting") from error
        lock_fd = storage._open_lock(root_fd, key)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ResearchBudgetError("research ledger has another writer, retry the same locator") from error
        storage._revalidate_directory(home_fd, "research-budgets", root_fd)
        yield root_fd, home_fd, actual_locator, identity
        storage._revalidate_directory(home_fd, "research-budgets", root_fd)
    except storage._MissingState as error:
        raise ResearchBudgetMissingState(str(error)) from error
    except (storage.PlanGraphError, OSError) as error:
        raise ResearchBudgetError(str(error)) from error
    finally:
        for descriptor in (lock_fd, catalog_fd, root_fd, home_fd):
            if descriptor >= 0:
                os.close(descriptor)


def _read(root_fd: int, locator: Path, identity: dict, now: int) -> dict:
    raw = storage._read_bytes_entry(root_fd, locator.name)
    if len(raw) > 131072:
        raise ResearchBudgetError("research ledger is oversized")
    return _validate(storage._decode_json(raw, "research ledger"), identity, now)


def _catalog_states(root_fd: int, now: int):
    entries = sorted(os.listdir(root_fd))
    if len(entries) > 1024:
        raise ResearchBudgetError("research discovery catalog exceeds its bounded scan")
    for name in entries:
        if name == "catalog.lock" or re.fullmatch(r"[0-9a-f]{64}\.lock", name) or re.fullmatch(r"\.(?:tmp|provenance)-[0-9]+-[0-9a-f]{16}", name):
            storage._validate_regular_stat(storage._entry_stat(root_fd, name), "research catalog entry")
            continue
        if re.fullmatch(r"[0-9a-f]{64}\.json", name) is None:
            raise ResearchBudgetError("ambiguous research catalog entry")
        raw = storage._read_bytes_entry(root_fd, name)
        if len(raw) > 131072:
            raise ResearchBudgetError("research discovery record is oversized")
        state = storage._decode_json(raw, "research discovery record")
        identity = state.get("identity")
        if not isinstance(identity, dict) or set(identity) != {"git_common_dir", "branch", "run_id", "scope_digest"} or not isinstance(identity["run_id"], str) or IDENTIFIER.fullmatch(identity["run_id"]) is None:
            raise ResearchBudgetError("ambiguous discovered research identity")
        if identity["git_common_dir"] is None:
            if identity["branch"] is not None or not isinstance(identity["scope_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", identity["scope_digest"]) is None:
                raise ResearchBudgetError("invalid discovered scope identity")
        elif not isinstance(identity["git_common_dir"], str) or not Path(identity["git_common_dir"]).is_absolute() or not isinstance(identity["branch"], str) or not identity["branch"] or identity["scope_digest"] is not None:
            raise ResearchBudgetError("invalid discovered repository identity")
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if name != f"{key}.json":
            raise ResearchBudgetError("substituted discovered research identity")
        yield name, _validate(state, identity, now)


def initialize(repo, branch, run_id, *, limits=None, limit_source="standalone-default", state_home=None, scope_digest=None) -> dict:
    limits = _limits(DEFAULT_LIMITS if limits is None else limits, DEFAULT_LIMITS)
    limit_source = _text(limit_source, "limit source")
    with _transaction(repo, branch, run_id, state_home=state_home, create=True, scope_digest=scope_digest) as (root_fd, home_fd, locator, identity):
        now = _now_ms()
        try:
            state = _read(root_fd, locator, identity, now)
        except storage._MissingState:
            for _, existing in _catalog_states(root_fd, now):
                previous = existing["identity"]
                if previous["git_common_dir"] == identity["git_common_dir"] and previous["run_id"] == run_id:
                    raise ResearchBudgetError("research run already has another original identity, discover and load it without resetting")
            state = {"schema_version": "research-budget.v1", "identity": identity, "revision": 0, "initial_limits": limits, "limits": limits, "lifecycle": "active", "closure_reference": None,
                     "limit_source": limit_source, "extensions": [], "dispatches": {}, "activity_intervals": [], "last_observed_at_ms": now}
            storage._revalidate_directory(home_fd, "research-budgets", root_fd)
            storage._create_json_entry_exclusive(root_fd, locator.name, _validate(state, identity, now))
        if state["initial_limits"] != limits or state["limit_source"] != limit_source:
            raise ResearchBudgetError("existing research allowance must be loaded, not reset")
        return _receipt(state, locator, now)


def load(repo, branch, run_id, *, locator=None, state_home=None, scope_digest=None) -> dict:
    with _transaction(repo, branch, run_id, locator=locator, state_home=state_home, scope_digest=scope_digest) as (root_fd, home_fd, actual, identity):
        now = _now_ms()
        return _receipt(_read(root_fd, actual, identity, now), actual, now)


def discover(repo=None, *, run_id=None, state_home=None, scope_digest=None) -> dict:
    if run_id is not None and (not isinstance(run_id, str) or IDENTIFIER.fullmatch(run_id) is None):
        raise ResearchBudgetError("invalid discovery run ID")
    pools = []
    try:
        with _transaction(repo, "research-discovery" if repo is not None else None, "discovery", state_home=state_home, scope_digest=scope_digest) as (root_fd, home_fd, locator, expected):
            now = _now_ms()
            for name, state in _catalog_states(root_fd, now):
                identity = state["identity"]
                same_scope = identity["git_common_dir"] == expected["git_common_dir"] and identity["scope_digest"] == expected["scope_digest"]
                if state["lifecycle"] != "active" or not same_scope or (run_id is not None and identity["run_id"] != run_id):
                    continue
                if repo is not None:
                    storage._check_branch_name(storage._repository(Path(repo)), identity["branch"], allow_protected=True)
                receipt = _receipt(state, locator.parent / name, now)
                pools.append({field: receipt[field] for field in ("locator", "identity", "revision", "limit_source", "initial_limits", "limits", "spent_turns", "outstanding_dispatch_ids", "active_seconds", "remaining")})
                if len(pools) > 128:
                    raise ResearchBudgetError("research discovery contains too many active pools")
    except ResearchBudgetMissingState:
        return {"status": "none", "pools": []}
    return {"status": "none" if not pools else "found" if len(pools) == 1 else "ambiguous", "pools": pools}


def apply(repo, branch, run_id, revision, operation, *, locator=None, state_home=None, scope_digest=None) -> dict:
    with _transaction(repo, branch, run_id, locator=locator, state_home=state_home, scope_digest=scope_digest) as (root_fd, home_fd, actual, identity):
        now = _now_ms()
        state = _read(root_fd, actual, identity, now)
        if not _integer(revision) or revision != state["revision"]:
            raise ResearchBudgetError("stale research revision, reload the same ledger")
        if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
            raise ResearchBudgetError("invalid research operation")
        op = operation["op"]
        if state["lifecycle"] == "closed":
            if operation == {"op": "close", "completion_reference": state["closure_reference"]}:
                return _receipt(state, actual, now)
            raise ResearchBudgetError("closed research pools cannot restart capacity")
        if op == "reserve":
            if set(operation) != {"op", "dispatch_id", "question"}:
                raise ResearchBudgetError("invalid research reservation fields")
            dispatch_id = operation["dispatch_id"]
            if not isinstance(dispatch_id, str) or IDENTIFIER.fullmatch(dispatch_id) is None or dispatch_id in state["dispatches"]:
                raise ResearchBudgetError("research dispatch ID must be new and unique")
            question = _text(operation["question"], "named research question")
            remaining = _receipt(state, actual, now)["remaining"]
            if any(remaining[key] <= 0 for key in remaining):
                raise ResearchBudgetError("cumulative research allowance exhausted")
            state["dispatches"][dispatch_id] = {"question": question, "status": "reserved", "started_at_ms": now, "ended_at_ms": None, "evidence_reference": None}
        elif op in {"interrupt", "reconcile"}:
            required = {"op", "dispatch_id"} if op == "interrupt" else {"op", "dispatch_id", "outcome", "evidence_reference"}
            optional = {"ended_at_ms"} if op == "reconcile" else set()
            if not required <= set(operation) <= required | optional or not isinstance(operation["dispatch_id"], str) or operation["dispatch_id"] not in state["dispatches"]:
                raise ResearchBudgetError("invalid research return fields or dispatch ID")
            dispatch = state["dispatches"][operation["dispatch_id"]]
            if op == "interrupt":
                if dispatch["status"] not in OUTSTANDING:
                    raise ResearchBudgetError("completed research cannot become outstanding")
                if dispatch["status"] == "interrupted":
                    return _receipt(state, actual, now)
                dispatch["status"] = "interrupted"
            else:
                outcome = operation["outcome"]
                reference = _text(operation["evidence_reference"], "reconciliation evidence reference")
                if not isinstance(outcome, str) or outcome not in TERMINAL:
                    raise ResearchBudgetError("invalid research return outcome")
                if dispatch["status"] in TERMINAL:
                    if dispatch["status"] != outcome or dispatch["evidence_reference"] != reference or ("ended_at_ms" in operation and operation["ended_at_ms"] != dispatch["ended_at_ms"]):
                        raise ResearchBudgetError("conflicting duplicate research reconciliation")
                    return _receipt(state, actual, now)
                ended_at = operation.get("ended_at_ms", now)
                if not _integer(ended_at, dispatch["started_at_ms"], now):
                    raise ResearchBudgetError("invalid attested research end time")
                dispatch.update(status=outcome, ended_at_ms=ended_at, evidence_reference=reference)
        elif op in {"activity-start", "activity-stop"}:
            if set(operation) != {"op"}:
                raise ResearchBudgetError("invalid coordinator activity fields")
            intervals = state["activity_intervals"]
            active = bool(intervals and intervals[-1][1] is None)
            if op == "activity-start":
                if active:
                    return _receipt(state, actual, now)
                if len(intervals) >= 128:
                    raise ResearchBudgetError("coordinator activity ledger exhausted")
                intervals.append([now, None])
            elif not active:
                raise ResearchBudgetError("no open coordinator activity to stop")
            else:
                intervals[-1][1] = now
        elif op == "close":
            if set(operation) != {"op", "completion_reference"}:
                raise ResearchBudgetError("invalid workflow closure fields")
            reference = _text(operation["completion_reference"], "workflow closure reference")
            if any(dispatch["status"] in OUTSTANDING for dispatch in state["dispatches"].values()) or (state["activity_intervals"] and state["activity_intervals"][-1][1] is None):
                raise ResearchBudgetError("cannot close research with outstanding work")
            state["lifecycle"] = "closed"
            state["closure_reference"] = reference
        elif op == "extend":
            if set(operation) != {"op", "limits", "authorization_reference"} or len(state["extensions"]) >= 16:
                raise ResearchBudgetError("invalid bounded research extension")
            limits = _limits(operation["limits"])
            reference = _text(operation["authorization_reference"], "extension authorization reference")
            if limits == state["limits"] or any(limits[key] < state["limits"][key] for key in limits):
                raise ResearchBudgetError("research extension must increase limits without resetting totals")
            state["extensions"].append({"limits": limits, "authorization_reference": reference})
            state["limits"] = limits
        else:
            raise ResearchBudgetError("unknown research operation")
        state["revision"] += 1
        state["last_observed_at_ms"] = now
        storage._revalidate_directory(home_fd, "research-budgets", root_fd)
        storage._atomic_write_entry(root_fd, actual.name, _validate(state, identity, now))
        return _receipt(state, actual, now)


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description="Persist bounded coordinator research accounting outside the repository")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("initialize", "discover", "load", "apply"):
        command = subparsers.add_parser(name)
        identity_mode = command.add_mutually_exclusive_group(required=True)
        identity_mode.add_argument("--repo", type=Path)
        identity_mode.add_argument("--scope-digest")
        command.add_argument("--branch")
        command.add_argument("--run-id", required=name != "discover")
        command.add_argument("--state-home", type=Path)
        if name in {"load", "apply"}:
            command.add_argument("--locator", type=Path)
        if name == "apply":
            command.add_argument("--revision", type=int, required=True)
        if name == "initialize":
            command.add_argument("--turns", type=int, default=6)
            command.add_argument("--active-seconds", type=int, default=1200)
            command.add_argument("--in-flight", type=int, default=3)
            command.add_argument("--limit-source", default="standalone-default")
    args = parser.parse_args(arguments)
    try:
        options = {"state_home": args.state_home, "scope_digest": args.scope_digest}
        if args.command in {"load", "apply"}:
            options["locator"] = args.locator
        if args.command == "initialize":
            result = initialize(args.repo, args.branch, args.run_id, limits={"turns": args.turns, "active_seconds": args.active_seconds, "in_flight": args.in_flight}, limit_source=args.limit_source, **options)
        elif args.command == "discover":
            result = discover(args.repo, run_id=args.run_id, **options)
        elif args.command == "load":
            result = load(args.repo, args.branch, args.run_id, **options)
        else:
            raw = sys.stdin.buffer.read(65537)
            if len(raw) > 65536:
                raise ResearchBudgetError("research operation is oversized")
            operation = json.loads(raw, object_pairs_hook=storage._unique_object)
            result = apply(args.repo, args.branch, args.run_id, args.revision, operation, **options)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 2 if result.get("status") == "ambiguous" else 0
    except (ResearchBudgetError, ValueError, TypeError, UnicodeError) as error:
        print(f"research_budget_error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
