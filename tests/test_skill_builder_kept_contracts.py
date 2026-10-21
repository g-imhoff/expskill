from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests import test_skill_builder_evaluate as evaluate
from tests import test_skill_builder_modes as modes
from tests import test_skill_builder_state as support


PACKAGE = {
    "SKILL.md": support.CANDIDATE_SKILL_BYTES,
    "references/keep.md": modes.KEPT_REFERENCE_BYTES,
}
PRE_CONTRACT_STAGES = (
    ("baseline", "baseline-report", "capture-baseline", "baseline"),
    ("research", "research-pack", "complete-research", "research"),
    ("sieve", "evidence-sieve", "sieve-evidence", "sieve"),
    ("design", "design-record", "accept-design", "design"),
)
SWITCH_CONFIRMATION = {
    "user_identity": "test-user",
    "confirmed_at": "2026-09-05T12:00:00Z",
    "confirmation_text": "Yes, this changes the contract, so switch.",
}
SWITCH_EVENT_DIGEST = support.fixture_digest(b"user answers yes to the switch\n")
SWITCHED_AWAY = ["confirmation", "contract", "design", "evaluation", "sieve"]


def target_path(tmp_path: Path) -> Path:
    return tmp_path / "skills" / "sample-skill"


def store_path(tmp_path: Path) -> Path:
    canonical = support.target_identity(target_path(tmp_path))["canonical"]
    return (
        tmp_path
        / "state"
        / "contracts"
        / f"{support.fixture_digest(canonical.encode('utf-8'))}.json"
    )


def payload_of(state_root: Path, workflow_id: str, artifact_id: str) -> dict[str, object]:
    path = state_root / "live" / workflow_id / "artifacts" / artifact_id / "raw" / "record.json"
    return json.loads(path.read_text(encoding="utf-8"))


def envelope_of(state_root: Path, workflow_id: str, artifact_id: str) -> dict[str, object]:
    path = state_root / "live" / workflow_id / "artifacts" / artifact_id / "envelope.json"
    return json.loads(path.read_text(encoding="utf-8"))


def verified_build_run(
    helper: ModuleType, tmp_path: Path, mode: str = "improve"
) -> tuple[Path, str, int]:
    state_root, workflow_id, sequence, _ = support.build_verified_stage(
        helper,
        tmp_path,
        mode=mode,
        candidate_additional_skill_files={"references/keep.md": modes.KEPT_REFERENCE_BYTES},
        candidate_owned_paths=["SKILL.md"],
        candidate_resulting_bytes=support.candidate_result_bytes(),
        trial_loaded_skill_files=PACKAGE,
    )
    return state_root, workflow_id, sequence


def finalized_build_run(
    helper: ModuleType, tmp_path: Path, mode: str = "improve"
) -> tuple[Path, str, dict[str, object]]:
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = verified_build_run(helper, tmp_path, mode)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    return state_root, workflow_id, finalized


def delivered_build_run(
    helper: ModuleType, tmp_path: Path
) -> tuple[Path, str, int, dict[str, object]]:
    state_root, workflow_id, finalized = finalized_build_run(helper, tmp_path)
    support.write_skill_package(target_path(tmp_path), PACKAGE)
    delivered = modes.record_current_delivery(
        helper, state_root, workflow_id, finalized["sequence"], target_path(tmp_path)
    )
    return state_root, workflow_id, delivered["sequence"], finalized


def advance_to_research(helper: ModuleType, tmp_path: Path, mode: str) -> tuple[Path, str, int]:
    state_root = tmp_path / "state"
    workflow_id = str(
        modes.initialize_existing(helper, tmp_path, target_path(tmp_path), mode)["workflow_id"]
    )
    sequence = 0
    for artifact_id, artifact_type, event, stage in PRE_CONTRACT_STAGES[:2]:
        sequence = support.advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )["sequence"]
    return state_root, workflow_id, sequence


def advance_to_contract(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    *,
    suffix: str = "",
    overrides: dict[str, object] | None = None,
) -> int:
    for artifact_id, artifact_type, event, stage in PRE_CONTRACT_STAGES[2:]:
        sequence = support.advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id + suffix,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )["sequence"]
    return accept_contract(
        helper,
        state_root,
        workflow_id,
        sequence,
        "contract" + suffix,
        "design" + suffix,
        overrides,
    )


def accept_contract(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    contract_id: str = "contract",
    design_id: str = "design",
    overrides: dict[str, object] | None = None,
) -> int:
    contract = support.contract_payload(helper, state_root, workflow_id)
    for binding in contract["evidence_bindings"]:
        binding["artifact_id"] = design_id
    contract.update(overrides or {})
    retained = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=contract_id,
        artifact_type="skill-contract",
        payload=contract,
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=retained["sequence"],
        event="accept-contract",
        destination_stage="contract",
        artifact_ids=[contract_id],
        state_root=state_root,
    )["sequence"]


