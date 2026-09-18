"""Fail fast in GitHub Actions when required scraper env is missing."""

from __future__ import annotations

import json
import os
import sys


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        print(f"Missing required env/secret: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _roles_ok() -> None:
    raw = (os.getenv("PERMITTED_ROLES") or "").strip()
    if raw:
        try:
            roles = json.loads(raw)
            if isinstance(roles, list) and roles:
                print(f"PERMITTED_ROLES ok ({len(roles)} roles)")
                return
        except json.JSONDecodeError:
            if "," in raw or len(raw) > 2:
                print("PERMITTED_ROLES ok (comma-separated)")
                return
    r1 = (os.getenv("PERMITTED_ROLES_1") or "").strip()
    r2 = (os.getenv("PERMITTED_ROLES_2") or "").strip()
    if not r1 and not r2:
        print(
            "Missing PERMITTED_ROLES or PERMITTED_ROLES_1 / PERMITTED_ROLES_2",
            file=sys.stderr,
        )
        sys.exit(1)
    for label, raw in (("PERMITTED_ROLES_1", r1), ("PERMITTED_ROLES_2", r2)):
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list) or not parsed:
                raise ValueError("empty list")
        except (json.JSONDecodeError, ValueError):
            print(f"Invalid JSON in {label}", file=sys.stderr)
            sys.exit(1)
    print("PERMITTED_ROLES_1 / _2 ok")


def main() -> None:
    _require("SUPABASE_URL")
    _require("SUPABASE_SERVICE_ROLE_KEY")
    boards = (os.getenv("JOB_SCRAPING_BOARDS") or "").strip()
    if not boards:
        print("JOB_SCRAPING_BOARDS empty; default boards will apply at runtime")
    else:
        print(f"JOB_SCRAPING_BOARDS={boards}")
    _roles_ok()
    print("Preflight passed.")


if __name__ == "__main__":
    main()
