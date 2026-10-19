from datetime import datetime, timezone

from scripts.schedule_gate import should_scrape

NOW = datetime(2026, 10, 19, 10, 33, tzinfo=timezone.utc)


def test_scrapes_when_this_hour_has_no_success():
    runs = [
        {
            "id": 1,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-10-19T09:13:00Z",
        }
    ]
    scrape, reason = should_scrape(runs, NOW, "99")
    assert scrape is True
    assert "no successful scrape" in reason


def test_skips_when_this_hour_already_succeeded():
    runs = [
        {
            "id": 2,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-10-19T10:13:00Z",
        }
    ]
    scrape, reason = should_scrape(runs, NOW, "99")
    assert scrape is False
    assert "already started" in reason


def test_retries_when_this_hour_failed():
    runs = [
        {
            "id": 3,
            "status": "completed",
            "conclusion": "failure",
            "run_started_at": "2026-10-19T10:13:00Z",
        }
    ]
    scrape, _reason = should_scrape(runs, NOW, "99")
    assert scrape is True


def test_skips_when_another_run_is_still_going():
    runs = [{"id": 4, "status": "in_progress", "conclusion": None}]
    scrape, reason = should_scrape(runs, NOW, "99")
    assert scrape is False
    assert "in_progress" in reason


def test_ignores_the_current_run():
    runs = [
        {
            "id": 99,
            "status": "in_progress",
            "conclusion": None,
            "run_started_at": "2026-10-19T10:33:00Z",
        }
    ]
    scrape, _reason = should_scrape(runs, NOW, "99")
    assert scrape is True
