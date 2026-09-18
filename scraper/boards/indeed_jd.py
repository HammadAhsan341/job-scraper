"""Indeed job-description extraction (multiple strategies, no single DOM dependency)."""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, List, Optional, Tuple

from scraper.guest import jsonld_job_description, jsonld_job_posting

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html_text(raw: str) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", html_lib.unescape(raw))
    return _WS_RE.sub(" ", text).strip()


def _decode_json_string_literal(fragment: str) -> str:
    """Decode a JSON string body (already inside quotes, escapes preserved)."""
    try:
        return json.loads(f'"{fragment}"')
    except json.JSONDecodeError:
        return html_lib.unescape(fragment.replace("\\n", "\n").replace('\\"', '"'))


def extract_sanitized_job_descriptions(html: str) -> List[str]:
    """
    Indeed embeds full JD HTML in SERP / viewjob bootstrap JSON as
    sanitizedJobDescription (works when #jobDescriptionText is not in static HTML).
    """
    if not html:
        return []
    needle = '"sanitizedJobDescription":"'
    out: List[str] = []
    idx = 0
    while True:
        start = html.find(needle, idx)
        if start < 0:
            break
        i = start + len(needle)
        chunks: List[str] = []
        while i < len(html):
            ch = html[i]
            if ch == "\\":
                chunks.append(html[i : i + 2])
                i += 2
                continue
            if ch == '"':
                break
            chunks.append(ch)
            i += 1
        decoded = _decode_json_string_literal("".join(chunks))
        text = _clean_html_text(decoded)
        if len(text) >= 50:
            out.append(text)
        idx = i + 1
    return out


def extract_description_div_text(response: Any) -> str:
    """Strategy: inspect-style — entire JD container div (#jobDescriptionText and variants)."""
    if response is None:
        return ""
    selectors = [
        "div#jobDescriptionText",
        "#jobDescriptionText",
        "div.jobsearch-JobComponent-description",
        "div[id='jobDescriptionText']",
        "div[data-testid='jobsearch-JobComponent-description']",
        "#vjs-desc",
    ]
    for selector in selectors:
        try:
            nodes = response.css(selector)
        except Exception:
            continue
        if not nodes:
            continue
        try:
            parts = nodes[0].css("::text").getall()
        except Exception:
            parts = []
        text = _clean_html_text(" ".join(parts))
        if len(text) >= 50:
            return text
    return ""


def extract_description_multi(html: str, response: Any = None) -> Tuple[str, str]:
    """
    Try several approaches; return (description_text, source_tag).
    Order: DOM JD div → mosaic JSON → JSON-LD.
    """
    div_text = extract_description_div_text(response) if response is not None else ""
    if len(div_text) >= 50:
        return div_text, "dom:jobDescriptionText"

    mosaic_candidates = extract_sanitized_job_descriptions(html)
    if mosaic_candidates:
        best = max(mosaic_candidates, key=len)
        return best, "mosaic:sanitizedJobDescription"

    ld = jsonld_job_posting(html).get("description") or ""
    ld_text = _clean_html_text(ld)
    if len(ld_text) >= 50:
        return ld_text, "jsonld:JobPosting"

    legacy = _clean_html_text(jsonld_job_description(html))
    if len(legacy) >= 50:
        return legacy, "jsonld:description"

    if len(div_text) > len(ld_text):
        return div_text, "dom:partial"
    return ld_text or div_text, "none"


def jk_from_job_url(job_url: str) -> str:
    if not job_url:
        return ""
    match = re.search(r"[?&]jk=([a-f0-9]+)", job_url, re.IGNORECASE)
    return match.group(1) if match else ""
