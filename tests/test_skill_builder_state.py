from __future__ import annotations

import importlib.util
import hashlib
import json
import multiprocessing
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    ROOT
    / "plugins"
    / "codex-dev-flow"
    / "skills"
    / "skill-builder"
    / "scripts"
    / "run_state.py"
)
SCORE_CATEGORIES = (
    "triggering",
    "scope discipline",
    "workflow quality",
    "collaboration",
    "output contract",
    "safety",
    "recovery",
    "composability",
    "context efficiency",
    "testability",
)


def load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("skill_builder_run_state", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initialize_process(
    state_root: str,
    host: dict[str, object],
    target: dict[str, str],
    granted: dict[str, object],
    start: object,
    results: object,
) -> None:
    helper = load_helper()
    start.wait(10)
    try:
        receipt = helper.initialize_run(
            host_identity=host,
            target_identity=target,
            mode="create",
            authority=granted,
            absence_evidence={"searched": [target["locator"]], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=Path(state_root),
        )
        results.put(("success", receipt["workflow_id"]))
    except Exception as error:
        results.put(("error", type(error).__name__, str(error)))


def host_identity(root: Path) -> dict[str, object]:
    return {
        "kind": "codex",
        "canonical_id": "codex:test-host",
        "locator": str(root.resolve()),
        "discovery_evidence": ["explicit test fixture"],
    }


def target_identity(target: Path, name: str = "sample-skill") -> dict[str, str]:
    return {
        "requested": name,
        "canonical": f"codex:personal:{name}",
        "name": name,
        "invocation_token": f"${name}",
        "locator": str(target.resolve()),
    }


def authority() -> dict[str, object]:
    return {
        "reads": ["target", "host-registry"],
        "writes": ["isolated-candidate"],
        "delegation": ["research", "trial", "review", "verification"],
        "candidate_effects": ["isolated-only"],
        "delivery_effects": [],
    }


def git_repository(root: Path) -> tuple[Path, Path, str]:
    repository = root / "repo"
    target = repository / "skills" / "sample-skill"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# Sample\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", "-b", "feature/state", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=tests",
            "-c",
            "user.email=tests@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    return repository, target, head


def retain_json(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    artifact_id: str,
    artifact_type: str,
    payload: dict[str, object],
    input_bindings: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return helper.retain_artifact(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        files={
            "record.json": (
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
        },
        primary_path="record.json",
        producer="main-agent",
        input_bindings=input_bindings or [],
        limitations=[],
        state_root=state_root,
    )


def advance_with_artifact(
    helper: ModuleType,
    *,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    artifact_id: str,
    artifact_type: str,
    event: str,
    destination_stage: str,
    payload: dict[str, object] | None = None,
    authority_event_digest: str | None = None,
) -> dict[str, object]:
    retained = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        payload=payload or {"artifact": artifact_id},
    )
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=retained["sequence"],
        event=event,
        destination_stage=destination_stage,
        artifact_ids=[artifact_id],
        authority_event_digest=authority_event_digest,
        state_root=state_root,
    )


def build_candidate_stage(
    helper: ModuleType, tmp_path: Path
) -> tuple[Path, str, int, dict[str, object]]:
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    workflow_id = started["workflow_id"]
    sequence = 0
    for artifact_id, artifact_type, event, stage in (
        ("baseline", "baseline-report", "capture-baseline", "baseline"),
        ("research", "research-pack", "complete-research", "research"),
        ("sieve", "evidence-sieve", "sieve-evidence", "sieve"),
        ("design", "design-record", "accept-design", "design"),
    ):
        sequence = advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )["sequence"]
    contract = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="contract",
        artifact_type="skill-contract",
        payload={"purpose": "sample"},
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=contract["sequence"],
        event="accept-contract",
        destination_stage="contract",
        artifact_ids=["contract"],
        state_root=state_root,
    )["sequence"]
    authority_digest = "2" * 64
    confirmation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="confirmation",
        artifact_type="user-confirmation-record",
        payload={
            "accepted": True,
            "contract_digest": contract["artifact_digest"],
            "target_identity": target_identity(target)["canonical"],
            "target_snapshot_digest": started["target_snapshot_digest"],
            "confirmation_event_digest": authority_digest,
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=confirmation["sequence"],
        event="confirm-contract",
        destination_stage="confirmed",
        artifact_ids=["confirmation"],
        authority_event_digest=authority_digest,
        state_root=state_root,
    )["sequence"]
    evaluation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="evaluation",
        artifact_type="evaluation-pack",
        payload={
            "frozen": True,
            "contract_digest": contract["artifact_digest"],
            "confirmation_digest": confirmation["artifact_digest"],
            "target_snapshot_digest": started["target_snapshot_digest"],
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=evaluation["sequence"],
        event="freeze-evaluation",
        destination_stage="evaluation",
        artifact_ids=["evaluation"],
        state_root=state_root,
    )["sequence"]
    candidate = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="candidate",
        artifact_type="candidate-record",
        payload={
            "contract_digest": contract["artifact_digest"],
            "confirmation_digest": confirmation["artifact_digest"],
            "evaluation_digest": evaluation["artifact_digest"],
            "target_snapshot_digest": started["target_snapshot_digest"],
            "candidate_revision": "candidate-1",
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=candidate["sequence"],
        event="accept-candidate",
        destination_stage="candidate",
        artifact_ids=["candidate"],
        state_root=state_root,
    )["sequence"]
    return state_root, workflow_id, sequence, candidate


