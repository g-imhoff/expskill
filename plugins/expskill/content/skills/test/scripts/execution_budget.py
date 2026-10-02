from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ACTIONS = 8
DEFAULT_SECONDS = 780
HARD_ACTIONS = 64
HARD_WAVES = 16
HARD_SECONDS = 3600
FIELDS = {"semantic_actions_max", "waves_max", "usable_budget_seconds", "rationale", "waves"}


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


def validate_actions(budget: dict[str, object], action_ids: list[str]) -> None:
    if len(action_ids) > int(budget["semantic_actions_max"]):
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
    record = root / "bootstrap.json"
    if record.is_file() and not record.is_symlink():
        payload = json.loads(record.read_text(encoding="utf-8"))
        started = datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00")).timestamp()
    else:
        started = (root / "charter.json").stat().st_mtime
    return started + int(budget["usable_budget_seconds"]) - datetime.now(timezone.utc).timestamp()
