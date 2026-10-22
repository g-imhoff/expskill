"""Deterministic package regressions. Behavioral trials are retained separately."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'plugins/expskill/content/skills/review/SKILL.md'

class ReviewReportingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SKILL.read_text()

    def test_report_storage_directory_is_named(self):
        self.assertIn('dot expskill', self.raw)
        self.assertIn('skills', self.raw)
        self.assertNotIn('tmp/reports', self.raw)

    def test_review_names_its_reviewer_profile(self):
        self.assertIn('review', self.raw)

    def test_review_names_the_severity_labels(self):
        for label in ('`BLOCKING`', '`HIGH`', '`MEDIUM`', '`LOW`'):
            with self.subTest(label=label):
                self.assertIn(label, self.raw)

    def test_instructions_stay_bounded_and_punctuation_clean(self):
        self.assertLessEqual(len(self.raw.splitlines()), 220)
        self.assertNotIn('\u2014', self.raw)
        self.assertNotIn(';', self.raw)

if __name__ == '__main__':
    unittest.main()
