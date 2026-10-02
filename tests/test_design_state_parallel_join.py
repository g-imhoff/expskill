from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.design_state_test_support import passing_technical


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "plugins" / "expskill" / "content" / "scripts" / "design_state.py"
DIGEST = hashlib.sha256(b"source plan").hexdigest()


def load_helper():
    spec = importlib.util.spec_from_file_location("design_state_parallel", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def initialize(tmp_path: Path, invocation_mode: str = "routed"):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "expskill/design/ui")
    git(repo, "config", "user.name", "Design Tests")
    git(repo, "config", "user.email", "design@example.invalid")
    (repo / "component.txt").write_text("baseline\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    baseline = git(repo, "rev-parse", "HEAD")
    module = load_helper()
    receipt = module.initialize_workflow(
        repository=repo,
        branch="expskill/design/ui",
        worktree=repo,
        baseline=baseline,
        dirty_fingerprint=hashlib.sha256(b"").hexdigest(),
        ui_contract={"digest": DIGEST, "outcome": "approved checkout UI"},
        scope={"components": ["CheckoutForm"], "exclusions": ["routing"]},
        invocation_mode=invocation_mode,
        state_home=tmp_path / "state",
    )
    brief = {
        "objective": "Create the approved checkout UI",
        "requirements": ["Keep the existing submit behavior"],
        "responsive_expectations": {
            "compact": "Keep the control visible when the label wraps",
            "intermediate": "Use the project row spacing",
            "wide": "Respect the project content width",
        },
        "non_goals": ["Do not change routing"],
        "source": {
            "kind": "plan-graph",
            "workflow_id": "a" * 32,
            "revision": 3,
            "digest": DIGEST,
        },
    }
    confirmed = module.confirm_brief(
        workflow_id=receipt["workflow_id"],
        expected_revision=receipt["revision"],
        brief=brief,
        confirmed=True,
        state_home=tmp_path / "state",
    )
    return module, repo, tmp_path / "state", confirmed, baseline


def test_confirmed_brief_and_one_commit_candidate_are_digest_bound(tmp_path: Path) -> None:
    module, repo, state_home, receipt, baseline = initialize(tmp_path)
    state = module.load_workflow(workflow_id=receipt["workflow_id"], state_home=state_home)
    assert state["brief"]["confirmed"] is True
    assert state["brief"]["digest"] == module.canonical_digest(
        {key: value for key, value in state["brief"].items() if key != "digest"}
    )

    (repo / "component.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "component.txt")
    git(repo, "commit", "-m", "design candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    checkpoint = module.checkpoint_candidate(
        workflow_id=receipt["workflow_id"],
        expected_revision=receipt["revision"],
        candidate_commit=candidate,
        state_home=state_home,
    )
    loaded = module.load_workflow(workflow_id=receipt["workflow_id"], state_home=state_home)
    assert checkpoint["candidate_commit"] == candidate
    assert loaded["candidate"] == {
        "baseline": baseline,
        "branch": "expskill/design/ui",
        "commit": candidate,
        "brief_digest": loaded["brief"]["digest"],
    }


def test_pre_brief_state_is_loaded_with_route_neutral_defaults(tmp_path: Path) -> None:
    module, _, state_home, receipt, _ = initialize(tmp_path)
    state_path = state_home / "design" / receipt["workflow_id"]
    state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    state.pop("brief")
    state.pop("candidate")
    state.pop("invocation_mode")
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")

    loaded = module.load_workflow(
        workflow_id=receipt["workflow_id"],
        state_home=state_home,
    )
    assert loaded["brief"]["confirmed"] is False
    assert loaded["candidate"] is None
    assert loaded["invocation_mode"] == "direct"


def test_confirmed_direct_brief_does_not_require_candidate_checkpoint(tmp_path: Path) -> None:
    module, _, state_home, receipt, _ = initialize(tmp_path, invocation_mode="direct")
    payloads = (
        {"files": [{"path": "component", "digest": "1" * 64, "classification": "component"}]},
        {"files": [{"path": "review", "digest": "2" * 64, "classification": "review"}]},
        {"files": [{"path": "manifest", "digest": "3" * 64, "classification": "manifest"}]},
    )
    with pytest.raises(ValueError, match="current approvals required"):
        module.deliver_workflow(
            workflow_id=receipt["workflow_id"],
            expected_revision=receipt["revision"],
            candidate_payload=payloads[0],
            review_evidence=payloads[1],
            manifest=payloads[2],
            state_home=state_home,
        )


def test_direct_invocation_cannot_checkpoint_a_candidate(tmp_path: Path) -> None:
    module, repo, state_home, receipt, _ = initialize(tmp_path, invocation_mode="direct")
    (repo / "component.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "component.txt")
    git(repo, "commit", "-m", "candidate")

    with pytest.raises(PermissionError, match="routed Design invocation"):
        module.checkpoint_candidate(
            workflow_id=receipt["workflow_id"],
            expected_revision=receipt["revision"],
            candidate_commit=git(repo, "rev-parse", "HEAD"),
            state_home=state_home,
        )


def test_routed_delivery_requires_candidate_checkpoint(tmp_path: Path) -> None:
    module, _, state_home, receipt, _ = initialize(tmp_path)
    payloads = (
        {"files": [{"path": "component", "digest": "1" * 64, "classification": "component"}]},
        {"files": [{"path": "review", "digest": "2" * 64, "classification": "review"}]},
        {"files": [{"path": "manifest", "digest": "3" * 64, "classification": "manifest"}]},
    )

    with pytest.raises(ValueError, match="routed delivery requires candidate checkpoint"):
        module.deliver_workflow(
            workflow_id=receipt["workflow_id"],
            expected_revision=receipt["revision"],
            candidate_payload=payloads[0],
            review_evidence=payloads[1],
            manifest=payloads[2],
            state_home=state_home,
        )


@pytest.mark.parametrize("mutation", ["dirty", "multiple", "unrelated", "branch"])
def test_candidate_checkpoint_rejects_non_atomic_history(tmp_path: Path, mutation: str) -> None:
    module, repo, state_home, receipt, baseline = initialize(tmp_path)
    (repo / "component.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "component.txt")
    git(repo, "commit", "-m", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    if mutation == "dirty":
        (repo / "component.txt").write_text("dirty\n", encoding="utf-8")
    elif mutation == "multiple":
        (repo / "second.txt").write_text("second\n", encoding="utf-8")
        git(repo, "add", "second.txt")
        git(repo, "commit", "-m", "second")
        candidate = git(repo, "rev-parse", "HEAD")
    elif mutation == "unrelated":
        git(repo, "checkout", "--orphan", "unrelated")
        git(repo, "rm", "-rf", ".")
        (repo / "other.txt").write_text("other\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "unrelated")
        git(repo, "branch", "-M", "expskill/design/ui")
        candidate = git(repo, "rev-parse", "HEAD")
    else:
        git(repo, "switch", "-c", "expskill/design/other")
    with pytest.raises(ValueError):
        module.checkpoint_candidate(
            workflow_id=receipt["workflow_id"],
            expected_revision=receipt["revision"],
            candidate_commit=candidate,
            state_home=state_home,
        )


def test_amended_candidate_invalidates_dependent_records(tmp_path: Path) -> None:
    module, repo, state_home, receipt, _ = initialize(tmp_path)
    (repo / "component.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", "component.txt")
    git(repo, "commit", "-m", "candidate")
    first = git(repo, "rev-parse", "HEAD")
    receipt = module.checkpoint_candidate(
        workflow_id=receipt["workflow_id"],
        expected_revision=receipt["revision"],
        candidate_commit=first,
        state_home=state_home,
    )
    state = module.load_workflow(workflow_id=receipt["workflow_id"], state_home=state_home)
    brief_digest = state["brief"]["digest"]
    code_digest = hashlib.sha256(b"candidate").hexdigest()
    contract_digest = hashlib.sha256(b"contract").hexdigest()
    evidence_digest = hashlib.sha256(b"evidence").hexdigest()
    receipt = module.apply_updates(
        workflow_id=receipt["workflow_id"],
        expected_revision=receipt["revision"],
        updates={
            "components": {
                "CheckoutForm": {
                    "id": "CheckoutForm",
                    "code_digest": code_digest,
                    "contract_digest": contract_digest,
                    "brief_digest": brief_digest,
                    "evidence_ids": ["E1"],
                    "dependency_ids": ["D1"],
                    "approval_id": "A1",
                    "eligible": True,
                    "approved": True,
                }
            },
            "dependencies": {
                "D1": {
                    "id": "D1",
                    "digest": hashlib.sha256(b"dependency").hexdigest(),
                    "component_ids": ["CheckoutForm"],
                }
            },
            "evidence": {
                "E1": {
                    "id": "E1",
                    "component_id": "CheckoutForm",
                    "digest": evidence_digest,
                    "code_digest": code_digest,
                    "contract_digest": contract_digest,
                    "brief_digest": brief_digest,
                    "widths": ["compact", "intermediate", "wide"],
                    "themes": ["default"],
                    "states": ["ready"],
                    "technical": passing_technical(evidence_digest),
                }
            },
            "approvals": {
                "A1": {
                    "id": "A1",
                    "component_id": "CheckoutForm",
                    "code_digest": code_digest,
                    "contract_digest": contract_digest,
                    "brief_digest": brief_digest,
                    "evidence_ids": ["E1"],
                    "dependency_ids": ["D1"],
                    "decision": "approved",
                }
            },
        },
        state_home=state_home,
    )
    (repo / "component.txt").write_text("amended\n", encoding="utf-8")
    git(repo, "add", "component.txt")
    git(repo, "commit", "--amend", "--no-edit")
    amended = git(repo, "rev-parse", "HEAD")
    updated = module.checkpoint_candidate(
        workflow_id=receipt["workflow_id"],
        expected_revision=receipt["revision"],
        candidate_commit=amended,
        state_home=state_home,
    )
    state = module.load_workflow(workflow_id=receipt["workflow_id"], state_home=state_home)
    assert updated["candidate_commit"] == amended
    assert state["components"] == {}
    assert state["dependencies"] == {}
    assert state["evidence"] == {}
    assert state["approvals"] == {}


def test_owned_scope_allows_authorized_bytes_and_rejects_unrelated_bytes(tmp_path: Path) -> None:
    module, repo, state_home, receipt, baseline = initialize(tmp_path, invocation_mode="direct")
    module.discard_workflow(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], confirmed=True, state_home=state_home)
    (repo / "protected.txt").write_text("pre-existing user work\n")
    receipt = module.initialize_workflow(repository=repo, branch="expskill/design/ui", worktree=repo, baseline=baseline, dirty_fingerprint=module._dirty(repo), ui_contract={"digest": DIGEST}, scope={"components": ["CheckoutForm"], "exclusions": [], "owned_paths": ["component.txt", "new-preview.html"]}, invocation_mode="direct", state_home=state_home)
    (repo / "component.txt").write_text("accepted component change\n")
    (repo / "new-preview.html").write_text("accepted specimen\n")
    updated = module.apply_updates(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], updates={}, state_home=state_home)
    (repo / "protected.txt").write_text("overwritten with the same untracked status\n")
    with pytest.raises(ValueError, match="unrelated workspace bytes changed"):
        module.apply_updates(workflow_id=receipt["workflow_id"], expected_revision=updated["revision"], updates={}, state_home=state_home)


@pytest.mark.parametrize("owned", [".", "../outside", "/tmp/outside", "component*", "src/**", ".git/config", "folder", "linked"])
def test_owned_scope_rejects_directory_wildcard_traversal_and_symlink(tmp_path: Path, owned: str) -> None:
    module, repo, state_home, receipt, baseline = initialize(tmp_path, invocation_mode="direct")
    module.discard_workflow(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], confirmed=True, state_home=state_home)
    (repo / "folder").mkdir()
    (repo / "linked").symlink_to(repo / "component.txt")
    with pytest.raises(ValueError):
        module.initialize_workflow(repository=repo, branch="expskill/design/ui", worktree=repo, baseline=baseline, dirty_fingerprint=module._dirty(repo), ui_contract={"digest": DIGEST}, scope={"components": [], "exclusions": [], "owned_paths": [owned]}, invocation_mode="direct", state_home=state_home)


def test_private_accepted_direct_input_is_bound_without_brainstorm_file(tmp_path):
    module, repo, home, receipt, _ = initialize(tmp_path, invocation_mode="direct")
    source = tmp_path / "accepted-input.json"
    source.write_text('{"request":"A complete accepted direct UI request","non_goals":["No navigation"]}')
    source.chmod(0o400)
    brief = {key: value for key, value in module.load_workflow(workflow_id=receipt["workflow_id"], state_home=home)["brief"].items() if key not in {"confirmed", "digest"}}
    brief["source"] = {"kind": "accepted-input", "path": str(source), "digest": hashlib.sha256(source.read_bytes()).hexdigest(), "decision_reference": "original explicit user request"}
    confirmed = module.confirm_brief(workflow_id=receipt["workflow_id"], expected_revision=receipt["revision"], brief=brief, confirmed=True, state_home=home)
    assert module.load_workflow(workflow_id=receipt["workflow_id"], state_home=home)["brief"]["digest"] == confirmed["brief_digest"]
    source.chmod(0o600)
    source.write_text('{"request":"Changed accepted meaning"}')
    source.chmod(0o400)
    with pytest.raises(ValueError, match="source digest mismatch"):
        module.load_workflow(workflow_id=receipt["workflow_id"], state_home=home)
