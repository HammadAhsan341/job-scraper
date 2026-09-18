# GitHub Actions — scraper fails instantly (no steps)

If **Daily Job Scrape** finishes in **~2–5 seconds** with an empty job log and **no steps** (`runner_id: 0`), GitHub never assigned a hosted runner. On **private** repos this is usually billing/minutes; this repo is **public** and should get runners unless org policy blocks it.

## Required repository secrets

| Secret | Required |
|--------|----------|
| `SUPABASE_URL` | Yes |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes |
| `PERMITTED_ROLES_1` | Yes (JSON array) |
| `PERMITTED_ROLES_2` | Yes (JSON array) |
| `JOB_SCRAPING_MAX_PAGES_PER_BOARD` | Yes |
| `JOB_SCRAPING_MAX_JOBS_PER_BOARD` | Yes |
| `JOB_SCRAPING_DOWNLOAD_DELAY` | Yes |
| `JOB_STALE_AFTER_DAYS` | Yes |
| `JOB_SCRAPING_RETRY_BACKOFF_SECONDS` | Yes |

Preflight runs `scripts/validate_ci_env.py` before scraping.

## Indeed full JDs on CI

Uses search-pane `vjk` + `sanitizedJobDescription` extraction (`JOB_SCRAPING_FETCH_TIMEOUT_SECONDS=0`, `JOB_SCRAPING_INDEED_INTERACTIVE=false`).
