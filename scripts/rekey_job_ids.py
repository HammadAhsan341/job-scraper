"""One-time migration: re-key stored jobs to board-native job_ids.

Without it, the first scrape after the job_id change inserts every live
posting again under its new id, and browse shows both copies until the old
rows age out. Rows that map to the same posting are merged, keeping the
longest description.

    uv run python scripts/rekey_job_ids.py            # dry run: counts only
    uv run python scripts/rekey_job_ids.py --apply    # write changes
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.job_identity import stable_job_id  # noqa: E402
from services.supabase import get_supabase_service  # noqa: E402

PAGE = 1000


def _load_rows(client) -> list[dict]:
    rows, offset = [], 0
    while True:
        page = (
            client.table("jobs")
            .select("job_id,url,job_source,job_description")
            .order("job_id")
            .range(offset, offset + PAGE - 1)
            .execute()
        )
        batch = page.data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def _plan(rows: list[dict]) -> tuple[dict[str, str], list[str], int]:
    """Return (renames old->new, old ids to delete, rows without a native id)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    fallback = 0
    for row in rows:
        new_id = stable_job_id({"board": row.get("job_source"), "job_url": row.get("url")})
        if not new_id:
            fallback += 1
            new_id = row["job_id"]
        groups[new_id].append(row)

    renames: dict[str, str] = {}
    deletes: list[str] = []
    for new_id, members in groups.items():
        winner = max(members, key=lambda r: len(r.get("job_description") or ""))
        deletes.extend(r["job_id"] for r in members if r is not winner)
        if winner["job_id"] != new_id:
            renames[winner["job_id"]] = new_id
    return renames, deletes, fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    client = get_supabase_service().client
    rows = _load_rows(client)
    renames, deletes, fallback = _plan(rows)
    print(f"rows: {len(rows)}  re-keyed: {len(renames)}  merged duplicates: {len(deletes)}  "
          f"no native id (kept): {fallback}")
    if not args.apply:
        print("dry run: nothing written (pass --apply to write)")
        return 0

    # Delete merged losers first: one of them may already hold a winner's new id.
    for i in range(0, len(deletes), 100):
        chunk = deletes[i:i + 100]
        client.table("jobs").delete().in_("job_id", chunk).execute()
        client.table("processed_jobs").delete().in_("job_id", chunk).execute()
    now = datetime.utcnow().isoformat()
    for old_id, new_id in renames.items():
        client.table("jobs").update({"job_id": new_id}).eq("job_id", old_id).execute()
        client.table("processed_jobs").delete().eq("job_id", old_id).execute()
        client.table("processed_jobs").upsert({"job_id": new_id, "processed_at": now}, on_conflict="job_id").execute()
    print(f"applied: deleted {len(deletes)}, re-keyed {len(renames)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
