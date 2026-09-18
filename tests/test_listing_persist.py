import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.listing_persist import (
    ensure_indeed_listing_description,
    is_persistable_job,
    prepare_job_for_persist,
)


class TestListingPersist(unittest.TestCase):
    def test_indeed_snippet_is_persistable(self):
        job = {
            "board": "indeed",
            "title": "Engineer",
            "company": "Acme",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "description": "Build APIs with Python and PostgreSQL daily.",
        }
        self.assertTrue(is_persistable_job(job))

    def test_indeed_without_snippet_gets_placeholder(self):
        job = {
            "board": "indeed",
            "title": "Engineer",
            "company": "Acme",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "description": "",
        }
        prepared = prepare_job_for_persist(job)
        self.assertGreaterEqual(len(prepared["description"]), 20)
        self.assertTrue(is_persistable_job(prepared))

    def test_linkedin_still_requires_full_jd(self):
        job = {
            "board": "linkedin",
            "title": "Engineer",
            "company": "Acme",
            "job_url": "https://linkedin.com/jobs/view/1",
            "description": "short",
        }
        self.assertFalse(is_persistable_job(job))


if __name__ == "__main__":
    unittest.main()
