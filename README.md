<div align="center">

# Job Board Scraper

**Multi-board job scraper for Pakistan**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![Scrapling](https://img.shields.io/badge/Scrapling-0.4.7-FF6B35)](https://github.com/D4Vinci/Scrapling)
[![Supabase](https://img.shields.io/badge/Supabase-2.30-3ECF8E?logo=supabase&logoColor=white)](https://supabase.com)
[![uv](https://img.shields.io/badge/uv-package%20manager-7C3AED)](https://github.com/astral-sh/uv)

Scrape LinkedIn and Indeed for job listings in Pakistan. Cleans and enriches each posting, deduplicates in Supabase, and upserts the full enriched record.

</div>

---

## Table of Contents

- [Overview](#overview)
- [Pipeline](#pipeline)
- [What Supabase stores](#what-supabase-stores)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Deployment](#deployment)
- [Roles Covered](#roles-covered)
- [Project Structure](#project-structure)
- [Frontend](#frontend)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

`main.py` iterates over a configured list of permitted roles and runs the full pipeline for each one using a configurable number of parallel workers (default: sequential). The spider fetches listing pages and individual job postings using Scrapling's `StealthyFetcher` which handles Cloudflare challenges and anti-bot detection. Each parser extracts structured fields from the board's HTML. Roles that fail during the main pass are retried once sequentially after all other roles complete.

New jobs are checked against a Supabase `processed_jobs` table so duplicates are never written twice. Unique jobs pass through the enricher which cleans the description, matches skills against a master Excel list, parses experience level and year ranges, normalises salary strings and detects education and job type. The full enriched record is upserted to Supabase in batches of 200.

A separate `digital_scout_node` in `pipeline/scout.py` handles interactive, query-driven scraping with role-allowlist enforcement. It is not called by `main.py` but is available for integration into a wider agent workflow.

---

## Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  Permitted roles (PERMITTED_ROLES_1 / _2 env var)       │
└──────────────────────────┬──────────────────────────────┘
                           │
                    ┌──────┴──────┐
                    │ Worker Pool │
                    └──────┬──────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Spider  (scraper/spider.py)                            │
│  StealthyFetcher with Cloudflare bypass                 │
│  One parser per board - LinkedIn and Indeed             │
│  Each with adaptive CSS selectors                       │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Supabase Deduplication  (services/supabase.py)         │
│  SHA-256 job ID checked against processed_jobs          │
│  Stale rows are deleted so closed jobs can be recrawled │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Enricher  (pipeline/enricher.py)                       │
│  Description cleaning + section splitting               │
│  Skill extraction from master Excel list                │
│  Experience level + year-range parsing                  │
│  Salary normalisation + education/job-type detection    │
│  Enrichment confidence score per job                    │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Supabase Upsert  (services/supabase.py)                │
│  Full enriched payload + structured columns             │
│  Batched upsert on conflict (job_id)                    │
│  200 records per batch                                  │
└─────────────────────────────────────────────────────────┘
```

---

## What Supabase stores

Apply [`supabase/migrations/001_full_job_payload.sql`](supabase/migrations/001_full_job_payload.sql) once before the next scrape. The upsert writes the **full enriched job**, not the old 13-column subset.

### `jobs` columns written on every upsert

| Column                  | Source                              | Notes                                               |
| :---------------------- | :---------------------------------- | :-------------------------------------------------- |
| `job_id`                | SHA-256 of title\|company\|location | Conflict key                                        |
| `job_title`             | `title`                             |                                                     |
| `job_description`       | cleaned `description`               | Original uncleaned text is not kept separately      |
| `skills_required`       | flattened skill list                | Loaded from Excel columns B–K, not role names       |
| `experience_required`   | parsed min years                    | `0` is stored for entry-level; unknown stays `NULL` |
| `education_required`    | detected degree                     |                                                     |
| `job_type`              | employment type                     |                                                     |
| `location`              | location                            |                                                     |
| `industry`              | industry                            | Parsers do not currently populate this              |
| `company`               | company                             |                                                     |
| `url`                   | job URL                             |                                                     |
| `job_source`            | board name                          |                                                     |
| `date_scrapped`         | scrape timestamp                    | Defaults to now                                     |
| `posted_date`           | board posted date                   |                                                     |
| `salary_raw`            | raw salary string                   |                                                     |
| `salary_normalized`     | jsonb                               | `currency`, `min`, `max`, `period`, `raw`           |
| `description_sections`  | jsonb                               | responsibilities, requirements, etc.                |
| `skills_categorized`    | jsonb                               | technical / soft / tools                            |
| `experience_parsed`     | jsonb                               | level + year range                                  |
| `enrichment_confidence` | 0–1 score                           |                                                     |
| `enrichment_timestamp`  | UTC timestamp                       |                                                     |
| `raw_payload`           | jsonb snapshot                      | Full in-memory object except `raw_html`             |

### Intentionally not stored

| Field                 | Reason                                              |
| :-------------------- | :-------------------------------------------------- |
| `raw_html`            | Large page dumps; omit unless you need replay/debug |
| Uncleaned description | Replaced by the cleaned `job_description`           |

### Dedup table

`processed_jobs(job_id, processed_at)` replaces the Redis processed set. When stale jobs are deleted, matching `processed_jobs` rows are removed so the same posting can be scraped again.

---

## Features

| Feature                    | Description                                                                               |
| :------------------------- | :---------------------------------------------------------------------------------------- |
| **Multi-board scraping**   | LinkedIn and Indeed with two alternating role sets                                        |
| **Anti-bot bypass**        | Scrapling `StealthyFetcher` with Cloudflare solver and headless/headful fallback          |
| **Role allowlist**         | Only scrapes roles listed in `PERMITTED_ROLES` - rejects everything else                  |
| **Supabase deduplication** | SHA-256 job IDs tracked in `processed_jobs`; stale IDs are cleared so listings can return |
| **Description cleaning**   | HTML entity decoding, whitespace normalisation, section splitting, sentence deduplication |
| **Skill extraction**       | Word-boundary regex matching against a configurable master Excel skill list               |
| **Experience parsing**     | Detects level (entry/junior/mid/senior/lead) and min/max year ranges                      |
| **Salary normalisation**   | Parses currency, amount range and period from free-text salary strings                    |
| **Parallel workers**       | Role thread pool (default 4) with a per-board fetch cap (default 3)                       |
| **Failed role retry**      | Failed roles wait for a cooldown, then retry once sequentially                            |
| **Batched upsert**         | Supabase upsert in configurable batches with conflict resolution on `job_id`              |
| **Enrichment confidence**  | Scores each job 0–1 based on how much structured data was extracted                       |
| **Stale job cleanup**      | Deletes jobs older than a configurable number of days at the start of each run            |

---

## Tech Stack

| Layer         | Technology                                                   |
| :------------ | :----------------------------------------------------------- |
| Scraping      | Scrapling 0.4.7 (`StealthyFetcher`)                          |
| Parsers       | Per-board CSS selector parsers with adaptive fallback chains |
| Enrichment    | pandas, regex, openpyxl                                      |
| Deduplication | Supabase `processed_jobs` table                              |
| Database      | Supabase (PostgreSQL via `supabase-py` 2.30)                 |
| State         | LangChain Core (message types)                               |
| Runtime       | Python 3.12, uv                                              |

---

## Prerequisites

- Python 3.12+
- Supabase project with service role key (run `supabase/migrations/001_full_job_payload.sql`)
- Scrapling fetcher extras (installs Playwright/Camoufox automatically via `scrapling[fetchers]`)
- Master skill list Excel file (default: `data/skills_master.xlsx`)

---

## Getting Started

```bash
git clone https://github.com/HammadAhsan341/job-scraper.git
cd job-scraper

# Install dependencies
uv sync

# Download Playwright browser binaries (required for StealthyFetcher)
uv run playwright install

# Copy and fill in environment variables
cp .env.example .env
```

Edit `.env` with your Supabase credentials, apply the SQL migration, then run:

```bash
uv run python main.py
```

---

## Deployment

This public repo is the GitHub Actions runner (unlimited minutes). The private resume-builder app only reads the same Supabase `jobs` table.

The scraper is deployed via **GitHub Actions** with 8 scheduled runs per day - LinkedIn and Indeed alternate every 3 hours with each role set scraped twice. No server required.

| Time (PKT) | Board    | Role Set |
| :--------- | :------- | :------- |
| 1:00 AM    | LinkedIn | Set 1    |
| 4:00 AM    | Indeed   | Set 1    |
| 7:00 AM    | LinkedIn | Set 2    |
| 10:00 AM   | Indeed   | Set 2    |
| 1:00 PM    | LinkedIn | Set 1    |
| 4:00 PM    | Indeed   | Set 1    |
| 7:00 PM    | LinkedIn | Set 2    |
| 10:00 PM   | Indeed   | Set 2    |

### Setup

1. Push the repo to GitHub
2. Go to **Settings → Secrets and variables → Actions** and add the following secrets:

| Secret                                | Description                                   |
| :------------------------------------ | :-------------------------------------------- |
| `SUPABASE_URL`                        | Your Supabase project URL                     |
| `SUPABASE_SERVICE_ROLE_KEY`           | Your Supabase service role key                |
| `PERMITTED_ROLES_1`                   | JSON array of roles for the first daily pass  |
| `PERMITTED_ROLES_2`                   | JSON array of roles for the second daily pass |
| `JOB_SCRAPING_WORKERS`                | Role workers (default: 4)                     |
| `JOB_SCRAPING_MAX_CONCURRENT_FETCHES` | Max in-flight fetches per board (default: 3)  |
| `JOB_SCRAPING_RETRY_BACKOFF_SECONDS`  | Cooldown before retrying failed roles         |
| `JOB_SCRAPING_MAX_PAGES_PER_BOARD`    | Max listing pages to scrape per board         |
| `JOB_SCRAPING_MAX_JOBS_PER_BOARD`     | Max jobs to scrape per board                  |
| `JOB_SCRAPING_DOWNLOAD_DELAY`         | Delay in seconds between requests             |
| `JOB_STALE_AFTER_DAYS`                | Days after which scraped jobs are deleted     |

3. Go to **Actions → Daily Job Scrape → Run workflow** to trigger a manual run - use the dropdowns to select a specific board and role set, or leave as "all" for both

The workflow file is at `.github/workflows/scrape.yml`.

---

## Roles Covered

60 roles across two sets, each scraped twice daily on both LinkedIn and Indeed.

**Set 1:**

Software Engineer, Data Engineer, UI/UX Designer, DevOps Engineer, Java Developer, Data Analyst, React Developer, MERN Stack Developer, Mobile App Developer, Backend Developer, Associate Software Engineer, Node.js Developer, Flutter Developer, Cybersecurity Analyst, LLM Engineer, NLP Engineer, Information Security Analyst, MLOps Engineer, BI Developer, AWS Cloud Engineer, QA Automation Engineer, iOS Developer, Salesforce Developer, Ethical Hacker, SOC Analyst, DevSecOps Engineer, AI Research Engineer, Conversational AI Developer, Game Developer, AI Product Developer

**Set 2:**

Full-Stack Developer, Machine Learning Engineer, AI Engineer, Frontend Developer, SQA Engineer, Data Scientist, Python Developer, Blockchain Developer, Generative AI Engineer, Business Analyst, Product Manager, AI Automation Engineer, Cybersecurity Engineer, Android Developer, Agentic AI Developer, Cloud Engineer, Computer Vision Engineer, Business Intelligence Analyst, Analytics Engineer, Network Security Engineer, Azure Engineer, Solutions Architect, Penetration Tester, Web3 Developer, Application Security Engineer, Cloud Security Engineer, SIEM Engineer, Technical Project Manager, Big Data Engineer, Polyglot Engineer

---

## Project Structure

```
main.py                    <- entry point: runs full pipeline for all permitted roles
pyproject.toml             <- dependencies (managed with uv)
.env.example               <- environment variable template
data/
  skills_master.xlsx       <- role-to-skills grid used by the enricher
supabase/migrations/
  001_full_job_payload.sql <- jobs columns + processed_jobs table
core/
  settings.py              <- env-backed settings singleton
  state.py                 <- AgentState and JobData TypedDicts
  role_filters.py          <- role allowlist enforcement helpers
scraper/
  spider.py                <- multi-board scrape coordinator (JobScraperSpider)
  boards/
    base.py                <- BaseJobParser ABC with shared utilities
    linkedin.py            <- LinkedIn parser
    indeed.py              <- Indeed parser
pipeline/
  enricher.py              <- JobEnricher: description cleaning, skill/experience/salary extraction
  skill_list.py            <- flatten Excel skill grid (columns B–K)
  enricher_node.py         <- LangChain node wrapper around JobEnricher
  scout.py                 <- digital_scout_node: query-driven scraping with role guard
services/
  job_record.py            <- JobData → jobs-table mapping (full enriched payload)
  redis.py                 <- optional leftover for unused agent vetting flows
  supabase.py              <- jobs CRUD, processed_jobs dedup, full-payload upsert
tests/
  test_scout.py            <- query-intent validation tests for digital_scout_node
```

---

## Frontend

The dashboard for browsing scraped jobs lives in a separate repo:

**[Scrapling Job Boards Scrapper Frontend](https://github.com/muhammadhaider02/Scrapling-Job-Boards-Scrapper-Frontend)** A Next.js app with live stats, filters and paginated job listings powered by Supabase.

---

## Contributing

1. Open an issue describing the bug or feature before starting any work
2. Fork the repo and create a branch from `main`
3. Make your changes and reference the issue in your PR
4. Submit a pull request for review

---

## License

This project is licensed under the [MIT License](LICENSE).
