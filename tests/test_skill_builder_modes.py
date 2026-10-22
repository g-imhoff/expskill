from __future__ import annotations

import json
import os
from pathlib import Path
from types import ModuleType

import pytest

from tests import test_skill_builder_state as support


OLD_SKILL_BYTES = b"---\nname: sample-skill\n---\n# Old sample skill\n"
KEPT_REFERENCE_BYTES = b"# Kept reference\n"


def existing_target(tmp_path: Path) -> Path:
    target = tmp_path / "skills" / "sample-skill"
    support.write_skill_package(
        target,
        {"SKILL.md": OLD_SKILL_BYTES, "references/keep.md": KEPT_REFERENCE_BYTES},
    )
    return target


def initialize_existing(
    helper: ModuleType, tmp_path: Path, target: Path, mode: str
) -> dict[str, object]:
    return helper.initialize_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(target),
        mode=mode,
        authority=support.authority(),
        target_manifest=helper.snapshot_target(target),
        git_identity={"present": False},
        state_root=tmp_path / "state",
    )


def record_current_delivery(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    target: Path,
    grant_event: bytes = b"authorize exact package installation\n",
) -> dict[str, object]:
    authority_event = grant_event
    acceptance_evidence = b"exact package destination snapshot\n"
    return helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        delivery={
            "action": "installation",
            "destination_identity": support.target_identity(target)["canonical"],
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": helper.snapshot_target(target)[
                "manifest_digest"
            ],
            "acceptance_evidence": [support.fixture_digest(acceptance_evidence)],
            "user_authority_event_digest": support.fixture_digest(authority_event),
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=support.fixture_digest(authority_event),
        authority_event=authority_event,
        evidence_files={"destination-verification.bin": acceptance_evidence},
        state_root=state_root,
    )


