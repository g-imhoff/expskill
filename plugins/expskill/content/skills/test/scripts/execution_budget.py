from __future__ import annotations

import json
import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ACTIONS = 8
DEFAULT_SECONDS = 780
HARD_ACTIONS = 64
HARD_WAVES = 16
HARD_SECONDS = 3600
FIELDS = {"semantic_actions_max", "waves_max", "usable_budget_seconds", "rationale", "waves"}
SCOPE_FIELDS = ("workflow_id", "accepted_behavior", "scope", "material_oracles", "exemption_grounding_artifact_ids")


def _private_json(path: Path) -> dict[str, object]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_size > 2_000_000:
        raise ValueError("successor evidence must be a bounded private owned file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("successor evidence must be an object")
    return value


def retained_files(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("successor history cannot contain symlinks")
        if path.is_file() and path != root / "successor.json":
            if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise ValueError("successor history must remain private and owned")
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def successor_context(charter: dict[str, object], root: Path, seen: set[Path] | None = None) -> dict[str, object]:
    seen = set() if seen is None else seen
    if root in seen or len(seen) >= HARD_ACTIONS:
        raise ValueError("successor history is cyclic or exceeds the bounded chain")
    seen.add(root)
    bootstrap = json.loads((root / "bootstrap.json").read_text()) if (root / "bootstrap.json").is_file() else {}
    link = bootstrap.get("successor")
    if link is None:
        return {"actions_used": 0, "rerun_action_ids": []}
    _private_json(root / "bootstrap.json")
    if not isinstance(link, dict) or set(link) != {"predecessor_root", "reason", "classification", "correction", "source_files"}:
        raise ValueError("successor binding fields are invalid")
    predecessor = Path(str(link["predecessor_root"]))
    if not predecessor.is_absolute() or predecessor.parent != root.parent or predecessor.is_symlink() or predecessor == root:
        raise ValueError("successor must retain a canonical predecessor in the same worktree")
    if link["reason"] != "recovery" or link["classification"] not in {"test-system-defect", "environment-blocker"} or not isinstance(link["correction"], str) or not link["correction"].strip():
        raise ValueError("successor requires a classified recovery and correction")
    if retained_files(predecessor) != link["source_files"]:
        raise ValueError("retained predecessor evidence changed after succession")
    closure = _private_json(predecessor / "successor.json")
    if closure != {"root": str(root), "run_id": root.name}:
        raise ValueError("predecessor closure does not bind this successor")
    previous = _private_json(predecessor / "charter.json")
    ledger = _private_json(predecessor / "ledger.json")
    opening = _private_json(predecessor / "bootstrap.json")
    if previous.get("repository") != charter.get("repository") or previous.get("branch") != charter.get("branch") or previous.get("head") != charter.get("head"):
        raise ValueError("recovery successor must preserve repository, branch, and HEAD")
    if any(previous.get(field) != charter.get(field) for field in SCOPE_FIELDS) or for_charter(previous) != for_charter(charter):
        raise ValueError("successor cannot change accepted scope or reset the execution budget")
    if bootstrap.get("started_at") != opening.get("started_at"):
        raise ValueError("successor cannot reset the original start time")
    entries = ledger.get("entries")
    if ledger.get("run_id") != predecessor.name or not isinstance(entries, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("action_id"), str) for entry in entries):
        raise ValueError("retained predecessor ledger is invalid")
    prior = successor_context(previous, predecessor, seen)
    final_records = 0
    if (predecessor / "final-action.json").exists():
        specification = _private_json(predecessor / "final-action.json")
        metadata_path = Path(str(specification.get("metadata_path", "")))
        if metadata_path.is_absolute() or not metadata_path.parts or ".." in metadata_path.parts:
            raise ValueError("retained final-action metadata path is invalid")
        if (predecessor / metadata_path).exists():
            final_records = int(_private_json(predecessor / metadata_path).get("schema_version") == "test-final-action-record.v1")
            if final_records and (predecessor / "draft.json").exists():
                draft = _private_json(predecessor / "draft.json")
                identifiers = {item.get("artifact_id") for item in draft.get("artifacts", []) if isinstance(item, dict) and item.get("path") == metadata_path.as_posix()}
                if any(identifiers & set(entry.get("artifact_ids", [])) for entry in entries):
                    final_records = 0
    return {"actions_used": int(prior["actions_used"]) + len(entries) + final_records,
            "rerun_action_ids": sorted(set(prior["rerun_action_ids"]) | {entry["action_id"] for entry in entries})}


def _decimal(value: object, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a decimal string")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def validate(value: object, oracles: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("execution budget fields are not exact")
    actions = _decimal(value["semantic_actions_max"], "semantic_actions_max", 1, HARD_ACTIONS)
    wave_limit = _decimal(value["waves_max"], "waves_max", 1, HARD_WAVES)
    _decimal(value["usable_budget_seconds"], "usable_budget_seconds", DEFAULT_SECONDS, HARD_SECONDS)
    rationale = value["rationale"]
    if not isinstance(rationale, str) or not rationale.strip() or "\x00" in rationale:
        raise ValueError("execution budget needs a scope-derived rationale")
    waves = value["waves"]
    if not isinstance(waves, list) or not waves or len(waves) > wave_limit:
        raise ValueError("waves must fit the declared wave limit")
    planned: list[str] = []
    for wave in waves:
        if not isinstance(wave, list) or not 1 <= len(wave) <= DEFAULT_ACTIONS:
            raise ValueError("each wave must contain between one and eight action IDs")
        if any(not isinstance(item, str) or not item or "\x00" in item for item in wave):
            raise ValueError("wave action IDs must be nonempty strings")
        planned.extend(wave)
    if len(planned) != len(set(planned)) or len(planned) > actions:
        raise ValueError("planned actions must be unique and fit the declared action limit")
    if not isinstance(oracles, list):
        raise ValueError("material oracles must be an array")
    required: set[str] = set()
    for oracle in oracles:
        if not isinstance(oracle, dict) or not isinstance(oracle.get("required_action_ids"), list):
            raise ValueError("material oracle action IDs must be an array")
        identifiers = oracle["required_action_ids"]
        if any(not isinstance(action, str) or not action for action in identifiers):
            raise ValueError("material oracle action IDs must be nonempty strings")
        required.update(identifiers)
    if not required <= set(planned):
        raise ValueError("execution budget omits required scope actions")
    return value


def for_charter(charter: dict[str, object]) -> dict[str, object]:
    if charter.get("schema_version") == "test-charter.v1":
        return {"semantic_actions_max": str(DEFAULT_ACTIONS), "waves_max": "1", "usable_budget_seconds": str(DEFAULT_SECONDS), "rationale": "Legacy bounded run.", "waves": []}
    if charter.get("schema_version") != "test-charter.v2":
        raise ValueError("unsupported charter budget schema")
    return validate(charter.get("execution_budget"), charter.get("material_oracles"))


def validate_actions(budget: dict[str, object], action_ids: list[str], *, root: Path | None = None, charter: dict[str, object] | None = None) -> None:
    consumed = int(successor_context(charter, root)["actions_used"]) if root is not None and charter is not None else 0
    if consumed + len(action_ids) > int(budget["semantic_actions_max"]):
        raise ValueError("executed actions exceed the frozen action budget")
    order = {action: index for index, wave in enumerate(budget["waves"]) for action in wave}
    if order:
        if any(action not in order for action in action_ids):
            raise ValueError("executed action is absent from the frozen waves")
        indexes = [order[action] for action in action_ids]
        if indexes != sorted(indexes):
            raise ValueError("executed actions cross the frozen wave order")


def remaining_seconds(charter: dict[str, object], root: Path) -> float:
    budget = for_charter(charter)
    successor_context(charter, root)
    record = root / "bootstrap.json"
    if record.is_file() and not record.is_symlink():
        payload = json.loads(record.read_text(encoding="utf-8"))
        started = datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00")).timestamp()
    else:
        started = (root / "charter.json").stat().st_mtime
    return started + int(budget["usable_budget_seconds"]) - datetime.now(timezone.utc).timestamp()
