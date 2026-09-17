"""Live guest scrape of all four boards for a report."""

from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.guest import is_auth_wall
from scraper.spider import JobScraperSpider, reset_fetch_runtime_state


QUERY = "Software Engineer"
LOCATION = "Pakistan"
BOARDS = ["rozee", "mustakbil", "indeed", "linkedin"]


def main() -> int:
    reset_fetch_runtime_state()
    spider = JobScraperSpider()
    report = []

    for board in BOARDS:
        print("\n" + "#" * 72, flush=True)
        print(f"TEST {board.upper()}", flush=True)
        started = time.time()
        try:
            jobs = spider.scrape_board(
                board=board,
                query=QUERY,
                location=LOCATION,
                max_pages=1,
                max_jobs=5,
            )
            elapsed = time.time() - started
            with_desc = sum(1 for j in jobs if (j.get("description") or "").strip())
            long_desc = sum(1 for j in jobs if len(j.get("description") or "") >= 80)
            sample = [
                {
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "location": j.get("location"),
                    "url": j.get("job_url"),
                    "desc_len": len(j.get("description") or ""),
                }
                for j in jobs[:3]
            ]
            row = SimpleNamespace(
                board=board,
                ok=True,
                elapsed=elapsed,
                jobs=len(jobs),
                with_desc=with_desc,
                long_desc=long_desc,
                error="",
                sample=sample,
            )
        except Exception as exc:
            elapsed = time.time() - started
            row = SimpleNamespace(
                board=board,
                ok=False,
                elapsed=elapsed,
                jobs=0,
                with_desc=0,
                long_desc=0,
                error=str(exc),
                sample=[],
            )
        report.append(row)
        print(
            f"RESULT {board}: jobs={row.jobs} snippets={row.with_desc} "
            f"fullish={row.long_desc} {row.elapsed:.1f}s {row.error}",
            flush=True,
        )

    print("\n" + "=" * 72, flush=True)
    print("PLATFORM REPORT", flush=True)
    print(f"Query: {QUERY} / {LOCATION} / max 5 jobs / 1 page", flush=True)
    for row in report:
        status = "PASS" if row.ok and row.jobs > 0 else ("EMPTY" if row.ok else "FAIL")
        print(
            f"  {row.board:10} {status:6} jobs={row.jobs:2d}  "
            f"with_text={row.with_desc:2d}  long_desc={row.long_desc:2d}  "
            f"{row.elapsed:5.1f}s  {row.error}",
            flush=True,
        )
        for item in row.sample:
            print(
                f"           - {item['title']} @ {item['company']} "
                f"({item['desc_len']} chars) {item['url']}",
                flush=True,
            )
    return 0 if any(r.jobs > 0 for r in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