def finalized_modify_run(
    helper: ModuleType, tmp_path: Path
) -> tuple[Path, str, int, Path, dict[str, bytes]]:
    target = existing_target(tmp_path)
    package = {
        "SKILL.md": support.CANDIDATE_SKILL_BYTES,
        "references/keep.md": KEPT_REFERENCE_BYTES,
    }
    state_root, workflow_id, sequence, _ = support.build_verified_stage(
        helper,
        tmp_path,
        mode="modify",
        candidate_additional_skill_files={
            "references/keep.md": KEPT_REFERENCE_BYTES
        },
        candidate_owned_paths=["SKILL.md"],
        candidate_resulting_bytes=support.candidate_result_bytes(),
        trial_loaded_skill_files=package,
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    return state_root, workflow_id, finalized["sequence"], target, package


def test_delivery_accepts_a_post_finalize_grant_naming_the_scope(
    tmp_path: Path,
) -> None:
    """Delivery authority is asked after finalization, so a retained user grant naming the scope extends empty initialize authority."""
    helper = support.load_helper()
    authority = dict(support.authority())
    authority["delivery_effects"] = []
    target = existing_target(tmp_path)
    package = {
        "SKILL.md": support.CANDIDATE_SKILL_BYTES,
        "references/keep.md": KEPT_REFERENCE_BYTES,
    }
    state_root, workflow_id, sequence, _ = support.build_verified_stage(
        helper,
        tmp_path,
        mode="modify",
        granted_authority=authority,
        candidate_additional_skill_files={
            "references/keep.md": KEPT_REFERENCE_BYTES
        },
        candidate_owned_paths=["SKILL.md"],
        candidate_resulting_bytes=support.candidate_result_bytes(),
        trial_loaded_skill_files=package,
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    scope = "installation:codex:personal:sample-skill"
    support.write_skill_package(target, package)
    with pytest.raises(helper.RunStateError, match="authority scope"):
        record_current_delivery(
            helper,
            state_root,
            workflow_id,
            finalized["sequence"],
            target,
            grant_event=b"yes, deliver it\n",
        )
    delivered = record_current_delivery(
        helper,
        state_root,
        workflow_id,
        finalized["sequence"],
        target,
        grant_event=f"yes, deliver {scope} now\n".encode("utf-8"),
    )
    assert delivered["stage"] == "delivered"


def test_modify_initializes_against_the_exact_existing_target(
    tmp_path: Path,
) -> None:
    """Modify binds the same existing-target evidence as improve."""
    helper = support.load_helper()
    target = existing_target(tmp_path)
    manifest = helper.snapshot_target(target)

    started = initialize_existing(helper, tmp_path, target, "modify")
    loaded = helper.load_run(
        workflow_id=started["workflow_id"], state_root=tmp_path / "state"
    )

    assert loaded["mode"] == {
        "name": "modify",
        "evidence": {"target_manifest_digest": manifest["manifest_digest"]},
    }
    assert loaded["target_snapshot"]["exists"] is True
    assert loaded["target_snapshot"]["manifest"] == manifest
    assert loaded["stage"] == "resolved"


def test_modify_requires_an_existing_target_and_its_manifest(
    tmp_path: Path,
) -> None:
    """Modify cannot start from absent-target or caller-claimed evidence."""
    helper = support.load_helper()
    missing = tmp_path / "skills" / "missing-skill"
    with pytest.raises(helper.RunStateError, match="existing"):
        helper.initialize_run(
            host_identity=support.host_identity(tmp_path),
            target_identity=support.target_identity(missing, "missing-skill"),
            mode="modify",
            authority=support.authority(),
            target_manifest={"claimed": "existing"},
            git_identity={"present": False},
            state_root=tmp_path / "missing-state",
        )
    assert not (tmp_path / "missing-state").exists()

    target = existing_target(tmp_path)
    with pytest.raises(helper.RunStateError, match="manifest"):
        helper.initialize_run(
            host_identity=support.host_identity(tmp_path),
            target_identity=support.target_identity(target),
            mode="modify",
            authority=support.authority(),
            absence_evidence={"searched": [str(target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=tmp_path / "absent-state",
        )
    assert not (tmp_path / "absent-state").exists()


@pytest.mark.parametrize("mode", ("redesign", "evaluation", "", None, ["modify"]))
def test_initialize_rejects_unknown_modes(tmp_path: Path, mode: object) -> None:
    """Only the helper's named modes may start a run."""
    helper = support.load_helper()
    target = existing_target(tmp_path)

    with pytest.raises(helper.RunStateError, match="mode"):
        initialize_existing(helper, tmp_path, target, mode)  # type: ignore[arg-type]
    assert not (tmp_path / "state").exists()


def test_modify_baseline_uses_the_existing_target_shape(tmp_path: Path) -> None:
    """A modify baseline binds the snapshot and claims no absent-target proof."""
    helper = support.load_helper()
    target = existing_target(tmp_path)
    started = initialize_existing(helper, tmp_path, target, "modify")
    state_root = tmp_path / "state"
    workflow_id = started["workflow_id"]
    baseline = support.valid_baseline_payload(helper, state_root, workflow_id)
    assert baseline["mode"] == "modify"
    assert baseline["absent_target_proof"] is None

    for artifact_id, payload, error in (
        ("unknown-mode", {**baseline, "mode": "evaluation"}, "mode"),
        (
            "absent-proof",
            {
                **baseline,
                "absent_target_proof": {
                    "absence_evidence_digest": "1" * 64,
                    "overlap_map_digest": "2" * 64,
                },
            },
            "absent-target proof",
        ),
    ):
        with pytest.raises(helper.RunStateError, match=error):
            support.retain_json(
                helper,
                state_root=state_root,
                workflow_id=workflow_id,
                sequence=0,
                artifact_id=artifact_id,
                artifact_type="baseline-report",
                payload=payload,
                input_bindings=support.current_bindings(
                    helper, state_root, workflow_id
                ),
            )

    improve_shaped = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=0,
        artifact_id="improve-baseline",
        artifact_type="baseline-report",
        payload={**baseline, "mode": "improve"},
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    with pytest.raises(helper.RunStateError, match="baseline mode"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=improve_shaped["sequence"],
            event="capture-baseline",
            destination_stage="baseline",
            artifact_ids=["improve-baseline"],
            state_root=state_root,
        )

    retained = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=improve_shaped["sequence"],
        artifact_id="baseline",
        artifact_type="baseline-report",
        payload=baseline,
        input_bindings=[
            binding
            for binding in support.current_bindings(
                helper, state_root, workflow_id
            )
            if binding["artifact_id"] == "resolution"
        ],
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=retained["sequence"],
        event="capture-baseline",
        destination_stage="baseline",
        artifact_ids=["baseline"],
        state_root=state_root,
    )
    assert advanced["stage"] == "baseline"


def test_modify_delivery_inside_owned_paths_is_accepted(tmp_path: Path) -> None:
    """A modify delivery that changes only owned paths keeps the rest intact."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, target, package = finalized_modify_run(
        helper, tmp_path
    )
    support.write_skill_package(target, package)

    delivered = record_current_delivery(
        helper, state_root, workflow_id, sequence, target
    )

    assert delivered["stage"] == "delivered"
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)[
        "stage"
    ] == "delivered"


@pytest.mark.parametrize("change", ("unowned-file-mode", "unowned-directory"))
def test_modify_delivery_outside_owned_paths_is_rejected(
    tmp_path: Path, change: str
) -> None:
    """Modify enforces the same ownership boundary as improve at delivery."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, target, package = finalized_modify_run(
        helper, tmp_path
    )
    support.write_skill_package(target, package)
    if change == "unowned-file-mode":
        os.chmod(target / "references" / "keep.md", 0o600)
    else:
        (target / "unowned").mkdir(mode=0o755)

    with pytest.raises(helper.RunStateError, match="outside candidate ownership"):
        record_current_delivery(helper, state_root, workflow_id, sequence, target)
    current = json.loads(
        (state_root / "live" / workflow_id / "current.json").read_text(
            encoding="utf-8"
        )
    )
    assert (current["stage"], current["head_sequence"]) == ("finalized", sequence)


def initialize_create(
    helper: ModuleType, tmp_path: Path, target: Path, name: str = "sample-skill"
) -> dict[str, object]:
    return helper.initialize_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(target, name),
        mode="create",
        authority=support.authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=tmp_path / "state",
    )


def discover(
    helper: ModuleType, tmp_path: Path, target: Path, name: str = "sample-skill"
) -> dict[str, object]:
    return helper.discover_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(target, name),
        state_root=tmp_path / "state",
    )


def stale_run(
    helper: ModuleType, tmp_path: Path, stale_stage: str
) -> tuple[str, int, Path, str]:
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    if stale_stage == "finalized":
        _, workflow_id, sequence, _ = support.build_verified_stage(helper, tmp_path)
        sequence = helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )["sequence"]
        error = "target changed after its absent snapshot"
    elif stale_stage == "delivered":
        state_root, workflow_id, sequence, target, package = finalized_modify_run(
            helper, tmp_path
        )
        support.write_skill_package(target, package)
        sequence = record_current_delivery(
            helper, state_root, workflow_id, sequence, target
        )["sequence"]
        error = "delivery destination digest does not match the exact target"
    else:
        if stale_stage == "paused-create":
            workflow_id = initialize_create(helper, tmp_path, target)["workflow_id"]
            error = "target changed after its absent snapshot"
        else:
            target = existing_target(tmp_path)
            workflow_id = initialize_existing(
                helper, tmp_path, target, stale_stage.removeprefix("paused-")
            )["workflow_id"]
            error = "target changed after its exact snapshot"
        sequence = helper.pause_run(
            workflow_id=workflow_id, expected_sequence=0, state_root=state_root
        )["sequence"]
    support.write_skill_package(target, {"SKILL.md": b"# Edited outside the run\n"})
    return workflow_id, sequence, target, error