def build_run_to_contract(
    helper: ModuleType,
    tmp_path: Path,
    mode: str,
    overrides: dict[str, object] | None = None,
) -> tuple[Path, str, int]:
    state_root, workflow_id, sequence = advance_to_research(helper, tmp_path, mode)
    return (
        state_root,
        workflow_id,
        advance_to_contract(helper, state_root, workflow_id, sequence, overrides=overrides),
    )


def confirm_with_user(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    contract_id: str = "contract",
    suffix: str = "",
    authority_digest: str = "2" * 64,
) -> int:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    retained = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="confirmation" + suffix,
        artifact_type="user-confirmation-record",
        payload=support.confirmation_payload(
            contract_id=contract_id,
            contract_digest=current["artifact_index"][contract_id]["digest"],
            target=current["target_identity"]["canonical"],
            snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_digest=authority_digest,
        ),
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=retained["sequence"],
        event="confirm-contract",
        destination_stage="confirmed",
        artifact_ids=["confirmation" + suffix],
        authority_event_digest=authority_digest,
        state_root=state_root,
    )["sequence"]


def freeze_evaluation(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    contract_id: str,
    confirmation_id: str,
    artifact_id: str = "evaluation",
    cases: list[dict[str, object]] | None = None,
) -> int:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    retained = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        artifact_type="evaluation-pack",
        payload=support.evaluation_payload(
            contract_digest=current["artifact_index"][contract_id]["digest"],
            confirmation_digest=current["artifact_index"][confirmation_id]["digest"],
            snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            cases=support.frozen_cases() if cases is None else cases,
        ),
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=retained["sequence"],
        event="freeze-evaluation",
        destination_stage="evaluation",
        artifact_ids=[artifact_id],
        state_root=state_root,
    )["sequence"]


def reuse(
    helper: ModuleType, state_root: Path, workflow_id: str, sequence: int
) -> dict[str, object]:
    shown = helper.read_kept_contract(workflow_id=workflow_id, state_root=state_root)
    return helper.reuse_kept_contract(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        kept_contract_digest=shown["kept_contract_digest"] or "0" * 64,
        state_root=state_root,
    )


def switch(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    mode: str,
    **overrides: object,
) -> dict[str, object]:
    request: dict[str, object] = {
        "workflow_id": workflow_id,
        "expected_sequence": sequence,
        "mode": mode,
        "reason": "the request changes the contract",
        "confirmation": dict(SWITCH_CONFIRMATION),
        "authority_event_digest": SWITCH_EVENT_DIGEST,
        "state_root": state_root,
    }
    request.update(overrides)
    return helper.switch_mode_run(**request)


def redigest(helper: ModuleType, record: dict[str, object]) -> dict[str, object]:
    record["contract_digest"] = helper.canonical_digest(record["contract"])
    record["confirmation_digest"] = helper.canonical_digest(record["confirmation"])
    record["record_digest"] = helper.canonical_digest(record, "record_digest")
    return record


def confirm_by_reuse(monkeypatch: pytest.MonkeyPatch, helper: ModuleType) -> dict[str, str]:
    """Send the stock builders' confirmation step through reuse-contract."""
    retain_json = support.retain_json
    transition_run = helper.transition_run
    used: dict[str, str] = {}

    def retain(helper_arg: ModuleType, **request: Any) -> dict[str, object]:
        if request["artifact_type"] != "user-confirmation-record":
            return retain_json(helper_arg, **request)
        reused = reuse(
            helper_arg, request["state_root"], request["workflow_id"], request["sequence"]
        )
        used["id"] = str(reused["confirmation_artifact_id"])
        return {
            "sequence": reused["sequence"],
            "artifact_digest": reused["confirmation_artifact_digest"],
        }

    def transition(**request: Any) -> dict[str, object]:
        if request["event"] == "confirm-contract":
            return {"sequence": request["expected_sequence"]}
        return transition_run(**request)

    monkeypatch.setattr(support, "retain_json", retain)
    monkeypatch.setattr(helper, "transition_run", transition)
    return used


def switch_inside_builder(
    monkeypatch: pytest.MonkeyPatch, helper: ModuleType, after_stage: str
) -> None:
    """Switch the stock builder's improve run to modify once it reaches after_stage."""
    advance = support.advance_with_artifact
    contract_payload = support.contract_payload
    stages = [stage for *_, stage in PRE_CONTRACT_STAGES]
    redo = PRE_CONTRACT_STAGES[2 : stages.index(after_stage) + 1]

    def advance_and_switch(helper_arg: ModuleType, **request: Any) -> dict[str, object]:
        result = advance(helper_arg, **request)
        if request["destination_stage"] != after_stage:
            return result
        state_root, workflow_id = request["state_root"], request["workflow_id"]
        sequence = switch(helper_arg, state_root, workflow_id, result["sequence"], "modify")[
            "sequence"
        ]
        for artifact_id, artifact_type, event, stage in redo:
            sequence = advance(
                helper_arg,
                state_root=state_root,
                workflow_id=workflow_id,
                sequence=sequence,
                artifact_id=artifact_id + "-modify",
                artifact_type=artifact_type,
                event=event,
                destination_stage=stage,
            )["sequence"]
        return {**result, "sequence": sequence}

    def contract_after_switch(
        helper_arg: ModuleType, state_root: Path, workflow_id: str, **options: object
    ) -> dict[str, object]:
        payload = contract_payload(helper_arg, state_root, workflow_id, **options)
        design_id, _ = support.accepted_artifact(
            helper_arg, state_root, workflow_id, "design-record"
        )
        for binding in payload["evidence_bindings"]:
            binding["artifact_id"] = design_id
        return payload

    monkeypatch.setattr(support, "advance_with_artifact", advance_and_switch)
    monkeypatch.setattr(support, "contract_payload", contract_after_switch)


