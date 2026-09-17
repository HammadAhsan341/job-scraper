"""Tests for the full enriched job → Supabase row mapping."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.job_record import (
    experience_years,
    incoming_improves_stored,
    job_to_record,
    merge_incoming_over_stored,
)


class TestExperienceYears(unittest.TestCase):
    def test_preserves_entry_level_zero(self):
        job = {
            "experience_parsed": {
                "min_years": 0,
                "max_years": None,
                "level": "entry",
                "raw_text": "0-1 years",
            }
        }
        self.assertEqual(experience_years(job), 0)

    def test_unknown_without_years_is_none(self):
        job = {
            "experience_parsed": {
                "min_years": 0,
                "max_years": None,
                "level": "unknown",
                "raw_text": None,
            }
        }
        self.assertIsNone(experience_years(job))

    def test_falls_back_to_raw_text_digits(self):
        job = {"experience_required": "minimum 4 years"}
        self.assertEqual(experience_years(job), 4)


class TestJobToRecord(unittest.TestCase):
    def test_persists_enrichment_and_omits_raw_html(self):
        job = {
            "job_id": "abc123",
            "title": "Backend Developer",
            "description": "Build APIs with Django and PostgreSQL.",
            "skills": ["Django", "PostgreSQL"],
            "education_required": "Bachelor's",
            "employment_type": "Full-time",
            "location": "Lahore",
            "industry": None,
            "company": "Acme",
            "job_url": "https://example.com/job/1",
            "board": "indeed",
            "posted_date": "2 days ago",
            "salary": "PKR 80,000 - 120,000",
            "salary_normalized": {
                "currency": "PKR",
                "min": 80000,
                "max": 120000,
                "period": "month",
                "raw": "PKR 80,000 - 120,000",
            },
            "description_sections": {"responsibilities": "Build APIs"},
            "skills_categorized": {"technical": ["Django"], "soft": [], "tools": []},
            "experience_parsed": {
                "min_years": 3,
                "max_years": 5,
                "level": "mid",
                "raw_text": "3-5 years",
            },
            "enrichment_confidence": 0.85,
            "enrichment_timestamp": "2026-09-14T00:00:00",
            "raw_html": "<html>do not persist</html>",
        }

        record = job_to_record(job)

        self.assertEqual(record["job_id"], "abc123")
        self.assertEqual(record["job_title"], "Backend Developer")
        self.assertEqual(record["skills_required"], ["Django", "PostgreSQL"])
        self.assertEqual(record["experience_required"], 3)
        self.assertEqual(record["posted_date"], "2 days ago")
        self.assertEqual(record["salary_raw"], "PKR 80,000 - 120,000")
        self.assertEqual(record["salary_normalized"]["min"], 80000)
        self.assertEqual(record["description_sections"]["responsibilities"], "Build APIs")
        self.assertEqual(record["skills_categorized"]["technical"], ["Django"])
        self.assertEqual(record["experience_parsed"]["level"], "mid")
        self.assertEqual(record["enrichment_confidence"], 0.85)
        self.assertNotIn("raw_html", record["raw_payload"])
        self.assertEqual(record["raw_payload"]["title"], "Backend Developer")

    def test_raw_payload_drops_unserializable_objects(self):
        record = job_to_record({
            "job_id": "x",
            "title": "SE",
            "company": "Acme",
            "description": "",
            "odd": object(),
        })
        json.dumps(record)


class TestEmptyJdBackfill(unittest.TestCase):
    def test_longer_description_is_an_improvement(self):
        incoming = {"description": "A full guest job description from the detail tab."}
        stored = {"job_description": ""}
        self.assertTrue(incoming_improves_stored(incoming, stored))

    def test_same_empty_description_is_not_an_improvement(self):
        incoming = {"description": "", "company": "Unknown"}
        stored = {"job_description": "", "company": "Unknown"}
        self.assertFalse(incoming_improves_stored(incoming, stored))

    def test_does_not_overwrite_longer_stored_jd(self):
        incoming = {"description": "short", "company": "Unknown"}
        stored = {"job_description": "A much longer stored job description.", "company": "Acme"}
        merged = merge_incoming_over_stored(incoming, stored)
        self.assertEqual(merged["description"], stored["job_description"])
        self.assertEqual(merged["company"], "Acme")

    def test_fills_unknown_company(self):
        incoming = {"description": "", "company": "KalSoft"}
        stored = {"job_description": "", "company": "Unknown"}
        self.assertTrue(incoming_improves_stored(incoming, stored))


if __name__ == "__main__":
    unittest.main()
