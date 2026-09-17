"""Killable child-process worker for one StealthyFetcher page load.

A wedged Chromium cannot be cancelled from a Python thread. The parent starts
this worker with ``spawn``, waits a hard timeout, then tree-kills the browser.
The value crossing the process boundary is a plain dict.

Job-tab fetches parse the JD on the live Scrapling page before pickling HTML.
That matches muhammadhaider02/Scrapling-Job-Boards-Scrapper: wait for network
idle, then css() the real response — not a reconstructed snapshot.
"""

from __future__ import annotations

import logging
from typing import Any


def _parser_for(board: str):
    from scraper.boards.indeed import IndeedParser
    from scraper.boards.linkedin import LinkedInParser
    from scraper.boards.mustakbil import MustakbilParser
    from scraper.boards.rozee import RozeeParser

    parsers = {
        "indeed": IndeedParser,
        "linkedin": LinkedInParser,
        "mustakbil": MustakbilParser,
        "rozee": RozeeParser,
    }
    cls = parsers.get((board or "").lower())
    return cls() if cls else None


def _stealthy_fetch(
    url: str,
    board: str,
    headless: bool,
    timeout_ms: int,
    network_idle: bool,
):
    """Same kwargs as the reference scraper; omit timeout when it is 0."""
    from scrapling import StealthyFetcher

    kwargs: dict[str, Any] = {
        "headless": headless,
        "network_idle": network_idle,
        "solve_cloudflare": True,
        "google_search": (board == "mustakbil"),
    }
    if timeout_ms and timeout_ms > 0:
        kwargs["timeout"] = timeout_ms
    return StealthyFetcher.fetch(url, **kwargs)


def run_fetch(
    url: str,
    board: str,
    headless: bool,
    timeout_ms: int,
    network_idle: bool,
    parse_job: bool = False,
) -> dict[str, Any]:
    """Fetch one URL and return picklable html/url/status, plus a parsed job."""
    scrapling_log = logging.getLogger("scrapling")
    scrapling_log.handlers.clear()
    scrapling_log.addHandler(logging.NullHandler())
    scrapling_log.setLevel(logging.CRITICAL)
    scrapling_log.propagate = False

    page = _stealthy_fetch(url, board, headless, timeout_ms, network_idle)
    if page is None:
        return {"error": "Scrapling returned empty response"}

    html = getattr(page, "html_content", None) or getattr(page, "body", None) or ""
    result: dict[str, Any] = {
        "final_url": str(getattr(page, "url", None) or url),
        "status": getattr(page, "status", None),
        "html": str(html),
        "job": None,
        "error": None,
    }
    if parse_job:
        try:
            parser = _parser_for(board)
            job = parser.parse_job(page) if parser else None
            if job:
                job = dict(job)
                job.pop("raw_html", None)
            result["job"] = job
        except Exception as exc:  # noqa: BLE001
            result["parse_error"] = f"{type(exc).__name__}: {exc}"
    return result


def worker_entry(
    q: Any,
    url: str,
    board: str,
    headless: bool,
    timeout_ms: int,
    network_idle: bool,
    parse_job: bool = False,
) -> None:
    try:
        q.put(run_fetch(url, board, headless, timeout_ms, network_idle, parse_job))
    except Exception as exc:  # noqa: BLE001
        q.put({"error": f"{type(exc).__name__}: {exc}"})


def _selftest_sleep(q: Any, *_args: Any) -> None:
    import time

    time.sleep(30)


def _selftest_quick(q: Any, *_args: Any) -> None:
    q.put(
        {
            "final_url": "https://example.test/viewjob?jk=1",
            "status": 200,
            "html": "<html><title>ok</title></html>",
            "job": None,
            "error": None,
        }
    )


def _selftest_parsed_job(q: Any, *_args: Any) -> None:
    q.put(
        {
            "final_url": "https://pk.indeed.com/viewjob?jk=1",
            "status": 200,
            "html": "<html><title>ok</title></html>",
            "job": {
                "job_id": "j1",
                "title": "Software Engineer",
                "company": "Acme",
                "location": "Lahore",
                "job_url": "https://pk.indeed.com/viewjob?jk=1",
                "board": "indeed",
                "description": "A full guest job description parsed on the live viewjob tab.",
            },
            "error": None,
        }
    )