@pytest.mark.parametrize(
    "stale_stage",
    ("finalized", "delivered", "paused-create", "paused-improve", "paused-modify"),
)
def test_stale_run_does_not_block_discovery_of_other_targets(
    tmp_path: Path, stale_stage: str
) -> None:
    """A run whose target changed is reported only for its own target."""
    helper = support.load_helper()
    state_root = tmp_path / "state"
    stale_id, sequence, stale_target, stale_error = stale_run(
        helper, tmp_path, stale_stage
    )

    other_target = tmp_path / "skills" / "other-skill"
    other = initialize_create(helper, tmp_path, other_target, "other-skill")
    discovered = discover(helper, tmp_path, other_target, "other-skill")
    assert discovered["workflow_id"] == other["workflow_id"]
    assert discovered["stage"] == "resolved"

    with pytest.raises(helper.RunStateError, match=stale_error):
        discover(helper, tmp_path, stale_target)
    with pytest.raises(helper.RunStateError, match=stale_error):
        helper.load_run(workflow_id=stale_id, state_root=state_root)
    with pytest.raises(helper.RunStateError, match=stale_error):
        helper.recover_run(workflow_id=stale_id, state_root=state_root)
    if stale_stage.startswith("paused"):
        with pytest.raises(helper.RunStateError, match=stale_error):
            helper.resume_run(
                workflow_id=stale_id,
                expected_sequence=sequence,
                state_root=state_root,
            )