def build_verified_stage(
    helper: ModuleType,
    tmp_path: Path,
    *,
    triggering_score: int = 10,
    review_fresh: bool = True,
    verification_fresh: bool = True,
) -> tuple[Path, str, int, dict[str, object]]:
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    trials = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload={
            "candidate_digest": candidate["artifact_digest"],
            "candidate_revision": "candidate-1",
            "status": "pass",
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )["sequence"]
    review = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="final-review",
        artifact_type="review-record",
        payload={
            "candidate_digest": candidate["artifact_digest"],
            "candidate_revision": "candidate-1",
            "independent": True,
            "read_only": True,
            "valid": True,
            "fresh": review_fresh,
            "verdict": "ready",
            "findings": [],
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=review["sequence"],
        event="accept-review",
        destination_stage="reviewed",
        artifact_ids=["final-review"],
        state_root=state_root,
    )["sequence"]
    conformance = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="conformance",
        artifact_type="builder-run-conformance-ledger",
        payload={
            "candidate_digest": candidate["artifact_digest"],
            "gates": {
                gate: {"status": "pass", "evidence_digests": ["a" * 64]}
                for gate in (
                    "research",
                    "confirmation",
                    "acceptance-first",
                    "isolation",
                    "evidence",
                    "state",
                    "authority",
                    "cleanup",
                )
            },
        },
    )
    scorecard = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=conformance["sequence"],
        artifact_id="scores",
        artifact_type="target-scorecard",
        payload={
            "candidate_digest": candidate["artifact_digest"],
            "candidate_revision": "candidate-1",
            "categories": [
                {
                    "name": name,
                    "score": triggering_score if name == "triggering" else 10,
                    "criteria": {"criterion-1": True},
                }
                for name in SCORE_CATEGORIES
            ],
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=scorecard["sequence"],
        event="accept-scores",
        destination_stage="scored",
        artifact_ids=["conformance", "scores"],
        state_root=state_root,
    )["sequence"]
    verification = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="verification",
        artifact_type="verification-record",
        payload={
            "candidate_digest": candidate["artifact_digest"],
            "candidate_revision": "candidate-1",
            "independent": True,
            "read_only": True,
            "fresh": verification_fresh,
            "status": "pass",
            "commands": [
                {"command": "python3 -m pytest", "exit_status": 0, "output_digest": "b" * 64}
            ],
        },
    )
    sequence = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=verification["sequence"],
        event="accept-verification",
        destination_stage="verified",
        artifact_ids=["verification"],
        state_root=state_root,
    )["sequence"]
    release = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="release",
        artifact_type="release-record",
        payload={
            "candidate_digest": candidate["artifact_digest"],
            "candidate_revision": "candidate-1",
            "contract_digest": helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["contract"]["digest"],
            "confirmation_digest": helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["confirmation"]["digest"],
            "evaluation_digest": helper.load_run(
                workflow_id=workflow_id, state_root=state_root
            )["artifact_index"]["evaluation"]["digest"],
            "conformance_digest": conformance["artifact_digest"],
            "scorecard_digest": scorecard["artifact_digest"],
            "review_digest": review["artifact_digest"],
            "verification_digest": verification["artifact_digest"],
            "retained_limitations": [],
            "authorized_delivery_scope": [],
        },
    )
    return state_root, workflow_id, release["sequence"], release


def test_canonical_digest_excludes_one_top_level_field_and_uses_one_lf() -> None:
    """A serializer regression must not change cross-runtime durable digests."""
    helper = load_helper()
    value = {
        "z": "é",
        "receipt_digest": "not-part-of-the-hash",
        "a": [True, None, 1],
    }

    encoded = helper.canonical_json_bytes(value, "receipt_digest")

    assert encoded == b'{"a":[true,null,1],"z":"\xc3\xa9"}\n'
    assert (
        helper.canonical_digest(value, "receipt_digest")
        == "8f0c5813b5110d117e19fbf2415e73a97e731374b1d90264faa4fee8ab3e7d0e"
    )


