"""When Indeed/LinkedIn detail tabs hit auth walls, still persist usable listing cards."""

from __future__ import annotations

from typing import Any, Dict

FULL_JD_MIN_LEN = 50
INDEED_LISTING_MIN_LEN = 20
INDEED_LISTING_PLACEHOLDER_MARKER = "Listing summary from Indeed search results"


def is_indeed_listing_placeholder(description: str) -> bool:
    return INDEED_LISTING_PLACEHOLDER_MARKER in (description or "")


def _board(job: Dict[str, Any], hint: str = "") -> str:
    return (job.get("board") or job.get("job_source") or hint or "").lower()


def description_length(job: Dict[str, Any]) -> int:
    return len((job.get("description") or "").strip())


def ensure_indeed_listing_description(job: Dict[str, Any]) -> Dict[str, Any]:
    """Fill a short placeholder JD so Indeed cards can be enriched and stored."""
    if _board(job) != "indeed":
        return job
    desc = (job.get("description") or "").strip()
    if desc and not is_indeed_listing_placeholder(desc):
        if description_length(job) >= INDEED_LISTING_MIN_LEN:
            return job
    if description_length(job) >= FULL_JD_MIN_LEN:
        return job
    title = (job.get("title") or "").strip()
    if not title:
        return job
    company = (job.get("company") or "").strip() or "company"
    location = (job.get("location") or "").strip()
    line = f"{title} at {company}"
    if location:
        line = f"{line} · {location}"
    out = dict(job)
    out["description"] = (
        f"{line}. Listing summary from Indeed search results. "
        "Open the posting link for the full job description."
    )
    return out


def is_persistable_job(job: Dict[str, Any], board_hint: str = "") -> bool:
    board = _board(job, board_hint)
    desc_len = description_length(job)
    if desc_len >= FULL_JD_MIN_LEN:
        return True
    if board == "indeed" and desc_len >= INDEED_LISTING_MIN_LEN:
        return True
    if board == "indeed":
        title = (job.get("title") or "").strip()
        company = (job.get("company") or "").strip()
        url = (job.get("job_url") or job.get("url") or "").strip()
        if title and url and company.lower() not in {"", "unknown"}:
            return True
    if board in {"linkedin", "indeed"}:
        return False
    return desc_len > 0 or bool((job.get("title") or "").strip())


def prepare_job_for_persist(job: Dict[str, Any], board_hint: str = "") -> Dict[str, Any]:
    prepared = ensure_indeed_listing_description(job)
    if is_persistable_job(prepared, board_hint):
        return prepared
    return job
