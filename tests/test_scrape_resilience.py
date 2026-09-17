"""Timeouts, cooldowns, and per-board upserts for all×all scraping."""

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.settings import _parse_boards
from scraper.spider import (
    _fetch_with_scrapling,
    is_board_cooling_down,
    reset_fetch_runtime_state,
)

try:
    from main import _TotalsAccumulator, _persist_scraped_jobs, _process_single_role
except ImportError:  # pragma: no cover
    _TotalsAccumulator = None
    _persist_scraped_jobs = None
    _process_single_role = None


def _settings(**overrides):
    values = dict(
        job_scraping_fetch_timeout_seconds=0.25,
        job_scraping_fetch_retries=1,
        job_scraping_fetch_retry_delay_seconds=0.0,
        job_scraping_network_idle=False,
        job_scraping_detail_network_idle=False,
        job_scraping_fetch_isolate=False,
        job_scraping_browser_hard_timeout_seconds=0.4,
        job_scraping_board_timeout_limit=3,
        job_scraping_board_cooldown_seconds=0.4,
        job_scraping_max_concurrent_fetches=1,
        job_scraping_download_delay=0.0,
        job_scraping_max_pages_per_board=1,
        job_scraping_max_jobs_per_board=5,
        job_scraping_boards=["rozee", "indeed"],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class TestBoardOrder(unittest.TestCase):
    def test_all_puts_local_boards_first(self):
        self.assertEqual(
            _parse_boards("all"),
            ["rozee", "mustakbil", "indeed", "linkedin"],
        )


class TestFetchTimeoutRetry(unittest.TestCase):
    def setUp(self):
        reset_fetch_runtime_state()

    def tearDown(self):
        reset_fetch_runtime_state()

    def test_retries_after_timeout_then_succeeds(self):
        calls = {"n": 0}

        def fake_fetch(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                time.sleep(0.8)
                return None
            return SimpleNamespace(status=200)

        with patch("scraper.spider.get_settings", return_value=_settings()), patch(
            "scraper.spider._do_stealthy_fetch", side_effect=fake_fetch
        ):
            page = _fetch_with_scrapling("https://example.com/job", "indeed", True)

        self.assertIsNotNone(page)
        self.assertEqual(page.status, 200)
        self.assertEqual(calls["n"], 2)

    def test_cooldown_then_allows_board_again(self):
        def hang(*_args, **_kwargs):
            time.sleep(0.8)
            return None

        settings = _settings(
            job_scraping_fetch_retries=0,
            job_scraping_board_timeout_limit=2,
            job_scraping_board_cooldown_seconds=0.35,
        )
        with patch("scraper.spider.get_settings", return_value=settings), patch(
            "scraper.spider._do_stealthy_fetch", side_effect=hang
        ):
            self.assertIsNone(
                _fetch_with_scrapling("https://example.com/one", "linkedin", True)
            )
            self.assertFalse(is_board_cooling_down("linkedin"))
            self.assertIsNone(
                _fetch_with_scrapling("https://example.com/two", "linkedin", True)
            )
            self.assertTrue(is_board_cooling_down("linkedin"))
            time.sleep(0.45)
            self.assertFalse(is_board_cooling_down("linkedin"))

    def test_zero_fetch_timeout_does_not_kill_slow_page(self):
        captured = {}

        def slow_fetch(*_args, **_kwargs):
            captured["called"] = True
            return SimpleNamespace(status=200)

        settings = _settings(
            job_scraping_fetch_timeout_seconds=0,
            job_scraping_fetch_retries=0,
            job_scraping_fetch_isolate=False,
            job_scraping_browser_hard_timeout_seconds=0,
            job_scraping_detail_hard_timeout_seconds=0,
        )
        with patch("scraper.spider.get_settings", return_value=settings), patch(
            "scraper.spider._do_stealthy_fetch", side_effect=slow_fetch
        ):
            page = _fetch_with_scrapling("https://example.com/job", "indeed", True)

        self.assertIsNotNone(page)
        self.assertEqual(page.status, 200)
        self.assertTrue(captured.get("called"))


@unittest.skipIf(_process_single_role is None, "scraper runtime deps are required")
class TestPerBoardPersist(unittest.TestCase):
    def test_upserts_after_each_board(self):
        settings = _settings()
        totals = _TotalsAccumulator()
        supabase = Mock()
        supabase.is_job_processed.return_value = False
        supabase.bulk_insert_jobs.return_value = 1

        jobs_by_board = {
            "rozee": [{"job_id": "r1", "board": "rozee", "description": "Rozee listing text from the card."}],
            "indeed": [{
                "job_id": "i1",
                "board": "indeed",
                "description": "A full Indeed job description from the guest viewjob tab.",
            }],
        }

        def fake_scrape(self, board, **_kwargs):
            return jobs_by_board[board]

        with patch("main.JobScraperSpider.scrape_board", fake_scrape), patch(
            "main.job_enricher_node", side_effect=lambda state: state
        ):
            ok = _process_single_role(
                "Software Engineer", 1, 1, settings, supabase, totals
            )

        self.assertTrue(ok)
        self.assertEqual(supabase.bulk_insert_jobs.call_count, 2)
        self.assertEqual(totals.scraped, 2)
        self.assertEqual(totals.db_upserts, 2)

    def test_persist_skips_empty_board(self):
        totals = _TotalsAccumulator()
        supabase = Mock()
        affected = _persist_scraped_jobs([], supabase, totals, 200, {})
        self.assertEqual(affected, 0)
        supabase.bulk_insert_jobs.assert_not_called()

    def test_persist_updates_empty_jd_on_later_scrape(self):
        totals = _TotalsAccumulator()
        supabase = Mock()
        supabase.is_job_processed.return_value = True
        supabase.get_jobs_by_ids.return_value = {
            "j1": {"job_id": "j1", "job_description": "", "company": "Acme"}
        }
        supabase.bulk_insert_jobs.return_value = 1
        raw = [{
            "job_id": "j1",
            "title": "SE",
            "company": "Acme",
            "description": "Full JD from the later guest detail fetch.",
        }]
        with patch("main.job_enricher_node", side_effect=lambda state: state):
            affected = _persist_scraped_jobs(raw, supabase, totals, 200, {})
        self.assertEqual(affected, 1)
        supabase.bulk_insert_jobs.assert_called_once()
        written = supabase.bulk_insert_jobs.call_args[0][0]
        self.assertIn("Full JD", written[0]["description"])

    def test_persist_skips_duplicate_when_still_empty(self):
        totals = _TotalsAccumulator()
        supabase = Mock()
        supabase.is_job_processed.return_value = True
        supabase.get_jobs_by_ids.return_value = {
            "j1": {
                "job_id": "j1",
                "job_description": "",
                "company": "Acme",
                "job_title": "SE",
                "location": "",
                "url": "",
                "job_type": "",
                "salary_raw": None,
                "posted_date": None,
                "skills_required": [],
            }
        }
        raw = [{"job_id": "j1", "title": "SE", "company": "Acme", "description": ""}]
        affected = _persist_scraped_jobs(raw, supabase, totals, 200, {})
        self.assertEqual(affected, 0)
        supabase.bulk_insert_jobs.assert_not_called()

    def test_persist_reinserts_when_jobs_row_was_deleted(self):
        totals = _TotalsAccumulator()
        supabase = Mock()
        supabase.is_job_processed.return_value = True
        supabase.get_jobs_by_ids.return_value = {}
        supabase.bulk_insert_jobs.return_value = 1
        raw = [{"job_id": "j1", "title": "SE", "company": "Acme", "description": ""}]
        with patch("main.job_enricher_node", side_effect=lambda state: state):
            affected = _persist_scraped_jobs(raw, supabase, totals, 200, {})
        self.assertEqual(affected, 1)
        supabase.bulk_insert_jobs.assert_called_once()
        supabase.mark_job_processed.assert_called_once_with("j1")

    def test_persist_skips_new_indeed_card_without_jd(self):
        totals = _TotalsAccumulator()
        supabase = Mock()
        supabase.is_job_processed.return_value = False
        supabase.get_jobs_by_ids.return_value = {}
        raw = [{
            "job_id": "j1",
            "title": "SE",
            "company": "Acme",
            "board": "indeed",
            "description": "",
        }]
        affected = _persist_scraped_jobs(raw, supabase, totals, 200, {})
        self.assertEqual(affected, 0)
        supabase.bulk_insert_jobs.assert_not_called()

    def test_persist_writes_jobs_in_batches(self):
        totals = _TotalsAccumulator()
        supabase = Mock()
        supabase.is_job_processed.return_value = False
        supabase.get_jobs_by_ids.return_value = {}
        supabase.bulk_insert_jobs.side_effect = lambda batch: len(batch)
        raw = [
            {
                "job_id": "j1",
                "title": "SE",
                "company": "Acme",
                "board": "indeed",
                "description": "A full Indeed job description from the guest viewjob tab.",
            },
            {
                "job_id": "j2",
                "title": "BE",
                "company": "Beta",
                "board": "indeed",
                "description": "Another full Indeed job description from a later viewjob tab.",
            },
        ]
        with patch("main.job_enricher_node", side_effect=lambda state: state) as enrich:
            affected = _persist_scraped_jobs(raw, supabase, totals, 100, {})
        self.assertEqual(affected, 2)
        self.assertEqual(enrich.call_count, 1)
        self.assertEqual(len(enrich.call_args_list[0][0][0]["raw_job_list"]), 2)
        supabase.bulk_insert_jobs.assert_called_once()
        self.assertEqual(len(supabase.bulk_insert_jobs.call_args[0][0]), 2)
        supabase.mark_job_processed.assert_any_call("j1")
        supabase.mark_job_processed.assert_any_call("j2")


if __name__ == "__main__":
    unittest.main()
