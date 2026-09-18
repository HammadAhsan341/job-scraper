"""
Bulk pipeline runner for all permitted roles:
Scraping -> Enrichment/Cleaning/Skill extraction -> Optional vetting -> DB upsert.

Run this to execute the end-to-end scout stack in one go.
"""

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.state import AgentState
from pipeline.enricher_node import job_enricher_node
from langchain_core.messages import HumanMessage
from core.settings import get_settings
from services.job_record import incoming_improves_stored, merge_incoming_over_stored
from services.supabase import get_supabase_service
from scraper.listing_persist import is_persistable_job, prepare_job_for_persist
from scraper.spider import JobScraperSpider

PERSIST_BATCH_SIZE = 100


def _batched(items, batch_size):
    size = max(1, int(batch_size or 1))
    for i in range(0, len(items), size):
        yield items[i:i + size]


@contextmanager
def _suppress_scrapling_logs():
    """Filter noisy Scrapling [INFO]/[ERROR] lines from stdout/stderr."""
    class _StreamFilter:
        def __init__(self, stream):
            self.stream = stream

        def write(self, data):
            if "] INFO:" in data or "] ERROR:" in data:
                return
            try:
                self.stream.write(data)
            except UnicodeEncodeError:
                self.stream.write(data.encode("ascii", errors="ignore").decode("ascii"))

        def flush(self):
            self.stream.flush()

    original_out = sys.stdout
    original_err = sys.stderr
    sys.stdout = _StreamFilter(original_out)
    sys.stderr = _StreamFilter(original_err)
    try:
        yield
    finally:
        sys.stdout = original_out
        sys.stderr = original_err


class _TotalsAccumulator:
    """Thread-safe counters for pipeline summary."""

    def __init__(self):
        self._lock = threading.Lock()
        self.scraped = 0
        self.enriched = 0
        self.db_upserts = 0
        self.failed_roles: list = []

    def add_scraped(self, n: int):
        with self._lock:
            self.scraped += n

    def add_enriched(self, n: int):
        with self._lock:
            self.enriched += n

    def add_db_upserts(self, n: int):
        with self._lock:
            self.db_upserts += n

    def add_failed_role(self, role: str):
        with self._lock:
            self.failed_roles.append(role)


def _persist_scraped_jobs(raw_jobs, supabase, totals, batch_size, state):
    """Dedup, enrich, and upsert one board's jobs immediately."""
    if not raw_jobs:
        print("   Raw jobs: 0")
        return 0

    totals.add_scraped(len(raw_jobs))
    print(f"   Raw jobs: {len(raw_jobs)}")

    job_ids = [job.get("job_id") for job in raw_jobs if job.get("job_id")]
    stored_by_id: dict = {}
    getter = getattr(supabase, "get_jobs_by_ids", None)
    if callable(getter):
        fetched = getter(job_ids)
        if isinstance(fetched, dict):
            stored_by_id = fetched

    new_jobs = []
    refill_count = 0
    duplicate_count = 0
    for job in raw_jobs:
        job_id = job.get("job_id")
        stored = stored_by_id.get(job_id) if job_id else None
        # processed_jobs alone is not enough: deleting jobs rows leaves those
        # IDs marked, and the next scrape would skip forever with an empty table.
        already = bool(job_id) and supabase.is_job_processed(job_id) and stored is not None
        if not already:
            board = (job.get("board") or "").lower()
            if not is_persistable_job(job, board):
                print(f"   Skipping {board} card without usable text: {job.get('title')}")
                continue
            new_jobs.append(prepare_job_for_persist(job, board))
        elif incoming_improves_stored(job, stored):
            new_jobs.append(merge_incoming_over_stored(job, stored))
            refill_count += 1
        else:
            duplicate_count += 1

    print(f"   New jobs: {len(new_jobs) - refill_count}")
    print(f"   Backfilled missing fields: {refill_count}")
    print(f"   Duplicates filtered: {duplicate_count}")

    if not new_jobs:
        return 0

    enrich_input = dict(state)
    enrich_input["raw_job_list"] = new_jobs
    enrich_input["scraping_status"] = "completed"
    enrich_result = job_enricher_node(enrich_input)
    enriched_jobs = enrich_result.get("raw_job_list", new_jobs)
    totals.add_enriched(len(enriched_jobs))
    print(f"   Enriched jobs: {len(enriched_jobs)}")

    affected = 0
    if enriched_jobs:
        for batch in _batched(enriched_jobs, batch_size):
            written = int(supabase.bulk_insert_jobs(batch) or 0)
            affected += written
            if written:
                for job in batch:
                    job_id = job.get("job_id")
                    if job_id:
                        supabase.mark_job_processed(job_id)
            print(f"   DB upsert batch: {written} jobs")
        totals.add_db_upserts(affected)
        print(f"   DB upserts: {affected}")
    return affected


