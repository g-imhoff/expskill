from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "plugins/expskill/content/skills/test/scripts/execution_budget.py"
SPEC = importlib.util.spec_from_file_location("audit_test_execution_budget", SOURCE)
assert SPEC is not None and SPEC.loader is not None
BUDGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUDGET)


class ExecutionBudgetTests(unittest.TestCase):
    def budget(self) -> dict[str, object]:
        return {
            "semantic_actions_max": "10", "waves_max": "2",
            "usable_budget_seconds": "1200", "rationale": "Ten required consumers.",
            "waves": [[f"action-{index}" for index in range(5)], [f"action-{index}" for index in range(5, 10)]],
        }

    def test_required_scope_and_explicit_budget_are_validated_together(self) -> None:
        value = self.budget()
        self.assertEqual(BUDGET.validate(value, [{"required_action_ids": ["action-9"]}]), value)
        with self.assertRaisesRegex(ValueError, "omits required"):
            BUDGET.validate(value, [{"required_action_ids": ["missing"]}])

    def test_hard_ceilings_and_numeric_encoding_cannot_be_bypassed(self) -> None:
        for field, invalid in (("semantic_actions_max", "65"), ("waves_max", "17"), ("usable_budget_seconds", "3601"), ("semantic_actions_max", 10)):
            with self.subTest(field=field, value=invalid):
                value = self.budget()
                value[field] = invalid
                with self.assertRaises(ValueError):
                    BUDGET.validate(value, [])

    def test_execution_must_follow_declared_wave_order(self) -> None:
        value = self.budget()
        BUDGET.validate_actions(value, ["action-1", "action-0", "action-9"])
        with self.assertRaisesRegex(ValueError, "wave order"):
            BUDGET.validate_actions(value, ["action-9", "action-0"])
        with self.assertRaisesRegex(ValueError, "absent"):
            BUDGET.validate_actions(value, ["unplanned"])


if __name__ == "__main__":
    unittest.main()
