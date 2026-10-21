from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests import test_skill_builder_state as support


TARGET_FILES = {"SKILL.md": support.CANDIDATE_SKILL_BYTES}
EMPTY_DIGEST = support.fixture_digest(b"")
SKIPPED_GATES = ("BR3", "BR10")
REPORT_LIMITATION = "scores cover the frozen cases only"
USER_ONLY_SKILL_BYTES = (
    b"---\nname: sample-skill\ndescription: Build the exact sample skill. "
    b"Only the user can load $sample-skill.\n---\n# Sample skill\n"
)
SELECTION_BACKED = ("TR2", "TR3", "TR4", "TR6", "TE3", "CP5")


def codex_adapter_bytes(implicit: str) -> bytes:
    return (
        b"interface:\n"
        b'  short_description: "Build the exact sample skill"\n'
        b'  default_prompt: "Use $sample-skill to build the exact sample skill."\n'
        b"policy:\n"
        b"  allow_implicit_invocation: " + implicit.encode() + b"\n"
    )


def evaluate_target(tmp_path: Path, files: dict[str, bytes] = TARGET_FILES) -> Path:
    target = tmp_path / "skills" / "sample-skill"
    support.write_skill_package(target, files)
    return target


def initialize(
    helper: ModuleType, tmp_path: Path, mode: str = "evaluate"
) -> dict[str, object]:
    target = tmp_path / "skills" / "sample-skill"
    return helper.initialize_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(target),
        mode=mode,
        authority=support.authority(),
        target_manifest=helper.snapshot_target(target),
        git_identity={"present": False},
        state_root=tmp_path / "state",
    )


def retain(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    artifact_id: str,
    artifact_type: str,
    payload: dict[str, object],
    **options: object,
) -> dict[str, object]:
    return support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        payload=payload,
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
        **options,
    )


def advance(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    event: str,
    destination: str,
    artifact_ids: list[str],
    authority_event_digest: str | None = None,
) -> int:
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        event=event,
        destination_stage=destination,
        artifact_ids=artifact_ids,
        authority_event_digest=authority_event_digest,
        state_root=state_root,
    )["sequence"]


def digest_of(helper: ModuleType, state_root: Path, workflow_id: str, artifact_id: str) -> str:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    return current["artifact_index"][artifact_id]["digest"]


def evaluate_contract(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    baseline_digest = digest_of(helper, state_root, workflow_id, "baseline")
    payload = support.contract_v1_payload(baseline_digest)
    payload.update(
        {
            "schema_version": "skill-builder-contract.v2",
            "design_digest": None,
            "load_policy": support.AGENT_LOAD_POLICY,
            "hosts": ["claude-code"],
            "aliases": ["sample builder"],
            "ambiguity": ["ask which sample before building"],
            "collaboration": ["the user confirms the sample"],
            "evidence_bindings": [
                {
                    "statement": "the baseline records current behavior",
                    "artifact_id": "baseline",
                    "digest": baseline_digest,
                }
            ],
            **(overrides or {}),
        }
    )
    return payload


def build_baseline(
    helper: ModuleType, tmp_path: Path, files: dict[str, bytes] = TARGET_FILES
) -> tuple[Path, str, int]:
    evaluate_target(tmp_path, files)
    started = initialize(helper, tmp_path)
    state_root = tmp_path / "state"
    workflow_id = str(started["workflow_id"])
    sequence = support.advance_with_artifact(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        event="capture-baseline",
        destination_stage="baseline",
    )["sequence"]
    return state_root, workflow_id, sequence


def build_contract(
    helper: ModuleType,
    tmp_path: Path,
    files: dict[str, bytes] = TARGET_FILES,
    contract_overrides: dict[str, object] | None = None,
) -> tuple[Path, str, int]:
    state_root, workflow_id, sequence = build_baseline(helper, tmp_path, files)
    contract = retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "contract",
        "skill-contract",
        evaluate_contract(helper, state_root, workflow_id, contract_overrides),
    )
    sequence = advance(
        helper, state_root, workflow_id, contract["sequence"], "accept-contract", "contract", ["contract"]
    )
    return state_root, workflow_id, sequence


def evaluate_candidate_payload(
    helper: ModuleType,
    tmp_path: Path,
    state_root: Path,
    workflow_id: str,
    files: dict[str, bytes],
) -> dict[str, object]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    payload = support.candidate_payload(
        tmp_path=tmp_path,
        candidate_id="candidate",
        revision="candidate-1",
        contract_digest=current["artifact_index"]["contract"]["digest"],
        confirmation_digest=current["artifact_index"]["confirmation"]["digest"],
        evaluation_digest=current["artifact_index"]["evaluation"]["digest"],
        snapshot_digest=current["target_snapshot"]["snapshot_digest"],
        brief_digest=EMPTY_DIGEST,
        loaded_skill_bytes=files["SKILL.md"],
        additional_skill_files={
            path: content for path, content in files.items() if path != "SKILL.md"
        },
    )
    payload.update(
        {
            "brief_digest": None,
            "checker_version": None,
            "writable_role": None,
            "owned_paths": [],
            "diff_digest": EMPTY_DIGEST,
        }
    )
    return payload


