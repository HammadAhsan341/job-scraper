"""Map in-memory JobData onto the Supabase jobs table schema."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

_PAYLOAD_EXCLUDE = {"raw_html"}
_PLACEHOLDERS = {"", "unknown", "untitled", "n/a", "none", "null"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def is_blank_field(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return _text(value).lower() in _PLACEHOLDERS


def incoming_improves_stored(incoming: Dict[str, Any], stored: Optional[Dict[str, Any]]) -> bool:
    """True when a later scrape can fill an empty JD or other missing job fields."""
    if not stored:
        return True
    if len(_text(incoming.get("description"))) > len(_text(stored.get("job_description"))):
        return True
    pairs = (
        (incoming.get("company"), stored.get("company")),
        (incoming.get("location"), stored.get("location")),
        (incoming.get("job_url"), stored.get("url")),
        (incoming.get("title"), stored.get("job_title")),
        (incoming.get("employment_type"), stored.get("job_type")),
        (incoming.get("salary"), stored.get("salary_raw")),
        (incoming.get("posted_date"), stored.get("posted_date")),
    )
    if any(not is_blank_field(new) and is_blank_field(old) for new, old in pairs):
        return True
    new_skills = incoming.get("skills") or []
    old_skills = stored.get("skills_required") or []
    return bool(new_skills) and not old_skills


def merge_incoming_over_stored(
    incoming: Dict[str, Any],
    stored: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Keep the longer JD and any stored field the new scrape still lacks."""
    merged = dict(incoming)
    if not stored:
        return merged
    old_desc = _text(stored.get("job_description"))
    new_desc = _text(incoming.get("description"))
    if len(old_desc) > len(new_desc):
        merged["description"] = stored.get("job_description")
    field_map = (
        ("company", "company"),
        ("location", "location"),
        ("job_url", "url"),
        ("title", "job_title"),
        ("employment_type", "job_type"),
        ("salary", "salary_raw"),
        ("posted_date", "posted_date"),
    )
    for incoming_key, stored_key in field_map:
        if is_blank_field(merged.get(incoming_key)) and not is_blank_field(stored.get(stored_key)):
            merged[incoming_key] = stored.get(stored_key)
    if not (merged.get("skills") or []) and (stored.get("skills_required") or []):
        merged["skills"] = stored["skills_required"]
    return merged


def experience_years(job: Dict[str, Any]) -> Optional[int]:
    """Coerce parsed experience to an integer, preserving a true zero."""
    parsed = job.get("experience_parsed") or {}
    min_years = parsed.get("min_years")
    level = parsed.get("level")
    raw_text = parsed.get("raw_text")

    if raw_text or (level and level != "unknown"):
        if min_years is None:
            return 0
        return int(min_years)

    if isinstance(min_years, int) and min_years > 0:
        return min_years

    raw = job.get("experience_required")
    if raw is None or raw == "":
        return None
    match = re.search(r"(\d+)", str(raw))
    return int(match.group(1)) if match else None


def _json_safe(value: Any) -> Any:
    """Coerce scrapling/numpy leftovers so PostgREST can serialize the row."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        return str(value)


def job_to_record(job: Dict[str, Any]) -> Dict[str, Any]:
    """Map an in-memory JobData dict onto the jobs table schema."""
    date_scrapped = job.get("date_scrapped") or datetime.utcnow().isoformat()
    raw_payload = {
        key: _json_safe(value)
        for key, value in job.items()
        if key not in _PAYLOAD_EXCLUDE
    }

    return {
        "job_id": job["job_id"],
        "job_title": job.get("title", ""),
        "job_description": job.get("description", ""),
        "skills_required": job.get("skills", []),
        "experience_required": experience_years(job),
        "education_required": job.get("education_required"),
        "job_type": job.get("employment_type", ""),
        "location": job.get("location", ""),
        "industry": job.get("industry"),
        "company": job.get("company", ""),
        "url": job.get("job_url", ""),
        "job_source": job.get("job_source") or job.get("board", ""),
        "date_scrapped": date_scrapped,
        "posted_date": job.get("posted_date"),
        "salary_raw": job.get("salary"),
        "salary_normalized": job.get("salary_normalized"),
        "description_sections": job.get("description_sections") or {},
        "skills_categorized": job.get("skills_categorized") or {},
        "experience_parsed": job.get("experience_parsed") or {},
        "enrichment_confidence": job.get("enrichment_confidence"),
        "enrichment_timestamp": job.get("enrichment_timestamp"),
        "raw_payload": raw_payload,
    }
