import unittest

from main import _role_searches
from scraper.boards.linkedin import LinkedInParser
from tests.test_scrape_resilience import _settings

ALL_BOARDS = ["rozee", "mustakbil", "indeed", "linkedin"]


class TestRemoteRoleSearches(unittest.TestCase):
    def test_remote_role_gets_pakistan_then_remote_on_linkedin_and_indeed(self):
        settings = _settings(job_scraping_boards=ALL_BOARDS, remote_roles=["Graphic Designer"])
        self.assertEqual(
            _role_searches("graphic designer", settings),
            [(b, "Pakistan") for b in ALL_BOARDS] + [("indeed", "Remote"), ("linkedin", "Remote")],
        )

    def test_other_roles_only_search_pakistan(self):
        settings = _settings(job_scraping_boards=ALL_BOARDS, remote_roles=["Graphic Designer"])
        self.assertEqual(_role_searches("Software Engineer", settings), [(b, "Pakistan") for b in ALL_BOARDS])

    def test_remote_pass_skips_disabled_boards(self):
        settings = _settings(job_scraping_boards=["rozee"], remote_roles=["Graphic Designer"])
        self.assertEqual(_role_searches("Graphic Designer", settings), [("rozee", "Pakistan")])


class TestLinkedInRemoteUrl(unittest.TestCase):
    def test_remote_uses_worldwide_and_remote_filter(self):
        url = LinkedInParser().build_search_url("Graphic Designer", "Remote", 1)
        self.assertIn("location=Worldwide", url)
        self.assertIn("f_WT=2", url)

    def test_pakistan_search_is_unchanged(self):
        url = LinkedInParser().build_search_url("Graphic Designer", "Pakistan", 1)
        self.assertIn("location=Pakistan", url)
        self.assertNotIn("f_WT", url)


if __name__ == "__main__":
    unittest.main()