def test_initialize_non_git_create_run_is_private_and_bound_to_absence(tmp_path: Path) -> None:
    """Invented Git identity or advisory-only absence evidence could overwrite a target."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"

    receipt = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    loaded = helper.load_run(
        workflow_id=receipt["workflow_id"], state_root=state_root
    )

    assert receipt["sequence"] == 0
    assert receipt["stage"] == "resolved"
    assert loaded["target_identity"] == target_identity(target)
    assert loaded["mode"]["name"] == "create"
    assert loaded["target_snapshot"]["exists"] is False
    assert loaded["git_identity"] == {"present": False}
    assert loaded["head_sequence"] == 0
    assert len(loaded["head_transition_digest"]) == 64
    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_root / "live").stat().st_mode) == 0o700
    assert stat.S_IMODE((state_root / "tombstones").stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (state_root / "live" / receipt["workflow_id"]).stat().st_mode
    ) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (state_root / "live" / receipt["workflow_id"]).rglob("*")
        if path.is_file()
    )
    assert not any(key in loaded["git_identity"] for key in ("repository", "branch", "commit"))


def test_initialize_git_improve_run_binds_exact_target_manifest(tmp_path: Path) -> None:
    """A substituted target or Git revision must not inherit improve evidence."""
    helper = load_helper()
    repository, target, head = git_repository(tmp_path)
    expected_manifest = helper.snapshot_target(target)

    receipt = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="improve",
        authority=authority(),
        target_manifest=expected_manifest,
        state_root=tmp_path / "state",
    )
    loaded = helper.load_run(
        workflow_id=receipt["workflow_id"], state_root=tmp_path / "state"
    )

    assert loaded["mode"]["name"] == "improve"
    assert loaded["target_snapshot"]["exists"] is True
    assert loaded["target_snapshot"]["manifest"] == expected_manifest
    assert loaded["target_snapshot"]["manifest_digest"] == helper.canonical_digest(
        expected_manifest, "manifest_digest"
    )
    assert loaded["git_identity"] == {
        "present": True,
        "repository": str(repository.resolve()),
        "branch": "feature/state",
        "commit": head,
        "dirty_state_digest": hashlib.sha256(b"").hexdigest(),
    }


def test_mode_requires_absent_create_and_existing_improve_target(
    tmp_path: Path,
) -> None:
    """Mode evidence must describe the exact target rather than caller intent alone."""
    helper = load_helper()
    existing = tmp_path / "existing-skill"
    existing.mkdir()
    (existing / "SKILL.md").write_text("# Existing\n", encoding="utf-8")
    create_state = tmp_path / "create-state"
    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(existing),
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(existing)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=create_state,
        )
    except helper.RunStateError as error:
        assert "absent" in str(error)
    else:
        raise AssertionError("create mode accepted an existing exact target")
    assert not create_state.exists()

    missing = tmp_path / "missing-skill"
    improve_state = tmp_path / "improve-state"
    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(missing),
            mode="improve",
            authority=authority(),
            target_manifest={"claimed": "existing"},
            git_identity={"present": False},
            state_root=improve_state,
        )
    except helper.RunStateError as error:
        assert "existing" in str(error)
    else:
        raise AssertionError("improve mode accepted a missing exact target")
    assert not improve_state.exists()


def test_discover_run_uses_complete_canonical_host_and_target_identity(tmp_path: Path) -> None:
    """Name-only discovery could resume evidence belonging to another host target."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    created = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )

    discovered = helper.discover_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        state_root=state_root,
    )

    assert discovered["workflow_id"] == created["workflow_id"]
    assert discovered["sequence"] == 0
    wrong_host = host_identity(tmp_path)
    wrong_host["canonical_id"] = "codex:other-host"
    try:
        helper.discover_run(
            host_identity=wrong_host,
            target_identity=target_identity(target),
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "not found" in str(error)
    else:
        raise AssertionError("discovery accepted a substituted host identity")


def test_retain_artifact_preserves_exact_bounded_bytes_and_immutable_envelope(
    tmp_path: Path,
) -> None:
    """Rewriting payloads or omitting raw files could fabricate later evidence."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    before = helper.load_run(workflow_id=started["workflow_id"], state_root=state_root)

    accepted = helper.retain_artifact(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        files={"prompt.txt": b"line\r\n", "nested/output.json": b"{}\n"},
        primary_path="prompt.txt",
        producer="main-agent",
        input_bindings=[
            {
                "artifact_id": "resolution",
                "digest": before["artifact_index"]["resolution"]["digest"],
            }
        ],
        limitations=[],
        state_root=state_root,
    )
    loaded = helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )
    artifact = state_root / "live" / started["workflow_id"] / "artifacts" / "baseline"
    envelope = json.loads((artifact / "envelope.json").read_text(encoding="utf-8"))
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))

    assert accepted["sequence"] == 1
    assert loaded["head_sequence"] == 1
    assert loaded["artifact_index"]["baseline"]["derived_status"] == "accepted"
    assert (artifact / "raw" / "prompt.txt").read_bytes() == b"line\r\n"
    assert {entry["path"] for entry in manifest["entries"]} == {
        "artifacts/baseline/raw/prompt.txt",
        "artifacts/baseline/raw/nested/output.json",
    }
    assert manifest["observed_item_count"] == 2
    assert manifest["observed_byte_count"] == 9
    assert envelope["manifest_digest"] == helper.canonical_digest(
        manifest, "manifest_digest"
    )
    assert envelope["envelope_digest"] == helper.canonical_digest(
        envelope, "envelope_digest"
    )
    try:
        helper.retain_artifact(
            workflow_id=started["workflow_id"],
            expected_sequence=1,
            artifact_id="baseline",
            artifact_type="baseline-report",
            files={"prompt.txt": b"replacement"},
            primary_path="prompt.txt",
            producer="main-agent",
            input_bindings=[],
            limitations=[],
            state_root=state_root,
        )
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("an immutable artifact was overwritten")
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 1


def test_artifact_bounds_and_escaping_paths_fail_without_partial_state(
    tmp_path: Path,
) -> None:
    """Oversized or escaping collections must not leave artifacts or receipts behind."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    cases = (
        (
            "too-many",
            {f"item-{index:03d}": b"x" for index in range(helper.MAX_ARTIFACT_ITEMS + 1)},
            "item-000",
        ),
        (
            "too-large",
            {"payload.bin": b"x" * (helper.MAX_ARTIFACT_BYTES + 1)},
            "payload.bin",
        ),
        ("escape", {"../outside.txt": b"x"}, "../outside.txt"),
    )

    for artifact_id, files, primary_path in cases:
        try:
            helper.retain_artifact(
                workflow_id=started["workflow_id"],
                expected_sequence=0,
                artifact_id=artifact_id,
                artifact_type="baseline-report",
                files=files,
                primary_path=primary_path,
                producer="main-agent",
                input_bindings=[],
                limitations=[],
                state_root=state_root,
            )
        except helper.RunStateError:
            pass
        else:
            raise AssertionError(f"unsafe artifact collection {artifact_id} was accepted")

    run = state_root / "live" / started["workflow_id"]
    assert {path.name for path in (run / "artifacts").iterdir()} == {"resolution"}
    assert {path.name for path in (run / "receipts").iterdir()} == {"00000000.json"}
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 0
    assert not (tmp_path / "outside.txt").exists()