def build_evaluation(
    helper: ModuleType,
    tmp_path: Path,
    files: dict[str, bytes] = TARGET_FILES,
    contract_overrides: dict[str, object] | None = None,
    selection: dict[str, str] = support.SCORED_SELECTION,
) -> tuple[Path, str, int]:
    state_root, workflow_id, sequence = build_contract(
        helper, tmp_path, files, contract_overrides
    )
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    authority_digest = "2" * 64
    confirmation = retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "confirmation",
        "user-confirmation-record",
        support.confirmation_payload(
            contract_id="contract",
            contract_digest=current["artifact_index"]["contract"]["digest"],
            target=current["target_identity"]["canonical"],
            snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            authority_digest=authority_digest,
        ),
    )
    sequence = advance(
        helper,
        state_root,
        workflow_id,
        confirmation["sequence"],
        "confirm-contract",
        "confirmed",
        ["confirmation"],
        authority_digest,
    )
    evaluation = retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "evaluation",
        "evaluation-pack",
        support.evaluation_payload(
            contract_digest=current["artifact_index"]["contract"]["digest"],
            confirmation_digest=confirmation["artifact_digest"],
            snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            cases=support.frozen_cases(selection),
        ),
        retain_review_source_components=True,
    )
    sequence = advance(
        helper, state_root, workflow_id, evaluation["sequence"], "freeze-evaluation", "evaluation", ["evaluation"]
    )
    return state_root, workflow_id, sequence


def retain_candidate(
    helper: ModuleType,
    tmp_path: Path,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    files: dict[str, bytes] = TARGET_FILES,
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = evaluate_candidate_payload(helper, tmp_path, state_root, workflow_id, files)
    payload.update(overrides or {})
    return retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "candidate",
        "candidate-record",
        payload,
        retain_review_source_components=True,
        extra_files={"review-components/empty-diff.bin": b""},
    )


def build_reviewed(
    helper: ModuleType,
    tmp_path: Path,
    files: dict[str, bytes] = TARGET_FILES,
    contract_overrides: dict[str, object] | None = None,
    selection: dict[str, str] = support.SCORED_SELECTION,
) -> tuple[Path, str, int, dict[str, object], dict[str, object], dict[str, object]]:
    state_root, workflow_id, sequence = build_evaluation(
        helper, tmp_path, files, contract_overrides, selection
    )
    candidate = retain_candidate(helper, tmp_path, state_root, workflow_id, sequence, files)
    sequence = advance(
        helper, state_root, workflow_id, candidate["sequence"], "accept-candidate", "candidate", ["candidate"]
    )
    cases = support.frozen_cases(selection)
    trials_payload = support.trial_payload(
        candidate["artifact_digest"], cases=cases, loaded_skill_files=files
    )
    trials = retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "trials",
        "trial-pack",
        trials_payload,
        frozen_trial_cases=cases,
        trial_loaded_skill_files=files,
    )
    sequence = advance(helper, state_root, workflow_id, trials["sequence"], "complete-trials", "trials", ["trials"])
    review = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="final-review",
        artifact_type="review-record",
        payload=support.review_payload(
            candidate["artifact_digest"],
            input_artifacts=support.review_input_provenance(helper, state_root, workflow_id),
        ),
        input_bindings=support.review_envelope_bindings(helper, state_root, workflow_id),
    )
    sequence = advance(
        helper, state_root, workflow_id, review["sequence"], "accept-review", "reviewed", ["final-review"]
    )
    ledger = support.conformance_payload(candidate["artifact_digest"])
    for gate in SKIPPED_GATES:
        ledger["gates"][gate]["status"] = "skip"
    conformance = retain(
        helper, state_root, workflow_id, sequence, "conformance", "builder-run-conformance-ledger", ledger
    )
    return state_root, workflow_id, conformance["sequence"], candidate, trials_payload, review


def retain_scorecard(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    reviewed: tuple[dict[str, object], dict[str, object], dict[str, object]],
    artifact_id: str = "scores",
    failing: tuple[str, ...] = ("CE6",),
) -> dict[str, object]:
    candidate, trials_payload, review = reviewed
    criteria = {
        name: {criterion: criterion not in failing for criterion in support.SCORE_CRITERIA[name]}
        for name in support.SCORE_CATEGORIES
    }
    scorecard = support.scorecard_payload(
        candidate_digest=candidate["artifact_digest"],
        evaluation_digest=digest_of(helper, state_root, workflow_id, "evaluation"),
        review_digest=review["artifact_digest"],
        criteria_overrides=criteria,
        trial_cases=trials_payload["cases"],
    )
    for category in scorecard["categories"]:
        category["score"] = sum(criteria[category["name"]].values())
    return retain(helper, state_root, workflow_id, sequence, artifact_id, "target-scorecard", scorecard)


