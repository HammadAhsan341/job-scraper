"""
Digital Scout Node: Autonomous job discovery agent.

Orchestrates scraping, Supabase deduplication, and storage for the multi-agent workflow.
"""

from typing import Any, Dict

from langchain_core.messages import AIMessage

from core.role_filters import extract_allowed_role_from_query
from core.settings import get_settings
from core.state import AgentState
from scraper.spider import get_spider
from services.job_record import incoming_improves_stored, merge_incoming_over_stored
from services.supabase import get_supabase_service


def _is_job_search_query(search_query: str) -> bool:
    """Heuristic guard to ensure scout only handles job-intent queries."""
    normalized = (search_query or "").lower().strip()
    if not normalized:
        return False

    job_signals = {
        "job", "jobs", "hiring", "career", "careers", "vacancy", "vacancies",
        "position", "positions", "role", "roles", "opening", "openings",
        "developer", "engineer", "analyst", "manager", "designer", "intern",
        "accountant", "teacher", "nurse", "remote", "onsite", "full-time",
        "part-time", "contract", "internship",
    }

    non_job_signals = {
        "adopt", "adoption", "cat", "cats", "dog", "dogs", "pet", "pets",
        "kittens", "puppies", "buy", "sell", "rental", "rent", "marriage",
        "dating", "recipe", "restaurant", "hotel", "news",
    }

    has_job_signal = any(signal in normalized for signal in job_signals)
    has_non_job_signal = any(signal in normalized for signal in non_job_signals)

    if has_non_job_signal and not has_job_signal:
        return False

    return has_job_signal


def digital_scout_node(state: AgentState) -> Dict[str, Any]:
    """
    Digital Scout: Scrape jobs matching user query.
    
    Responsibilities:
    1. Parse search query from user input
    2. Scrape jobs from multiple boards
    3. Deduplicate via Redis
    4. Store in Supabase
    5. Update state with raw_job_list
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state dictionary
    """
    print("\n" + "="*60)
    print("DIGITAL SCOUT ACTIVATED")
    print("="*60 + "\n")
    
    # Extract search parameters from state
    search_query = state.get("search_query", "")
    user_id = state.get("user_id", "")
    
    if not search_query:
        error_msg = "No search query provided"
        print(f"{error_msg}")
        return {
            "scraping_status": "failed",
            "error": error_msg,
            "raw_job_list": [],
            "messages": [AIMessage(content=f"Error: {error_msg}")]
        }

    if not _is_job_search_query(search_query):
        error_msg = (
            "This query does not look like a job search. "
            "Please provide a role-based job query, for example: "
            "'data engineer jobs in lahore'."
        )
        print(f"{error_msg}")
        return {
            "scraping_status": "failed",
            "error": error_msg,
            "raw_job_list": [],
            "messages": [AIMessage(content=error_msg)],
        }

    settings = get_settings()
    matched_role = extract_allowed_role_from_query(search_query, settings.permitted_roles)
    if not matched_role:
        error_msg = (
            "This role is not in the approved scraping list. "
            "Please use one of the configured roles."
        )
        print(f"{error_msg}")
        return {
            "scraping_status": "failed",
            "error": error_msg,
            "raw_job_list": [],
            "messages": [AIMessage(content=error_msg)],
        }
    
    # Parse location from query (basic implementation)
    location = "Pakistan"
    if " in " in search_query.lower():
        parts = search_query.lower().split(" in ")
        location = parts[1].strip().title()

    # Lock scraping to approved role labels to keep queries consistent.
    search_query = matched_role
    
    print(f"Search Query: {search_query}")
    print(f"Location: {location}\n")
    
    # Initialize services
    spider = get_spider()
    supabase = get_supabase_service()
    
    try:
        # Scrape jobs from all boards
        raw_jobs = spider.scrape_all_boards(
            query=search_query,
            location=location,
            boards=["linkedin", "rozee", "indeed", "mustakbil"],
            max_pages_per_board=settings.job_scraping_max_pages_per_board,
            max_jobs_per_board=settings.job_scraping_max_jobs_per_board,
        )
        
        if not raw_jobs:
            print("No jobs found across all boards")
            return {
                "scraping_status": "completed",
                "raw_job_list": [],
                "current_page": 1,
                "messages": [AIMessage(content="No jobs found matching your query. Try different keywords.")]
            }
        
        print(f"\nDeduplicating and storing {len(raw_jobs)} jobs...")
        
        job_ids = [job.get("job_id") for job in raw_jobs if job.get("job_id")]
        stored_by_id = {}
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
            already = bool(job_id) and supabase.is_job_processed(job_id) and stored is not None
            if not already:
                board = (job.get("board") or "").lower()
                desc = (job.get("description") or "").strip()
                if board in {"linkedin", "indeed"} and len(desc) < 50:
                    continue
                new_jobs.append(job)
            elif incoming_improves_stored(job, stored):
                new_jobs.append(merge_incoming_over_stored(job, stored))
                refill_count += 1
            else:
                duplicate_count += 1

        print(f"{len(new_jobs) - refill_count} new jobs")
        print(f"{refill_count} backfilled missing fields")
        print(f"{duplicate_count} duplicates filtered\n")
        
        if new_jobs:
            stored = 0
            for job in new_jobs:
                written = int(supabase.bulk_insert_jobs([job]) or 0)
                if written:
                    stored += 1
                    job_id = job.get("job_id")
                    if job_id:
                        supabase.mark_job_processed(job_id)
            if stored == 0:
                print("Failed to store some jobs in database")
        
        # Update state
        print(f"\nDigital Scout completed: {len(new_jobs)} jobs scraped\n")
        
        # Build summary message
        board_counts = {}
        for job in new_jobs:
            board = job["board"]
            board_counts[board] = board_counts.get(board, 0) + 1
        
        summary = f"Found {len(new_jobs)} jobs:\n"
        for board, count in board_counts.items():
            summary += f"  • {board.upper()}: {count} jobs\n"
        
        return {
            "scraping_status": "completed",
            "raw_job_list": new_jobs,
            "current_page": 1,
            "error": None,
            "messages": [AIMessage(content=summary)]
        }
    
    except Exception as e:
        error_msg = f"Scraping error: {str(e)}"
        print(f"\n{error_msg}\n")
        
        # Log error to Supabase
        supabase.log_scraping_error(
            url=search_query,
            error=error_msg,
            retries=state.get("retry_count", 0)
        )
        
        return {
            "scraping_status": "failed",
            "error": error_msg,
            "raw_job_list": state.get("raw_job_list", []),
            "messages": [AIMessage(content=f"Scraping failed: {error_msg}")]
        }


# Export node
__all__ = ["digital_scout_node"]