def test_transition_chain_is_contiguous_and_recovers_replaceable_index(
    tmp_path: Path,
) -> None:
    """A damaged current pointer must not erase or invent immutable run history."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    retained = helper.retain_artifact(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        files={"baseline.json": b'{"absence":true}\n'},
        primary_path="baseline.json",
        producer="main-agent",
        input_bindings=[],
        limitations=[],
        state_root=state_root,
    )
    advanced = helper.transition_run(
        workflow_id=started["workflow_id"],
        expected_sequence=retained["sequence"],
        event="capture-baseline",
        destination_stage="baseline",
        artifact_ids=["baseline"],
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    receipt_zero = json.loads((run / "receipts" / "00000000.json").read_text())
    receipt_one = json.loads((run / "receipts" / "00000001.json").read_text())
    receipt_two = json.loads((run / "receipts" / "00000002.json").read_text())

    assert advanced["sequence"] == 2
    assert receipt_one["prior_receipt_digest"] == receipt_zero["receipt_digest"]
    assert receipt_two["prior_receipt_digest"] == receipt_one["receipt_digest"]
    assert receipt_two["source_stage"] == "resolved"
    assert receipt_two["destination_stage"] == "baseline"
    (run / "current.json").write_text("{broken", encoding="utf-8")
    recovered = helper.recover_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )

    assert recovered["sequence"] == 2
    assert recovered["stage"] == "baseline"
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_transition_digest"] == receipt_two["receipt_digest"]


def test_payload_tampering_and_receipt_gaps_fail_closed(tmp_path: Path) -> None:
    """Neither a stale index nor retained hashes may bridge broken immutable evidence."""
    helper = load_helper()
    for case in ("payload", "gap"):
        case_root = tmp_path / case
        state_root = case_root / "state"
        target = case_root / "skills" / "sample-skill"
        started = helper.initialize_run(
            host_identity=host_identity(case_root),
            target_identity=target_identity(target),
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            state_root=state_root,
        )
        retain_json(
            helper,
            state_root=state_root,
            workflow_id=started["workflow_id"],
            sequence=0,
            artifact_id="baseline",
            artifact_type="baseline-report",
            payload={"baseline": "exact"},
        )
        run = state_root / "live" / started["workflow_id"]
        if case == "payload":
            payload = run / "artifacts" / "baseline" / "raw" / "record.json"
            payload.write_bytes(b'{"baseline":"forged"}\n')
        else:
            (run / "receipts" / "00000001.json").rename(
                run / "receipts" / "00000002.json"
            )
        (run / "current.json").write_text("{broken", encoding="utf-8")

        try:
            helper.recover_run(
                workflow_id=started["workflow_id"], state_root=state_root
            )
        except helper.RunStateError as error:
            assert any(
                word in str(error)
                for word in ("digest", "payload", "gap", "fork", "chain")
            )
        else:
            raise AssertionError(f"recovery accepted {case} tampering")


def test_pause_and_resume_are_validated_cas_transitions(tmp_path: Path) -> None:
    """Pause labels or stale resumes must not bypass the immutable transition chain."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )

    paused = helper.pause_run(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        state_root=state_root,
    )
    assert paused["sequence"] == 1
    assert paused["stage"] == "paused"
    try:
        helper.transition_run(
            workflow_id=started["workflow_id"],
            expected_sequence=1,
            event="capture-baseline",
            destination_stage="baseline",
            artifact_ids=["resolution"],
            state_root=state_root,
        )
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("paused run advanced through an ordinary transition")
    resumed = helper.resume_run(
        workflow_id=started["workflow_id"],
        expected_sequence=1,
        state_root=state_root,
    )
    assert resumed["sequence"] == 2
    assert resumed["stage"] == "resolved"
    try:
        helper.resume_run(
            workflow_id=started["workflow_id"],
            expected_sequence=1,
            state_root=state_root,
        )
    except helper.RevisionConflict:
        pass
    else:
        raise AssertionError("stale resume appended a receipt")
    assert helper.load_run(
        workflow_id=started["workflow_id"], state_root=state_root
    )["head_sequence"] == 2


def test_target_change_blocks_mutation_without_appending_a_receipt(tmp_path: Path) -> None:
    """A recomputed snapshot mismatch must fail before any lifecycle state advances."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    current_before = (run / "current.json").read_bytes()
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# Unexpected\n", encoding="utf-8")

    try:
        helper.pause_run(
            workflow_id=started["workflow_id"],
            expected_sequence=0,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "target changed" in str(error)
    else:
        raise AssertionError("a changed target advanced the lifecycle chain")
    assert {path.name for path in (run / "receipts").iterdir()} == {"00000000.json"}
    assert (run / "current.json").read_bytes() == current_before


def test_candidate_entry_requires_four_current_matching_bindings(tmp_path: Path) -> None:
    """A candidate must not start from stale intent, evaluation, or target evidence."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    workflow_id = started["workflow_id"]
    sequence = 0
    for artifact_id, artifact_type, event, stage in (
        ("baseline", "baseline-report", "capture-baseline", "baseline"),
        ("research", "research-pack", "complete-research", "research"),
        ("sieve", "evidence-sieve", "sieve-evidence", "sieve"),
        ("design", "design-record", "accept-design", "design"),
    ):
        advanced = advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )
        sequence = advanced["sequence"]

    contract = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="contract",
        artifact_type="skill-contract",
        payload={"purpose": "build the exact sample skill"},
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=contract["sequence"],
        event="accept-contract",
        destination_stage="contract",
        artifact_ids=["contract"],
        state_root=state_root,
    )
    confirmation_event = "1" * 64
    confirmation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=advanced["sequence"],
        artifact_id="confirmation",
        artifact_type="user-confirmation-record",
        payload={
            "accepted": True,
            "contract_digest": contract["artifact_digest"],
            "target_identity": target_identity(target)["canonical"],
            "target_snapshot_digest": started["target_snapshot_digest"],
            "confirmation_event_digest": confirmation_event,
        },
    )
    confirmed = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=confirmation["sequence"],
        event="confirm-contract",
        destination_stage="confirmed",
        artifact_ids=["confirmation"],
        authority_event_digest=confirmation_event,
        state_root=state_root,
    )
    evaluation = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=confirmed["sequence"],
        artifact_id="evaluation",
        artifact_type="evaluation-pack",
        payload={
            "frozen": True,
            "contract_digest": contract["artifact_digest"],
            "confirmation_digest": confirmation["artifact_digest"],
            "target_snapshot_digest": started["target_snapshot_digest"],
        },
    )
    evaluated = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=evaluation["sequence"],
        event="freeze-evaluation",
        destination_stage="evaluation",
        artifact_ids=["evaluation"],
        state_root=state_root,
    )
    valid_bindings = {
        "contract_digest": contract["artifact_digest"],
        "confirmation_digest": confirmation["artifact_digest"],
        "evaluation_digest": evaluation["artifact_digest"],
        "target_snapshot_digest": started["target_snapshot_digest"],
        "candidate_revision": "candidate-1",
    }
    sequence = evaluated["sequence"]
    for field in (
        "contract_digest",
        "confirmation_digest",
        "evaluation_digest",
        "target_snapshot_digest",
    ):
        payload = dict(valid_bindings)
        payload[field] = "f" * 64
        artifact_id = f"candidate-bad-{field.removesuffix('_digest')}"
        bad_candidate = retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type="candidate-record",
            payload=payload,
        )
        sequence = bad_candidate["sequence"]
        try:
            helper.transition_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                event="accept-candidate",
                destination_stage="candidate",
                artifact_ids=[artifact_id],
                state_root=state_root,
            )
        except helper.RunStateError:
            pass
        else:
            raise AssertionError(f"candidate entry accepted a mismatched {field}")
        assert helper.load_run(
            workflow_id=workflow_id, state_root=state_root
        )["head_sequence"] == sequence

    good_candidate = retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="candidate-good",
        artifact_type="candidate-record",
        payload={
            "contract_digest": contract["artifact_digest"],
            "confirmation_digest": confirmation["artifact_digest"],
            "evaluation_digest": evaluation["artifact_digest"],
            "target_snapshot_digest": started["target_snapshot_digest"],
            "candidate_revision": "candidate-2",
        },
    )
    accepted = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=good_candidate["sequence"],
        event="accept-candidate",
        destination_stage="candidate",
        artifact_ids=["candidate-good"],
        state_root=state_root,
    )
    assert accepted["stage"] == "candidate"


def test_candidate_change_appends_downstream_invalidation_without_rewrite(
    tmp_path: Path,
) -> None:
    """Candidate edits must not leave stale trials, review, scores, or release usable."""
    helper = load_helper()
    state_root, workflow_id, sequence, candidate = build_candidate_stage(
        helper, tmp_path
    )
    downstream = (
        ("trials-old", "trial-pack"),
        ("review-old", "review-record"),
        ("conformance-old", "builder-run-conformance-ledger"),
        ("scores-old", "target-scorecard"),
        ("verification-old", "verification-record"),
        ("release-old", "release-record"),
    )
    for artifact_id, artifact_type in downstream:
        retained = retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            payload={"candidate_digest": candidate["artifact_digest"]},
        )
        sequence = retained["sequence"]
    candidate_envelope = (
        state_root
        / "live"
        / workflow_id
        / "artifacts"
        / "candidate"
        / "envelope.json"
    )
    immutable_before = candidate_envelope.read_bytes()

    invalidated = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        change_kind="candidate",
        changed_artifact_id="candidate",
        reason="candidate content changed",
        state_root=state_root,
    )
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)

    assert invalidated["stage"] == "evaluation"
    assert candidate_envelope.read_bytes() == immutable_before
    assert loaded["artifact_index"]["candidate"]["derived_status"] == "superseded"
    for artifact_id, _ in downstream:
        assert loaded["artifact_index"][artifact_id]["derived_status"] == "invalidated"


def test_finalization_rejects_false_all_ten_scorecard_without_transition(
    tmp_path: Path,
) -> None:
    """A score label cannot hide a category below the required independent 10."""
    helper = load_helper()
    state_root, workflow_id, sequence, release = build_verified_stage(
        helper, tmp_path, triggering_score=9
    )

    try:
        helper.finalize_run(
            workflow_id=workflow_id,
            expected_sequence=sequence,
            release_artifact_id="release",
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "score" in str(error)
    else:
        raise AssertionError("finalization accepted a category below 10")
    loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    assert loaded["head_sequence"] == sequence
    assert loaded["stage"] == "verified"
    assert (state_root / "live" / workflow_id).is_dir()


def test_finalization_rejects_stale_review_and_verification(tmp_path: Path) -> None:
    """A release cannot reuse an unfresh review or verification for its revision."""
    helper = load_helper()
    cases = (
        ("review", {"review_fresh": False}),
        ("verification", {"verification_fresh": False}),
    )
    for label, options in cases:
        state_root, workflow_id, sequence, _ = build_verified_stage(
            helper, tmp_path / label, **options
        )
        try:
            helper.finalize_run(
                workflow_id=workflow_id,
                expected_sequence=sequence,
                release_artifact_id="release",
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert label in str(error)
        else:
            raise AssertionError(f"finalization accepted stale {label} evidence")
        loaded = helper.load_run(workflow_id=workflow_id, state_root=state_root)
        assert loaded["head_sequence"] == sequence
        assert loaded["stage"] == "verified"


def test_finalize_delivery_and_authorized_cleanup_leave_durable_tombstone(
    tmp_path: Path,
) -> None:
    """Cleanup must never precede exact-revision delivery evidence and durable authority."""
    helper = load_helper()
    state_root, workflow_id, sequence, release = build_verified_stage(
        helper, tmp_path
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    assert finalized["stage"] == "finalized"
    assert run.is_dir()
    candidate_revision = "candidate-1"
    delivery_authority = "3" * 64
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": candidate_revision,
            "resulting_destination_digest": "c" * 64,
            "acceptance_evidence": ["d" * 64],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        state_root=state_root,
    )
    cleanup_authority = "4" * 64
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest=cleanup_authority,
        actor="user",
        state_root=state_root,
    )
    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=authorized["sequence"],
            acknowledge_cleanup=False,
            state_root=state_root,
        )
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("cleanup proceeded without explicit CLI-style acknowledgement")
    assert run.is_dir()
    assert not (state_root / "tombstones" / f"{workflow_id}.json").exists()

    cleaned = helper.cleanup_run(
        workflow_id=workflow_id,
        expected_sequence=authorized["sequence"],
        acknowledge_cleanup=True,
        state_root=state_root,
    )
    tombstone_path = state_root / "tombstones" / f"{workflow_id}.json"
    tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))

    assert cleaned["stage"] == "cleaned"
    assert not run.exists()
    assert tombstone_path.is_file()
    assert stat.S_IMODE(tombstone_path.stat().st_mode) == 0o600
    assert tombstone["workflow_id"] == workflow_id
    assert tombstone["final_transition_receipt_digest"] == authorized["receipt_digest"]
    assert tombstone["cleanup_authority_event_digest"] == cleanup_authority
    assert tombstone["tombstone_digest"] == helper.canonical_digest(
        tombstone, "tombstone_digest"
    )
    recovered = helper.recover_run(workflow_id=workflow_id, state_root=state_root)
    assert recovered["stage"] == "cleaned"
    assert recovered["tombstone_digest"] == tombstone["tombstone_digest"]


def test_cleanup_rejects_missing_accepted_delivery_or_authority(
    tmp_path: Path,
) -> None:
    """Acknowledgement alone must not authorize deletion of retained live evidence."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    authority_digest = "5" * 64
    rejected_delivery = {
        "action": "installation",
        "destination_identity": "codex:personal:sample-skill",
        "finalized_revision": "candidate-1",
        "resulting_destination_digest": "c" * 64,
        "acceptance_evidence": ["d" * 64],
        "user_authority_event_digest": authority_digest,
        "actor": "main-agent",
        "accepted": False,
    }
    try:
        helper.record_delivery(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            delivery=rejected_delivery,
            authority_event_digest=authority_digest,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "accepted" in str(error)
    else:
        raise AssertionError("an unaccepted delivery record advanced the run")

    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=finalized["sequence"],
            acknowledge_cleanup=True,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "delivery" in str(error)
    else:
        raise AssertionError("cleanup proceeded before accepted delivery")

    accepted_delivery = dict(rejected_delivery)
    accepted_delivery["accepted"] = True
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery=accepted_delivery,
        authority_event_digest=authority_digest,
        state_root=state_root,
    )
    try:
        helper.cleanup_run(
            workflow_id=workflow_id,
            expected_sequence=delivery["sequence"],
            acknowledge_cleanup=True,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "authority" in str(error)
    else:
        raise AssertionError("cleanup proceeded without recorded user authority")
    assert (state_root / "live" / workflow_id).is_dir()
    assert not (state_root / "tombstones" / f"{workflow_id}.json").exists()


def test_cleanup_tombstone_publication_failure_deletes_nothing(
    tmp_path: Path,
) -> None:
    """The exact live boundary must survive when its parent tombstone cannot publish."""
    helper = load_helper()
    state_root, workflow_id, sequence, _ = build_verified_stage(helper, tmp_path)
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    delivery_authority = "6" * 64
    delivery = helper.record_delivery(
        workflow_id=workflow_id,
        expected_sequence=finalized["sequence"],
        delivery={
            "action": "installation",
            "destination_identity": "codex:personal:sample-skill",
            "finalized_revision": "candidate-1",
            "resulting_destination_digest": "c" * 64,
            "acceptance_evidence": ["d" * 64],
            "user_authority_event_digest": delivery_authority,
            "actor": "main-agent",
            "accepted": True,
        },
        authority_event_digest=delivery_authority,
        state_root=state_root,
    )
    authorized = helper.record_cleanup_authority(
        workflow_id=workflow_id,
        expected_sequence=delivery["sequence"],
        authority_event_digest="7" * 64,
        actor="user",
        state_root=state_root,
    )
    run = state_root / "live" / workflow_id
    before = {
        path.relative_to(run).as_posix(): path.read_bytes()
        for path in run.rglob("*")
        if path.is_file()
    }
    original_exclusive_json = helper._exclusive_json

    def fail_tombstone(path: Path, value: dict[str, object]) -> None:
        if path.parent == state_root / "tombstones":
            raise helper.RunStateError("injected tombstone publication failure")
        original_exclusive_json(path, value)

    helper._exclusive_json = fail_tombstone
    try:
        try:
            helper.cleanup_run(
                workflow_id=workflow_id,
                expected_sequence=authorized["sequence"],
                acknowledge_cleanup=True,
                state_root=state_root,
            )
        except helper.RunStateError as error:
            assert "tombstone" in str(error)
        else:
            raise AssertionError("cleanup deleted state after tombstone publication failed")
    finally:
        helper._exclusive_json = original_exclusive_json

    assert run.is_dir()
    for relative, payload in before.items():
        assert (run / relative).read_bytes() == payload
    assert not (state_root / "tombstones" / f"{workflow_id}.json").exists()


def test_cli_help_strict_json_xdg_default_and_success_receipts(tmp_path: Path) -> None:
    """An undocumented or permissive CLI would force callers to edit durable state."""
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg")

    help_result = subprocess.run(
        [sys.executable, str(HELPER), "--help"],
        text=True,
        capture_output=True,
        env=env,
    )
    assert help_result.returncode == 0
    for command in (
        "initialize",
        "discover",
        "load",
        "retain",
        "transition",
        "invalidate",
        "pause",
        "resume",
        "recover",
        "finalize",
        "deliver",
        "cleanup-authority",
        "cleanup",
    ):
        assert command in help_result.stdout
    target = tmp_path / "skills" / "sample-skill"
    initialize_payload = {
        "host_identity": host_identity(tmp_path),
        "target_identity": target_identity(target),
        "mode": "create",
        "authority": authority(),
        "absence_evidence": {"searched": [str(target)], "exists": False},
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }
    initialized = subprocess.run(
        [sys.executable, str(HELPER), "initialize"],
        input=json.dumps(initialize_payload),
        text=True,
        capture_output=True,
        env=env,
    )
    assert initialized.returncode == 0, initialized.stderr
    receipt = json.loads(initialized.stdout)
    assert receipt["operation"] == "initialize"
    assert receipt["sequence"] == 0
    default_root = tmp_path / "xdg" / "codex-dev-flow" / "skill-builder"
    assert (default_root / "live" / receipt["workflow_id"]).is_dir()
    before = {
        path.relative_to(default_root).as_posix(): path.read_bytes()
        for path in default_root.rglob("*")
        if path.is_file()
    }
    malformed = subprocess.run(
        [sys.executable, str(HELPER), "load"],
        input='{ "workflow_id": "first", "workflow_id": "second" }',
        text=True,
        capture_output=True,
        env=env,
    )
    assert malformed.returncode != 0
    assert malformed.stderr.strip()
    after = {
        path.relative_to(default_root).as_posix(): path.read_bytes()
        for path in default_root.rglob("*")
        if path.is_file()
    }
    assert after == before
    loaded = subprocess.run(
        [sys.executable, str(HELPER), "load"],
        input=json.dumps({"workflow_id": receipt["workflow_id"]}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert loaded.returncode == 0, loaded.stderr
    assert json.loads(loaded.stdout)["head_transition_digest"] == receipt["receipt_digest"]
    bad_argument = subprocess.run(
        [sys.executable, str(HELPER), "initialize", "--unknown"],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
    )
    assert bad_argument.returncode != 0


def test_state_root_rejects_weak_permissions_symlinks_and_relative_escape(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Trusted state boundaries must fail closed instead of repairing unsafe paths."""
    helper = load_helper()
    target = tmp_path / "skills" / "sample-skill"
    arguments = {
        "host_identity": host_identity(tmp_path),
        "target_identity": target_identity(target),
        "mode": "create",
        "authority": authority(),
        "absence_evidence": {"searched": [str(target)], "exists": False},
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }
    weak = tmp_path / "weak-state"
    weak.mkdir(mode=0o755)
    os.chmod(weak, 0o755)
    try:
        helper.initialize_run(state_root=weak, **arguments)
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("weak existing state root was silently accepted")
    assert stat.S_IMODE(weak.stat().st_mode) == 0o755
    assert list(weak.iterdir()) == []

    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked-state"
    linked.symlink_to(outside, target_is_directory=True)
    try:
        helper.initialize_run(state_root=linked, **arguments)
    except helper.RunStateError:
        pass
    else:
        raise AssertionError("symlinked state root was followed")
    assert list(outside.iterdir()) == []

    original = Path.cwd()
    os.chdir(tmp_path)
    try:
        try:
            helper.initialize_run(state_root=Path("../escape"), **arguments)
        except helper.RunStateError:
            pass
        else:
            raise AssertionError("relative escaping state root was accepted")
    finally:
        os.chdir(original)