def statuses(helper: ModuleType, state_root: Path, workflow_id: str) -> dict[str, str]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    return {
        artifact_id: record["derived_status"]
        for artifact_id, record in current["artifact_index"].items()
    }


def test_finalize_keeps_the_contract_outside_the_run_through_cleanup(
    tmp_path: Path,
) -> None:
    """A build run's confirmed contract outlives its run, keyed to the finalized files."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, finalized = delivered_build_run(helper, tmp_path)
    path = store_path(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))

    assert finalized["kept_contract_digest"] == record["record_digest"]
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert record["schema_version"] == "skill-builder-kept-contract.v1"
    assert record["skill_file_digest"] == support.fixture_loadable_skill_digest(PACKAGE)
    assert record["contract"] == payload_of(state_root, workflow_id, "contract")
    assert record["confirmation"] == payload_of(state_root, workflow_id, "confirmation")
    assert record["contract_digest"] == helper.canonical_digest(record["contract"])
    assert record["confirmation_digest"] == helper.canonical_digest(record["confirmation"])
    assert (record["contract_schema"], record["confirmation_schema"]) == (
        "skill-builder-contract.v2",
        "skill-builder-user-confirmation.v1",
    )
    assert (record["source_workflow_id"], record["source_mode"], record["source_stage"]) == (
        workflow_id,
        "improve",
        "finalized",
    )
    assert record["source_final_transition_digest"] == finalized["receipt_digest"]
    assert record["record_digest"] == helper.canonical_digest(record, "record_digest")

    cleanup_event = b"authorized cleanup event\n"
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        authority_event_digest=support.fixture_digest(cleanup_event),
        authority_event=cleanup_event,
        actor="user",
        state_root=state_root,
    )
    kept_bytes = path.read_bytes()
    helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert not (state_root / "live" / workflow_id).exists()
    assert path.read_bytes() == kept_bytes

    state_root, next_id, sequence = build_run_to_contract(helper, tmp_path, "improve")
    reused = reuse(helper, state_root, next_id, sequence)
    assert reused["stage"] == "confirmed"
    assert reused["kept_contract_digest"] == record["record_digest"]
    assert reused["kept_contract_source"] == workflow_id


def test_report_keeps_the_evaluated_contract_through_cleanup(tmp_path: Path) -> None:
    """An evaluation keys its kept contract to the snapshot it scored."""
    helper = support.load_helper()
    state_root, workflow_id, reported = evaluate.build_reported(helper, tmp_path)
    path = store_path(tmp_path)
    record = json.loads(path.read_text(encoding="utf-8"))

    assert reported["kept_contract_digest"] == record["record_digest"]
    assert record["skill_file_digest"] == support.fixture_loadable_skill_digest(
        evaluate.TARGET_FILES
    )
    assert (record["source_mode"], record["source_stage"]) == ("evaluate", "reported")
    assert record["source_final_transition_digest"] == reported["receipt_digest"]
    assert record["contract"]["design_digest"] is None

    authorized = evaluate.authorize_cleanup(
        helper, state_root, workflow_id, reported["sequence"]
    )
    kept_bytes = path.read_bytes()
    helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert path.read_bytes() == kept_bytes


def test_evaluate_and_improve_reuse_a_kept_contract_on_unchanged_files(
    tmp_path: Path,
) -> None:
    """A matching snapshot confirms the same contract without asking, and says which one."""
    helper = support.load_helper()
    state_root, first_id, _ = evaluate.build_reported(helper, tmp_path)
    kept = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))

    _, second_id, sequence = evaluate.build_contract(helper, tmp_path)
    shown = helper.read_kept_contract(workflow_id=second_id, state_root=state_root)
    assert shown["kept_contract"] == kept
    assert (shown["matches_target"], shown["reusable"]) == (True, True)
    reused = reuse(helper, state_root, second_id, sequence)
    confirmation = payload_of(state_root, second_id, str(reused["confirmation_artifact_id"]))
    assert confirmation["schema_version"] == "skill-builder-reused-confirmation.v1"
    assert confirmation["kept_contract_digest"] == kept["record_digest"]
    assert confirmation["kept_contract"] == kept
    sequence = freeze_evaluation(
        helper,
        state_root,
        second_id,
        reused["sequence"],
        "contract",
        str(reused["confirmation_artifact_id"]),
    )
    (state_root / "live" / second_id / "current.json").write_text("{broken", encoding="utf-8")
    recovered = helper.recover_run(workflow_id=second_id, state_root=state_root)
    assert (recovered["stage"], recovered["sequence"]) == ("evaluation", sequence)

    helper.invalidate_run(
        workflow_id=second_id,
        expected_sequence=sequence,
        change_kind="target-snapshot",
        changed_artifact_id="resolution",
        reason="hand the target to an improve run",
        state_root=state_root,
    )
    _, third_id, sequence = build_run_to_contract(helper, tmp_path, "improve")
    reused = reuse(helper, state_root, third_id, sequence)
    assert reused["kept_contract_source"] == first_id
    current = helper.load_run(workflow_id=third_id, state_root=state_root)
    assert current["stage"] == "confirmed"

    helper._keep_contract(
        state_root,
        state_root / "live" / third_id,
        current,
        {"destination_stage": "finalized", "receipt_digest": "a" * 64},
        kept["skill_file_digest"],
    )
    rekept = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    assert rekept["confirmation"] == kept["confirmation"]
    assert rekept["confirmation_limitations"] == kept["confirmation_limitations"]
    assert rekept["contract"] == payload_of(state_root, third_id, "contract")
    assert (rekept["source_workflow_id"], rekept["source_mode"]) == (third_id, "improve")


def test_an_identical_contract_keeps_frozen_selection_cases(tmp_path: Path) -> None:
    """Re-accepting the same contract keeps selection cases until the user confirms again."""
    helper = support.load_helper()
    state_root, _, _ = evaluate.build_reported(helper, tmp_path)
    selected = support.frozen_cases({"case-1": "candidate"})
    relabelled = support.frozen_cases({"case-1": "none"})

    def confirm(workflow_id: str, sequence: int, how: str, contract_id: str) -> tuple[int, str]:
        if how == "reuse":
            reused = reuse(helper, state_root, workflow_id, sequence)
            return reused["sequence"], str(reused["confirmation_artifact_id"])
        suffix = contract_id.removeprefix("contract")
        sequence = confirm_with_user(
            helper, state_root, workflow_id, sequence, contract_id, suffix, how
        )
        return sequence, "confirmation" + suffix

    def refreeze(first: str, second: str, cases: list[dict[str, object]]) -> str:
        _, workflow_id, sequence = build_run_to_contract(helper, tmp_path, "improve")
        try:
            sequence, confirmation_id = confirm(workflow_id, sequence, first, "contract")
            sequence = freeze_evaluation(
                helper,
                state_root,
                workflow_id,
                sequence,
                "contract",
                confirmation_id,
                cases=selected,
            )
            sequence = helper.invalidate_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                change_kind="contract",
                changed_artifact_id="contract",
                reason="accept the same contract again",
                state_root=state_root,
            )["sequence"]
            sequence = accept_contract(helper, state_root, workflow_id, sequence, "contract-2")
            sequence, confirmation_id = confirm(workflow_id, sequence, second, "contract-2")
            freeze_evaluation(
                helper,
                state_root,
                workflow_id,
                sequence,
                "contract-2",
                confirmation_id,
                artifact_id="evaluation-2",
                cases=cases,
            )
            return helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"]
        finally:
            helper.invalidate_run(
                workflow_id=workflow_id,
                expected_sequence=helper.load_run(
                    workflow_id=workflow_id, state_root=state_root
                )["head_sequence"],
                change_kind="target-snapshot",
                changed_artifact_id="resolution",
                reason="free the target for the next run",
                state_root=state_root,
            )

    for first, second in (("reuse", "reuse"), ("5" * 64, "reuse"), ("5" * 64, "5" * 64)):
        with pytest.raises(helper.RunStateError, match="changes a case's selection"):
            refreeze(first, second, relabelled)
    assert refreeze("reuse", "reuse", selected) == "evaluation"
    assert refreeze("reuse", "6" * 64, relabelled) == "evaluation"


def test_reuse_is_refused_when_files_contract_or_record_differ(tmp_path: Path) -> None:
    """Changed files, a changed contract, or a newer record each need the user again."""
    helper = support.load_helper()
    state_root, _, _ = evaluate.build_reported(helper, tmp_path)
    kept = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))

    _, changed_contract_id, sequence = evaluate.build_contract(
        helper, tmp_path, contract_overrides={"purpose": "build a different sample"}
    )
    with pytest.raises(helper.RunStateError, match="differs from the kept contract"):
        reuse(helper, state_root, changed_contract_id, sequence)
    with pytest.raises(helper.RunStateError, match="changed since it was read"):
        helper.reuse_kept_contract(
            workflow_id=changed_contract_id,
            expected_sequence=sequence,
            kept_contract_digest="0" * 64,
            state_root=state_root,
        )
    assert helper.load_run(workflow_id=changed_contract_id, state_root=state_root)[
        "stage"
    ] == "contract"
    helper.invalidate_run(
        workflow_id=changed_contract_id,
        expected_sequence=sequence,
        change_kind="target-snapshot",
        changed_artifact_id="resolution",
        reason="edit the target",
        state_root=state_root,
    )

    edited = {"SKILL.md": support.CANDIDATE_SKILL_BYTES + b"\nEdited.\n"}
    _, edited_id, sequence = evaluate.build_contract(helper, tmp_path, edited)
    shown = helper.read_kept_contract(workflow_id=edited_id, state_root=state_root)
    assert shown["kept_contract"] == kept
    assert (shown["matches_target"], shown["reusable"]) == (False, False)
    with pytest.raises(helper.RunStateError, match="other skill files"):
        reuse(helper, state_root, edited_id, sequence)
    assert json.loads(store_path(tmp_path).read_text(encoding="utf-8")) == kept

    store_path(tmp_path).unlink()
    with pytest.raises(helper.RunStateError, match="no kept contract"):
        reuse(helper, state_root, edited_id, sequence)


def test_modify_starts_from_the_kept_contract_and_confirms_the_change(
    tmp_path: Path,
) -> None:
    """Modify sees the kept contract but takes only the user's fresh confirmation."""
    helper = support.load_helper()
    state_root, _, _ = evaluate.build_reported(helper, tmp_path)
    kept = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))

    _, workflow_id, sequence = build_run_to_contract(
        helper, tmp_path, "modify", {"purpose": "build the sample with a new step"}
    )
    shown = helper.read_kept_contract(workflow_id=workflow_id, state_root=state_root)
    assert shown["kept_contract"] == kept
    assert (shown["mode"], shown["matches_target"], shown["reusable"]) == (
        "modify",
        True,
        False,
    )
    with pytest.raises(helper.RunStateError, match="modify confirms"):
        reuse(helper, state_root, workflow_id, sequence)
    assert confirm_with_user(helper, state_root, workflow_id, sequence) == sequence + 2


