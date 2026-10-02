"""Stable job_id from the board's own posting identity.

The old id hashed title|company|location, so two different postings with the
same three fields overwrote each other, and a listing card ("Unknown" company)
and its detail page produced two rows for one posting. Boards expose a native
posting id in the URL; key on board + that id, and fall back to the old hash
only when the URL has none.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

_LINKEDIN_ID = re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{5,})(?:[/?#]|$)")
_INDEED_JK = re.compile(r"^[a-f0-9]{8,}$", re.IGNORECASE)
# Listing/search pages are not postings: never key a job on them.
_SEARCH_PATHS = ("/jobs/search", "/job/jsearch", "/jobs")


def _native_key(board: str, url: str) -> Optional[str]:
    if not url:
        return None
    parts = urlsplit(url.strip())
    query = parse_qs(parts.query)
    if board == "indeed":
        jk = (query.get("jk") or query.get("vjk") or [""])[0]
        return jk.lower() if _INDEED_JK.match(jk) else None
    if board == "linkedin":
        match = _LINKEDIN_ID.search(parts.path)
        if match:
            return match.group(1)
        current = (query.get("currentJobId") or [""])[0]
        return current if current.isdigit() else None
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    if not host or not path or path in _SEARCH_PATHS or path.startswith(_SEARCH_PATHS[:2]):
        return None
    return f"{host}{path}"


def stable_job_id(job: Dict[str, Any], board_hint: str = "") -> Optional[str]:
    """Return board-native job_id, or None when the URL carries no posting id."""
    board = (job.get("board") or job.get("job_source") or board_hint or "").lower()
    key = _native_key(board, str(job.get("job_url") or job.get("url") or ""))
    if not key:
        return None
    return hashlib.sha256(f"{board}|{key}".encode()).hexdigest()[:16]
