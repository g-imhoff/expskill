from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "expskill"


def skill(name: str) -> str:
    return (PLUGIN / "content" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_router_drops_retired_ui_harness_names_and_bounds_the_route() -> None:
    router = " ".join(skill("use-expskill").split())
    for removed in ("inspect_setup.py", "seed-ui-harness", "$setup-ui-testing", ".ui-harness"):
        assert removed.lower() not in router.lower()

    policy = json.loads((PLUGIN / "content" / "policies" / "execution-policy.json").read_text())
    route = policy["routes"]["use-expskill"]["parallel-plan-design"]
    assert route["allowed_profiles"] == ["expskill-planner", "expskill-designer"]
    assert route["max_agent_calls"] == 2
    assert route["max_concurrency"] == 2
    assert route["max_depth"] == 1
    assert route["max_retries"] == 0


def test_router_names_the_parallel_route_and_invocation_mode() -> None:
    router = skill("use-expskill")
    assert "parallel-plan-design" in router
    assert "invocation_mode" in router


def test_route_skills_name_their_own_tokens() -> None:
    assert "$setup-design" in skill("setup-design")
    assert "$setup-test" in skill("setup-test")
    assert "$design" in skill("design")


def test_runtime_profile_source_files_exist() -> None:
    for name in (
        "expskill-explorer.md",
        "expskill-implementer.md",
        "expskill-review.md",
        "expskill-spec.md",
        "expskill-test-engineer.md",
    ):
        assert (PLUGIN / "content" / "agents" / name).is_file(), name


def test_helper_scripts_define_parallel_candidate_symbols_without_ui_harness_seeding() -> None:
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
