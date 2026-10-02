"""Regression tests for persistence, enrichment and date-parsing fixes."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.posted_date import parse_posted_date

try:
    from main import _TotalsAccumulator, _persist_scraped_jobs, _process_single_role
    from pipeline.enricher import JobEnricher
    from services.supabase import SupabaseService
except ImportError:  # pragma: no cover - runtime deps missing
    _persist_scraped_jobs = None

from tests.test_scrape_resilience import _settings

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _enricher():
    enricher = JobEnricher.__new__(JobEnricher)
    enricher.skill_list = ["C++", "C#", ".NET", "Python", "Node.js", "Java"]
    return enricher


class TestPostedDate(unittest.TestCase):
    def test_board_prefixes(self):
        self.assertEqual(parse_posted_date("Posted 2 days ago", now=NOW).day, 30)
        self.assertEqual(parse_posted_date("Active 3 days ago", now=NOW).day, 29)
        self.assertEqual(parse_posted_date("Posted 30+ days ago", now=NOW).month, 9)
        self.assertEqual(parse_posted_date("Just posted", now=NOW), NOW)

    def test_absolute_month_name(self):
        self.assertEqual(parse_posted_date("Sep 30, 2026", now=NOW).date().isoformat(), "2026-09-30")


@unittest.skipIf(_persist_scraped_jobs is None, "scraper runtime deps are required")
class TestEnricher(unittest.TestCase):
    def test_salary_k_applies_per_number_only(self):
        salary = _enricher().normalize_salary("PKR 80,000 - 120,000", "Pakistan")
        self.assertEqual((salary["min"], salary["max"]), (80000, 120000))

    def test_salary_k_suffix(self):
        salary = _enricher().normalize_salary("80k - 120k", "Pakistan")
        self.assertEqual((salary["min"], salary["max"]), (80000, 120000))

    def test_salary_week_period(self):
        salary = _enricher().normalize_salary("$1500-2000 per week", "Remote")
        self.assertEqual((salary["min"], salary["period"]), (1500, "week"))

    def test_skills_with_symbols(self):
        found = _enricher().extract_skills("C++ and C# and .NET, Node.js and Python", "SE")
        flat = {s for group in found.values() for s in group}
        self.assertTrue({"C++", "C#", ".NET", "Node.js", "Python"} <= flat)
        self.assertNotIn("Java", flat)

    def test_board_job_type_wins_over_prose(self):
        self.assertEqual(
            _enricher().extract_job_type("Contract type: Permanent", "full-time"), "Full-time"
        )


@unittest.skipIf(_persist_scraped_jobs is None, "scraper runtime deps are required")
class TestPersistence(unittest.TestCase):
    def _job(self, job_id="j1", desc="A full job description long enough to persist."):
        return {"job_id": job_id, "title": "SE", "company": "Acme", "board": "indeed", "description": desc}

    def test_unchanged_job_is_touched_not_rewritten(self):
        supabase = Mock()
        supabase.get_jobs_by_ids.return_value = {
            "j1": {"job_id": "j1", "job_description": "A full job description long enough to persist.",
                   "company": "Acme", "job_title": "SE", "location": "x", "url": "u", "job_type": "t",
                   "salary_raw": "s", "posted_date": "p", "skills_required": ["x"]}
        }
        supabase.get_processed_ids.return_value = {"j1"}
        affected = _persist_scraped_jobs([self._job()], supabase, _TotalsAccumulator(), 100, {})
        self.assertEqual(affected, 0)
        supabase.touch_jobs.assert_called_once_with(["j1"])
        supabase.bulk_insert_jobs.assert_not_called()

    def test_stored_but_unprocessed_job_is_merged_not_replaced(self):
        supabase = Mock()
        long_jd = "Stored full JD " * 20
        supabase.get_jobs_by_ids.return_value = {"j1": {"job_id": "j1", "job_description": long_jd}}
        supabase.get_processed_ids.return_value = set()
        supabase.bulk_insert_jobs.side_effect = lambda batch: len(batch)
        with patch("main.job_enricher_node", side_effect=lambda state: state):
            _persist_scraped_jobs([self._job(desc="Short card text for the listing.")], supabase,
                                  _TotalsAccumulator(), 100, {})
        written = supabase.bulk_insert_jobs.call_args[0][0][0]
        self.assertEqual(written["description"], long_jd)

    def test_enrichment_error_raises_instead_of_writing(self):
        supabase = Mock()
        supabase.get_jobs_by_ids.return_value = {}
        supabase.get_processed_ids.return_value = set()
        with patch("main.job_enricher_node", return_value={"raw_job_list": [], "error": "boom"}):
            with self.assertRaises(RuntimeError):
                _persist_scraped_jobs([self._job()], supabase, _TotalsAccumulator(), 100, {})
        supabase.bulk_insert_jobs.assert_not_called()

    def test_failed_batch_is_requeued_and_retried(self):
        supabase = Mock()
        supabase.get_processed_ids.return_value = set()
        supabase.get_jobs_by_ids.side_effect = [ConnectionError("blip"), {}, {}]
        supabase.bulk_insert_jobs.side_effect = lambda batch: len(batch)
        jobs = [self._job(f"j{i}", f"A full job description number {i} long enough.") for i in range(3)]

        def fake_scrape(self, board, on_jobs=None, **_kwargs):
            try:
                on_jobs(jobs[:2])  # first flush fails; spider swallows callback errors
            except ConnectionError:
                pass
            return jobs

        totals = _TotalsAccumulator()
        with patch("main.JobScraperSpider.scrape_board", fake_scrape), patch(
            "main.job_enricher_node", side_effect=lambda state: state
        ):
            ok = _process_single_role("SE", 1, 1, _settings(job_scraping_boards=["indeed"]),
                                      supabase, totals, batch_size=2)
        self.assertTrue(ok)
        written = [job["job_id"] for call in supabase.bulk_insert_jobs.call_args_list for job in call[0][0]]
        self.assertEqual(sorted(written), ["j0", "j1", "j2"])

    def test_role_fails_when_every_board_fails(self):
        def boom(self, board, **_kwargs):
            raise RuntimeError("blocked")

        totals = _TotalsAccumulator()
        with patch("main.JobScraperSpider.scrape_board", boom):
            ok = _process_single_role("SE", 1, 1, _settings(job_scraping_boards=["indeed", "linkedin"]),
                                      Mock(), totals)
        self.assertFalse(ok)
        self.assertEqual(totals.failed_roles, ["SE"])


@unittest.skipIf(_persist_scraped_jobs is None, "scraper runtime deps are required")
class TestBulkInsertDedup(unittest.TestCase):
    def test_duplicate_ids_in_one_batch_keep_longest(self):
        service = SupabaseService.__new__(SupabaseService)
        service.client = Mock()
        jobs = [
            {"job_id": "j1", "title": "SE", "company": "A", "description": "short"},
            {"job_id": "j1", "title": "SE", "company": "A", "description": "a much longer description"},
        ]
        self.assertEqual(service.bulk_insert_jobs(jobs), 1)
        records = service.client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(len(records), 1)
        self.assertIn("much longer", records[0]["job_description"])


if __name__ == "__main__":
    unittest.main()
