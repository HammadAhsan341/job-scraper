"""Ask the FlowResume API to rebuild its Redis browse snapshot after a run.

The jobs listing reads only from Redis, which the API rebuilds from Postgres
on a timer while it is awake. Calling the refresh endpoint right after a
scrape publishes new jobs immediately (and wakes a sleeping API host).
Optional: skipped when JOBS_CACHE_REFRESH_URL is unset.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

# A sleeping free-tier API can take a minute to boot before it answers.
TIMEOUT_SECONDS = 180


def refresh_jobs_cache() -> bool:
    url = (os.getenv("JOBS_CACHE_REFRESH_URL") or "").strip()
    secret = (os.getenv("JOBS_CRON_SECRET") or "").strip()
    if not url:
        print("Jobs cache refresh skipped: JOBS_CACHE_REFRESH_URL not set")
        return False
    if not secret:
        print("Jobs cache refresh skipped: JOBS_CRON_SECRET not set")
        return False

    request = urllib.request.Request(url, method="POST", headers={"X-Cron-Secret": secret})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            print(f"Jobs cache refreshed: HTTP {response.status} {response.read(200).decode(errors='replace')}")
            return True
    except urllib.error.HTTPError as exc:
        print(f"Jobs cache refresh FAILED: HTTP {exc.code} {exc.read(200).decode(errors='replace')}")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Jobs cache refresh FAILED: {exc}")
    return False
