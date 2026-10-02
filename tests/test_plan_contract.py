from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "expskill"
PLAN_ROOT = PLUGIN_ROOT / "content" / "skills" / "plan"
HELPER_PATH = PLUGIN_ROOT / "content" / "scripts" / "plan_graph.py"
PHASE_ROOTS = {
    name: PLUGIN_ROOT / "content" / "skills" / name
    for name in ("implement", "use-expskill")
}


def _parse_simple_yaml_mapping(contents: str) -> dict[str, object]:
    result: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, result)]
    for raw_line in contents.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indentation = len(raw_line) - len(raw_line.lstrip(" "))
        key, separator, raw_value = raw_line.strip().partition(":")
        if not separator:
            raise AssertionError(f"unsupported YAML line: {raw_line!r}")
        while stack[-1][0] >= indentation:
            stack.pop()
        parent = stack[-1][1]
        value = raw_value.strip()
        if not value:
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indentation, child))
        elif value in {"true", "false"}:
            parent[key] = value == "true"
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            parent[key] = value[1:-1]
        else:
            parent[key] = value
    return result


def _frontmatter(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError(f"missing frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError(f"unterminated frontmatter: {path}") from error
    return _parse_simple_yaml_mapping("\n".join(lines[1:end]))


def _markdown_section(contents: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        contents,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing section: {heading}")
    return " ".join(match.group("body").lower().split())


def _numbered_workflow(contents: str) -> list[str]:
    section = _markdown_section(contents, "Workflow")
    matches = list(re.finditer(r"(?<!\S)(\d+)\.\s", section))
    if not matches:
        raise AssertionError("workflow has no numbered operations")
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        items.append(section[match.end() : end].strip())
    return items


class PlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_path = PLAN_ROOT / "SKILL.md"
        self.metadata_path = PLUGIN_ROOT / "codex" / "skill-adapters" / "plan" / "agents" / "openai.yaml"
        self.body = self.skill_path.read_text(encoding="utf-8")
        self.normalized = " ".join(self.body.lower().split())

    def assertContainsAll(self, *phrases: str) -> None:
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(" ".join(phrase.lower().split()), self.normalized)

    def test_plan_package_is_minimal_regular_and_contains_no_runtime_shadow_resource(self) -> None:
        self.assertEqual(
            {
                path.relative_to(PLAN_ROOT).as_posix()
                for path in PLAN_ROOT.rglob("*")
                if path.is_file()
            },
            {"SKILL.md"},
        )
        self.assertEqual(
            {
                path.relative_to(PLAN_ROOT).as_posix()
                for path in PLAN_ROOT.rglob("*")
                if path.is_dir()
            },
            set(),
        )
        self.assertFalse(any(path.is_symlink() for path in PLAN_ROOT.rglob("*")))

    def test_frontmatter_and_metadata_remain_explicit_plan_only(self) -> None:
        frontmatter = _frontmatter(self.skill_path)
        self.assertEqual(set(frontmatter), {"name", "description"})
        self.assertEqual(frontmatter["name"], "plan")
        self.assertIsInstance(frontmatter["description"], str)
        self.assertTrue(str(frontmatter["description"]).strip())
        metadata = _parse_simple_yaml_mapping(self.metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(set(metadata), {"interface", "policy"})
        self.assertEqual(
            set(metadata["interface"]),
            {"display_name", "short_description", "default_prompt"},
        )
        self.assertEqual(metadata["policy"], {"allow_implicit_invocation": False})
        self.assertIn("$plan", str(metadata["interface"]["default_prompt"]))

    def test_plan_has_no_router_or_typed_downstream_handoff(self) -> None:
        for forbidden in (
            "phase-handoff-v1",
            "selected_phase",
            "next_skill",
            "invoke `$implement`",
            "open `$implement`",
            "recommend `$implement`",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden.lower(), self.body.lower())
        boundary = _markdown_section(self.body, "Boundary")
        stopping = _markdown_section(self.body, "Stop and downstream boundary")
        self.assertIn("standalone", boundary)
        self.assertRegex(boundary, r"do not route, invoke, open, select, or recommend another lifecycle skill")
        self.assertRegex(boundary, r"do not begin .*implementation")
        self.assertIn("emit no typed handoff", stopping)

    def test_trigger_and_near_neighbor_boundaries_are_explicit(self) -> None:
        description = str(_frontmatter(self.skill_path)["description"]).lower()
        boundary = _markdown_section(self.body, "Boundary")
        self.assertIn("explicit $plan", description)
        self.assertIn("sufficiently concrete", description)
        for accepted in ("concept brief", "concrete direction"):
            self.assertIn(accepted, boundary)
        self.assertRegex(boundary, r"return `not-ready` for a vague direction")
        self.assertRegex(boundary, r"must not repeat brainstorm, simulate brainstorm")
        for excluded in (
            "production or test implementation",
            "review",
            "verification",
            "integration",
            "github delivery",
        ):
            self.assertIn(excluded, boundary)

    def test_ordered_workflow_moves_from_grounding_to_complete_graph_before_projection(self) -> None:
        steps = _numbered_workflow(self.body)
        self.assertEqual(len(steps), 10)
        required_by_step = (
            ("branch policy", "repository revision", "user confirmation"),
            ("create or resume", "conceptual decisions"),
            ("main agent", "implementation seam", "never delegated"),
            ("classify feasibility", "contradicts", "smallest necessary amendment"),
            ("adaptive minimum", "one outcome", "proof obligation"),
            ("current technical reality", "not a confirmation gate"),
            ("technical uncertainty", "targeted", "broad research"),
            ("complete implementation-facing graph", "joins", "git topology"),
            ("structural validation", "semantic coverage", "adversarial audit"),
            ("backend graph is complete", "progressive user projections", "derive `ready`"),
        )
        for number, (step, required) in enumerate(zip(steps, required_by_step), start=1):
            for phrase in required:
                with self.subTest(step=number, phrase=phrase):
                    self.assertIn(phrase, step)

    def test_plan_write_and_git_authority_is_narrow_and_explicit(self) -> None:
        boundary = _markdown_section(self.body, "Boundary")
        branch_step = _numbered_workflow(self.body)[0]
        stopping = _markdown_section(self.body, "Stop and downstream boundary")
        for phrase in ("source-read-only", "concept-read-only", "private graph operations through the helper"):
            self.assertIn(phrase, boundary)
        self.assertRegex(boundary, r"writes no production code, tests, executable configuration, or repository planning document")
        self.assertIn("explicit user confirmation", branch_step)
        self.assertIn("only repository git mutation", branch_step)
        for forbidden in ("commit", "push", "merge", "rebase", "stash", "reset", "task worktree"):
            with self.subTest(forbidden=forbidden):
                self.assertRegex(branch_step, rf"never [^.]*\b{re.escape(forbidden)}\b")
        self.assertRegex(stopping, r"later authorized lifecycle, not `\$plan`, may push")

    def test_plan_defines_the_private_canonical_graph_and_adaptive_minimum(self) -> None:
        self.assertTrue(HELPER_PATH.is_file(), HELPER_PATH)
        graph = _markdown_section(self.body, "Canonical private graph")
        minimum = _numbered_workflow(self.body)[4]
        for phrase in (
            "private state outside the repository",
            "json-compatible yaml",
            "one active graph",
            "canonical repository identity",
            "target branch",
            "workflow id",
            "graph revision",
            "outcomes",
            "evidence",
            "decisions",
            "work",
            "proof",
            "user projections",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, graph)
        for phrase in ("one outcome", "one grounded work", "one proof obligation"):
            self.assertIn(phrase, minimum)
        self.assertNotIn("write the yaml directly", self.normalized)

    def test_plan_preserves_the_concept_boundary_and_grounds_before_questions(self) -> None:
        boundary = _markdown_section(self.body, "Boundary")
        grounding = _numbered_workflow(self.body)[2]
        orientation = _numbered_workflow(self.body)[5]
        self.assertRegex(boundary, r"must not repeat brainstorm, simulate brainstorm")
        for phrase in (
            "main agent",
            "implementation seam",
            "callers",
            "state and effects",
            "interfaces and consumers",
            "tests",
            "never delegated",
            "does not require confirmation",
            "exhaust supplied context",
            "repository evidence",
            "authoritative sources",
            "one focused question per turn",
            "material user-owned",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, grounding)
        self.assertLess(grounding.index("implementation seam"), grounding.index("one focused question"))
        self.assertIn("current technical reality", orientation)

    def test_plan_research_is_adaptive_bounded_and_user_owned(self) -> None:
        steps = _numbered_workflow(self.body)
        grounding, research = steps[2], steps[6]
        self.assertLess(steps.index(grounding), steps.index(research))
        for phrase in ("targeted official or primary lookup", "mechanism and source family"):
            self.assertIn(phrase, research)
        for phrase in (
            "broad research only",
            "one to three",
            "gpt-5.6-luna",
            "max reasoning",
            "at most one targeted follow-up",
            "no repository access",
            "preferred answer",
            "sibling output",
            "edit authority",
            "user interaction",
            "planning role",
            "rejected alternatives",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, research)

    def test_research_accounting_has_explicit_private_write_and_continuity_scope(self) -> None:
        self.assertIn("private research accounting through `../../scripts/research_budget.py`", self.body)
        self.assertIn("original run identity", _numbered_workflow(self.body)[6])
        private = _markdown_section(self.body, "Canonical private graph")
        for phrase in ("research-budgets", "initial research branch", "graph workflow id as the run id", "stop new research", "unique `dispatch_id`", "retains its open interval", "coordinator attestations"):
            self.assertIn(phrase, private)
        planner = (PLUGIN_ROOT / "content/agents/expskill-planner.md").read_text().lower()
        self.assertIn("private cumulative research accounting through research_budget.py", planner)
        self.assertIn("never reset its allowance", planner)

    def test_routed_planner_can_run_only_bounded_evidence_services_and_relay_their_provenance(self) -> None:
        planner = (PLUGIN_ROOT / "content/agents/expskill-planner.md").read_text().lower()
        self.assertNotIn("do not delegate,", planner)
        for phrase in ("delegate only evidence-only researchers and one independent plan auditor",
                       "no repository access", "fresh conversation", "no inherited planner history",
                       "only plan graph writer", "no product or git mutations", "no further delegation",
                       "inherited lifecycle allowance", "five-minute", "dispatch id", "output locator and digest",
                       "spent and outstanding", "router"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, planner)
        validation = _numbered_workflow(self.body)[8]
        for phrase in ("fresh read-only conversation", "frozen graph locator and digest",
                       "no inherited planner history", "one initial five-minute auditor call",
                       "inherited lifecycle allowance", "never certify your own audit as independent"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, validation)
        self.assertIn("research or auditor dispatch id", self.body.lower())
        self.assertIn("remaining allowance", self.body.lower())

    def test_real_material_corrections_have_finite_fresh_independent_audit_capacity(self) -> None:
        validation = _numbered_workflow(self.body)[8]
        for phrase in ("one initial five-minute auditor call", "at most two fresh five-minute correction checks",
                       "three calls and fifteen minutes", "actually corrected canonical graph",
                       "fresh read-only conversation", "broader scope requires an explicitly named user-authorized extension",
                       "retain every spent and outstanding call", "never attest that a correction was independently checked",
                       "missing or stale evidence"):
            self.assertIn(phrase, validation)
        planner = (PLUGIN_ROOT / "content/agents/expskill-planner.md").read_text().lower()
        for phrase in ("one initial audit", "at most two fresh correction checks", "retain spent and outstanding calls",
                       "no speculative retry or replacement", "named user-authorized extension"):
            self.assertIn(phrase, planner)

    def test_work_proof_and_parallelism_are_proportional_and_revision_bound(self) -> None:
        graph_step = _numbered_workflow(self.body)[7]
        proof = _markdown_section(self.body, "Implementation, proof, and Git execution contract")
        for phrase in (
            "slice",
            "leaf task",
            "join",
            "tests and proof",
            "behavior they validate",
            "proof obligations",
            "serial",
            "parallel-candidate",
            "parallel-safe",
            "one cheap concurrency pass",
            "never spend more effort",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, graph_step)
        for phrase in ("executed command", "exact repository revision", "future executors revalidate"):
            self.assertIn(phrase, proof)

    def test_progressive_projection_is_complete_concise_and_selectively_invalidated(self) -> None:
        projection = _numbered_workflow(self.body)[9]
        for phrase in (
            "backend graph is complete",
            "user never sees yaml",
            "one coherent part per turn",
            "every material decision",
            "confirm or correct",
            "affected subgraph",
            "only affected",
            "preserve unrelated confirmations",
            "wording-only",
            "no redundant final confirmation",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, projection)
        self.assertIn("routine reversible", _numbered_workflow(self.body)[2])
        self.assertNotRegex(self.normalized, r"exactly (?:five|5) (?:short )?bullets")
        self.assertNotRegex(self.normalized, r"(?:maximum|max) 200 words")

    def test_git_contract_uses_live_nonprotected_target_and_progressive_lane_cleanup(self) -> None:
        branch_step = _numbered_workflow(self.body)[0]
        execution = _markdown_section(self.body, "Implementation, proof, and Git execution contract")
        for phrase in (
            "non-protected workflow branch",
            "live integration target",
            "explicit user confirmation",
            "creating and switching",
            "only repository git mutation",
            "relevant uncommitted work",
        ):
            self.assertIn(phrase, branch_step)
        for phrase in (
            "concurrent writing lanes",
            "external worktrees",
            "one writer",
            "incremental target-side integration",
            "remove its worktree",
            "safely delete",
            "committed-to-target",
            "accepted-on-target",
            "dirty, failing, unmerged, unknown, or still-needed state is preserved",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, execution)
        for forbidden in ("plan creates task worktrees", "plan commits", "plan pushes"):
            self.assertNotIn(forbidden, self.normalized)

    def test_persistence_readiness_audit_pause_and_discard_are_explicit(self) -> None:
        graph = _markdown_section(self.body, "Canonical private graph")
        validation = _numbered_workflow(self.body)[8]
        for phrase in (
            "deterministic helper",
            "only graph writer",
            "outside the repository",
            "strict",
            "identity lock",
            "compare-and-swap",
            "atomic",
            "previous valid generation",
            "derives lifecycle state",
            "pause preserves state",
            "resume revalidates",
            "discard requires explicit user authority",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, graph)
        for phrase in (
            "main-agent semantic coverage closure",
            "independent adversarial audit",
            "broad, complex, or high-consequence",
            "tiny plans",
        ):
            self.assertIn(phrase, validation)

    def test_draft_request_stops_at_human_review_and_ai_never_merges(self) -> None:
        self.assertContainsAll(
            "first coherent implementation",
            "draft",
            "exact head",
            "ready-for-human-review",
            "delete",
            "ai must never",
            "approve",
            "merge",
            "auto-merge",
            "merge queue",
            "manual review",
            "outside observable ai control",
        )

    def test_shared_consumers_reference_one_canonical_graph_without_self_certification(self) -> None:
        required = {
            "implement": (
                "plan graph",
                "active coordinator",
                "graph updates",
                "do not make workers graph writers",
            ),
            "use-expskill": (
                "plan graph",
                "revalidate its revision and commit",
                "apply only receipts",
                "do not let workers write graph state",
            ),
        }
        for name, phrases in required.items():
            body = " ".join((PHASE_ROOTS[name] / "SKILL.md").read_text(encoding="utf-8").lower().split())
            self.assertIn("plugins/expskill/content/scripts/plan_graph.py", body)
            self.assertIn("../../scripts/plan_graph.py", body)
            self.assertIn("source-package locator", body)
            self.assertIn("branch", body)
            self.assertIn("commit", body)
            for phrase in phrases:
                with self.subTest(skill=name, phrase=phrase):
                    self.assertIn(phrase, body)


if __name__ == "__main__":
    unittest.main()
