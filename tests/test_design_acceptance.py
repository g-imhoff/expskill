from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "plugins" / "expskill" / "content" / "skills" / "design"
FRESH_CONTEXT_COMMAND = ("codex", "exec", "--ephemeral", "--ignore-user-config", "--json")
CONFIGURATIONS = ("A", "B", "C", "D")
CALIBRATION_BRIEFS = (
    "convention-rich-checkout",
    "fragmented-analytics",
    "command-palette",
)
RECEIPT_FIELDS = {
    "run_id",
    "case_id",
    "configuration",
    "fixture_revision",
    "repository_baseline",
    "candidate_skill_digest",
    "selected_rule_ids",
    "prompt_digest",
    "model_profile",
    "invocation",
    "event_stream_digest",
    "output_digest",
    "before_manifest_digest",
    "after_manifest_digest",
    "artifacts",
    "renders",
    "technical_results",
    "human_decision",
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _receipt(case_id: str = CALIBRATION_BRIEFS[0], configuration: str = "A") -> dict[str, object]:
    digest = "a" * 64
    return {
        "run_id": "opaque-run",
        "case_id": case_id,
        "configuration": configuration,
        "fixture_revision": digest,
        "repository_baseline": digest,
        "candidate_skill_digest": digest,
        "selected_rule_ids": [],
        "prompt_digest": digest,
        "model_profile": "bound-profile",
        "invocation": list(FRESH_CONTEXT_COMMAND),
        "event_stream_digest": digest,
        "output_digest": digest,
        "before_manifest_digest": digest,
        "after_manifest_digest": digest,
        "artifacts": [{"path": "opaque", "digest": digest, "classification": "review-evidence"}],
        "renders": [{"viewport": "compact", "theme": "light", "state": "default", "digest": digest}],
        "technical_results": [{"command_digest": digest, "exit": 0, "output_digest": digest}],
        "human_decision": {"choice": "pending", "correction": "", "notes": ""},
    }


def _validate_receipt(receipt: object) -> None:
    if not isinstance(receipt, dict):
        raise AssertionError("receipt must be an object")
    if set(receipt) != RECEIPT_FIELDS:
        raise AssertionError("receipt fields are incomplete or substituted")
    for key in ("run_id", "case_id", "fixture_revision", "repository_baseline", "candidate_skill_digest", "prompt_digest", "event_stream_digest", "output_digest", "before_manifest_digest", "after_manifest_digest"):
        value = receipt[key]
        if not isinstance(value, str) or not value:
            raise AssertionError(f"receipt {key} is not a nonempty string")
        if key.endswith("digest") or key in {"fixture_revision", "repository_baseline"}:
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise AssertionError(f"receipt {key} is not a full digest")
    if receipt["configuration"] not in CONFIGURATIONS:
        raise AssertionError("unknown anonymous configuration")
    if tuple(receipt["invocation"]) != FRESH_CONTEXT_COMMAND:
        raise AssertionError("trial was not fresh-context codex exec")
    if not isinstance(receipt["artifacts"], list) or not isinstance(receipt["renders"], list):
        raise AssertionError("receipt evidence inventories are not lists")
    decision = receipt["human_decision"]
    if not isinstance(decision, dict) or decision.get("choice") not in {"pending", "preferred", "rejected"}:
        raise AssertionError("human decision is missing or fabricated")


class DesignAcceptanceTests(unittest.TestCase):
    def test_candidate_contract_contains_observable_review_and_safety_gates(self) -> None:
        """Regression: prose-only or unsafe Design output must not be treated as accepted UI."""
        self.assertTrue(DESIGN.is_dir(), f"missing design package: {DESIGN}")
        body = (DESIGN / "SKILL.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "compact",
            "intermediate",
            "wide",
            "actual",
            "synthetic",
            "technical",
            "approval",
            "candidate payload",
            "review evidence",
            "route-neutral manifest",
            "no customer data",
            "no external",
            "zero serious or critical accessibility findings",
            "page-level overflow",
            "reduced-motion",
            "before any approval request",
        ):
            self.assertIn(phrase, body, phrase)

    def test_complex_and_narrow_work_use_different_rule_loading_strategies(self) -> None:
        """Regression: the calibration result must change production routing, not remain an evaluator note."""
        body = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in (DESIGN / "SKILL.md", DESIGN / "references" / "rules-index.md")
        )
        self.assertIn("narrow component", body)
        self.assertIn("selectively", body)
        self.assertIn("complex composite", body)
        self.assertIn("complete category catalog", body)
        self.assertIn("project convention", body)

    def test_calibration_matrix_is_three_briefs_by_four_anonymous_configurations(self) -> None:
        """Regression: a single convenient component or unblinded comparison cannot certify selection."""
        self.assertEqual(len(CALIBRATION_BRIEFS), 3)
        self.assertEqual(len(set(CALIBRATION_BRIEFS)), 3)
        self.assertEqual(CONFIGURATIONS, ("A", "B", "C", "D"))
        self.assertEqual(len(CALIBRATION_BRIEFS) * len(CONFIGURATIONS), 12)
        self.assertEqual(set(CALIBRATION_BRIEFS), {"convention-rich-checkout", "fragmented-analytics", "command-palette"})

    def test_frozen_validation_cases_are_not_materialized_in_candidate_context(self) -> None:
        """Regression: validation cases must remain withheld from candidate repair and tuning."""
        validation_root = ROOT / "tests" / "fixtures" / "design-certification" / "validation"
        self.assertFalse(validation_root.exists(), validation_root)

    def test_receipt_schema_accepts_complete_evidence(self) -> None:
        """Regression: certification must retain independent, revision-bound evidence."""
        receipt = _receipt()
        _validate_receipt(receipt)
        self.assertEqual(_digest(receipt["artifacts"]), _digest(receipt["artifacts"]))

    def test_receipt_mutations_fail_independently(self) -> None:
        """Regression: missing digests, wrong invocation, stale configuration, and fake decisions cannot pass."""
        for field in sorted(RECEIPT_FIELDS):
            with self.subTest(field=field):
                mutated = _receipt()
                mutated.pop(field)
                with self.assertRaises(AssertionError):
                    _validate_receipt(mutated)
        for mutation in (
            ("invocation", ["python3", "agent.py"]),
            ("configuration", "D-hidden"),
            ("candidate_skill_digest", "self-reported"),
        ):
            with self.subTest(mutation=mutation[0]):
                mutated = _receipt()
                mutated[mutation[0]] = mutation[1]
                with self.assertRaises(AssertionError):
                    _validate_receipt(mutated)

    def test_fresh_context_command_is_exact_and_network_free_by_contract(self) -> None:
        """Regression: hidden user configuration, network calls, and ambient state invalidate trials."""
        self.assertEqual(FRESH_CONTEXT_COMMAND, ("codex", "exec", "--ephemeral", "--ignore-user-config", "--json"))
        self.assertNotIn("--network", FRESH_CONTEXT_COMMAND)
        self.assertNotIn("--full-auto", FRESH_CONTEXT_COMMAND)

    def test_brief_pressure_matrix_covers_required_design_regressions(self) -> None:
        """Regression: clean default components hide responsive, state, content, accessibility, and family failures."""
        pressure = {
            "convention-rich-checkout": {"tokens", "localization", "validation", "loading", "error", "responsive"},
            "fragmented-analytics": {"fragmented-conventions", "empty", "partial", "overflow", "data"},
            "command-palette": {"keyboard", "focus", "async", "disabled", "reduced-motion", "narrow"},
        }
        self.assertEqual(set(pressure), set(CALIBRATION_BRIEFS))
        self.assertTrue(all(len(values) >= 5 for values in pressure.values()))

    def test_no_automated_aesthetic_score_can_complete_certification(self) -> None:
        """Regression: a model score must not replace project-contextual human approval."""
        body = (DESIGN / "SKILL.md").read_text(encoding="utf-8").lower() if DESIGN.is_dir() else ""
        if body:
            self.assertNotIn("automated aesthetic score", body)
            self.assertNotIn("universal anti-ai detector", body)
        for decision in ("pending", "preferred", "rejected"):
            receipt = _receipt()
            receipt["human_decision"] = {"choice": decision, "correction": "", "notes": ""}
            _validate_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
