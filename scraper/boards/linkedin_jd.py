"""LinkedIn job-description extraction (collapsed "See more" + JSON-LD)."""

from __future__ import annotations

import html as html_lib
import re
from typing import Any, List, Tuple

from scraper.guest import jsonld_job_descriptions

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MARKUP_BLOCK_RE = re.compile(
    r'<div[^>]*class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


def _clean_html_text(raw: str) -> str:
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", html_lib.unescape(raw))
    return _WS_RE.sub(" ", text).strip()


def _text_from_nodes(response: Any, selector: str) -> List[str]:
    if response is None:
        return []
    try:
        nodes = response.css(selector)
    except Exception:
        return []
    if not nodes:
        return []
    out: List[str] = []
    for node in nodes:
        try:
            parts = node.css("::text").getall()
            text = " ".join(parts) if parts else ""
        except Exception:
            text = str(getattr(node, "text", "") or "")
        cleaned = _WS_RE.sub(" ", text).strip()
        if len(cleaned) >= 20:
            out.append(cleaned)
    return out


def markup_descriptions_from_html(html: str) -> List[str]:
    """Full JD blocks LinkedIn keeps in show-more-less markup (often hidden until click)."""
    if not html:
        return []
    out: List[str] = []
    for match in _MARKUP_BLOCK_RE.finditer(html):
        text = _clean_html_text(match.group(1) or "")
        if len(text) >= 50:
            out.append(text)
    return out


def extract_description_multi(html: str, response: Any) -> Tuple[str, str]:
    """
    Pick the longest plausible JD from DOM + raw HTML + JSON-LD.

    Guest pages often show a short preview in description__text while the full
    body lives in div.show-more-less-html__markup (visible only after "See more").
    """
    candidates: List[tuple[str, str]] = []

    for text in _text_from_nodes(response, "div.show-more-less-html__markup"):
        candidates.append(("markup_css", text))
    for text in _text_from_nodes(response, "div[class*='show-more-less-html__markup']"):
        candidates.append(("markup_css", text))

    for text in _text_from_nodes(
        response,
        "div.description__text, .jobs-description-content__text, .jobs-description__content, section.description",
    ):
        candidates.append(("description_container", text))

    for text in markup_descriptions_from_html(html):
        candidates.append(("markup_html", text))

    for text in jsonld_job_descriptions(html):
        candidates.append(("jsonld", text))

    if not candidates:
        return "", ""

    best_source, best = max(candidates, key=lambda item: len(item[1]))
    return best, best_source
