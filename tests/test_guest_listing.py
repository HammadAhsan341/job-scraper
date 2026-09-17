"""Guest listing cards, auth-wall detection, and detail merge."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.boards.indeed import IndeedParser
from scraper.boards.linkedin import LinkedInParser
from scraper.boards.mustakbil import MustakbilParser
from scraper.fetch_worker import _selftest_parsed_job, _selftest_quick, _selftest_sleep, _stealthy_fetch
from scraper.guest import (
    destination_job_url,
    is_auth_wall,
    jsonld_job_description,
    merge_listing_with_detail,
    meta_refresh_url,
)
from scraper.spider import (
    JobScraperSpider,
    _fetch_isolated,
    _fetch_with_scrapling,
    reset_fetch_runtime_state,
)


class TextNodes:
    def __init__(self, texts):
        self._texts = texts

    def getall(self):
        return self._texts

    def get(self):
        return self._texts[0] if self._texts else None


class FakeEl:
    def __init__(self, text="", href="", children=None, attrib=None):
        self.text = text
        self.attrib = dict(attrib or {})
        if href:
            self.attrib["href"] = href
        self._children = children or {}

    def css(self, selector):
        if selector == "::text":
            return TextNodes([self.text] if self.text else [])
        return self._children.get(selector, [])


class FakeResponse:
    def __init__(self, url="", html="", by_selector=None):
        self.url = url
        self.body = html
        self._by_selector = by_selector or {}

    def css(self, selector):
        if selector == "::text":
            return TextNodes([])
        return self._by_selector.get(selector, [])


class TestAuthWall(unittest.TestCase):
    def test_indeed_auth_url(self):
        response = SimpleNamespace(
            url="https://secure.indeed.com/auth?from=bot-detection-anonymous",
            body="",
        )
        self.assertTrue(is_auth_wall(response, "https://pk.indeed.com/viewjob?jk=abc"))

    def test_linkedin_authwall_html(self):
        response = SimpleNamespace(
            url="https://www.linkedin.com/authwall?sessionRedirect=/jobs/view/1",
            body="<html>join linkedin</html>",
        )
        self.assertTrue(is_auth_wall(response))

    def test_normal_job_page_is_not_wall(self):
        response = SimpleNamespace(
            url="https://pk.indeed.com/viewjob?jk=abc",
            body="<html><h1>Software Engineer</h1></html>",
        )
        self.assertFalse(is_auth_wall(response))

    def test_guest_search_page_with_sign_in_nav_is_not_wall(self):
        response = SimpleNamespace(
            url="https://pk.indeed.com/jobs?q=Software+Engineer",
            body="<html>Sign in to Indeed to save jobs</html>",
        )
        self.assertFalse(is_auth_wall(response))


class TestMerge(unittest.TestCase):
    def test_keeps_listing_url_and_longer_description(self):
        listing = {
            "title": "SE",
            "company": "Acme",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "description": "short",
        }
        detail = {
            "title": "SE",
            "company": "Acme Inc",
            "job_url": "https://secure.indeed.com/auth",
            "description": "A much longer guest job description for this role.",
        }
        merged = merge_listing_with_detail(listing, detail)
        self.assertEqual(merged["job_url"], listing["job_url"])
        self.assertEqual(merged["company"], "Acme Inc")
        self.assertIn("longer", merged["description"])


class TestJsonLdDescription(unittest.TestCase):
    def test_reads_jobposting_html(self):
        html = '''
        <script type="application/ld+json">
        {"@type":"JobPosting","description":"<p>Build APIs with Django and PostgreSQL in Lahore.</p>"}
        </script>
        '''
        text = jsonld_job_description(html)
        self.assertIn("Build APIs", text)
        self.assertNotIn("<p>", text)


class TestIndeedCards(unittest.TestCase):
    def test_viewjob_url_from_jk(self):
        self.assertEqual(
            IndeedParser.viewjob_url("/rc/clk?jk=ae59229661c2e928&from=serp"),
            "https://pk.indeed.com/viewjob?jk=ae59229661c2e928",
        )

    def test_parse_listing_saves_card_and_returns_url(self):
        title = FakeEl("Software Engineer", href="/rc/clk?jk=abc123")
        company = FakeEl("KalSoft")
        location = FakeEl("Lahore")
        card = FakeEl(
            children={
                "a.jcs-JobTitle": [title],
                "[data-testid='company-name']": [company],
                "[data-testid='text-location']": [location],
            }
        )
        response = FakeResponse(
            url="https://pk.indeed.com/jobs?q=Software+Engineer",
            by_selector={"div.job_seen_beacon": [card]},
        )
        parser = IndeedParser()
        urls = parser.parse_listing(response)
        self.assertEqual(urls, ["https://pk.indeed.com/viewjob?jk=abc123"])
        self.assertEqual(len(parser._listing_jobs), 1)
        self.assertEqual(parser._listing_jobs[0]["title"], "Software Engineer")
        self.assertEqual(parser._listing_jobs[0]["company"], "KalSoft")


class TestLinkedInCards(unittest.TestCase):
    def test_parse_listing_saves_card(self):
        link = FakeEl("Software Engineer", href="https://www.linkedin.com/jobs/view/12345?refId=x")
        title = FakeEl("Software Engineer")
        company = FakeEl("Acme")
        location = FakeEl("Karachi, Pakistan")
        card = FakeEl(
            children={
                "h3.base-search-card__title": [title],
                "a.base-card__full-link": [link],
                "h4.base-search-card__subtitle": [company],
                "span.job-search-card__location": [location],
            }
        )
        response = FakeResponse(
            url="https://www.linkedin.com/jobs/search/",
            by_selector={"div.base-card": [card]},
        )
        parser = LinkedInParser()
        urls = parser.parse_listing(response)
        self.assertEqual(urls, ["https://www.linkedin.com/jobs/view/12345"])
        self.assertEqual(parser._listing_jobs[0]["company"], "Acme")


class TestMustakbilSearchUrl(unittest.TestCase):
    def test_query_is_in_url(self):
        url = MustakbilParser().build_search_url("Software Engineer", "Pakistan", 1)
        self.assertIn("keywords=Software%20Engineer", url)
        self.assertIn("mustakbil.com/jobs/search", url)


class TestSpiderKeepsListingOnAuthWall(unittest.TestCase):
    def test_auth_wall_keeps_card(self):
        parser = IndeedParser()
        listing_job = {
            "job_id": "x",
            "title": "Software Engineer",
            "company": "Acme",
            "location": "Lahore",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "board": "indeed",
            "description": "snippet",
            "skills": [],
        }

        listing_response = SimpleNamespace(url="https://pk.indeed.com/jobs", status=200, body="<html>jobs</html>")
        auth_response = SimpleNamespace(
            url="https://secure.indeed.com/auth?from=bot-detection-anonymous",
            status=200,
            body="login",
        )

        spider = JobScraperSpider()
        spider.parsers["indeed"] = parser

        def fake_parse(_response):
            parser._listing_jobs = [dict(listing_job)]
            return [listing_job["job_url"]]

        with patch("scraper.spider._fetch_with_scrapling") as fetch:
            fetch.side_effect = [listing_response, auth_response]
            with patch.object(parser, "parse_listing", side_effect=fake_parse):
                with patch.object(parser, "build_search_url", return_value="https://pk.indeed.com/jobs"):
                    jobs = spider.scrape_board("indeed", "Software Engineer", max_pages=1, max_jobs=5)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_url"], listing_job["job_url"])
        self.assertEqual(jobs[0]["title"], "Software Engineer")


class TestDestinationUrl(unittest.TestCase):
    def test_meta_refresh(self):
        html = '<meta http-equiv="refresh" content="0; url=https://pk.indeed.com/viewjob?jk=abc">'
        self.assertEqual(
            meta_refresh_url(html, "https://pk.indeed.com/jobs"),
            "https://pk.indeed.com/viewjob?jk=abc",
        )

    def test_continue2_from_auth_url(self):
        response = SimpleNamespace(
            url="https://secure.indeed.com/auth?continue2=https%3A%2F%2Fpk.indeed.com%2Fviewjob%3Fjk%3Dabc&from=bot-detection-anonymous",
            body="login",
        )
        self.assertEqual(
            destination_job_url(response, "https://pk.indeed.com/viewjob?jk=abc"),
            "https://pk.indeed.com/viewjob?jk=abc",
        )

    def test_linkedin_session_redirect(self):
        response = SimpleNamespace(
            url="https://www.linkedin.com/authwall?sessionRedirect=/jobs/view/12345",
            body="",
        )
        self.assertEqual(
            destination_job_url(response),
            "https://www.linkedin.com/jobs/view/12345",
        )


class TestSpiderFollowsDestination(unittest.TestCase):
    def test_follow_continue2_enriches_card(self):
        parser = IndeedParser()
        listing_job = {
            "job_id": "x",
            "title": "Software Engineer",
            "company": "Acme",
            "location": "Lahore",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "board": "indeed",
            "description": "snippet",
            "skills": [],
        }
        listing_response = SimpleNamespace(
            url="https://pk.indeed.com/jobs", status=200, body="<html>jobs</html>"
        )
        auth_response = SimpleNamespace(
            url="https://secure.indeed.com/auth?continue2=https%3A%2F%2Fpk.indeed.com%2Fviewjob%3Fjk%3Dabc&from=bot-detection-anonymous",
            status=200,
            body="login",
        )
        detail_response = SimpleNamespace(
            url="https://pk.indeed.com/viewjob?jk=abc",
            status=200,
            body="<html><h1>Software Engineer</h1></html>",
        )
        spider = JobScraperSpider()
        spider.parsers["indeed"] = parser
        spider.settings = SimpleNamespace(
            job_scraping_detail_network_idle=True,
            job_scraping_download_delay=0,
        )

        def fake_parse(_response):
            parser._listing_jobs = [dict(listing_job)]
            return [listing_job["job_url"]]

        def fake_parse_job(response):
            if "viewjob" in (response.url or "") and "auth" not in (response.url or ""):
                return {**listing_job, "description": "A full guest job description from the idle tab for this Software Engineer role."}
            return None

        with patch("scraper.spider._fetch_with_scrapling") as fetch:
            fetch.side_effect = [listing_response, auth_response, detail_response]
            with patch.object(parser, "parse_listing", side_effect=fake_parse):
                with patch.object(parser, "parse_job", side_effect=fake_parse_job):
                    with patch.object(parser, "build_search_url", return_value="https://pk.indeed.com/jobs"):
                        jobs = spider.scrape_board("indeed", "Software Engineer", max_pages=1, max_jobs=5)

        self.assertEqual(len(jobs), 1)
        self.assertIn("full guest", jobs[0]["description"])
        self.assertEqual(fetch.call_count, 3)


class TestFetchIsolate(unittest.TestCase):
    def setUp(self):
        reset_fetch_runtime_state()

    def tearDown(self):
        reset_fetch_runtime_state()

    def test_isolated_success(self):
        page = _fetch_isolated(
            "https://example.test/viewjob?jk=1",
            "indeed",
            True,
            1000,
            False,
            2.0,
            worker=_selftest_quick,
        )
        self.assertIsNotNone(page)
        self.assertEqual(page.status, 200)
        self.assertIn("ok", page.body)

    def test_isolated_hard_timeout_kills(self):
        started = __import__("time").monotonic()
        page = _fetch_isolated(
            "https://example.test/viewjob?jk=1",
            "indeed",
            True,
            1000,
            False,
            1.0,
            worker=_selftest_sleep,
        )
        elapsed = __import__("time").monotonic() - started
        self.assertIsNone(page)
        self.assertLess(elapsed, 12)

    def test_isolated_detail_attaches_live_parsed_job(self):
        page = _fetch_isolated(
            "https://pk.indeed.com/viewjob?jk=1",
            "indeed",
            True,
            0,
            True,
            2.0,
            worker=_selftest_parsed_job,
            parse_job=True,
        )
        self.assertIsNotNone(page)
        self.assertIn("full guest", page.parsed_job["description"])


class TestReferenceJobTabFetch(unittest.TestCase):
    def setUp(self):
        reset_fetch_runtime_state()

    def tearDown(self):
        reset_fetch_runtime_state()

    def test_detail_omits_scrapling_timeout_and_waits_idle(self):
        captured = {}

        def fake_fetch(url, board, headless, timeout_ms, network_idle):
            captured["timeout_ms"] = timeout_ms
            captured["network_idle"] = network_idle
            return SimpleNamespace(status=200, url=url, html_content="<html/>")

        settings = SimpleNamespace(
            job_scraping_fetch_timeout_seconds=60.0,
            job_scraping_fetch_retries=0,
            job_scraping_fetch_retry_delay_seconds=0.0,
            job_scraping_network_idle=False,
            job_scraping_fetch_isolate=False,
            job_scraping_browser_hard_timeout_seconds=90.0,
            job_scraping_detail_hard_timeout_seconds=180.0,
            job_scraping_board_timeout_limit=3,
            job_scraping_board_cooldown_seconds=0.0,
            job_scraping_max_concurrent_fetches=1,
        )
        with patch("scraper.spider.get_settings", return_value=settings), patch(
            "scraper.spider._do_stealthy_fetch", side_effect=fake_fetch
        ):
            page = _fetch_with_scrapling(
                "https://pk.indeed.com/viewjob?jk=1",
                "indeed",
                True,
                job_detail=True,
            )

        self.assertIsNotNone(page)
        self.assertEqual(captured["timeout_ms"], 0)
        self.assertTrue(captured["network_idle"])

    def test_stealthy_helper_omits_timeout_kwarg_when_zero(self):
        captured = {}

        class FakeFetcher:
            @staticmethod
            def fetch(url, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(status=200, url=url)

        fake_mod = SimpleNamespace(StealthyFetcher=FakeFetcher)
        with patch.dict(sys.modules, {"scrapling": fake_mod}):
            _stealthy_fetch("https://pk.indeed.com/viewjob?jk=1", "indeed", True, 0, True)

        self.assertTrue(captured["network_idle"])
        self.assertNotIn("timeout", captured)

    def test_scrape_uses_live_parsed_job_instead_of_empty_html(self):
        parser = IndeedParser()
        listing_job = {
            "job_id": "x",
            "title": "Software Engineer",
            "company": "Acme",
            "location": "Lahore",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "board": "indeed",
            "description": "",
            "skills": [],
        }
        listing_response = SimpleNamespace(
            url="https://pk.indeed.com/jobs", status=200, body="<html>jobs</html>"
        )
        detail_response = SimpleNamespace(
            url="https://pk.indeed.com/viewjob?jk=abc",
            status=200,
            body="<html></html>",
            parsed_job={
                **listing_job,
                "description": "A full guest job description parsed on the live Indeed viewjob tab.",
            },
        )
        spider = JobScraperSpider()
        spider.parsers["indeed"] = parser
        spider.settings = SimpleNamespace(
            job_scraping_detail_network_idle=True,
            job_scraping_download_delay=0,
            job_scraping_max_detail_fetches=0,
        )

        def fake_parse(_response):
            parser._listing_jobs = [dict(listing_job)]
            return [listing_job["job_url"]]

        with patch("scraper.spider._fetch_with_scrapling") as fetch:
            fetch.side_effect = [listing_response, detail_response]
            with patch.object(parser, "parse_listing", side_effect=fake_parse):
                with patch.object(parser, "parse_job", return_value=None) as parse_job:
                    with patch.object(parser, "build_search_url", return_value="https://pk.indeed.com/jobs"):
                        jobs = spider.scrape_board(
                            "indeed", "Software Engineer", max_pages=1, max_jobs=5
                        )

        self.assertEqual(len(jobs), 1)
        self.assertIn("live Indeed", jobs[0]["description"])
        parse_job.assert_not_called()

    def test_emits_each_enriched_job_immediately(self):
        parser = IndeedParser()
        cards = [
            {
                "job_id": "j1",
                "title": "Software Engineer",
                "company": "Acme",
                "location": "Lahore",
                "job_url": "https://pk.indeed.com/viewjob?jk=aaa",
                "board": "indeed",
                "description": "",
                "skills": [],
            },
            {
                "job_id": "j2",
                "title": "Backend Engineer",
                "company": "Beta",
                "location": "Karachi",
                "job_url": "https://pk.indeed.com/viewjob?jk=bbb",
                "board": "indeed",
                "description": "",
                "skills": [],
            },
        ]
        listing_response = SimpleNamespace(
            url="https://pk.indeed.com/jobs", status=200, body="<html>jobs</html>"
        )
        details = [
            SimpleNamespace(
                url=card["job_url"],
                status=200,
                body="<html></html>",
                parsed_job={
                    **card,
                    "description": f"A full guest job description for {card['title']} at {card['company']}.",
                },
            )
            for card in cards
        ]
        spider = JobScraperSpider()
        spider.parsers["indeed"] = parser
        spider.settings = SimpleNamespace(
            job_scraping_detail_network_idle=True,
            job_scraping_download_delay=0,
            job_scraping_max_detail_fetches=0,
        )
        emitted = []

        def fake_parse(_response):
            parser._listing_jobs = [dict(card) for card in cards]
            return [card["job_url"] for card in cards]

        with patch("scraper.spider._fetch_with_scrapling") as fetch:
            fetch.side_effect = [listing_response, *details]
            with patch.object(parser, "parse_listing", side_effect=fake_parse):
                with patch.object(parser, "parse_job", return_value=None):
                    with patch.object(parser, "build_search_url", return_value="https://pk.indeed.com/jobs"):
                        spider.scrape_board(
                            "indeed",
                            "Software Engineer",
                            max_pages=1,
                            max_jobs=5,
                            on_jobs=lambda batch: emitted.append(list(batch)),
                        )

        self.assertEqual(len(emitted), 2)
        self.assertEqual([len(batch) for batch in emitted], [1, 1])
        self.assertEqual(emitted[0][0]["job_id"], "j1")
        self.assertEqual(emitted[1][0]["job_id"], "j2")
        self.assertIn("full guest", emitted[0][0]["description"])


if __name__ == "__main__":
    unittest.main()
