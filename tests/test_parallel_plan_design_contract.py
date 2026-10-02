from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"

PRESERVED_PROFILES = {
    "expskill-explorer.md": "5c5004325f030473c618ac634619eea57e4e2b2eef02459cc9fa2d746c9a7240",
    "expskill-implementer.md": "d7b9b5454d4dc070388558bee44cbc73ce8b476ca7748177902061854a7a7263",
    "expskill-review.md": "f5d69d752465b63dd3f4633ae027cb73a9f20106ec0ead075d1fd89682209216",
    "expskill-spec.md": "8c4fb0d8ec94fc6ea3bd34124f2b1991eba8391394b3d5af0ace22903aa12866",
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


def test_customized_profiles_match_reviewed_content() -> None:
    profiles = PLUGIN / "content" / "agents"
    for name, expected in PRESERVED_PROFILES.items():
        assert hashlib.sha256((profiles / name).read_bytes()).hexdigest() == expected


def test_parallel_input_and_answer_relay_preserve_phase_ownership():
    router = skill("use-expskill")
    for clause in (
        "complete accepted input", "Do not invoke Brainstorm to materialize settled intent",
        "conversation-relay.json", "actual session identifier", "pending question ID",
        "codex exec resume --json", "claude -p --resume", "opencode run --session",
        "Never send it to both sessions", "relaunch the initial prompt as a substitute", "same Plan session",
        "Recovery never implies approval, resets a budget", "preference-first policy",
    ):
        assert clause.lower() in router.lower()