def _process_single_role(role, index, total, settings, supabase, totals, batch_size=PERSIST_BATCH_SIZE):
    """
    Process one role board-by-board so a hang on LinkedIn cannot delay
    Rozee/Mustakbil/Indeed writes.
    Returns True on success, False on failure.
    """
    print(f"\n[{index}/{total}] Role: {role}")
    print("-" * 60)

    spider = JobScraperSpider()

    state: AgentState = {
        "messages": [HumanMessage(content=f"Find {role} jobs")],
        "user_id": "bulk_scraper_admin",
        "search_query": role,
        "raw_job_list": [],
        "scraping_status": "pending",
        "current_page": 1,
        "error": None,
        "retry_count": 0,
    }

    try:
        for board in settings.job_scraping_boards:
            print(f"   Board: {board}")
            try:
                buffer = []
                queued = set()

                def _queue_jobs(raw_jobs, _board=board):
                    for job in raw_jobs or []:
                        job_id = job.get("job_id")
                        desc = (job.get("description") or "").strip()
                        board_name = (job.get("board") or _board or "").lower()
                        if not is_persistable_job(job, board_name):
                            continue
                        job = prepare_job_for_persist(job, board_name)
                        desc = (job.get("description") or "").strip()
                        key = (job_id, len(desc))
                        if not job_id or key in queued:
                            continue
                        queued.add(key)
                        buffer.append(job)

                def persist(raw_jobs, _board=board, flush=False):
                    _queue_jobs(raw_jobs, _board)
                    while len(buffer) >= batch_size:
                        chunk = buffer[:batch_size]
                        del buffer[:batch_size]
                        print(f"   Persisting {_board} batch ({len(chunk)} jobs)")
                        _persist_scraped_jobs(
                            chunk, supabase, totals, batch_size, state
                        )
                    if flush and buffer:
                        leftover = list(buffer)
                        buffer.clear()
                        print(f"   Persisting {_board} leftover ({len(leftover)} jobs)")
                        _persist_scraped_jobs(
                            leftover, supabase, totals, batch_size, state
                        )

                raw_jobs = spider.scrape_board(
                    board=board,
                    query=role,
                    location="Pakistan",
                    max_pages=settings.job_scraping_max_pages_per_board,
                    max_jobs=settings.job_scraping_max_jobs_per_board,
                    on_jobs=persist,
                )
                persist(raw_jobs, flush=True)
            except Exception as board_exc:
                print(f"   Board {board} failed: {board_exc}")
                continue
        return True

    except Exception as exc:
        totals.add_failed_role(role)
        print(f"   Role failed: {exc}")
        return False


def run_bulk_pipeline():
    """Run scrape + enrichment for all permitted roles and upsert in batches."""
    settings = get_settings()
    roles = settings.permitted_roles
    batch_size = PERSIST_BATCH_SIZE
    supabase = get_supabase_service()
    max_workers = settings.job_scraping_workers

    # Pre-load the enricher singleton before threads start (avoids lazy-init race)
    from pipeline.enricher import get_enricher
    get_enricher()

    supabase.delete_stale_jobs(days=settings.job_stale_after_days)
    clearer = getattr(supabase, "clear_orphan_processed_jobs", None)
    if callable(clearer):
        clearer()

    print("\n" + "=" * 78)
    print(
        f"RUNNING BULK PIPELINE FOR {len(roles)} ROLES "
        f"({max_workers} worker(s), "
        f"max {settings.job_scraping_max_concurrent_fetches} fetches/board)"
    )
    print("=" * 78 + "\n")

    totals = _TotalsAccumulator()

    # Main pass
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _process_single_role,
                    role, idx, len(roles),
                    settings, supabase, totals, batch_size,
                ): role
                for idx, role in enumerate(roles, 1)
            }
            for future in as_completed(futures):
                role = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    print(f"   Unexpected worker error for '{role}': {exc}")
                    totals.add_failed_role(role)
    else:
        for idx, role in enumerate(roles, 1):
            _process_single_role(
                role, idx, len(roles),
                settings, supabase, totals, batch_size,
            )

    # Retry after a cooldown so a burst of blocks is not immediately retried.
    failed = totals.failed_roles[:]
    if failed:
        backoff = max(0.0, settings.job_scraping_retry_backoff_seconds)
        print("\n" + "=" * 78)
        print(
            f"RETRYING {len(failed)} FAILED ROLES (sequential, "
            f"{backoff:.0f}s cooldown first)"
        )
        print("=" * 78 + "\n")
        if backoff:
            time.sleep(backoff)

        totals.failed_roles.clear()

        for idx, role in enumerate(failed, 1):
            _process_single_role(
                role, idx, len(failed),
                settings, supabase, totals, batch_size,
            )

    # Summary
    print("\n" + "=" * 78)
    print("BULK PIPELINE SUMMARY")
    print(f"Roles processed: {len(roles)}")
    print(f"Roles failed (after retry): {len(totals.failed_roles)}")
    if totals.failed_roles:
        print(f"Failed roles: {', '.join(totals.failed_roles)}")
    print(f"Jobs scraped: {totals.scraped}")
    print(f"Jobs enriched: {totals.enriched}")
    print(f"DB upserts: {totals.db_upserts}")
    print("=" * 78 + "\n")

    return {
        "roles": len(roles),
        "scraped": totals.scraped,
        "enriched": totals.enriched,
        "vetted": 0,
        "db_upserts": totals.db_upserts,
        "failed_roles": len(totals.failed_roles),
    }


def _configure_stdio():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError, ValueError):
            pass


if __name__ == "__main__":
    _configure_stdio()
    with _suppress_scrapling_logs():
        run_bulk_pipeline()
