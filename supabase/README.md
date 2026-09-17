# Supabase schema

Run [`migrations/001_full_job_payload.sql`](migrations/001_full_job_payload.sql) in the Supabase SQL editor (or `supabase db push`) before the next scrape.

That migration:

1. Adds the full-enrichment columns to `jobs` (`salary_*`, sections, categorized skills, confidence, `raw_payload`, and so on).
2. Creates `processed_jobs` so deduplication no longer depends on Redis.

`raw_html` is not stored. Everything else from the in-memory enriched job is written by `job_to_record`.