def test_stale_finalized_run_does_not_hide_a_newer_run_on_its_target(
    tmp_path: Path,
) -> None:
    """Discovery prefers the active run and leaves the stale terminal run alone."""
    helper = support.load_helper()
    stale_id, _, target, stale_error = stale_run(helper, tmp_path, "finalized")

    newer = initialize_existing(helper, tmp_path, target, "improve")
    discovered = discover(helper, tmp_path, target)

    assert discovered["workflow_id"] == newer["workflow_id"]
    with pytest.raises(helper.RunStateError, match=stale_error):
        helper.load_run(workflow_id=stale_id, state_root=tmp_path / "state")


def test_tampered_lock_on_another_target_still_fails_discovery(
    tmp_path: Path,
) -> None:
    """Skipping another target's staleness keeps its lock integrity check."""
    helper = support.load_helper()
    state_root = tmp_path / "state"
    tampered_target = tmp_path / "skills" / "sample-skill"
    initialize_create(helper, tmp_path, tampered_target)
    other_target = tmp_path / "skills" / "other-skill"
    initialize_create(helper, tmp_path, other_target, "other-skill")

    canonical = support.target_identity(tampered_target)["canonical"]
    lock_path = (
        state_root
        / "target-locks"
        / f"{support.fixture_digest(canonical.encode('utf-8'))}.json"
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["lock_nonce"] = "0" * 32
    mode = lock_path.stat().st_mode
    os.chmod(lock_path, 0o600)
    lock_path.write_bytes(helper.canonical_json_bytes(lock))
    os.chmod(lock_path, mode)

    for target, name in ((other_target, "other-skill"), (tampered_target, "sample-skill")):
        with pytest.raises(helper.RunStateError, match="lock binding mismatch"):
            discover(helper, tmp_path, target, name)


def test_activate_next_starts_a_modify_run_on_an_existing_target(
    tmp_path: Path,
) -> None:
    """Queue activation accepts modify with the exact existing-target manifest."""
    helper = support.load_helper()
    first_identity = support.target_identity(tmp_path / "skills" / "sample-skill")
    second_target = tmp_path / "skills" / "second"
    support.write_skill_package(second_target, {"SKILL.md": OLD_SKILL_BYTES})
    second_identity = support.target_identity(second_target, "second")
    state_root, workflow_id, sequence, _ = support.build_verified_stage(
        helper, tmp_path, queued_targets=[first_identity, second_identity]
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    manifest = helper.snapshot_target(second_target)

    activated = helper.activate_next_target(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        mode="modify",
        target_manifest=manifest,
        git_identity={"present": False},
        state_root=state_root,
    )
    loaded = helper.load_run(
        workflow_id=activated["workflow_id"], state_root=state_root
    )

    assert loaded["target_identity"] == second_identity
    assert loaded["mode"] == {
        "name": "modify",
        "evidence": {"target_manifest_digest": manifest["manifest_digest"]},
    }