def test_state_root_must_be_outside_target_and_target_repository(
    tmp_path: Path,
) -> None:
    """Helper state inside a target can falsify snapshots or be deleted with the target."""
    helper = load_helper()
    absent_target = tmp_path / "absent-skill"
    nested_state = absent_target / ".state"
    create_arguments = {
        "host_identity": host_identity(tmp_path),
        "target_identity": target_identity(absent_target),
        "mode": "create",
        "authority": authority(),
        "absence_evidence": {
            "searched": [str(absent_target)],
            "exists": False,
        },
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }

    try:
        helper.initialize_run(state_root=nested_state, **create_arguments)
    except helper.RunStateError as error:
        assert "state root" in str(error) or "target" in str(error)
    else:
        raise AssertionError("state root inside an absent target was accepted")
    assert not absent_target.exists()

    repository, existing_target, _ = git_repository(tmp_path / "git-case")
    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=target_identity(existing_target),
            mode="improve",
            authority=authority(),
            target_manifest=helper.snapshot_target(existing_target),
            state_root=repository / ".helper-state",
        )
    except helper.RunStateError as error:
        assert "state root" in str(error) or "repository" in str(error)
    else:
        raise AssertionError("state root inside the target repository was accepted")
    assert not (repository / ".helper-state").exists()


