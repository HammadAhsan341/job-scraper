"""Playwright page hooks for LinkedIn guest job pages (expand collapsed JD)."""

from __future__ import annotations

import time
from typing import Any

# Buttons / links that reveal div.show-more-less-html__markup
_EXPAND_SELECTORS = (
    "button.show-more-less-html__button",
    "button[data-tracking-control-name='public_jobs_show-more-html']",
    "button.jobs-description__footer-button",
    "button[aria-label*='See more' i]",
    "button[aria-label*='Show more' i]",
    "button[aria-expanded='false'][class*='show-more']",
)


def expand_linkedin_job_description(page: Any) -> None:
    """Click 'See more' so the full JD markup is present before HTML snapshot."""
    if page is None:
        return
    for selector in _EXPAND_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=1500):
                continue
            loc.click(timeout=4000)
            time.sleep(0.6)
        except Exception:
            continue
    try:
        page.wait_for_timeout(400)
    except Exception:
        pass
