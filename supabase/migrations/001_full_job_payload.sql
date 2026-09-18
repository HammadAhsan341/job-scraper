-- Persist the full enriched job and replace Redis processed-set tracking.
-- Apply in the Supabase SQL editor or via `supabase db push`.

alter table if exists public.jobs
    add column if not exists posted_date text,
    add column if not exists posted_at timestamptz,
    add column if not exists salary_raw text,
    add column if not exists salary_normalized jsonb,
    add column if not exists description_sections jsonb,
    add column if not exists skills_categorized jsonb,
    add column if not exists experience_parsed jsonb,
    add column if not exists enrichment_confidence numeric,
    add column if not exists enrichment_timestamp timestamptz,
    add column if not exists raw_payload jsonb;

create table if not exists public.processed_jobs (
    job_id text primary key,
    processed_at timestamptz not null default timezone('utc', now())
);

create index if not exists processed_jobs_processed_at_idx
    on public.processed_jobs (processed_at);