def test_recovery_rejects_rehashed_unsupported_manifest_schema(tmp_path: Path) -> None:
    """Digest recomputation must not turn an unsupported artifact schema into truth."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    retained = retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        payload={"baseline": "exact"},
    )
    run = state_root / "live" / started["workflow_id"]
    artifact = run / "artifacts" / "baseline"
    manifest_path = artifact / "manifest.json"
    envelope_path = artifact / "envelope.json"
    receipt_path = run / "receipts" / "00000001.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["unsupported_forgery"] = True
    manifest["manifest_digest"] = helper.canonical_digest(manifest, "manifest_digest")
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))
    envelope = json.loads(envelope_path.read_text())
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(envelope, "envelope_digest")
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))
    receipt = json.loads(receipt_path.read_text())
    receipt["relevant_artifact_digests"][0]["envelope_digest"] = envelope[
        "envelope_digest"
    ]
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "manifest" in str(error)
    else:
        raise AssertionError("recovery accepted a rehashed unsupported manifest field")
    assert retained["sequence"] == 1


def test_queue_has_one_active_target_and_unfinished_work_blocks_next(
    tmp_path: Path,
) -> None:
    """A pending queued target must not become active while its predecessor is unfinished."""
    helper = load_helper()
    state_root = tmp_path / "state"
    first_target = tmp_path / "skills" / "first"
    second_target = tmp_path / "skills" / "second"
    first_identity = target_identity(first_target, "first")
    second_identity = target_identity(second_target, "second")
    first = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=first_identity,
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(first_target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        queue=[first_identity, second_identity],
        state_root=state_root,
    )
    loaded = helper.load_run(workflow_id=first["workflow_id"], state_root=state_root)
    assert loaded["queue"] == {
        "order": [first_identity["canonical"], second_identity["canonical"]],
        "states": {
            first_identity["canonical"]: "active",
            second_identity["canonical"]: "pending",
        },
    }

    try:
        helper.initialize_run(
            host_identity=host_identity(tmp_path),
            target_identity=second_identity,
            mode="create",
            authority=authority(),
            absence_evidence={"searched": [str(second_target)], "exists": False},
            overlap_map={"exact": [], "near_neighbours": []},
            git_identity={"present": False},
            queue=[first_identity, second_identity],
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "unfinished" in str(error) or "active" in str(error)
    else:
        raise AssertionError("pending target acquired an active lock")
    assert len(list((state_root / "live").iterdir())) == 1


def test_load_rejects_weak_nested_raw_artifact_directory(tmp_path: Path) -> None:
    """Every trusted directory boundary, not only the collection root, must stay private."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    helper.retain_artifact(
        workflow_id=started["workflow_id"],
        expected_sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        files={"nested/evidence.txt": b"evidence\n"},
        primary_path="nested/evidence.txt",
        producer="main-agent",
        input_bindings=[],
        limitations=[],
        state_root=state_root,
    )
    nested = (
        state_root
        / "live"
        / started["workflow_id"]
        / "artifacts"
        / "baseline"
        / "raw"
        / "nested"
    )
    os.chmod(nested, 0o755)

    try:
        helper.load_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "0700" in str(error) or "private" in str(error)
    else:
        raise AssertionError("weak nested raw directory was trusted")


def test_recovery_rejects_rehashed_artifact_target_substitution(tmp_path: Path) -> None:
    """Artifact hashes cannot authorize moving evidence to another canonical target."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    retain_json(
        helper,
        state_root=state_root,
        workflow_id=started["workflow_id"],
        sequence=0,
        artifact_id="baseline",
        artifact_type="baseline-report",
        payload={"baseline": "exact"},
    )
    run = state_root / "live" / started["workflow_id"]
    artifact = run / "artifacts" / "baseline"
    manifest_path = artifact / "manifest.json"
    envelope_path = artifact / "envelope.json"
    receipt_path = run / "receipts" / "00000001.json"
    forged_target = "codex:personal:other-skill"
    manifest = json.loads(manifest_path.read_text())
    manifest["target_identity"] = forged_target
    manifest["manifest_digest"] = helper.canonical_digest(manifest, "manifest_digest")
    manifest_path.write_bytes(helper.canonical_json_bytes(manifest))
    envelope = json.loads(envelope_path.read_text())
    envelope["target_identity"] = forged_target
    envelope["manifest_digest"] = manifest["manifest_digest"]
    envelope["envelope_digest"] = helper.canonical_digest(envelope, "envelope_digest")
    envelope_path.write_bytes(helper.canonical_json_bytes(envelope))
    receipt = json.loads(receipt_path.read_text())
    receipt["relevant_artifact_digests"][0]["envelope_digest"] = envelope[
        "envelope_digest"
    ]
    receipt["receipt_digest"] = helper.canonical_digest(receipt, "receipt_digest")
    receipt_path.write_bytes(helper.canonical_json_bytes(receipt))
    (run / "current.json").write_text("{broken", encoding="utf-8")

    try:
        helper.recover_run(workflow_id=started["workflow_id"], state_root=state_root)
    except helper.RunStateError as error:
        assert "target" in str(error) or "identity" in str(error)
    else:
        raise AssertionError("recovery accepted a substituted artifact target")


def test_competing_processes_cannot_both_acquire_same_target(tmp_path: Path) -> None:
    """Process-local checks alone would permit two genesis chains for one target."""
    state_root = tmp_path / "state"
    target = target_identity(tmp_path / "skills" / "sample-skill")
    context = multiprocessing.get_context("fork")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=initialize_process,
            args=(
                str(state_root),
                host_identity(tmp_path),
                target,
                authority(),
                start,
                results,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0

    assert [item[0] for item in outcomes].count("success") == 1
    assert [item[0] for item in outcomes].count("error") == 1
    assert len(list((state_root / "live").iterdir())) == 1


def test_mutation_rejects_substituted_active_target_lock(tmp_path: Path) -> None:
    """The process lock must not hide loss or substitution of target ownership."""
    helper = load_helper()
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / "sample-skill"
    started = helper.initialize_run(
        host_identity=host_identity(tmp_path),
        target_identity=target_identity(target),
        mode="create",
        authority=authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    run = state_root / "live" / started["workflow_id"]
    lock_path = (
        state_root
        / "target-locks"
        / helper._target_lock_name(target_identity(target)["canonical"])
    )
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["lock_nonce"] = "substituted-lock"
    lock_path.write_bytes(helper.canonical_json_bytes(lock))

    try:
        helper.pause_run(
            workflow_id=started["workflow_id"],
            expected_sequence=0,
            state_root=state_root,
        )
    except helper.RunStateError as error:
        assert "lock" in str(error)
    else:
        raise AssertionError("mutation accepted a substituted active-target lock")
    assert {path.name for path in (run / "receipts").iterdir()} == {"00000000.json"}
