"""Collect every Indeed JD from one search page by clicking its job cards.

Each per-job navigation (viewjob, or a ?vjk= search URL) is a fresh page
load, and Indeed bot-walls datacenter IPs after a handful of those. Clicking a
card in the already-loaded results tab only fires the same-origin
``viewjob?...&viewtype=embedded`` JSON request a person's click makes, so one
page load yields the full JD for every card on it.

Runs as a Scrapling ``page_action`` inside the fetch worker; the JDs ride back
in the returned HTML as a JSON <script> that ``extract_clicked_pane_jds`` reads.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Set

CARD_SELECTOR = "a[data-jk]"
RESULT_SCRIPT_ID = "flowresume-indeed-jds"
# Listing fetches have no hard timeout in CI, so the clicks carry their own.
CLICK_BUDGET_S = 150
RESPONSE_TIMEOUT_MS = 15_000
# A person reads a pane before the next click; back-to-back clicks look scripted.
PAUSE_MS = 800
# Consecutive failed clicks that mean the session is walled, not one slow job.
MAX_CONSECUTIVE_FAILURES = 3

_SCRIPT_RE = re.compile(
    rf'<script[^>]*id="{RESULT_SCRIPT_ID}"[^>]*>(.*?)</script>', re.DOTALL
)


def _is_pane_response(response: Any, jk: str) -> bool:
    url = response.url
    return "/viewjob?" in url and "viewtype=embedded" in url and f"jk={jk}" in url


def _description_from(payload: Dict[str, Any]) -> str:
    """sanitizedJobDescription from the embedded viewjob JSON, or '' if absent."""
    if payload.get("status") != "success":
        return ""
    info = (payload.get("body") or {}).get("jobInfoWrapperModel") or {}
    return (info.get("jobInfoModel") or {}).get("sanitizedJobDescription") or ""


def collect_indeed_pane_jds(page: Any) -> None:
    # StealthyFetcher drives patchright, whose errors do not subclass playwright's.
    from patchright.sync_api import Error as PlaywrightError

    cards = page.locator(CARD_SELECTOR)
    total = cards.count()
    jds: Dict[str, str] = {}
    hidden: list[str] = []
    failures = 0
    started = time.monotonic()
    deadline = started + CLICK_BUDGET_S
    for i in range(total):
        if time.monotonic() > deadline:
            print(f"Indeed pane clicks: {CLICK_BUDGET_S}s budget spent at card {i}/{total}", flush=True)
            break
        card = cards.nth(i)
        jk = card.get_attribute("data-jk") or ""
        if not jk or jk in jds:
            continue
        if not card.is_visible():
            # Indeed plants hidden decoy cards; only a bot clicks them.
            print(f"Indeed pane clicks: skipped hidden card {jk}", flush=True)
            hidden.append(jk)
            continue
        try:
            with page.expect_response(
                lambda r, jk=jk: _is_pane_response(r, jk), timeout=RESPONSE_TIMEOUT_MS
            ) as info:
                card.click(timeout=5_000)
            description = _description_from(info.value.json())
            if not description:
                print(f"Indeed pane click {jk}: response carried no JD", flush=True)
        except (PlaywrightError, ValueError) as exc:
            description = ""
            print(f"Indeed pane click {jk} failed: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        if description:
            jds[jk] = description
            failures = 0
        else:
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"Indeed pane clicks: {failures} failures in a row, stopping", flush=True)
                break
        page.wait_for_timeout(PAUSE_MS)

    # "</" must not end the <script> early once the page is serialized.
    data = json.dumps({"jds": jds, "hidden": hidden}).replace("</", "<\\/")
    page.evaluate(
        """([id, data]) => {
            const s = document.createElement('script');
            s.type = 'application/json';
            s.id = id;
            s.textContent = data;
            document.body.appendChild(s);
        }""",
        [RESULT_SCRIPT_ID, data],
    )
    print(
        f"Indeed pane clicks: {len(jds)}/{total} full JDs in {time.monotonic() - started:.0f}s",
        flush=True,
    )


def _click_results(html: str) -> Dict[str, Any]:
    match = _SCRIPT_RE.search(html or "")
    return json.loads(match.group(1)) if match else {}


def extract_clicked_pane_jds(html: str) -> Dict[str, str]:
    """{jk: JD html} collected by ``collect_indeed_pane_jds``; {} when it did not run."""
    return _click_results(html).get("jds", {})


def extract_hidden_cards(html: str) -> Set[str]:
    """jks of the hidden decoy cards the click step skipped."""
    return set(_click_results(html).get("hidden", []))
