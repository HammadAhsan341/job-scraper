"""Indeed results-tab click-through: one page load yields every card's JD."""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.boards.indeed import IndeedParser
from scraper.boards.indeed_pane_clicks import (
    RESULT_SCRIPT_ID,
    _description_from,
    collect_indeed_pane_jds,
    extract_clicked_pane_jds,
)
from tests.test_guest_listing import FakeEl, FakeResponse

JD_HTML = "<div><p>Build data pipelines.</p><p>Python, SQL.</p></div>"


def _payload(desc=JD_HTML, status="success"):
    return {"status": status, "body": {"jobInfoWrapperModel": {"jobInfoModel": {"sanitizedJobDescription": desc}}}}


class FakeRawResponse:
    def __init__(self, url, payload):
        self.url = url
        self._payload = payload

    def json(self):
        return self._payload


class FakeCard:
    def __init__(self, page, jk):
        self.page, self.jk = page, jk

    def get_attribute(self, name):
        return self.jk

    def is_visible(self):
        return not self.jk.startswith("hidden")

    def click(self, timeout=None):
        self.page.clicked.append(self.jk)


class FakeExpect:
    def __init__(self, page, predicate):
        self.page, self.predicate = page, predicate

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        jk = self.page.clicked[-1]
        raw = FakeRawResponse(
            f"https://pk.indeed.com/viewjob?jk={jk}&from=vjs&viewtype=embedded",
            self.page.payloads[jk],
        )
        assert self.predicate(raw)
        self.value = raw
        return False


class FakeLocator:
    def __init__(self, page):
        self.page = page

    def count(self):
        return len(self.page.jks)

    def nth(self, i):
        return FakeCard(self.page, self.page.jks[i])


class FakePage:
    def __init__(self, payloads):
        self.payloads = payloads
        self.jks = list(payloads)
        self.clicked = []
        self.injected = None

    def locator(self, selector):
        return FakeLocator(self)

    def expect_response(self, predicate, timeout=None):
        return FakeExpect(self, predicate)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script, args):
        self.injected = args


class TestCollect(unittest.TestCase):
    def test_clicks_each_card_and_injects_jds(self):
        page = FakePage({"aaa111": _payload(), "bbb222": _payload("<p>Second &amp; job</p>")})
        collect_indeed_pane_jds(page)
        self.assertEqual(page.clicked, ["aaa111", "bbb222"])
        script_id, data = page.injected
        self.assertEqual(script_id, RESULT_SCRIPT_ID)
        self.assertEqual(json.loads(data)["jds"]["bbb222"], "<p>Second &amp; job</p>")

    def test_hidden_decoy_card_is_never_clicked(self):
        page = FakePage({"hidden01": _payload(), "aaa111": _payload()})
        collect_indeed_pane_jds(page)
        self.assertEqual(page.clicked, ["aaa111"])
        self.assertEqual(json.loads(page.injected[1])["hidden"], ["hidden01"])

    def test_stops_after_consecutive_walled_responses(self):
        walled = {f"j{i}": _payload(status="error") for i in range(6)}
        page = FakePage(walled)
        collect_indeed_pane_jds(page)
        self.assertEqual(len(page.clicked), 3)
        self.assertEqual(json.loads(page.injected[1])["jds"], {})

    def test_script_close_tag_in_jd_is_escaped(self):
        page = FakePage({"aaa111": _payload("<p>x</script><b>y</b></p>")})
        collect_indeed_pane_jds(page)
        self.assertNotIn("</script>", page.injected[1])
        html = f'<script type="application/json" id="{RESULT_SCRIPT_ID}">{page.injected[1]}</script>'
        self.assertEqual(extract_clicked_pane_jds(html)["aaa111"], "<p>x</script><b>y</b></p>")


class TestExtract(unittest.TestCase):
    def test_missing_script_means_no_clicks(self):
        self.assertEqual(extract_clicked_pane_jds("<html></html>"), {})

    def test_description_requires_success(self):
        self.assertEqual(_description_from(_payload(status="error")), "")
        self.assertEqual(_description_from(_payload()), JD_HTML)


def _card(jk):
    return FakeEl(
        children={
            "a.jcs-JobTitle": [FakeEl("Data Engineer", href=f"/rc/clk?jk={jk}")],
            "[data-testid='company-name']": [FakeEl("Acme")],
            "[data-testid='text-location']": [FakeEl("Lahore")],
        }
    )


def _listing(cards, results):
    script = json.dumps(results)
    return FakeResponse(
        url="https://pk.indeed.com/jobs?q=Data+Engineer",
        html=f'<html><script type="application/json" id="{RESULT_SCRIPT_ID}">{script}</script></html>',
        by_selector={"div.job_seen_beacon": cards},
    )


class TestListingAttachesJds(unittest.TestCase):
    def test_card_gets_clicked_jd_as_text(self):
        parser = IndeedParser()
        parser.parse_listing(_listing([_card("abc123")], {"jds": {"abc123": JD_HTML}, "hidden": []}))
        self.assertEqual(parser._listing_jobs[0]["description"], "Build data pipelines. Python, SQL.")

    def test_hidden_decoy_card_is_dropped(self):
        parser = IndeedParser()
        urls = parser.parse_listing(
            _listing([_card("abc123"), _card("def456")], {"jds": {}, "hidden": ["def456"]})
        )
        self.assertEqual(urls, ["https://pk.indeed.com/viewjob?jk=abc123"])
        self.assertEqual([j["job_url"] for j in parser._listing_jobs], urls)


class TestWorkerWiring(unittest.TestCase):
    def _kwargs(self, url):
        import scraper.fetch_worker as fw

        captured = {}

        class FakeFetcher:
            @staticmethod
            def fetch(u, **kwargs):
                captured.update(kwargs)
                return "ok"

        with patch.dict(sys.modules, {"scrapling": type(sys)("scrapling")}):
            sys.modules["scrapling"].StealthyFetcher = FakeFetcher
            fw._stealthy_fetch(url, "indeed", True, 0, True)
        return captured

    def test_listing_page_clicks_cards_with_resources_loaded(self):
        kw = self._kwargs("https://pk.indeed.com/jobs?q=x&l=Pakistan")
        self.assertIs(kw["page_action"], collect_indeed_pane_jds)
        self.assertNotIn("disable_resources", kw)

    def test_pane_url_does_not_click(self):
        kw = self._kwargs("https://pk.indeed.com/jobs?q=x&l=Pakistan&vjk=abc")
        self.assertNotIn("page_action", kw)


if __name__ == "__main__":
    unittest.main()