def test_switch_improve_to_modify_and_back_keeps_baseline_and_research(
    tmp_path: Path,
) -> None:
    """Each switch rewinds to the sieve, and later artifacts carry the new mode."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = build_run_to_contract(helper, tmp_path, "improve")
    sequence = confirm_with_user(helper, state_root, workflow_id, sequence)
    sequence = freeze_evaluation(
        helper, state_root, workflow_id, sequence, "contract", "confirmation"
    )

    switched = switch(helper, state_root, workflow_id, sequence, "modify")
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert (switched["stage"], switched["mode"]) == ("research", "modify")
    assert switched["invalidated_artifact_ids"] == SWITCHED_AWAY
    assert current["mode"]["name"] == "modify"
    assert current["mode"]["evidence"]["mode_switch_digest"] == switched["switch_artifact_digest"]
    assert {key: statuses(helper, state_root, workflow_id)[key] for key in ("baseline", "research")} == {
        "baseline": "accepted",
        "research": "accepted",
    }
    assert all(statuses(helper, state_root, workflow_id)[key] == "invalidated" for key in SWITCHED_AWAY)
    switch_record = payload_of(state_root, workflow_id, str(switched["switch_artifact_id"]))
    assert (switch_record["from_mode"], switch_record["to_mode"]) == ("improve", "modify")
    assert switch_record["confirmation_event_digest"] == SWITCH_EVENT_DIGEST

    sequence = advance_to_contract(
        helper,
        state_root,
        workflow_id,
        switched["sequence"],
        suffix="-modify",
        overrides={"purpose": "build the sample with a new step"},
    )
    sequence = confirm_with_user(
        helper, state_root, workflow_id, sequence, "contract-modify", "-modify"
    )
    assert envelope_of(state_root, workflow_id, "contract-modify")["mode"] == "modify"
    assert envelope_of(state_root, workflow_id, "baseline")["mode"] == "improve"

    back = switch(helper, state_root, workflow_id, sequence, "improve")
    assert (back["stage"], back["mode"]) == ("research", "improve")
    assert back["invalidated_artifact_ids"] == [
        "confirmation-modify",
        "contract-modify",
        "design-modify",
        "sieve-modify",
    ]
    sequence = advance_to_contract(
        helper, state_root, workflow_id, back["sequence"], suffix="-improve"
    )
    assert envelope_of(state_root, workflow_id, "sieve-improve")["mode"] == "improve"

    run = state_root / "live" / workflow_id
    (run / "current.json").write_text("{broken", encoding="utf-8")
    recovered = helper.recover_run(workflow_id=workflow_id, state_root=state_root)
    assert (recovered["stage"], recovered["sequence"]) == ("contract", sequence)
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert loaded["mode"]["name"] == "improve"
    discovered = modes.discover(helper, tmp_path, target_path(tmp_path))
    assert discovered["workflow_id"] == workflow_id


def test_switch_before_the_baseline_keeps_a_retained_baseline_in_the_family(
    tmp_path: Path,
) -> None:
    """A baseline retained before a switch still opens the baseline stage."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root = tmp_path / "state"
    workflow_id = str(
        modes.initialize_existing(helper, tmp_path, target_path(tmp_path), "improve")[
            "workflow_id"
        ]
    )
    retained = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        payload=support.valid_baseline_payload(helper, state_root, workflow_id),
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    switched = switch(helper, state_root, workflow_id, retained["sequence"], "modify")
    assert (switched["stage"], switched["invalidated_artifact_ids"]) == ("resolved", [])
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=switched["sequence"],
        event="capture-baseline",
        destination_stage="baseline",
        artifact_ids=["baseline"],
        state_root=state_root,
    )
    assert advanced["stage"] == "baseline"


