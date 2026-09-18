"""Parse board-specific posted_date strings into a sortable UTC timestamp."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


_RELATIVE = re.compile(
    r"^(?:(?:about|approx\.?|approximately)\s+)?"
    r"(\d+)\+?\s*"
    r"(minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks|month|months|year|years)"
    r"\s*ago$",
    re.IGNORECASE,
)


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _from_iso(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_posted_date(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """Best-effort parse of scraped posted_date for sorting (newest first)."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    iso = _from_iso(raw)
    if iso is not None:
        return iso

    lower = raw.lower()
    if lower in {"just now", "today", "recently"}:
        return _utc_now(now)
    if lower == "yesterday":
        return _utc_now(now) - timedelta(days=1)

    match = _RELATIVE.match(lower)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("h"):
            delta = timedelta(hours=amount)
        elif unit.startswith("day"):
            delta = timedelta(days=amount)
        elif unit.startswith("week"):
            delta = timedelta(weeks=amount)
        elif unit.startswith("month"):
            delta = timedelta(days=amount * 30)
        elif unit.startswith("year"):
            delta = timedelta(days=amount * 365)
        else:
            delta = timedelta(days=amount)
        return _utc_now(now) - delta

    return None
