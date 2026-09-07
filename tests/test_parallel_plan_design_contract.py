from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "packages" / "codex"

PRESERVED_PROFILES = {
    "expskill-explorer.toml": "5a84e1baf021979ceec721efb60a0fc41aa3fddb4301a2d77586c72400b9bdd4",
    "expskill-implementer.toml": "c3947c9a092d3c52231c3c1351ec7b8636b85e52e4348913f03e33a646f7217d",
    "expskill-review.toml": "b97ee34c852fc8929a8e611761aa62cb234264f9e8ad0e1c13459a79a148e951",
    "expskill-spec.toml": "8dc4d43d91c35e2d7d98fb4c638dae61f8ac957c4cae96c5f533fe4b02311c43",
    "expskill-test-engineer.toml": "7d9098c0f02dd5864a5d217cdaf06730c5b2dddeef9ea966fa7cc0a84c39007a",
}


def skill(name: str) -> str:
    return (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_router_has_one_bounded_parallel_plan_design_route() -> None:
    router = " ".join(skill("use-expskill").split())
    required = (
        "inspect_setup.py",
        "absent",
        "invalid",
        "select only `$setup-ui-testing`",
        "parallel-plan-design",
        "before waiting",
        "same exact repository baseline",
        "only Plan Graph writer",
        "seed-ui-harness",
        "invocation_mode",
        "at most one current question at a time",
        "same Plan session",
        "never edits tracked source",
        "never copy `.ui-harness/evidence`",
    )
    for phrase in required:
        assert phrase.lower() in router.lower()

    policy = json.loads((PLUGIN / "assets" / "execution-policy.json").read_text())
    route = policy["routes"]["use-expskill"]["parallel-plan-design"]
    assert route["allowed_profiles"] == ["expskill-planner", "expskill-designer"]
    assert route["max_agent_calls"] == 2
    assert route["max_concurrency"] == 2
    assert "max_depth" not in route
    assert "max_retries" not in route


def test_setup_plan_and_design_remain_independently_invokable() -> None:
    setup = skill("setup-ui-testing")
    plan = skill("plan")
    design = skill("design")
    assert "$setup-ui-testing` is standalone" in setup
    assert "router-selected" in setup
    assert "$plan` is a standalone" in plan
    assert "routed parallel mode" in plan
    assert "explicitly invokes `$design`" in design
    assert "In routed mode" in design
    assert "In routed mode only" in design
    assert "without creating a candidate commit" in design


def test_helper_contracts_bind_and_clean_the_parallel_candidate() -> None:
    design = (PLUGIN / "scripts" / "design_state.py").read_text(encoding="utf-8")
    plan = (PLUGIN / "scripts" / "plan_graph.py").read_text(encoding="utf-8")
    worktrees = (PLUGIN / "scripts" / "worktrees.py").read_text(encoding="utf-8")
    for symbol in ("confirm_brief", "checkpoint_candidate", "brief_digest", "candidate_commit", "invocation_mode"):
        assert symbol in design
    for symbol in ("issue_design_join_receipt", "record-design-join", "design_join", "_commit_is_ancestor"):
        assert symbol in plan
    for symbol in ("seed_ui_harness", "seed-ui-harness", "_load_seed_record", '"README.md"', '"agent"'):
        assert symbol in worktrees


def test_existing_customized_profiles_are_byte_for_byte_unchanged() -> None:
    profiles = PLUGIN / "assets" / "agents"
    for name, expected in PRESERVED_PROFILES.items():
        assert hashlib.sha256((profiles / name).read_bytes()).hexdigest() == expected