def test_switch_needs_the_user_and_an_improve_or_modify_run(tmp_path: Path) -> None:
    """No confirmation, another mode pair, or a finished run refuses the switch."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = advance_to_research(helper, tmp_path, "improve")
    for overrides, error in (
        ({"confirmation": {}}, "user's confirmation"),
        (
            {"confirmation": {**SWITCH_CONFIRMATION, "confirmation_text": " "}},
            "confirmation text",
        ),
        ({"authority_event_digest": None}, "confirmation event digest"),
        ({"mode": "improve"}, "only between improve and modify"),
        ({"mode": "create"}, "only between improve and modify"),
        ({"mode": "evaluate"}, "only between improve and modify"),
        ({"reason": ""}, "reason"),
    ):
        with pytest.raises(helper.RunStateError, match=error):
            switch(
                helper,
                state_root,
                workflow_id,
                sequence,
                str(overrides.pop("mode", "modify")),
                **overrides,
            )
    with pytest.raises(helper.RunStateError, match="record their own artifacts"):
        support.retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id="forged-switch",
            artifact_type="mode-switch-record",
            payload={"schema_version": "skill-builder-mode-switch.v1"},
            input_bindings=support.current_bindings(helper, state_root, workflow_id),
        )
    paused = helper.pause_run(
        workflow_id=workflow_id, expected_sequence=sequence, state_root=state_root
    )
    with pytest.raises(helper.RunStateError, match="only an active run"):
        switch(helper, state_root, workflow_id, paused["sequence"], "modify")
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["mode"][
        "name"
    ] == "improve"

    other_root = tmp_path / "evaluate-run"
    _, evaluate_id, evaluate_sequence = evaluate.build_baseline(helper, other_root)
    with pytest.raises(helper.RunStateError, match="only between improve and modify"):
        switch(
            helper,
            other_root / "state",
            evaluate_id,
            evaluate_sequence,
            "modify",
        )
    create_root = tmp_path / "create-run"
    create_id = str(
        modes.initialize_create(helper, create_root, target_path(create_root))["workflow_id"]
    )
    with pytest.raises(helper.RunStateError, match="only between improve and modify"):
        switch(helper, create_root / "state", create_id, 0, "improve")


def test_replay_rejects_a_tampered_switch(tmp_path: Path) -> None:
    """The switch record must keep its mode pair and exact invalidation set."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = build_run_to_contract(helper, tmp_path, "improve")
    switched = switch(helper, state_root, workflow_id, sequence, "modify")
    record = payload_of(state_root, workflow_id, str(switched["switch_artifact_id"]))
    pristine = state_root.parent / "pristine"
    shutil.copytree(state_root, pristine, symlinks=True)
    for tampered, error in (
        ({**record, "to_mode": "create"}, "only between improve and modify"),
        ({**record, "invalidated_artifact_ids": []}, "invalidation is mismatched"),
    ):
        shutil.rmtree(state_root)
        shutil.copytree(pristine, state_root, symlinks=True)
        support.rewrite_artifact_payload_and_receipts(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            artifact_id=str(switched["switch_artifact_id"]),
            payload=tampered,
            rewrite_receipts=True,
        )
        with pytest.raises(helper.RunStateError, match=error):
            helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_kept_contract_store_rejects_unexpected_entries(tmp_path: Path) -> None:
    """The store holds only private records named by target digest."""
    helper = support.load_helper()
    state_root, _, _ = evaluate.build_reported(helper, tmp_path)
    _, workflow_id, sequence = evaluate.build_contract(helper, tmp_path)
    contracts = state_root / "contracts"
    stray = contracts / ("f" * 64 + ".json")
    os.symlink(store_path(tmp_path), stray)
    with pytest.raises(helper.RunStateError, match="unexpected entry"):
        helper.read_kept_contract(workflow_id=workflow_id, state_root=state_root)
    stray.unlink()
    (contracts / "notes.txt").write_bytes(b"stray\n")
    os.chmod(contracts / "notes.txt", 0o600)
    with pytest.raises(helper.RunStateError, match="unexpected entry"):
        reuse(helper, state_root, workflow_id, sequence)
    (contracts / "notes.txt").unlink()
    assert reuse(helper, state_root, workflow_id, sequence)["stage"] == "confirmed"


