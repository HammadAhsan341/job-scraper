"""Fetch one listing page per board and report why GitHub vs local differs."""

from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.boards import IndeedParser, LinkedInParser, MustakbilParser, RozeeParser
from scraper.spider import _fetch_with_scrapling, reset_fetch_runtime_state


QUERY = "Software Engineer"
LOCATION = "Pakistan"
BOARDS = [
    ("rozee", RozeeParser()),
    ("mustakbil", MustakbilParser()),
    ("indeed", IndeedParser()),
    ("linkedin", LinkedInParser()),
]


def _snippet(response) -> str:
    html = ""
    for attr in ("html_content", "body", "text"):
        value = getattr(response, attr, None)
        if value:
            html = str(value)
            break
    if not html and response is not None:
        html = str(response)[:2000]
    lower = html.lower()
    flags = []
    for needle in (
        "cloudflare",
        "attention required",
        "captcha",
        "unusual traffic",
        "enable javascript",
        "sign in",
        "authwall",
        "challenge",
    ):
        if needle in lower:
            flags.append(needle)
    return f"len={len(html)} flags={flags or ['none']} preview={html[:180]!r}"


def main() -> int:
    reset_fetch_runtime_state()
    print(f"CI={os.getenv('CI')!r}", flush=True)
    print(f"timeout={os.getenv('JOB_SCRAPING_FETCH_TIMEOUT_SECONDS')}", flush=True)
    print(f"query={QUERY!r} location={LOCATION!r}\n", flush=True)

    results = []
    for board, parser in BOARDS:
        url = parser.build_search_url(QUERY, LOCATION, 1)
        print("=" * 72, flush=True)
        print(f"BOARD {board}", flush=True)
        print(f"URL {url}", flush=True)
        started = time.time()
        try:
            response = _fetch_with_scrapling(url, board, True)
            elapsed = time.time() - started
            if response is None:
                print(f"RESULT none after {elapsed:.1f}s", flush=True)
                results.append((board, "none", elapsed, 0))
                continue
            status = getattr(response, "status", "?")
            urls = parser.parse_listing(response) or []
            listing_jobs = getattr(parser, "_listing_jobs", None) or []
            print(f"STATUS {status} in {elapsed:.1f}s", flush=True)
            print(f"JOB_URLS {len(urls)} LISTING_JOBS {len(listing_jobs)}", flush=True)
            print(_snippet(response), flush=True)
            results.append((board, str(status), elapsed, len(urls) or len(listing_jobs)))
        except Exception as exc:
            elapsed = time.time() - started
            print(f"ERROR after {elapsed:.1f}s: {exc}", flush=True)
            traceback.print_exc()
            results.append((board, f"error:{type(exc).__name__}", elapsed, 0))

    print("\n" + "=" * 72, flush=True)
    print("SUMMARY", flush=True)
    for board, status, elapsed, n in results:
        print(f"  {board:10} status={status:16} {elapsed:6.1f}s jobs={n}", flush=True)
    return 0 if any(n > 0 for *_, n in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
