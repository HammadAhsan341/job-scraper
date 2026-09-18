"""
Environment-backed settings for the standalone scraper.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv


@dataclass
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    redis_url: str
    redis_max_retries: int
    redis_job_queue_prefix: str
    redis_processed_ttl: int
    job_scraping_max_pages_per_board: int
    job_scraping_max_jobs_per_board: int
    job_scraping_download_delay: float
    permitted_roles: List[str]
    excel_skill_gap: str
    job_stale_after_days: int
    job_scraping_boards: List[str]
    job_scraping_workers: int
    job_scraping_max_concurrent_fetches: int
    job_scraping_retry_backoff_seconds: float
    job_scraping_fetch_timeout_seconds: float
    job_scraping_fetch_retries: int
    job_scraping_fetch_retry_delay_seconds: float
    job_scraping_network_idle: bool
    job_scraping_detail_network_idle: bool
    job_scraping_fetch_isolate: bool
    job_scraping_browser_hard_timeout_seconds: float
    job_scraping_detail_hard_timeout_seconds: float
    job_scraping_board_timeout_limit: int
    job_scraping_board_cooldown_seconds: float
    job_scraping_max_detail_fetches: int
    job_scraping_indeed_interactive: bool
    job_scraping_indeed_interactive_wait_seconds: float


_settings: Settings | None = None


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_permitted_roles(raw: str | None) -> List[str]:
    if not raw:
        return ["Software Engineer", "Backend Developer", "Data Engineer"]

    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass

    return [part.strip() for part in raw.split(",") if part.strip()]


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_boards(raw: str | None) -> List[str]:
    # Local boards first so a LinkedIn/Indeed hang cannot delay the first writes.
    if not raw or raw.strip().lower() == "all":
        return ["rozee", "mustakbil", "indeed", "linkedin"]
    return [b.strip().lower() for b in raw.split(",") if b.strip()]


def _build_settings() -> Settings:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=False)

    return Settings(
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_max_retries=_get_env_int("REDIS_MAX_RETRIES", 3),
        redis_job_queue_prefix=os.getenv("REDIS_JOB_QUEUE_PREFIX", "jobs"),
        redis_processed_ttl=_get_env_int("REDIS_PROCESSED_TTL", 86400),
        job_scraping_max_pages_per_board=_get_env_int("JOB_SCRAPING_MAX_PAGES_PER_BOARD", 2),
        job_scraping_max_jobs_per_board=_get_env_int("JOB_SCRAPING_MAX_JOBS_PER_BOARD", 50),
        job_scraping_download_delay=_get_env_float(
            "JOB_SCRAPING_DOWNLOAD_DELAY",
            _get_env_float("DOWNLOAD_DELAY", 2.0),
        ),
        permitted_roles=_parse_permitted_roles(
            os.getenv("PERMITTED_ROLES") or os.getenv("PERMITTED_ROLES_1")
        ),
        excel_skill_gap=os.getenv("EXCEL_SKILL_GAP", "data/skills_master.xlsx"),
        job_stale_after_days=_get_env_int("JOB_STALE_AFTER_DAYS", 7),
        job_scraping_boards=_parse_boards(os.getenv("JOB_SCRAPING_BOARDS")),
        job_scraping_workers=_get_env_int("JOB_SCRAPING_WORKERS", 4),
        job_scraping_max_concurrent_fetches=_get_env_int(
            "JOB_SCRAPING_MAX_CONCURRENT_FETCHES",
            3,
        ),
        job_scraping_retry_backoff_seconds=_get_env_float(
            "JOB_SCRAPING_RETRY_BACKOFF_SECONDS",
            90.0,
        ),
        # 0 = no per-fetch timeout (same as the public Scrapling scraper).
        job_scraping_fetch_timeout_seconds=_get_env_float(
            "JOB_SCRAPING_FETCH_TIMEOUT_SECONDS",
            0.0,
        ),
        job_scraping_fetch_retries=_get_env_int("JOB_SCRAPING_FETCH_RETRIES", 0),
        job_scraping_fetch_retry_delay_seconds=_get_env_float(
            "JOB_SCRAPING_FETCH_RETRY_DELAY_SECONDS",
            8.0,
        ),
        job_scraping_network_idle=_get_env_bool("JOB_SCRAPING_NETWORK_IDLE", True),
        job_scraping_detail_network_idle=_get_env_bool(
            "JOB_SCRAPING_DETAIL_NETWORK_IDLE",
            True,
        ),
        # Isolate is off by default: in-process StealthyFetcher like the
        # public GitHub scraper. Set true only to kill a wedged local browser.
        job_scraping_fetch_isolate=_get_env_bool("JOB_SCRAPING_FETCH_ISOLATE", False),
        job_scraping_browser_hard_timeout_seconds=_get_env_float(
            "JOB_SCRAPING_BROWSER_HARD_TIMEOUT_SECONDS",
            0.0,
        ),
        job_scraping_detail_hard_timeout_seconds=_get_env_float(
            "JOB_SCRAPING_DETAIL_HARD_TIMEOUT_SECONDS",
            0.0,
        ),
        job_scraping_board_timeout_limit=_get_env_int(
            "JOB_SCRAPING_BOARD_TIMEOUT_LIMIT",
            3,
        ),
        job_scraping_board_cooldown_seconds=_get_env_float(
            "JOB_SCRAPING_BOARD_COOLDOWN_SECONDS",
            90.0,
        ),
        # Indeed/LinkedIn detail tabs are 45–90s each on GitHub. Cap them so
        # listing cards are stored instead of waiting on 50 viewjob timeouts.
        # 0 = open every job tab (same as the reference Scrapling scraper).
        job_scraping_max_detail_fetches=_get_env_int(
            "JOB_SCRAPING_MAX_DETAIL_FETCHES",
            0,
        ),
        job_scraping_indeed_interactive=_get_env_bool(
            "JOB_SCRAPING_INDEED_INTERACTIVE",
            False,
        ),
        job_scraping_indeed_interactive_wait_seconds=_get_env_float(
            "JOB_SCRAPING_INDEED_INTERACTIVE_WAIT_SECONDS",
            300.0,
        ),
    )


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings

