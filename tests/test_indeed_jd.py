import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.boards.indeed import IndeedParser
from scraper.guest import jsonld_job_posting
from scraper.boards.indeed_jd import extract_sanitized_job_descriptions, extract_description_multi
from scraper.listing_persist import (
    ensure_indeed_listing_description,
    is_indeed_listing_placeholder,
)


SAMPLE_JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Backend Engineer",
  "description": "<p>Build REST APIs with Python.</p><p>Own PostgreSQL schemas.</p>",
  "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Karachi",
      "addressCountry": "PK"
    }
  }
}
</script>
</head><body><h1>ignored</h1></body></html>
"""


MOSAIC_SAMPLE = (
    '{"jobDescriptionSectionModel":{"sanitizedJobDescription":"\\u003Cdiv>\\n '
    '\\u003Cp>Build REST APIs with Python and PostgreSQL in production.\\u003C/p>\\n '
    '\\u003Cp>Collaborate with senior engineers on cloud deployments.\\u003C/p>\\n '
    '\\u003C/div>"}}'
)


class TestIndeedJd(unittest.TestCase):
    def test_sanitized_job_description_from_mosaic(self):
        texts = extract_sanitized_job_descriptions(MOSAIC_SAMPLE)
        self.assertTrue(texts)
        self.assertIn("PostgreSQL", texts[0])
        self.assertGreaterEqual(len(texts[0]), 50)

    def test_extract_description_multi_prefers_mosaic(self):
        text, source = extract_description_multi(MOSAIC_SAMPLE, None)
        self.assertGreaterEqual(len(text), 50)
        self.assertIn("mosaic", source)

    def test_jsonld_job_posting_fields(self):
        fields = jsonld_job_posting(SAMPLE_JSONLD_HTML)
        self.assertEqual(fields["title"], "Backend Engineer")
        self.assertIn("PostgreSQL", fields["description"])
        self.assertEqual(fields["company"], "Acme Corp")
        self.assertIn("Karachi", fields["location"])

    def test_parse_from_html_jsonld_only(self):
        parser = IndeedParser()
        job = parser.parse_from_html(
            SAMPLE_JSONLD_HTML,
            "https://pk.indeed.com/viewjob?jk=abc",
            {"title": "Backend Engineer", "company": "Acme Corp", "location": "Karachi"},
        )
        self.assertIsNotNone(job)
        self.assertGreaterEqual(len(job["description"]), 50)
        self.assertEqual(job["title"], "Backend Engineer")

    def test_listing_snippet_not_replaced_by_placeholder(self):
        job = {
            "board": "indeed",
            "title": "Engineer",
            "company": "Acme",
            "job_url": "https://pk.indeed.com/viewjob?jk=abc",
            "description": "Real card snippet from search results with enough text.",
        }
        out = ensure_indeed_listing_description(job)
        self.assertEqual(out["description"], job["description"])
        self.assertFalse(is_indeed_listing_placeholder(out["description"]))


if __name__ == "__main__":
    unittest.main()
