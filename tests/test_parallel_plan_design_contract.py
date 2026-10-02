from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"

PRESERVED_PROFILES = {
    "expskill-explorer.md": "5c5004325f030473c618ac634619eea57e4e2b2eef02459cc9fa2d746c9a7240",
    "expskill-implementer.md": "330ca9b92040e47ecf7a210e3987a1d2542ba2d79dc4915d8c49f1551937183c",
    "expskill-review.md": "c9c841cc2f68802b7e956ae737561461ebd5eed48726290efa6d7774d712b0b8",
    "expskill-spec.md": "97e7b61c6a20ad801cc814da47d1a4aca22fe5f9d85b170178fe4eb20293aa5a",
    "expskill-test-engineer.md": "7db7bea7f92597fd8cec6c474404531f7c8f09906d5abf5e3d56472c6355c985",
}


def skill(name: str) -> str:
    return (PLUGIN / "content" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_router_has_one_bounded_parallel_plan_design_route() -> None:
    router = " ".join(skill("use-expskill").split())
    required = (
        "There is no setup gate.",
        "discoverable project-native capabilities",
        "never acts as a routing precondition",
        "absent",
        "invalid",
        "parallel-plan-design",
        "before waiting",
        "same exact repository baseline",
        "only Plan Graph writer",
        "invocation_mode",
        "at most one current question at a time",
        "same Plan session",
        "never edits tracked source",
    )
    for phrase in required:
        assert phrase.lower() in router.lower()
    assert "stop before Plan, Design, or feature implementation" not in router
    for removed in ("inspect_setup.py", "seed-ui-harness", "$setup-ui-testing", ".ui-harness"):
        assert removed.lower() not in router.lower()

    policy = json.loads((PLUGIN / "content" / "policies" / "execution-policy.json").read_text())
    route = policy["routes"]["use-expskill"]["parallel-plan-design"]
    assert route["allowed_profiles"] == ["expskill-planner", "expskill-designer"]
    assert route["max_agent_calls"] == 2
    assert route["max_concurrency"] == 2
    assert route["max_depth"] == 1
    assert route["max_retries"] == 0


def test_setup_plan_and_design_remain_independently_invokable() -> None:
    design_setup = skill("setup-design")
    test_setup = skill("setup-test")
    plan = skill("plan")
    design = skill("design")
    assert "invokes $setup-design" in design_setup
    assert "user explicitly asks" in design_setup
    assert "Never add a setup gate" in design_setup
    assert "invokes $setup-test" in test_setup
    assert "user explicitly asks" in test_setup
    assert "Never add a setup gate" in test_setup
    assert "$plan` is a standalone" in plan
    assert "routed parallel mode" in plan
    assert "explicitly invokes `$design`" in design
    assert "In routed mode" in design
    assert "In routed mode only" in design
    assert "without creating a candidate commit" in design


def test_helper_contracts_bind_and_clean_the_parallel_candidate() -> None:
    design = (PLUGIN / "content" / "scripts" / "design_state.py").read_text(encoding="utf-8")
    plan = (PLUGIN / "content" / "scripts" / "plan_graph.py").read_text(encoding="utf-8")
    worktrees = (PLUGIN / "content" / "scripts" / "worktrees.py").read_text(encoding="utf-8")
    for symbol in ("confirm_brief", "checkpoint_candidate", "brief_digest", "candidate_commit", "invocation_mode"):
        assert symbol in design
    for symbol in ("issue_design_join_receipt", "record-design-join", "design_join", "_commit_is_ancestor"):
        assert symbol in plan
    for symbol in ("create_worktree", "finish_worktree", "_owned_path", "_branch_parts"):
        assert symbol in worktrees
    for removed in ("seed_ui_harness", "seed-ui-harness", "_load_seed_record", "_inspect_ui_harness"):
        assert removed not in worktrees


def test_existing_customized_profiles_are_byte_for_byte_unchanged() -> None:
    profiles = PLUGIN / "content" / "agents"
    for name, expected in PRESERVED_PROFILES.items():
        assert hashlib.sha256((profiles / name).read_bytes()).hexdigest() == expected
