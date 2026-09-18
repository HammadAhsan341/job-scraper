"""
Headful Playwright fetch for Indeed: stay on the page through Cloudflare / captcha,
then read the full JD from #jobDescriptionText (inspect-style container div).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scraper.boards.indeed_jd import extract_description_multi

_CAPTCHA_MARKERS = (
    "additional verification required",
    "just a moment",
    "checking your browser",
    "secure.indeed.com/auth",
    "bot-detection",
    "cf-challenge",
)


def _profile_dir() -> Path:
    base = Path(__file__).resolve().parent.parent / "data" / "indeed_browser_profile"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _page_needs_human(url: str, body: str) -> bool:
    u = (url or "").lower()
    b = (body or "").lower()
    if any(m in u for m in _CAPTCHA_MARKERS):
        return True
    return any(m in b for m in _CAPTCHA_MARKERS)


def _read_jd_from_live_page(page) -> Tuple[str, str]:
    """Evaluate the same div users pick in DevTools inspect."""
    script = """
    () => {
      const selectors = [
        '#jobDescriptionText',
        'div.jobsearch-JobComponent-description',
        '[data-testid="jobsearch-JobComponent-description"]',
      ];
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (!el) continue;
        const text = (el.innerText || el.textContent || '').trim();
        if (text.length >= 50) {
          return { text, html: el.outerHTML || '', selector: sel };
        }
      }
      return { text: '', html: '', selector: '' };
    }
    """
    try:
        payload = page.evaluate(script)
    except Exception:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    return str(payload.get("text") or ""), str(payload.get("html") or "")


def fetch_indeed_interactive(
    urls: List[str],
    *,
    wait_seconds: float = 300.0,
    poll_seconds: float = 2.0,
    headless: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Open Chromium (persistent profile), navigate, wait for captcha clearance / JD div.
    Returns dict with html, final_url, description, source — or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Indeed interactive: playwright not installed")
        return None

    urls = [u for u in urls if u]
    if not urls:
        return None

    profile = str(_profile_dir())
    deadline = time.monotonic() + max(30.0, wait_seconds)
    last_html = ""
    last_url = ""

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            profile,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            except Exception as exc:
                print(f"Indeed interactive: goto failed ({exc})")
                continue

            warned = False
            while time.monotonic() < deadline:
                last_url = page.url or url
                try:
                    last_html = page.content()
                except Exception:
                    last_html = ""

                live_text, _live_html = _read_jd_from_live_page(page)
                if len(live_text) >= 50:
                    context.close()
                    return {
                        "final_url": last_url,
                        "html": last_html,
                        "description": live_text,
                        "source": "interactive:dom:jobDescriptionText",
                    }

                multi_text, multi_src = extract_description_multi(last_html, None)
                if len(multi_text) >= 50:
                    context.close()
                    return {
                        "final_url": last_url,
                        "html": last_html,
                        "description": multi_text,
                        "source": f"interactive:{multi_src}",
                    }

                if _page_needs_human(last_url, last_html):
                    if not warned:
                        print(
                            "Indeed interactive: complete verification in the open "
                            "browser window (captcha / sign-in), then wait…"
                        )
                        warned = True
                time.sleep(poll_seconds)

        context.close()

    if last_html:
        text, src = extract_description_multi(last_html, None)
        if len(text) >= 50:
            return {
                "final_url": last_url,
                "html": last_html,
                "description": text,
                "source": f"interactive-timeout:{src}",
            }
    return None