def test_finalize_refuses_a_corrupt_store_before_finalizing(tmp_path: Path) -> None:
    """A store with a foreign entry stops finalization before its receipt."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = verified_build_run(helper, tmp_path)
    (state_root / "contracts" / "foreign").mkdir(mode=0o700)
    with pytest.raises(helper.RunStateError, match="unexpected entry"):
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == (
        "verified"
    )


def test_runs_without_contract_v2_or_a_store_keep_working(tmp_path: Path) -> None:
    """A v1-contract run finalizes without a kept contract, and old roots gain the store."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = support.build_verified_stage(
        helper, tmp_path, selection_v5=True
    )
    shutil.rmtree(state_root / "contracts")
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == (
        "verified"
    )
    assert (state_root / "contracts").is_dir()
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    assert finalized["kept_contract_digest"] is None
    assert list((state_root / "contracts").iterdir()) == []


def test_cli_reads_the_kept_contract_and_switches_mode(tmp_path: Path) -> None:
    """The CLI exposes kept-contract, reuse-contract, and switch-mode."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = advance_to_research(helper, tmp_path, "improve")

    def run_cli(command: str, request: dict[str, object]) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(support.HELPER), command, "--state-root", str(state_root)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    help_text = subprocess.run(
        [sys.executable, str(support.HELPER), "--help"], text=True, capture_output=True
    ).stdout
    for command in ("kept-contract", "reuse-contract", "switch-mode"):
        assert command in help_text
    shown = run_cli("kept-contract", {"workflow_id": workflow_id})
    assert (shown["kept_contract"], shown["reusable"]) == (None, False)
    switched = run_cli(
        "switch-mode",
        {
            "workflow_id": workflow_id,
            "expected_sequence": sequence,
            "mode": "modify",
            "reason": "the request changes the contract",
            "confirmation": SWITCH_CONFIRMATION,
            "authority_event_digest": SWITCH_EVENT_DIGEST,
        },
    )
    assert (switched["mode"], switched["stage"]) == ("modify", "research")


def test_a_reused_confirmation_finalizes_and_keeps_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An improve run confirmed by reuse finalizes, recovers, and keeps the user's confirmation."""
    helper = support.load_helper()
    state_root, _, _, _ = delivered_build_run(helper, tmp_path)
    first = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    used = confirm_by_reuse(monkeypatch, helper)
    _, workflow_id, sequence = verified_build_run(helper, tmp_path)
    assert used["id"].startswith("kept-confirmation-")
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    rekept = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    assert finalized["kept_contract_digest"] == rekept["record_digest"]
    assert (rekept["confirmation"], rekept["confirmation_limitations"]) == (
        first["confirmation"],
        first["confirmation_limitations"],
    )
    assert rekept["source_workflow_id"] == workflow_id
    (state_root / "live" / workflow_id / "current.json").write_text("{broken", encoding="utf-8")
    assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == (
        "finalized"
    )


