"""Deterministic package regressions. Behavioral trials are retained separately."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'packages/expskill/skills/review/SKILL.md'

class ReviewReportingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SKILL.read_text()
        cls.body = ' '.join(cls.raw.lower().split())

    def test_report_storage_and_lifetime_are_explicit(self):
        self.assertIn('tmp/reports', self.body)
        self.assertIn('seconds', self.body)
        self.assertRegex(self.body, r'manual|manually')
        self.assertRegex(self.body, r'overwrit')
        self.assertRegex(self.body, r'outside.*repositor')
        self.assertRegex(self.body, r'ask.*access|request.*access')

    def test_ratings_use_content_and_ai_judgment(self):
        for word in ('blocking','high','medium','low','weighted','criteria','weights'):
            self.assertIn(word, self.body)
        self.assertRegex(self.body, r'0.*10')
        self.assertRegex(self.body, r'fixed.*deduct|deduct.*fixed')
        self.assertIn('ceiling', self.body)

    def test_evidence_and_revision_rules_are_present(self):
        for word in ('safeguard','structural','testing gap','pin','drift'):
            self.assertIn(word, self.body)
        self.assertRegex(self.body, r'example|scenario')
        self.assertRegex(self.body, r'out.of.scope|outside.*scope')

    def test_review_is_not_a_repair_or_delegate(self):
        self.assertIn('expskill-review', self.body)
        self.assertIn('invoking agent', self.body)
        self.assertRegex(self.body, r'(?:no|not).*subagent')
        self.assertRegex(self.body, r'(?:no|not|never).*advice|(?:do not|never).*prescrib')

    def test_instructions_stay_bounded_and_plain(self):
        self.assertLessEqual(len(self.raw.splitlines()), 220)
        self.assertNotIn('\u2014', self.raw)
        self.assertNotIn(';', self.raw)

if __name__ == '__main__':
    unittest.main()
