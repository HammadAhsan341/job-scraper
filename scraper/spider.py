"""
JobScraperSpider: Multi-session job scraper using Scrapling.

Coordinates scraping across LinkedIn, Rozee, Indeed, and Mustakbil with:
- StealthyFetcher for anti-bot bypass
- Checkpoint-based resume
- Concurrent requests with rate limiting
- Hard fetch timeouts so a blocked board cannot stall the whole run
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import contextlib
import multiprocessing as mp
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from core.settings import get_settings
from core.state import JobData
from scraper.boards import (
    BaseJobParser,
    IndeedParser,
    LinkedInParser,
    MustakbilParser,
    RozeeParser,
)
from scraper.fetch_worker import worker_entry, _stealthy_fetch
from scraper.boards.indeed_jd import jk_from_job_url
from scraper.guest import destination_job_url, is_auth_wall, merge_listing_with_detail
from scraper.listing_persist import is_persistable_job, prepare_job_for_persist

_board_fetch_gates: Dict[str, threading.Semaphore] = {}
_board_fetch_gates_lock = threading.Lock()
_board_health_lock = threading.Lock()
_board_consecutive_timeouts: Dict[str, int] = {}
_board_cooldown_until: Dict[str, float] = {}
_fetch_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="scrapling-fetch")
_mp_ctx = mp.get_context("spawn")
_live_procs: set[Any] = set()
_live_lock = threading.Lock()


def reset_fetch_runtime_state() -> None:
    """Clear per-run board health. Used by tests."""
    with _board_fetch_gates_lock:
        _board_fetch_gates.clear()
    with _board_health_lock:
        _board_consecutive_timeouts.clear()
        _board_cooldown_until.clear()
    with _live_lock:
        _live_procs.clear()


def is_board_cooling_down(board: str) -> bool:
    with _board_health_lock:
        return _board_cooldown_until.get(board, 0.0) > time.monotonic()


def wait_for_board_ready(board: str) -> None:
    """Wait out a cooldown so later roles still scrape every board."""
    while True:
        with _board_health_lock:
            remaining = _board_cooldown_until.get(board, 0.0) - time.monotonic()
        if remaining <= 0:
            return
        print(
            f"   {board} cooling down {remaining:.0f}s after timeouts",
            flush=True,
        )
        time.sleep(min(remaining, 5.0))


def _board_fetch_gate(board: str) -> threading.Semaphore:
    """Limit in-flight browser fetches per board across all role workers."""
    cap = max(1, get_settings().job_scraping_max_concurrent_fetches)
    with _board_fetch_gates_lock:
        gate = _board_fetch_gates.get(board)
        if gate is None:
            gate = threading.Semaphore(cap)
            _board_fetch_gates[board] = gate
        return gate


def _note_board_timeout(board: str) -> None:
    limit = max(1, get_settings().job_scraping_board_timeout_limit)
    cooldown = max(0.0, get_settings().job_scraping_board_cooldown_seconds)
    with _board_health_lock:
        count = _board_consecutive_timeouts.get(board, 0) + 1
        _board_consecutive_timeouts[board] = count
        if count >= limit:
            _board_cooldown_until[board] = time.monotonic() + cooldown
            _board_consecutive_timeouts[board] = 0
            print(
                f"Cooling down {board} for {cooldown:.0f}s after {limit} timed-out fetches",
                flush=True,
            )


def _note_board_success(board: str) -> None:
    with _board_health_lock:
        _board_consecutive_timeouts[board] = 0


class FetchedPage:
    """Rebuilt from a subprocess dict so parsers still get .url / .css() / .status."""

    def __init__(self, url: str, status: Any, html: str, parsed_job: Optional[dict] = None):
        self.url = url
        self.status = status
        self.body = html or ""
        self.html_content = html or ""
        self.parsed_job = parsed_job
        self._selector = None

    def get(self):
        return self.body

    def css(self, selector: str):
        if self._selector is None:
            from scrapling.parser import Selector

            self._selector = Selector(content=self.body, url=self.url or "")
        return self._selector.css(selector)


def _kill_tree(proc: Any) -> None:
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    try:
        import psutil

        parent = psutil.Process(pid)
        victims = parent.children(recursive=True)
        victims.append(parent)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()
        return
    for victim in victims:
        with contextlib.suppress(Exception):
            victim.kill()


def _page_from_worker_result(url: str, result: dict | None):
    if not result or result.get("error"):
        return None
    return FetchedPage(
        url=str(result.get("final_url") or url),
        status=result.get("status"),
        html=str(result.get("html") or ""),
        parsed_job=result.get("job") if isinstance(result.get("job"), dict) else None,
    )


def _do_stealthy_fetch(
    url: str,
    board: str,
    headless: bool,
    timeout_ms: int,
    network_idle: bool,
):
    return _stealthy_fetch(url, board, headless, timeout_ms, network_idle)


def _fetch_isolated(
    url: str,
    board: str,
    headless: bool,
    timeout_ms: int,
    network_idle: bool,
    hard_timeout_s: float,
    worker=worker_entry,
    parse_job: bool = False,
):
    result: dict | None = None
    proc = None
    q = None
    try:
        q = _mp_ctx.Queue()
        proc = _mp_ctx.Process(
            target=worker,
            args=(q, url, board, headless, timeout_ms, network_idle, parse_job),
            daemon=True,
        )
        proc.start()
        with _live_lock:
            _live_procs.add(proc)
        try:
            result = q.get(timeout=hard_timeout_s)
        except queue.Empty:
            print(
                f"   Browser hard timeout ({hard_timeout_s:.0f}s) ({board}): {url}",
                flush=True,
            )
            result = {"error": f"browser hard timeout ({hard_timeout_s:.0f}s)"}
    except Exception as exc:
        print(f"   Fetch subprocess error ({board}): {exc}", flush=True)
        result = {"error": str(exc)}
    finally:
        try:
            if getattr(proc, "pid", None) is not None:
                proc.join(timeout=5)
                if proc.is_alive():
                    _kill_tree(proc)
                    proc.join(timeout=5)
        except Exception:
            print(f"   Error reaping fetch subprocess ({board})", flush=True)
        finally:
            if proc is not None:
                with _live_lock:
                    _live_procs.discard(proc)
            if q is not None:
                with contextlib.suppress(Exception):
                    q.close()
    return _page_from_worker_result(url, result)


def _resolve_network_idle(_board: str, network_idle: Optional[bool]) -> bool:
    if network_idle is not None:
        return bool(network_idle)
    return bool(getattr(get_settings(), "job_scraping_network_idle", False))


def _fetch_once(
    url: str,
    board: str,
    headless: bool,
    network_idle: Optional[bool] = None,
    job_detail: bool = False,
):
    settings = get_settings()
    # 0 = unlimited, same as muhammadhaider02/Scrapling-Job-Boards-Scrapper.
    timeout_s = float(getattr(settings, "job_scraping_fetch_timeout_seconds", 0) or 0)
    idle = _resolve_network_idle(board, network_idle)
    isolate = bool(getattr(settings, "job_scraping_fetch_isolate", False))
    hard_timeout_s = float(
        getattr(settings, "job_scraping_browser_hard_timeout_seconds", 0) or 0
    )
    if timeout_s > 0 and hard_timeout_s < timeout_s:
        hard_timeout_s = timeout_s

    # Reference scraper: StealthyFetcher(network_idle=True) with no timeout.
    timeout_ms = int(timeout_s * 1000) if timeout_s > 0 else 0
    parse_job = False
    if job_detail:
        idle = True
        timeout_ms = 0
        parse_job = True
        detail_hard = float(
            getattr(settings, "job_scraping_detail_hard_timeout_seconds", 0) or 0
        )
        if detail_hard > hard_timeout_s:
            hard_timeout_s = detail_hard

    board_key = (board or "").lower()
    if job_detail and board_key == "indeed":
        isolate = True
        if hard_timeout_s <= 0:
            hard_timeout_s = 180.0
        elif hard_timeout_s < 120.0:
            hard_timeout_s = 120.0

    gate = _board_fetch_gate(board)
    gate_wait_s = timeout_s if timeout_s > 0 else None
    acquired = gate.acquire(timeout=gate_wait_s) if gate_wait_s else gate.acquire()
    if not acquired:
        print(f"   Fetch gate timed out for {board}: {url}", flush=True)
        _note_board_timeout(board)
        return None

    try:
        if isolate:
            kill_after = hard_timeout_s if hard_timeout_s > 0 else 180.0
            response = _fetch_isolated(
                url,
                board,
                headless,
                timeout_ms,
                idle,
                kill_after,
                parse_job=parse_job,
            )
            if response is None:
                _note_board_timeout(board)
                return None
            _note_board_success(board)
            return response

        wait_s = 0.0
        if job_detail and hard_timeout_s > 0:
            wait_s = hard_timeout_s
        elif not job_detail and timeout_s > 0:
            wait_s = timeout_s

        if wait_s > 0:
            future = _fetch_pool.submit(
                _do_stealthy_fetch,
                url,
                board,
                headless,
                timeout_ms,
                idle,
            )
            try:
                response = future.result(timeout=wait_s)
            except FuturesTimeoutError:
                print(f"   Fetch timed out after {wait_s:.0f}s ({board}): {url}", flush=True)
                _note_board_timeout(board)
                return None
        else:
            response = _do_stealthy_fetch(url, board, headless, timeout_ms, idle)

        if response is None:
            return None
        _note_board_success(board)
        return response
    except Exception as exc:
        print(f"   Fetch error ({board}): {exc}", flush=True)
        if "timeout" in str(exc).lower() or "Timeout" in type(exc).__name__:
            _note_board_timeout(board)
        return None
    finally:
        gate.release()


def _fetch_with_scrapling(
    url: str,
    board: str,
    headless: bool,
    network_idle: Optional[bool] = None,
    job_detail: bool = False,
):
    wait_for_board_ready(board)
    response = _fetch_once(
        url, board, headless, network_idle=network_idle, job_detail=job_detail
    )
    if response is not None:
        return response

    retries = max(0, int(get_settings().job_scraping_fetch_retries))
    delay = max(0.0, float(get_settings().job_scraping_fetch_retry_delay_seconds))
    for attempt in range(retries):
        print(
            f"   Retry {attempt + 1}/{retries} after {delay:.0f}s ({board}): {url}",
            flush=True,
        )
        if delay:
            time.sleep(delay)
        wait_for_board_ready(board)
        response = _fetch_once(
            url, board, headless, network_idle=network_idle, job_detail=job_detail
        )
        if response is not None:
            return response
    return None


class JobScraperSpider:
    """
    Multi-board job scraper with Scrapling's adaptive parsing.
    
    Features:
    - StealthyFetcher with Cloudflare bypass
    - Session management for cookies/headers
    - Concurrent requests with configurable delays
    """
    
    def __init__(self):
        """Initialize spider with parsers and settings."""
        self.settings = get_settings()
        
        # Initialize parsers
        self.parsers: Dict[str, BaseJobParser] = {
            "linkedin": LinkedInParser(),
            "rozee": RozeeParser(),
            "indeed": IndeedParser(),
            "mustakbil": MustakbilParser()
        }
        
        # StealthyFetcher options (passed as kwargs)
        self.fetcher_options = {
            "auto_match": True,  # Enable adaptive parsing
            "stealth": True,  # Enable stealth mode
        }
        
        print("JobScraperSpider initialized with 4 parsers")
    
    def scrape_board(
        self,
        board: str,
        query: str,
        location: str = "",
        max_pages: int = 3,
        max_jobs: int = None,
        on_jobs: Optional[Callable[[List[JobData]], None]] = None,
    ) -> List[JobData]:
        """
        Scrape single job board.
        
        Args:
            board: Board name ("linkedin", "rozee", "indeed", "mustakbil")
            query: Search query
            location: Location filter
            max_pages: Maximum pages to scrape
            max_jobs: Maximum jobs to scrape (None = unlimited)
            on_jobs: Optional callback. Called with one job as soon as its
                details are ready so the DB can upsert without waiting for
                the rest of the board.
            
        Returns:
            List of JobData dictionaries
        """
        if board not in self.parsers:
            print(f"Unknown board: {board}")
            return []

        wait_for_board_ready(board)
        
        parser = self.parsers[board]
        jobs = []
        jobs_by_url = {}
        listing_failures = 0
        details_used = 0
        max_details = int(getattr(self.settings, "job_scraping_max_detail_fetches", 0) or 0)

        emitted_desc_len: Dict[str, int] = {}

        def _emit_job(job: Optional[JobData]) -> None:
            """Upsert this job immediately; skip empty Indeed/LinkedIn JDs."""
            if not on_jobs or not job:
                return
            job_id = job.get("job_id") or ""
            desc = (job.get("description") or "").strip()
            board_name = (job.get("board") or board or "").lower()
            if not is_persistable_job(job, board_name):
                return
            job = prepare_job_for_persist(job, board_name)
            desc = (job.get("description") or "").strip()
            prev_len = emitted_desc_len.get(job_id, -1)
            if job_id and len(desc) <= prev_len:
                return
            try:
                on_jobs([job])
            except Exception as exc:
                print(f"   persist-during-scrape failed: {exc}")
                return
            if job_id:
                emitted_desc_len[job_id] = len(desc)
        
        print(f"\nScraping {board.upper()} for '{query}' in '{location}'...")
        
        def _fetch(
            url: str,
            headless: bool,
            network_idle: Optional[bool] = None,
            job_detail: bool = False,
        ):
            return _fetch_with_scrapling(
                url=url,
                board=board,
                headless=headless,
                network_idle=network_idle,
                job_detail=job_detail,
            )

        def _fetch_with_fallback(
            url: str,
            network_idle: Optional[bool] = None,
            job_detail: bool = False,
        ):
            response = _fetch(
                url, headless=True, network_idle=network_idle, job_detail=job_detail
            )
            if getattr(response, "status", None) == 410:
                # Mustakbil intermittently blocks headless requests; retry headful.
                response = _fetch(
                    url, headless=False, network_idle=network_idle, job_detail=job_detail
                )
            return response

        def _board_idle() -> bool:
            # Same as muhammadhaider02/Scrapling-Job-Boards-Scrapper.
            return True

        def _job_from_page(job_url: str, job_response, listing=None):
            """Prefer the live-page parse from the fetch worker; fall back to HTML."""
            parsed = getattr(job_response, "parsed_job", None) if job_response else None
            if parsed and len((parsed.get("description") or "").strip()) >= 50:
                if not parsed.get("job_url"):
                    parsed["job_url"] = job_url
                return parsed
            if not job_response:
                return None
            if board == "indeed" and hasattr(parser, "parse_from_response"):
                return parser.parse_from_response(job_response, listing)
            return parser.parse_job(job_response)

        def _fetch_job_page(job_url: str, headless_first: Optional[bool] = None):
            if headless_first is None:
                headless_first = board != "indeed"

            def _load(url: str, headless: bool):
                return _fetch(
                    url,
                    headless=headless,
                    network_idle=True,
                    job_detail=True,
                )

            order = (True, False) if headless_first else (False, True)
            response = None
            for headless in order:
                response = _load(job_url, headless)
                dest = destination_job_url(response, job_url)
                landed = getattr(response, "url", "") or ""
                bounced = is_auth_wall(response, job_url) or not response
                if dest and bounced and dest.split("?")[0] != landed.split("?")[0]:
                    print(f"      following rendered job URL: {dest}")
                    followed = _load(dest, headless)
                    if followed:
                        response = followed
                if response and not is_auth_wall(response, job_url):
                    break
            return response

        def _fetch_indeed_serp_jd(job_url: str, listing_card=None, serp_page: int = 1):
            """Approach 1: stay on search results (?vjk=) and read mosaic / pane JD."""
            jk = jk_from_job_url(job_url)
            if not jk or not hasattr(parser, "build_serp_vjk_url"):
                return None
            serp_url = parser.build_serp_vjk_url(query, location, jk, page=serp_page)
            print(f"      Indeed SERP pane fetch: vjk={jk[:10]}…")
            serp_resp = _fetch(
                serp_url,
                headless=True,
                network_idle=True,
                job_detail=False,
            )
            if not serp_resp or is_auth_wall(serp_resp, serp_url):
                return None
            serp_job = parser.parse_serp_detail(serp_resp, job_url, listing_card)
            if not serp_job or len((serp_job.get("description") or "")) < 50:
                return None
            print(
                f"      JD from search pane ({len(serp_job['description'])} chars)"
            )
            try:
                serp_resp.parsed_job = dict(serp_job)
            except Exception:
                pass
            return serp_resp

        def _indeed_interactive_fetch(job_url: str, listing_card=None):
            """Approach 2: headful browser — user passes captcha; scrape #jobDescriptionText div."""
            if not getattr(self.settings, "job_scraping_indeed_interactive", False):
                return None
            from scraper.indeed_interactive import fetch_indeed_interactive

            jk = jk_from_job_url(job_url)
            urls = []
            if jk and hasattr(parser, "build_serp_vjk_url"):
                urls.append(parser.build_serp_vjk_url(query, location, jk))
            urls.append(job_url)
            wait_s = float(
                getattr(self.settings, "job_scraping_indeed_interactive_wait_seconds", 300)
                or 300
            )
            print(
                f"      Indeed interactive browser (solve captcha if shown, "
                f"up to {wait_s:.0f}s)…"
            )
            payload = fetch_indeed_interactive(
                urls,
                wait_seconds=wait_s,
                headless=False,
            )
            if not payload:
                return None
            desc = (payload.get("description") or "").strip()
            if len(desc) < 50:
                return None
            listing = listing_card or {}
            job = parser.parse_from_html(
                payload.get("html") or "",
                job_url,
                listing,
            )
            if not job:
                job = {
                    "job_id": parser.generate_job_id(
                        listing.get("title") or "Untitled",
                        listing.get("company") or "Unknown",
                        listing.get("location") or "Pakistan",
                    ),
                    "title": listing.get("title") or "Untitled",
                    "company": listing.get("company") or "Unknown",
                    "location": listing.get("location") or "Pakistan",
                    "job_url": job_url,
                    "board": board,
                    "description": desc,
                    "skills": [],
                }
            elif len((job.get("description") or "")) < 50:
                job = dict(job)
                job["description"] = desc
            print(f"      JD from interactive browser ({len(desc)} chars)")
            return FetchedPage(
                str(payload.get("final_url") or job_url),
                200,
                str(payload.get("html") or ""),
                parsed_job=dict(job),
            )

        def _fetch_indeed_job_page(
            job_url: str,
            listing_card=None,
            serp_page: int = 1,
        ):
            serp = _fetch_indeed_serp_jd(job_url, listing_card, serp_page=serp_page)
            if serp:
                return serp
            view = _fetch_job_page(job_url)
            if view and not is_auth_wall(view, job_url):
                return view
            interactive = _indeed_interactive_fetch(job_url, listing_card)
            return interactive or view

        for page in range(1, max_pages + 1):
            try:
                search_url = parser.build_search_url(query, location, page)
                print(f"   Page {page}: {search_url}")

                listing_response = _fetch_with_fallback(search_url, network_idle=True)

                if not listing_response:
                    listing_failures += 1
                    print(f"   Failed to fetch page {page}")
                    if listing_failures >= 2:
                        print(f"   Stopping {board} for this role after repeated listing failures")
                        break
                    continue

                if is_auth_wall(listing_response, search_url):
                    listing_failures += 1
                    print(f"   Listing hit auth/bot wall for {board}")
                    if listing_failures >= 2:
                        print(f"   Stopping {board} for this role after listing auth walls")
                        break
                    continue

                listing_failures = 0

                job_urls = parser.parse_listing(listing_response) or []
                listing_jobs = list(getattr(parser, "_listing_jobs", None) or [])
                if hasattr(parser, "_listing_jobs"):
                    parser._listing_jobs = []

                listing_only = not getattr(parser, "enrich_from_detail_pages", True)
                for lj in listing_jobs:
                    if max_jobs and len(jobs) >= max_jobs:
                        break
                    job_url = lj.get("job_url") or ""
                    if job_url and job_url in jobs_by_url:
                        continue
                    jobs.append(lj)
                    jobs_by_url[job_url] = len(jobs) - 1
                    print(f"      {lj['title']} at {lj['company']}")
                    if listing_only:
                        _emit_job(lj)

                if listing_jobs and listing_only:
                    if max_jobs and len(jobs) >= max_jobs:
                        print(f"   Reached max_jobs limit ({max_jobs})")
                        break
                    time.sleep(self.settings.job_scraping_download_delay * 2)
                    continue

                if not job_urls:
                    job_urls = [j.get("job_url") for j in listing_jobs if j.get("job_url")]

                if not job_urls and not listing_jobs:
                    print(f"   No jobs found on page {page}")
                    break

                if max_jobs:
                    remaining_slots = max_jobs - len(jobs)
                    extra_urls = [u for u in job_urls if u not in jobs_by_url]
                    if remaining_slots <= 0:
                        job_urls = [u for u in job_urls if u in jobs_by_url]
                    else:
                        job_urls = [u for u in job_urls if u in jobs_by_url] + extra_urls[:remaining_slots]

                auth_walls = 0
                for i, job_url in enumerate(job_urls, 1):
                    if max_details > 0 and details_used >= max_details:
                        print(
                            f"   Skipping remaining {board} detail tabs "
                            f"(cap {max_details}); listing cards kept"
                        )
                        break
                    try:
                        print(f"   Job {i}/{len(job_urls)}: {job_url}")
                        details_used += 1

                        listing_card = None
                        if job_url in jobs_by_url:
                            listing_card = jobs[jobs_by_url[job_url]]

                        if board == "indeed":
                            job_response = _fetch_indeed_job_page(
                                job_url, listing_card, serp_page=page
                            )
                        else:
                            job_response = _fetch_job_page(job_url)
                        job_data = _job_from_page(
                            job_url, job_response, listing_card
                        )
                        desc_len = len((job_data or {}).get("description") or "")

                        # Reference scraper retries headful when the JD is missing
                        # and this is not a login bounce.
                        if (
                            board != "indeed"
                            and desc_len < 50
                            and job_response
                            and not is_auth_wall(job_response, job_url)
                        ):
                            print("      retrying job tab headful for JD")
                            headful = _fetch_job_page(
                                job_url, headless_first=False
                            )
                            parsed = _job_from_page(
                                job_url, headful, listing_card
                            )
                            if parsed and len(parsed.get("description") or "") > desc_len:
                                job_data = parsed
                                desc_len = len(job_data.get("description") or "")

                        if board == "indeed" and desc_len < 50:
                            interactive = _indeed_interactive_fetch(
                                job_url, listing_card
                            )
                            if interactive:
                                parsed = _job_from_page(
                                    job_url, interactive, listing_card
                                )
                                if parsed and len(parsed.get("description") or "") > desc_len:
                                    job_data = parsed
                                    job_response = interactive
                                    desc_len = len(job_data.get("description") or "")

                        if job_data and desc_len >= 50:
                            if job_url in jobs_by_url:
                                idx = jobs_by_url[job_url]
                                jobs[idx] = merge_listing_with_detail(jobs[idx], job_data)
                                print(f"      Enriched {jobs[idx]['title']} at {jobs[idx]['company']}")
                                _emit_job(jobs[idx])
                            elif not max_jobs or len(jobs) < max_jobs:
                                if not job_data.get("job_url"):
                                    job_data["job_url"] = job_url
                                jobs.append(job_data)
                                jobs_by_url[job_url] = len(jobs) - 1
                                print(f"      {job_data['title']} at {job_data['company']}")
                                _emit_job(job_data)
                        elif is_auth_wall(job_response, job_url) or not job_response:
                            auth_walls += 1
                            reason = "auth/bot wall" if job_response else "fetch failed"
                            print(f"      {reason}; keeping listing card")
                            if job_url in jobs_by_url:
                                idx = jobs_by_url[job_url]
                                _emit_job(jobs[idx])
                        else:
                            print("      Failed to parse job; keeping listing card")
                            if job_url in jobs_by_url:
                                idx = jobs_by_url[job_url]
                                _emit_job(jobs[idx])

                        time.sleep(self.settings.job_scraping_download_delay)

                        if max_jobs and len(jobs) >= max_jobs:
                            print(f"   Reached max_jobs limit ({max_jobs})")
                            break

                    except Exception as e:
                        print(f"      Job scrape error: {e}")
                        continue

                if max_jobs and len(jobs) >= max_jobs:
                    break
                if max_details > 0 and details_used >= max_details:
                    break

                time.sleep(self.settings.job_scraping_download_delay * 2)

            except Exception as e:
                print(f"   Page {page} error: {e}")
                continue
        
        print(f"{board.upper()}: Scraped {len(jobs)} jobs\n")
        return jobs
    
    def scrape_all_boards(
        self,
        query: str,
        location: str = "Pakistan",
        boards: Optional[List[str]] = None,
        max_pages_per_board: int = 2,
        max_jobs_per_board: int = None,
        on_jobs: Optional[Callable[[List[JobData]], None]] = None,
    ) -> List[JobData]:
        """
        Scrape multiple job boards.
        
        Args:
            query: Search query
            location: Location filter
            boards: List of boards to scrape (None = all)
            max_pages_per_board: Max pages per board
            max_jobs_per_board: Max jobs to scrape per board (None = unlimited)
            
        Returns:
            Combined list of jobs from all boards
        """
        if boards is None:
            boards = ["rozee", "mustakbil", "indeed", "linkedin"]
        
        all_jobs = []
        
        print(f"\n{'='*60}")
        print(f"Starting multi-board scraping:")
        print(f"  Query: {query}")
        print(f"  Location: {location}")
        print(f"  Boards: {', '.join(boards)}")
        print(f"  Max pages per board: {max_pages_per_board}")
        if max_jobs_per_board:
            print(f"  Max jobs per board: {max_jobs_per_board}")
        print(f"{'='*60}\n")
        
        for board in boards:
            try:
                jobs = self.scrape_board(
                    board=board,
                    query=query,
                    location=location,
                    max_pages=max_pages_per_board,
                    max_jobs=max_jobs_per_board,
                    on_jobs=on_jobs,
                )
                all_jobs.extend(jobs)
            
            except Exception as e:
                print(f"Board {board} failed: {e}")
                continue
        
        print(f"\n{'='*60}")
        print(f"Total jobs scraped: {len(all_jobs)}")
        print(f"{'='*60}\n")
        
        return all_jobs
    


# Global spider instance
_spider: Optional[JobScraperSpider] = None


def get_spider() -> JobScraperSpider:
    """
    Get or create global spider instance.
    
    Returns:
        JobScraperSpider instance
    """
    global _spider
    if _spider is None:
        _spider = JobScraperSpider()
    return _spider