def test_a_reused_confirmation_reports_an_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An evaluate run confirmed by reuse reaches reported and re-keeps the contract."""
    helper = support.load_helper()
    evaluate.build_reported(helper, tmp_path)
    first = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    used = confirm_by_reuse(monkeypatch, helper)
    _, workflow_id, reported = evaluate.build_reported(helper, tmp_path)
    assert used["id"].startswith("kept-confirmation-")
    rekept = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    assert reported["kept_contract_digest"] == rekept["record_digest"]
    assert rekept["confirmation"] == first["confirmation"]
    assert rekept["source_workflow_id"] == workflow_id


@pytest.mark.parametrize("after_stage", ["research", "design"])
def test_a_switched_run_finalizes_delivers_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_stage: str
) -> None:
    """An improve run switched to modify runs to cleanup and keeps a modify contract."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    switch_inside_builder(monkeypatch, helper, after_stage)
    state_root, workflow_id, sequence = verified_build_run(helper, tmp_path)
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["mode"][
        "name"
    ] == "modify"
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    record = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    assert (record["source_mode"], record["record_digest"]) == (
        "modify",
        finalized["kept_contract_digest"],
    )
    support.write_skill_package(target_path(tmp_path), PACKAGE)
    delivered = modes.record_current_delivery(
        helper, state_root, workflow_id, finalized["sequence"], target_path(tmp_path)
    )
    authorized = evaluate.authorize_cleanup(
        helper, state_root, workflow_id, delivered["sequence"]
    )
    helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert not (state_root / "live" / workflow_id).exists()