def build_scored(
    helper: ModuleType, tmp_path: Path
) -> tuple[Path, str, int, dict[str, object]]:
    state_root, workflow_id, sequence, *reviewed = build_reviewed(helper, tmp_path)
    scores = retain_scorecard(helper, state_root, workflow_id, sequence, tuple(reviewed))
    sequence = advance(
        helper, state_root, workflow_id, scores["sequence"], "accept-scores", "scored", ["conformance", "scores"]
    )
    return state_root, workflow_id, sequence, reviewed[0]


def report_payload(
    helper: ModuleType, state_root: Path, workflow_id: str
) -> dict[str, object]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    index = current["artifact_index"]
    return {
        "schema_version": "skill-builder-evaluation-report.v1",
        "target_identity": current["target_identity"]["canonical"],
        "target_snapshot_digest": current["target_snapshot"]["snapshot_digest"],
        "candidate_digest": index["candidate"]["digest"],
        "candidate_revision": "candidate-1",
        "contract_digest": index["contract"]["digest"],
        "confirmation_digest": index["confirmation"]["digest"],
        "evaluation_digest": index["evaluation"]["digest"],
        "review_digest": index["final-review"]["digest"],
        "review_verdict": "ready",
        "conformance_digest": index["conformance"]["digest"],
        "scorecard_digest": index["scores"]["digest"],
        "scores": [
            {
                "name": name,
                "score": 9 if name == "context efficiency" else 10,
                "failing_criteria": ["CE6"] if name == "context efficiency" else [],
            }
            for name in support.SCORE_CATEGORIES
        ],
        "limitations": [REPORT_LIMITATION],
    }


def build_reported(
    helper: ModuleType, tmp_path: Path
) -> tuple[Path, str, dict[str, object]]:
    state_root, workflow_id, sequence, _ = build_scored(helper, tmp_path)
    report = retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "report",
        "evaluation-report",
        report_payload(helper, state_root, workflow_id),
    )
    reported = helper.report_run(
        workflow_id=workflow_id,
        expected_sequence=report["sequence"],
        report_artifact_id="report",
        state_root=state_root,
    )
    return state_root, workflow_id, reported


def lock_path(tmp_path: Path) -> Path:
    canonical = support.target_identity(tmp_path / "skills" / "sample-skill")["canonical"]
    return (
        tmp_path
        / "state"
        / "target-locks"
        / f"{support.fixture_digest(canonical.encode('utf-8'))}.json"
    )


def stage_of(helper: ModuleType, state_root: Path, workflow_id: str) -> tuple[str, int]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    return current["stage"], current["head_sequence"]


def authorize_cleanup(
    helper: ModuleType, state_root: Path, workflow_id: str, sequence: int
) -> dict[str, object]:
    event = b"user authorizes cleanup of the reported evaluation\n"
    return helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        authority_event_digest=support.fixture_digest(event),
        authority_event=event,
        actor="user",
        state_root=state_root,
    )


def test_evaluate_requires_an_existing_target_and_its_manifest(tmp_path: Path) -> None:
    """Evaluate binds an exact existing target and refuses absent-target evidence."""
    helper = support.load_helper()
    missing = tmp_path / "skills" / "missing-skill"
    with pytest.raises(helper.RunStateError, match="existing"):
        helper.initialize_run(
            host_identity=support.host_identity(tmp_path),
            target_identity=support.target_identity(missing, "missing-skill"),
            mode="evaluate",
            authority=support.authority(),
            target_manifest={"claimed": "existing"},
            git_identity={"present": False},
            state_root=tmp_path / "missing-state",
        )
    target = evaluate_target(tmp_path)
    with pytest.raises(helper.RunStateError, match="manifest"):
        helper.initialize_run(
            host_identity=support.host_identity(tmp_path),
            target_identity=support.target_identity(target),
            mode="evaluate",
            authority=support.authority(),
            absence_evidence={"searched": [str(target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=tmp_path / "absent-state",
        )
    assert not (tmp_path / "missing-state").exists()
    assert not (tmp_path / "absent-state").exists()

    started = initialize(helper, tmp_path)
    loaded = helper.load_run(workflow_id=started["workflow_id"], state_root=tmp_path / "state")
    assert loaded["mode"]["name"] == "evaluate"
    assert loaded["target_snapshot"]["manifest"] == helper.snapshot_target(target)


@pytest.mark.parametrize(
    ("event", "destination"),
    (
        ("complete-research", "research"),
        ("sieve-evidence", "sieve"),
        ("accept-design", "design"),
    ),
)
def test_evaluate_cannot_enter_research_sieve_or_design(
    tmp_path: Path, event: str, destination: str
) -> None:
    """The skipped stages stay closed even with an artifact of the right type."""
    helper = support.load_helper()
    state_root, workflow_id, sequence = build_baseline(helper, tmp_path)
    artifact_ids = ["baseline"]
    if event == "complete-research":
        sequence = retain(
            helper,
            state_root,
            workflow_id,
            sequence,
            "research",
            "research-pack",
            support.stage_payload(helper, state_root, workflow_id, "research-pack"),
        )["sequence"]
        artifact_ids = ["research"]
    with pytest.raises(helper.RunStateError, match="evaluate runs skip"):
        advance(helper, state_root, workflow_id, sequence, event, destination, artifact_ids)
    assert stage_of(helper, state_root, workflow_id) == ("baseline", sequence)


def test_evaluate_goes_from_baseline_to_contract(tmp_path: Path) -> None:
    """Evaluate accepts a design-free contract bound to its baseline."""
    helper = support.load_helper()
    state_root, workflow_id, sequence = build_baseline(helper, tmp_path)
    contract = evaluate_contract(helper, state_root, workflow_id)
    with pytest.raises(helper.RunStateError, match="no design"):
        retain(
            helper,
            state_root,
            workflow_id,
            sequence,
            "designed",
            "skill-contract",
            {**contract, "design_digest": "1" * 64},
        )
    retained = retain(helper, state_root, workflow_id, sequence, "contract", "skill-contract", contract)
    sequence = advance(
        helper, state_root, workflow_id, retained["sequence"], "accept-contract", "contract", ["contract"]
    )
    assert stage_of(helper, state_root, workflow_id) == ("contract", sequence)

    rewound = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        change_kind="contract",
        changed_artifact_id="contract",
        reason="the user's answers changed the contract",
        state_root=state_root,
    )
    assert rewound["stage"] == "baseline"
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "baseline"


def test_build_modes_keep_the_design_stage_before_the_contract(tmp_path: Path) -> None:
    """Improve still needs research, sieve, and design before a contract."""
    helper = support.load_helper()
    evaluate_target(tmp_path)
    started = initialize(helper, tmp_path, "improve")
    state_root = tmp_path / "state"
    workflow_id = str(started["workflow_id"])
    sequence = support.advance_with_artifact(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        event="capture-baseline",
        destination_stage="baseline",
    )["sequence"]
    with pytest.raises(helper.RunStateError, match="contract design"):
        retain(
            helper,
            state_root,
            workflow_id,
            sequence,
            "contract",
            "skill-contract",
            evaluate_contract(helper, state_root, workflow_id),
        )
    contract = evaluate_contract(helper, state_root, workflow_id)
    contract["design_digest"] = digest_of(helper, state_root, workflow_id, "baseline")
    retained = retain(helper, state_root, workflow_id, sequence, "contract", "skill-contract", contract)
    with pytest.raises(helper.RunStateError, match="current stage"):
        advance(
            helper, state_root, workflow_id, retained["sequence"], "accept-contract", "contract", ["contract"]
        )
    with pytest.raises(helper.RunStateError, match="only evaluate"):
        retain(
            helper,
            state_root,
            workflow_id,
            retained["sequence"],
            "report",
            "evaluation-report",
            {"schema_version": "skill-builder-evaluation-report.v1"},
        )


@pytest.mark.parametrize(
    ("change", "error"),
    (
        ("changed-skill", "unchanged copy of the target"),
        ("extra-file", "unchanged copy of the target"),
        ("file-mode", "unchanged copy of the target"),
        ("at-target", "overlaps the target"),
        ("writer", "no writer"),
        ("owned-paths", "no writer"),
        ("diff", "no writer"),
        ("brief", "no writer"),
    ),
)
def test_evaluate_rejects_a_candidate_that_is_not_the_unchanged_target(
    tmp_path: Path, change: str, error: str
) -> None:
    """The evaluated candidate is a separate copy of the target's exact entries with no writer."""
    helper = support.load_helper()
    state_root, workflow_id, sequence = build_evaluation(helper, tmp_path)
    files = dict(TARGET_FILES)
    if change == "changed-skill":
        files["SKILL.md"] = support.CANDIDATE_SKILL_BYTES + b"Improved line.\n"
    elif change == "extra-file":
        files["references/extra.md"] = b"# Extra\n"
    overrides: dict[str, object] = {
        "at-target": {"isolated_locator": str((tmp_path / "skills" / "sample-skill").resolve())},
        "writer": {"writable_role": "implementer"},
        "owned-paths": {"owned_paths": ["SKILL.md"]},
        "diff": {"diff_digest": support.fixture_digest(support.CANDIDATE_DIFF_BYTES)},
        "brief": {"brief_digest": "4" * 64},
    }.get(change, {})
    if change in {"changed-skill", "extra-file", "file-mode", "at-target"}:
        candidate = retain_candidate(
            helper, tmp_path, state_root, workflow_id, sequence, files, overrides
        )
        if change == "file-mode":
            (tmp_path / "candidate" / "SKILL.md").chmod(0o755)
        with pytest.raises(helper.RunStateError, match=error):
            advance(
                helper, state_root, workflow_id, candidate["sequence"], "accept-candidate", "candidate", ["candidate"]
            )
        assert stage_of(helper, state_root, workflow_id) == ("evaluation", candidate["sequence"])
    else:
        with pytest.raises(helper.RunStateError, match=error):
            retain_candidate(helper, tmp_path, state_root, workflow_id, sequence, files, overrides)
        assert stage_of(helper, state_root, workflow_id) == ("evaluation", sequence)


def test_evaluate_retains_no_implementer_brief(tmp_path: Path) -> None:
    """No implementer works in an evaluate run, so no brief is accepted."""
    helper = support.load_helper()
    state_root, workflow_id, sequence = build_evaluation(helper, tmp_path)
    with pytest.raises(helper.RunStateError, match="no implementer brief"):
        support.retain_brief(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id="brief",
            contract_digest=digest_of(helper, state_root, workflow_id, "contract"),
            confirmation_digest=digest_of(helper, state_root, workflow_id, "confirmation"),
            evaluation_digest=digest_of(helper, state_root, workflow_id, "evaluation"),
            cases=support.frozen_cases(support.SCORED_SELECTION),
            owned_paths=["SKILL.md"],
        )


def test_evaluate_ledger_records_its_skipped_gates(tmp_path: Path) -> None:
    """BR3 and BR10 are recorded as skips in evaluate and only there."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, candidate = build_scored(helper, tmp_path)
    ledger = support.conformance_payload(candidate["artifact_digest"])
    with pytest.raises(helper.RunStateError, match="conformance gate result"):
        retain(helper, state_root, workflow_id, sequence, "all-pass", "builder-run-conformance-ledger", ledger)
    ledger["gates"]["BR3"]["status"] = "skip"
    ledger["gates"]["BR10"]["status"] = "skip"
    ledger["gates"]["BR5"]["status"] = "skip"
    with pytest.raises(helper.RunStateError, match="conformance gate result"):
        retain(helper, state_root, workflow_id, sequence, "skip-five", "builder-run-conformance-ledger", ledger)
    del ledger["gates"]["BR5"]
    with pytest.raises(helper.RunStateError, match="BR1 to BR10"):
        retain(helper, state_root, workflow_id, sequence, "short", "builder-run-conformance-ledger", ledger)


def test_build_ledger_rejects_skipped_gates(tmp_path: Path) -> None:
    """A create run cannot pass a gate by skipping it."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, candidate = support.build_current_trials_stage(helper, tmp_path)
    ledger = support.conformance_payload(candidate["artifact_digest"])
    ledger["gates"]["BR3"]["status"] = "skip"
    with pytest.raises(helper.RunStateError, match="conformance gate result"):
        retain(helper, state_root, workflow_id, sequence, "conformance", "builder-run-conformance-ledger", ledger)


def test_evaluate_refuses_verification_finalization_and_delivery(tmp_path: Path) -> None:
    """Evaluate changes nothing, so it never verifies for release, finalizes, or delivers."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, candidate = build_scored(helper, tmp_path)
    verification = retain(
        helper,
        state_root,
        workflow_id,
        sequence,
        "verification",
        "verification-record",
        support.verification_payload(candidate["artifact_digest"]),
    )
    with pytest.raises(helper.RunStateError, match="evaluate runs skip"):
        advance(
            helper,
            state_root,
            workflow_id,
            verification["sequence"],
            "accept-verification",
            "verified",
            ["verification"],
        )
    with pytest.raises(helper.RunStateError, match="evaluate runs skip"):
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=verification["sequence"],
            release_artifact_id="verification",
            state_root=state_root,
        )
    assert stage_of(helper, state_root, workflow_id) == ("scored", verification["sequence"])

    state_root, workflow_id, reported = build_reported(helper, tmp_path / "second")
    target = tmp_path / "second" / "skills" / "sample-skill"
    with pytest.raises(helper.RunStateError, match="evaluate runs skip"):
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=reported["sequence"],
            delivery={
                "action": "installation",
                "destination_identity": support.target_identity(target)["canonical"],
                "finalized_revision": "candidate-1",
                "resulting_destination_digest": helper.snapshot_target(target)["manifest_digest"],
                "acceptance_evidence": [support.fixture_digest(b"evidence\n")],
                "user_authority_event_digest": support.fixture_digest(b"authority\n"),
                "actor": "main-agent",
                "accepted": True,
            },
            authority_event_digest=support.fixture_digest(b"authority\n"),
            authority_event=b"authority\n",
            evidence_files={"evidence.bin": b"evidence\n"},
            state_root=state_root,
        )
    with pytest.raises(helper.RunStateError, match="evaluate runs skip"):
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=reported["sequence"],
            release_artifact_id="report",
            state_root=state_root,
        )
    assert stage_of(helper, state_root, workflow_id) == ("reported", reported["sequence"])


def test_report_reaches_reported_and_releases_the_lock(tmp_path: Path) -> None:
    """The report binds the scored evidence, ends the run, and frees the target."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = build_scored(helper, tmp_path)
    assert lock_path(tmp_path).is_file()
    payload = report_payload(helper, state_root, workflow_id)
    for artifact_id, tampered in (
        ("hidden-failure", {**payload, "scores": [{**score, "failing_criteria": []} for score in payload["scores"]]}),
        ("wrong-verdict", {**payload, "review_verdict": "not ready"}),
        ("wrong-ledger", {**payload, "conformance_digest": "5" * 64}),
    ):
        retained = retain(helper, state_root, workflow_id, sequence, artifact_id, "evaluation-report", tampered)
        sequence = retained["sequence"]
        with pytest.raises(helper.RunStateError, match="stale or substituted"):
            helper.report_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                report_artifact_id=artifact_id,
                state_root=state_root,
            )
    report = retain(helper, state_root, workflow_id, sequence, "report", "evaluation-report", payload)
    reported = helper.report_run(
        workflow_id=workflow_id,
        expected_sequence=report["sequence"],
        report_artifact_id="report",
        state_root=state_root,
    )

    assert reported["stage"] == "reported"
    assert reported["report_artifact_digest"] == report["artifact_digest"]
    assert not lock_path(tmp_path).exists()
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert loaded["stage"] == "reported"
    assert loaded["active_target_lock"] is None
    assert loaded["queue"]["states"] == {loaded["target_identity"]["canonical"]: "reported"}
    assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "reported"
    assert not lock_path(tmp_path).exists()
    with pytest.raises(helper.RunStateError, match="immutable"):
        retain(helper, state_root, workflow_id, reported["sequence"], "late", "evaluation-report", payload)
    with pytest.raises(helper.RunStateError, match="terminal"):
        helper.invalidate_run(
            workflow_id=workflow_id,
            expected_sequence=reported["sequence"],
            change_kind="review",
            changed_artifact_id="final-review",
            reason="late objection",
            state_root=state_root,
        )


def test_build_runs_cannot_report(tmp_path: Path) -> None:
    """A create run ends at finalization, never at a report."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = support.build_verified_stage(helper, tmp_path)
    with pytest.raises(helper.RunStateError, match="only evaluate"):
        helper.report_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            report_artifact_id="release",
            state_root=state_root,
        )


def test_new_run_on_the_same_target_starts_after_reported(tmp_path: Path) -> None:
    """Reporting frees the target for the next evaluate or improve run."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = build_scored(helper, tmp_path)
    with pytest.raises(helper.RunStateError, match="unfinished"):
        initialize(helper, tmp_path)
    report = retain(
        helper, state_root, workflow_id, sequence, "report", "evaluation-report", report_payload(helper, state_root, workflow_id)
    )
    helper.report_run(
        workflow_id=workflow_id,
        expected_sequence=report["sequence"],
        report_artifact_id="report",
        state_root=state_root,
    )
    for mode in ("evaluate", "improve"):
        started = initialize(helper, tmp_path, mode)
        assert started["stage"] == "resolved"
        abandoned = helper.invalidate_run(
            workflow_id=started["workflow_id"],
            expected_sequence=0,
            change_kind="mode",
            changed_artifact_id="resolution",
            reason="test cycles through modes",
            state_root=state_root,
        )
        assert abandoned["stage"] == "abandoned"


def test_discovery_with_two_reported_runs(tmp_path: Path) -> None:
    """Reported runs are finished, survive later target edits, and leave discovery to the active run."""
    helper = support.load_helper()
    state_root, first_id, first = build_reported(helper, tmp_path)
    _, second_id, second = build_reported(helper, tmp_path)
    target = tmp_path / "skills" / "sample-skill"
    other_target = tmp_path / "skills" / "other-skill"
    other = helper.initialize_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(other_target, "other-skill"),
        mode="create",
        authority=support.authority(),
        absence_evidence={"searched": [str(other_target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    support.write_skill_package(target, {"SKILL.md": b"# Edited after both reports\n"})

    def discover(path: Path, name: str = "sample-skill") -> dict[str, object]:
        return helper.discover_run(
            host_identity=support.host_identity(tmp_path),
            target_identity=support.target_identity(path, name),
            state_root=state_root,
        )

    assert discover(other_target, "other-skill")["workflow_id"] == other["workflow_id"]
    for workflow_id, reported in ((first_id, first), (second_id, second)):
        loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        assert (loaded["stage"], loaded["head_sequence"]) == ("reported", reported["sequence"])
        assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "reported"
    with pytest.raises(helper.RunStateError, match="ambiguous"):
        discover(target)
    newer = initialize(helper, tmp_path, "improve")
    assert discover(target)["workflow_id"] == newer["workflow_id"]


def test_cleanup_of_a_reported_run_needs_explicit_authority(tmp_path: Path) -> None:
    """Cleanup deletes a reported run only after recorded user authority, leaving a report-bound tombstone."""
    helper = support.load_helper()
    state_root, workflow_id, reported = build_reported(helper, tmp_path)
    run = state_root / "live" / workflow_id
    tombstone_path = state_root / "tombstones" / f"{workflow_id}.json"

    with pytest.raises(helper.RunStateError, match="cleanup-authority-record"):
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=reported["sequence"],
            acknowledge_cleanup=True,
            state_root=state_root,
        )
    assert run.is_dir() and not tombstone_path.exists()

    authorized = authorize_cleanup(helper, state_root, workflow_id, reported["sequence"])
    assert authorized["stage"] == "reported"
    with pytest.raises(helper.RunStateError, match="acknowledgement"):
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=authorized["sequence"],
            acknowledge_cleanup=False,
            state_root=state_root,
        )
    assert run.is_dir() and not tombstone_path.exists()

    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))
    assert cleaned["stage"] == "cleaned"
    assert cleaned["report_artifact_id"] == "report"
    assert not run.exists()
    assert tombstone["schema_version"] == "skill-builder-cleanup-tombstone.v2"
    assert tombstone["evaluation_report_digest"] == reported["report_artifact_digest"]
    assert "accepted_delivery_record_digest" not in tombstone
    assert tombstone["final_transition_receipt_digest"] == authorized["receipt_digest"]
    assert (tmp_path / "skills" / "sample-skill" / "SKILL.md").read_bytes() == TARGET_FILES["SKILL.md"]
    recovered = helper.recover_run(workflow_id=workflow_id, state_root=state_root)
    assert (recovered["stage"], recovered["tombstone_digest"]) == ("cleaned", tombstone["tombstone_digest"])


