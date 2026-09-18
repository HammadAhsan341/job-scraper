"""Detect guest auth/bot walls and merge listing cards with optional detail pages."""

import html as html_lib
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

AUTH_URL_MARKERS = (
    "secure.indeed.com/auth",
    "from=bot-detection",
    "account/login",
    "/authwall",
    "/checkpoint/challenge",
    "linkedin.com/login",
    "linkedin.com/uas/login",
    "linkedin.com/signup",
)

AUTH_HTML_MARKERS = (
    "bot-detection-anonymous",
    "from=bot-detection",
)

JOB_URL_MARKERS = (
    "/viewjob",
    "/jobs/view/",
    "/jobs/job/",
    "jk=",
)

_META_REFRESH_RE = re.compile(
    r"""http-equiv=["']?refresh["'][^>]*content=["'][^"']*url=([^"'\s>]+)""",
    re.IGNORECASE,
)


def _response_url(response: Any, fallback: str = "") -> str:
    if response is None:
        return fallback or ""
    return str(getattr(response, "url", None) or fallback or "")


def _response_html(response: Any) -> str:
    if response is None:
        return ""
    for attr in ("html_content", "body", "text"):
        value = getattr(response, attr, None)
        if value:
            return str(value)
    getter = getattr(response, "get", None)
    if callable(getter):
        try:
            html = getter()
            if html:
                return str(html)
        except Exception:
            pass
    return str(response)[:4000]


def is_job_detail_url(url: str) -> bool:
    """True for a guest job page, not a login or search listing."""
    value = (url or "").lower()
    if not value or any(marker in value for marker in AUTH_URL_MARKERS):
        return False
    return any(marker in value for marker in JOB_URL_MARKERS)


def meta_refresh_url(html: str, base_url: str = "") -> str:
    """Return the first meta-refresh destination, if any."""
    if not html:
        return ""
    match = _META_REFRESH_RE.search(html)
    if not match:
        return ""
    return urljoin(base_url or "", match.group(1).strip())


def query_continue_url(url: str) -> str:
    """Pull viewjob /jobs/view targets out of Indeed/LinkedIn bounce query params."""
    if not url:
        return ""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("continue2", "continue", "sessionRedirect"):
        values = query.get(key) or []
        if not values:
            continue
        dest = unquote(values[0]).strip()
        if dest.startswith("/"):
            host = "https://www.linkedin.com" if "linkedin" in (parsed.netloc or url).lower() else f"{parsed.scheme}://{parsed.netloc}"
            dest = urljoin(host, dest)
        if is_job_detail_url(dest):
            return dest
    return ""


def destination_job_url(response: Any = None, requested_url: str = "") -> str:
    """Job URL hidden behind a redirect, meta-refresh, or login continue param."""
    final_url = _response_url(response, requested_url)
    html = _response_html(response)
    candidates = (
        query_continue_url(final_url),
        meta_refresh_url(html, final_url or requested_url),
        query_continue_url(requested_url),
    )
    seen_roots = {
        (final_url or "").split("?")[0].rstrip("/").lower(),
    }
    for candidate in candidates:
        if not candidate or not is_job_detail_url(candidate):
            continue
        root = candidate.split("?")[0].rstrip("/").lower()
        if root in seen_roots:
            continue
        return candidate
    return ""


def _jsonld_job_posting_nodes(html: str) -> list[dict]:
    if not html:
        return []
    out: list[dict] = []
    for match in _JSONLD_SCRIPT_RE.finditer(html):
        raw = (match.group(1) or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        expanded: list[Any] = []
        for node in nodes:
            if isinstance(node, dict) and node.get("@graph"):
                graph = node["@graph"]
                expanded.extend(graph if isinstance(graph, list) else [graph])
            else:
                expanded.append(node)
        for node in expanded:
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            if isinstance(types, str):
                types = [types]
            if "JobPosting" not in (types or []):
                continue
            out.append(node)
    return out


def _jsonld_plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("@value") or value.get("name") or ""
    text = _TAG_RE.sub(" ", html_lib.unescape(str(value)))
    return " ".join(text.split()).strip()


def jsonld_job_posting(html: str) -> Dict[str, str]:
    """Extract common JobPosting fields from JSON-LD (Indeed often serves these on viewjob)."""
    fields = {"title": "", "description": "", "company": "", "location": ""}
    for node in _jsonld_job_posting_nodes(html):
        if not fields["title"]:
            fields["title"] = _jsonld_plain(node.get("title"))
        if not fields["description"]:
            fields["description"] = _jsonld_plain(node.get("description"))
        if not fields["company"]:
            org = node.get("hiringOrganization") or {}
            if isinstance(org, dict):
                fields["company"] = _jsonld_plain(org.get("name") or org.get("legalName"))
            else:
                fields["company"] = _jsonld_plain(org)
        if not fields["location"]:
            loc = node.get("jobLocation")
            if isinstance(loc, list) and loc:
                loc = loc[0]
            if isinstance(loc, dict):
                addr = loc.get("address") or loc
                if isinstance(addr, dict):
                    parts = [
                        addr.get("addressLocality"),
                        addr.get("addressRegion"),
                        addr.get("addressCountry"),
                    ]
                    fields["location"] = ", ".join(
                        p for p in (_jsonld_plain(x) for x in parts) if p
                    )
                else:
                    fields["location"] = _jsonld_plain(loc)
            else:
                fields["location"] = _jsonld_plain(loc)
        if all(fields[k] for k in ("title", "description")):
            break
    return fields


def jsonld_job_description(html: str) -> str:
    """Pull JobPosting.description from JSON-LD, which guest pages often keep."""
    return jsonld_job_posting(html).get("description") or ""


def is_auth_wall(response: Any = None, url: str = "") -> bool:
    """True when a fetch landed on a login or bot-detection page."""
    final_url = _response_url(response, url).lower()
    if any(marker in final_url for marker in AUTH_URL_MARKERS):
        return True
    html = _response_html(response).lower()
    return any(marker in html for marker in AUTH_HTML_MARKERS)


def merge_listing_with_detail(listing: Dict, detail: Optional[Dict]) -> Dict:
    """Prefer listing public URL; take longer description and filled fields from detail."""
    merged = dict(listing)
    if not detail:
        return merged
    listing_url = listing.get("job_url") or ""
    for key, value in detail.items():
        if key == "job_url":
            continue
        if key == "description":
            current = merged.get("description") or ""
            incoming = value or ""
            if len(incoming) > len(current):
                merged["description"] = incoming
            continue
        if value not in (None, "", [], {}):
            merged[key] = value
    if listing_url and "auth" not in listing_url.lower() and "login" not in listing_url.lower():
        merged["job_url"] = listing_url
    elif detail.get("job_url"):
        merged["job_url"] = detail["job_url"]
    return merged
