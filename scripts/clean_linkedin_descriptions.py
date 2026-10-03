"""One-time cleanup: strip LinkedIn UI text from stored job descriptions.

Before the extractor fix, every LinkedIn JD ended with the collapse-button
labels and the job-criteria list ("Show more Show less Seniority level ...").
A re-scrape cannot fix those rows: the cleaned text is shorter, so the
"keep the longer description" merge keeps the old one.

    uv run python scripts/clean_linkedin_descriptions.py           # dry run
    uv run python scripts/clean_linkedin_descriptions.py --apply   # write
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.boards.linkedin_jd import strip_linkedin_chrome  # noqa: E402
from services.supabase import get_supabase_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    client = get_supabase_service().client
    rows = (
        client.table("jobs")
        .select("job_id,job_description")
        .eq("job_source", "linkedin")
        .limit(5000)
        .execute()
        .data
        or []
    )
    changes = []
    for row in rows:
        before = row.get("job_description") or ""
        after = strip_linkedin_chrome(before)
        if after and after != before:
            changes.append((row["job_id"], before, after))

    print(f"linkedin rows: {len(rows)}  to clean: {len(changes)}")
    if changes:
        job_id, before, after = changes[0]
        print(f"sample {job_id}: {len(before)} -> {len(after)} chars")
        print(f"  removed tail: ...{before[len(after):][:160]!r}")
    if not args.apply:
        print("dry run: nothing written (pass --apply to write)")
        return 0
    for job_id, _before, after in changes:
        client.table("jobs").update({"job_description": after}).eq("job_id", job_id).execute()
    print(f"applied: cleaned {len(changes)} descriptions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