def test_reported_cleanup_retry_revalidates_the_v2_tombstone(tmp_path: Path) -> None:
    """An interrupted reported cleanup resumes from its report-bound tombstone and manifest."""
    helper = support.load_helper()
    state_root, workflow_id, reported = build_reported(helper, tmp_path)
    authorized = authorize_cleanup(helper, state_root, workflow_id, reported["sequence"])
    original_move = helper._move_to_deleting

    def interrupt(_run: Path, _deleting: Path) -> None:
        raise helper.RunStateError("interrupted before the deletion move")

    helper._move_to_deleting = interrupt
    try:
        with pytest.raises(helper.RunStateError, match="interrupted"):
            helper.cleanup_run(
                workflow_id=workflow_id,
                expected_sequence=authorized["sequence"],
                acknowledge_cleanup=True,
                state_root=state_root,
            )
    finally:
        helper._move_to_deleting = original_move
    run = state_root / "live" / workflow_id
    manifest = json.loads((run / "final-run-manifest.json").read_text(encoding="utf-8"))
    tombstone = json.loads(
        (state_root / "tombstones" / f"{workflow_id}.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == "skill-builder-final-run-manifest.v2"
    assert manifest["report_receipt_digest"] == reported["receipt_digest"]
    assert manifest["evaluation_report_digest"] == reported["report_artifact_digest"]
    assert tombstone["final_run_manifest_digest"] == manifest["manifest_digest"]

    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert (cleaned["stage"], cleaned["tombstone_digest"]) == ("cleaned", tombstone["tombstone_digest"])
    assert not run.exists()


def test_cleanup_authority_waits_for_the_report(tmp_path: Path) -> None:
    """A scored evaluation is unfinished, so cleanup authority cannot attach to it."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = build_scored(helper, tmp_path)
    with pytest.raises(helper.RunStateError, match="reported evaluation"):
        authorize_cleanup(helper, state_root, workflow_id, sequence)
    with pytest.raises(helper.RunStateError, match="reported evaluation"):
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            acknowledge_cleanup=True,
            state_root=state_root,
        )


def test_finished_evaluation_outlives_its_disposable_copy(tmp_path: Path) -> None:
    """Only unfinished work needs the copy, so a reported run survives its removal."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = build_scored(helper, tmp_path)
    copy = tmp_path / "candidate"
    copy.rename(tmp_path / "candidate-aside")
    with pytest.raises(helper.RunStateError, match="isolated copy is missing"):
        helper.load_run(workflow_id=workflow_id, state_root=state_root)
    (tmp_path / "candidate-aside").rename(copy)
    report = retain(
        helper, state_root, workflow_id, sequence, "report", "evaluation-report", report_payload(helper, state_root, workflow_id)
    )
    reported = helper.report_run(
        workflow_id=workflow_id,
        expected_sequence=report["sequence"],
        report_artifact_id="report",
        state_root=state_root,
    )
    shutil.rmtree(copy)
    support.write_skill_package(
        tmp_path / "skills" / "sample-skill", {"SKILL.md": b"# Edited after the report\n"}
    )
    other_target = tmp_path / "skills" / "other-skill"
    other = helper.initialize_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(other_target, "other-skill"),
        mode="create",
        authority=support.authority(),
        absence_evidence={"searched": [str(other_target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    discovered = helper.discover_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(other_target, "other-skill"),
        state_root=state_root,
    )
    assert discovered["workflow_id"] == other["workflow_id"]
    assert stage_of(helper, state_root, workflow_id) == ("reported", reported["sequence"])
    authorized = authorize_cleanup(helper, state_root, workflow_id, reported["sequence"])
    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    assert cleaned["stage"] == "cleaned"


@pytest.mark.parametrize(
    ("skill_bytes", "implicit", "failing"),
    (
        (USER_ONLY_SKILL_BYTES, "false", ()),
        (USER_ONLY_SKILL_BYTES, "true", SELECTION_BACKED),
        (support.CANDIDATE_SKILL_BYTES, "false", ("TR4",)),
    ),
)
def test_evaluate_scores_follow_the_static_load_checks(
    tmp_path: Path, skill_bytes: bytes, implicit: str, failing: tuple[str, ...]
) -> None:
    """A contradicting Codex flag fails TR4 and the selection criteria it stands in for; a missing load sentence fails TR4."""
    helper = support.load_helper()
    files = {"SKILL.md": skill_bytes, "agents/openai.yaml": codex_adapter_bytes(implicit)}
    state_root, workflow_id, sequence, *reviewed = build_reviewed(
        helper,
        tmp_path,
        files,
        {"load_policy": support.USER_LOAD_POLICY, "hosts": ["codex"]},
        {},
    )
    scores = retain_scorecard(
        helper, state_root, workflow_id, sequence, tuple(reviewed), "full-marks", ()
    )
    if failing:
        with pytest.raises(helper.RunStateError, match="contradict the load policy"):
            advance(
                helper, state_root, workflow_id, scores["sequence"], "accept-scores", "scored", ["conformance", "full-marks"]
            )
        scores = retain_scorecard(
            helper, state_root, workflow_id, scores["sequence"], tuple(reviewed), "honest", failing
        )
    sequence = advance(
        helper,
        state_root,
        workflow_id,
        scores["sequence"],
        "accept-scores",
        "scored",
        ["conformance", "honest" if failing else "full-marks"],
    )
    assert stage_of(helper, state_root, workflow_id) == ("scored", sequence)


def test_delivered_cleanup_keeps_the_v1_tombstone(tmp_path: Path) -> None:
    """Build runs still bind the accepted delivery in a v1 tombstone that recovery accepts."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = support.build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivered = support.record_package_delivery(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=finalized["sequence"],
        files={"SKILL.md": support.CANDIDATE_SKILL_BYTES},
    )
    authorized = authorize_cleanup(helper, state_root, workflow_id, delivered["sequence"])
    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    tombstone = json.loads(
        (state_root / "tombstones" / f"{workflow_id}.json").read_text(encoding="utf-8")
    )
    assert cleaned["delivery_artifact_id"] == delivered["delivery_artifact_id"]
    assert tombstone["schema_version"] == "skill-builder-cleanup-tombstone.v1"
    assert tombstone["accepted_delivery_record_digest"] == delivered["delivery_artifact_digest"]
    assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "cleaned"


def test_cli_reports_an_evaluate_run(tmp_path: Path) -> None:
    """The report command is part of the helper's public interface."""
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = build_scored(helper, tmp_path)
    report = retain(
        helper, state_root, workflow_id, sequence, "report", "evaluation-report", report_payload(helper, state_root, workflow_id)
    )
    result = subprocess.run(
        [sys.executable, str(support.HELPER), "report", "--state-root", str(state_root)],
        input=json.dumps(
            {
                "workflow_id": workflow_id,
                "expected_sequence": report["sequence"],
                "report_artifact_id": "report",
            }
        ),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["stage"] == "reported"