@pytest.mark.parametrize(
    ("field", "error"),
    (
        ("skill_file_digest", "other skill files"),
        ("purpose", "differs from the kept contract"),
        ("confirmation_limitations", "limitations differ"),
    ),
)
def test_replay_rejects_a_tampered_reused_confirmation(
    tmp_path: Path, field: str, error: str
) -> None:
    """Replay rechecks the kept record a reused confirmation carries."""
    helper = support.load_helper()
    state_root, _, _ = evaluate.build_reported(helper, tmp_path)
    _, workflow_id, sequence = evaluate.build_contract(helper, tmp_path)
    artifact_id = str(reuse(helper, state_root, workflow_id, sequence)["confirmation_artifact_id"])
    payload = payload_of(state_root, workflow_id, artifact_id)
    record = payload["kept_contract"]
    assert isinstance(record, dict)
    if field == "purpose":
        record["contract"]["purpose"] = "something else"
    else:
        record[field] = "a" * 64 if field == "skill_file_digest" else ["extra limitation"]
    payload["kept_contract_digest"] = redigest(helper, record)["record_digest"]
    support.rewrite_artifact_payload_and_receipts(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        artifact_id=artifact_id,
        payload=payload,
        rewrite_receipts=True,
    )
    with pytest.raises(helper.RunStateError, match=error):
        helper.recover_run(workflow_id=workflow_id, state_root=state_root)


def test_reuse_needs_the_contract_stage_and_the_hermes_limitation(tmp_path: Path) -> None:
    """Reuse confirms only an accepted contract, and a restricted Hermes target needs its limitation."""
    helper = support.load_helper()
    state_root, _, _ = evaluate.build_reported(helper, tmp_path)
    _, workflow_id, sequence = evaluate.build_contract(helper, tmp_path)
    confirmed = reuse(helper, state_root, workflow_id, sequence)
    with pytest.raises(helper.RunStateError, match="only an accepted contract"):
        reuse(helper, state_root, workflow_id, int(confirmed["sequence"]))
    helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=confirmed["sequence"],
        change_kind="target-snapshot",
        changed_artifact_id="resolution",
        reason="start a restricted evaluation",
        state_root=state_root,
    )

    restricted = {"load_policy": support.USER_LOAD_POLICY, "hosts": ["claude-code", "hermes"]}
    record = json.loads(store_path(tmp_path).read_text(encoding="utf-8"))
    record["contract"].update(restricted)
    store_path(tmp_path).write_bytes(helper.canonical_json_bytes(redigest(helper, record)))
    _, workflow_id, sequence = evaluate.build_contract(
        helper, tmp_path, contract_overrides=restricted
    )
    with pytest.raises(helper.RunStateError, match="Hermes load-policy limitation"):
        reuse(helper, state_root, workflow_id, sequence)
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == (
        "contract"
    )


def test_a_v1_kept_record_is_refused(tmp_path: Path) -> None:
    """Only v2 contracts are kept, so a v1 record fails as an invalid store record."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root = tmp_path / "state"
    workflow_id = str(
        modes.initialize_existing(helper, tmp_path, target_path(tmp_path), "improve")[
            "workflow_id"
        ]
    )
    support.mark_run_as_selection_v5(helper, state_root, workflow_id)
    sequence = 0
    for artifact_id, artifact_type, event, stage in PRE_CONTRACT_STAGES[:2]:
        sequence = support.advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )["sequence"]
    sequence = advance_to_contract(helper, state_root, workflow_id, sequence)
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    contract = payload_of(state_root, workflow_id, "contract")
    assert contract["schema_version"] == "skill-builder-contract.v1"
    canonical = current["target_identity"]["canonical"]
    record: dict[str, object] = {
        "schema_version": "skill-builder-kept-contract.v1",
        "target_identity": canonical,
        "skill_file_digest": helper._target_skill_file_digest(current),
        "contract_schema": contract["schema_version"],
        "contract": contract,
        "confirmation_schema": "skill-builder-user-confirmation.v1",
        "confirmation": support.confirmation_payload(
            contract_id="contract",
            contract_digest=current["artifact_index"]["contract"]["digest"],
            target=canonical,
            snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_digest="2" * 64,
        ),
        "confirmation_limitations": [],
        "source_workflow_id": "0" * 32,
        "source_mode": "improve",
        "source_stage": "finalized",
        "source_final_transition_digest": "f" * 64,
        "created_at": "2026-09-05T12:00:00Z",
    }
    store_path(tmp_path).write_bytes(helper.canonical_json_bytes(redigest(helper, record)))
    os.chmod(store_path(tmp_path), 0o600)
    with pytest.raises(helper.RunStateError, match="payload binding is invalid"):
        reuse(helper, state_root, workflow_id, sequence)


def test_a_failed_store_write_leaves_the_run_finalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store write that fails after the final receipt is reported, not raised."""
    helper = support.load_helper()
    modes.existing_target(tmp_path)
    state_root, workflow_id, sequence = verified_build_run(helper, tmp_path)
    atomic_json = helper._atomic_json

    def failing(path: Path, value: dict[str, object]) -> None:
        if path.parent.name == "contracts":
            raise OSError(28, "No space left on device")
        atomic_json(path, value)

    monkeypatch.setattr(helper, "_atomic_json", failing)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    assert (finalized["stage"], finalized["kept_contract_digest"]) == ("finalized", None)
    assert "No space left on device" in str(finalized["kept_contract_error"])
    assert not store_path(tmp_path).exists()
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == (
        "finalized"
    )
