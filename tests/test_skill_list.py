"""Tests for flattening the master skill grid instead of reading role names."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.skill_list import flatten_skill_values


class TestFlattenSkillValues(unittest.TestCase):
    def test_skips_non_skill_cells_and_deduplicates(self):
        skills = flatten_skill_values(
            ["Django", "Docker", "PostgreSQL", "OpenCV", "Python", "Python", "", None, 12]
        )
        self.assertEqual(skills, ["Django", "Docker", "PostgreSQL", "OpenCV", "Python"])
        self.assertNotIn("Backend Developer", skills)

    def test_single_column_list_still_works(self):
        self.assertEqual(flatten_skill_values(["Python", "SQL", "Python"]), ["Python", "SQL"])


if __name__ == "__main__":
    unittest.main()
