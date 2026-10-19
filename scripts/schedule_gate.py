"""Decide whether a scheduled scrape should start.

GitHub drops cron events, especially at minute 0, and does not retry them.
The workflow registers three attempts per hour. This gate keeps the extra
attempts from scraping again after one success in the same UTC hour. A failed
hour is retried by the next attempt. If the check itself fails, scrape anyway.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


def should_scrape(runs: list[dict], now: datetime, run_id: str) -> tuple[bool, str]:
    hour = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    for run in runs:
        if str(run.get("id")) == str(run_id):
            continue
        status = run.get("status") or ""
        if status in ACTIVE:
            return False, f"run {run.get('id')} is {status}"
        if run.get("conclusion") != "success":
            continue
        started = _parse(run.get("run_started_at") or run.get("created_at") or "")
        if started is None:
            continue
        if started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H") == hour:
            return False, f"successful run {run.get('id')} already started this UTC hour"
    return True, "no successful scrape this UTC hour"


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_runs(repo: str, token: str, workflow: str = "scrape.yml") -> list[dict]:
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs"
        "?per_page=20&event=schedule"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "schedule-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return list(payload.get("workflow_runs") or [])


def main() -> int:
    try:
        runs = fetch_runs(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        scrape, reason = should_scrape(runs, datetime.now(timezone.utc), os.environ["GITHUB_RUN_ID"])
    except Exception as exc:
        scrape, reason = True, f"gate check failed ({exc}); scraping anyway"
    print(reason)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"scrape={'true' if scrape else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
