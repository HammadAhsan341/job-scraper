"""
Indeed Pakistan job parser using Scrapling.

Indeed is a global job search engine with Pakistan listings.
"""

from typing import Optional, List, Any, Dict
from core.state import JobData
from scraper.guest import jsonld_job_description, jsonld_job_posting
from scraper.boards.indeed_jd import extract_description_multi
from .base import BaseJobParser


class _HtmlResponse:
    """Minimal Scrapling-like wrapper for parsing saved HTML."""

    def __init__(self, html: str, url: str = ""):
        self.url = url or ""
        self.body = html or ""
        self.html_content = self.body
        self._selector = None

    def css(self, selector: str):
        if self._selector is None:
            from scrapling.parser import Selector

            self._selector = Selector(content=self.body, url=self.url)
        return self._selector.css(selector)


class IndeedParser(BaseJobParser):
    """Indeed job board parser."""
    
    board_name = "indeed"
    enrich_from_detail_pages = True

    def __init__(self):
        self._listing_jobs: List[JobData] = []

    def validate_job_data(self, data: dict) -> bool:
        for field in ["title", "company", "location", "job_url"]:
            if not data.get(field):
                print(f"Indeed: Missing required field: {field}")
                return False
        return True

    @staticmethod
    def viewjob_url(href: str) -> str:
        if not href:
            return ""
        job_key = ""
        if "jk=" in href:
            job_key = href.split("jk=")[1].split("&")[0]
        if job_key:
            return f"https://pk.indeed.com/viewjob?jk={job_key}"
        if "/viewjob" in href:
            if href.startswith("http"):
                return href.split("?")[0] + (("?" + href.split("?", 1)[1]) if "?" in href else "")
            return f"https://pk.indeed.com{href}"
        return ""
    
    def parse_job(self, response: Any) -> Optional[JobData]:
        """
        Parse Indeed job posting page.
        
        Indeed structure:
        - Title: h1.jobsearch-JobInfoHeader-title or .jobsearch-JobInfoHeader-title-container h1
        - Company: div.jobsearch-InlineCompanyRating > div
        - Location: div.jobsearch-JobInfoHeader-subtitle > div
        - Description: div#jobDescriptionText or .jobsearch-jobDescriptionText
        
        Args:
            response: Scrapling Response object
            
        Returns:
            JobData or None
        """
        try:
            html = self._get_html(response)
            ld = jsonld_job_posting(html)

            title_text = self.clean_text(ld.get("title") or "")
            title = None
            if not title_text:
                title = self._css_first(response, [
                    "h1.jobsearch-JobInfoHeader-title",
                    ".jobsearch-JobInfoHeader-title-container h1",
                    "h1[data-testid='jobsearch-JobInfoHeader-title']",
                    "[data-testid='jobsearch-JobInfoHeader-title']",
                    "h1",
                ])
                title_text = self.clean_text(self._get_text(title)) if title else ""
            if not title_text:
                og = self._css_first(response, [
                    "meta[property='og:title']",
                    "meta[name='twitter:title']",
                ])
                if og is not None and hasattr(og, "attrib"):
                    title_text = self.clean_text(og.attrib.get("content") or "")
            
            if not title_text:
                print(f"Indeed: No title found at {getattr(response, 'url', '')}")
                return None
            
            # Extract company
            company = self._css_first(response, [
                "div.jobsearch-InlineCompanyRating > div",
                "div[data-company-name='true']",
                ".jobsearch-CompanyInfoContainer a"
            ])
            
            company_text = self.clean_text(self._get_text(company)) if company else ""
            if not company_text or company_text.lower() == "unknown":
                company_text = self.clean_text(ld.get("company") or "") or "Unknown"
            
            # Extract location
            location = self._css_first(response, [
                "div.jobsearch-JobInfoHeader-subtitle > div:last-child",
                "div[data-testid='job-location']",
                ".jobsearch-JobInfoHeader-subtitle .jobsearch-JobInfoHeader-subtitle-link"
            ])
            
            location_text = self.clean_text(self._get_text(location)) if location else ""
            if not location_text:
                location_text = self.clean_text(ld.get("location") or "") or "Pakistan"
            
            # Extract description (DOM JD div, mosaic JSON, JSON-LD)
            description_text, _jd_source = extract_description_multi(html, response)
            if len(description_text) < 50:
                description = self._css_first(response, [
                    "div#jobDescriptionText",
                    ".jobsearch-jobDescriptionText",
                    "div[id='jobDescriptionText']",
                    "#job-description",
                    "div[data-testid='jobsearch-JobComponent-description']",
                    "div.jobsearch-JobComponent-description",
                    "#jobDescriptionText .jobsearch-JobComponent-description",
                ])
                if description:
                    description_text = self.clean_text(self._get_text(description))
            if len(description_text) < 50:
                description_text = self.clean_text(ld.get("description") or "") or description_text
            if len(description_text) < 50:
                description_text = self.clean_text(jsonld_job_description(html)) or description_text
            
            if not description_text or len(description_text) < 50:
                print(f"Indeed: Description too short at {response.url}")
                return None
            
            # Extract salary (if available)
            salary = None
            salary_elem = self._css_first(response, [
                ".jobsearch-JobMetadataHeader-item",
                "div[data-testid='jobsearch-JobMetadataHeader-salary']",
                ".salary-snippet"
            ])
            
            if salary_elem and ("PKR" in self._get_text(salary_elem) or "Rs" in self._get_text(salary_elem)):
                salary = self.clean_text(self._get_text(salary_elem))
            
            # Extract employment type
            employment_type = None
            job_type_elem = self._css_first(response, [
                "div[data-testid='job-type-text']",
                ".jobsearch-JobMetadataHeader-item"
            ])
            
            if job_type_elem:
                job_type_text = self._get_text(job_type_elem).lower()
                if "full-time" in job_type_text or "full time" in job_type_text:
                    employment_type = "full-time"
                elif "part-time" in job_type_text:
                    employment_type = "part-time"
                elif "contract" in job_type_text:
                    employment_type = "contract"
                elif "internship" in job_type_text or "intern" in job_type_text:
                    employment_type = "internship"
            
            # Skills are extracted by the enricher pipeline, not the parser.
            skills: List[str] = []
            
            # Generate job ID
            job_id = self.generate_job_id(title_text, company_text, location_text)
            
            # Build job data
            job_data: JobData = {
                "job_id": job_id,
                "title": title_text,
                "company": company_text,
                "location": location_text,
                "job_url": response.url,
                "board": self.board_name,
                "description": description_text,
                "skills": skills,
                "posted_date": None,  # Indeed shows relative dates ("Posted 2 days ago")
                "salary": salary,
                "employment_type": employment_type,
                "experience_required": None,
                "raw_html": self._get_html(response)
            }
            
            if self.validate_job_data(job_data):
                return job_data
            else:
                return None
        
        except Exception as e:
            print(f"Indeed parse error: {e}")
            return None

    def build_serp_vjk_url(
        self,
        query: str,
        location: str,
        job_key: str,
        page: int = 1,
    ) -> str:
        """Search results with right-pane JD bootstrap (avoids viewjob auth redirect)."""
        import urllib.parse

        if not job_key:
            return ""
        q = urllib.parse.quote(query or "")
        loc = urllib.parse.quote(location or "Pakistan")
        url = f"https://pk.indeed.com/jobs?q={q}&l={loc}&vjk={job_key}"
        start = (page - 1) * 10
        if start > 0:
            url += f"&start={start}"
        return url

    def parse_serp_detail(
        self,
        response: Any,
        job_url: str,
        listing: Optional[Dict[str, Any]] = None,
    ) -> Optional[JobData]:
        """Extract JD from jobs search page (?vjk=) mosaic / panel DOM."""
        if not response:
            return None
        html = self._get_html(response)
        description_text, _src = extract_description_multi(html, response)
        if len(description_text) < 50:
            return None
        listing = listing or {}
        ld = jsonld_job_posting(html)
        title_text = (
            listing.get("title")
            or self.clean_text(ld.get("title") or "")
            or ""
        )
        company_text = (
            listing.get("company")
            or self.clean_text(ld.get("company") or "")
            or "Unknown"
        )
        location_text = (
            listing.get("location")
            or self.clean_text(ld.get("location") or "")
            or "Pakistan"
        )
        canonical_url = job_url or listing.get("job_url") or getattr(response, "url", "")
        job_data: JobData = {
            "job_id": self.generate_job_id(
                str(title_text), str(company_text), str(location_text)
            ),
            "title": str(title_text),
            "company": str(company_text),
            "location": str(location_text),
            "job_url": canonical_url,
            "board": self.board_name,
            "description": description_text,
            "skills": [],
            "posted_date": listing.get("posted_date"),
            "salary": None,
            "employment_type": None,
            "experience_required": None,
            "raw_html": html,
        }
        if self.validate_job_data(job_data):
            return job_data
        return None

    def parse_from_html(
        self,
        html: str,
        job_url: str,
        listing: Optional[Dict[str, Any]] = None,
    ) -> Optional[JobData]:
        """Parse viewjob HTML when live selectors fail (JSON-LD + listing merge)."""
        if not html or not job_url:
            return None
        listing = listing or {}
        page = _HtmlResponse(html, job_url)
        multi_text, _ = extract_description_multi(html, page)
        job = self.parse_job(page)
        if job and len((job.get("description") or "")) < 50 and len(multi_text) >= 50:
            job = dict(job)
            job["description"] = multi_text
        if job and len((job.get("description") or "")) >= 50:
            return self._merge_listing_fields(job, listing, job_url)
        ld = jsonld_job_posting(html)
        title_text = (
            (job or {}).get("title")
            or listing.get("title")
            or self.clean_text(ld.get("title") or "")
        )
        company_text = (
            (job or {}).get("company")
            or listing.get("company")
            or self.clean_text(ld.get("company") or "")
            or "Unknown"
        )
        location_text = (
            (job or {}).get("location")
            or listing.get("location")
            or self.clean_text(ld.get("location") or "")
            or "Pakistan"
        )
        description_text = self.clean_text(
            ((job or {}).get("description") or "")
            or ld.get("description")
            or listing.get("description")
            or ""
        )
        if len(description_text) < 50:
            return job
        job_data: JobData = {
            "job_id": self.generate_job_id(
                str(title_text), str(company_text), str(location_text)
            ),
            "title": str(title_text),
            "company": str(company_text),
            "location": str(location_text),
            "job_url": job_url,
            "board": self.board_name,
            "description": description_text,
            "skills": (job or {}).get("skills") or [],
            "posted_date": (job or {}).get("posted_date") or listing.get("posted_date"),
            "salary": (job or {}).get("salary"),
            "employment_type": (job or {}).get("employment_type"),
            "experience_required": (job or {}).get("experience_required"),
            "raw_html": html,
        }
        if self.validate_job_data(job_data):
            return job_data
        return job

    def parse_from_response(
        self,
        response: Any,
        listing: Optional[Dict[str, Any]] = None,
    ) -> Optional[JobData]:
        """Live parse with HTML / JSON-LD fallback and optional listing merge."""
        listing = listing or {}
        job_url = (
            getattr(response, "url", None)
            or listing.get("job_url")
            or ""
        )
        job = self.parse_job(response) if response else None
        if job and len((job.get("description") or "")) >= 50:
            return self._merge_listing_fields(job, listing, job_url)
        html = self._get_html(response)
        fallback = self.parse_from_html(html, job_url, listing)
        if fallback and len((fallback.get("description") or "")) >= 50:
            return fallback
        return job

    @staticmethod
    def _merge_listing_fields(
        job: JobData,
        listing: Dict[str, Any],
        job_url: str,
    ) -> JobData:
        merged = dict(job)
        if listing.get("title") and not merged.get("title"):
            merged["title"] = listing["title"]
        if listing.get("company") and merged.get("company") in (None, "", "Unknown"):
            merged["company"] = listing["company"]
        if listing.get("location") and not merged.get("location"):
            merged["location"] = listing["location"]
        if job_url:
            merged["job_url"] = job_url
        cur = (merged.get("description") or "").strip()
        inc = (listing.get("description") or "").strip()
        if len(inc) > len(cur):
            merged["description"] = inc
        return merged
    
    def _parse_card(self, card: Any) -> Optional[JobData]:
        title_link = self._css_first(card, [
            "a.jcs-JobTitle",
            "a[data-testid='job-title']",
            "h2 a",
        ])
        if not title_link:
            return None
        title_text = self.clean_text(self._get_text(title_link))
        href = title_link.attrib.get("href", "") if hasattr(title_link, "attrib") else ""
        job_url = self.viewjob_url(href)
        if not title_text or not job_url:
            return None

        company = self._css_first(card, [
            "[data-testid='company-name']",
            "span.companyName",
            ".companyName",
        ])
        company_text = self.clean_text(self._get_text(company)) if company else "Unknown"

        location = self._css_first(card, [
            "[data-testid='text-location']",
            "div.companyLocation",
            ".companyLocation",
        ])
        location_text = self.clean_text(self._get_text(location)) if location else "Pakistan"

        snippet = self._css_first(card, [
            ".job-snippet",
            "[data-testid='job-snippet']",
            "div.jobCardShelfContainer",
        ])
        description_text = self.clean_text(self._get_text(snippet)) if snippet else ""

        posted = self._css_first(card, ["span.date", "[data-testid='myJobsStateDate']"])
        posted_date = self.clean_text(self._get_text(posted)) if posted else None

        job_data: JobData = {
            "job_id": self.generate_job_id(title_text, company_text, location_text),
            "title": title_text,
            "company": company_text,
            "location": location_text,
            "job_url": job_url,
            "board": self.board_name,
            "description": description_text,
            "skills": [],
            "posted_date": posted_date,
            "salary": None,
            "employment_type": None,
            "experience_required": None,
            "raw_html": "",
        }
        if self.validate_job_data(job_data):
            return job_data
        return None

    def parse_listing(self, response: Any) -> List[str]:
        """Extract guest cards plus viewjob URLs from Indeed search results."""
        self._listing_jobs = []
        urls: List[str] = []
        seen = set()

        try:
            cards = response.css("div.job_seen_beacon") or []
            if not cards:
                cards = response.css("div.result") or []
            if not cards:
                cards = response.css("li.css-5lfssm") or []

            for card in cards:
                job = self._parse_card(card)
                if not job:
                    continue
                if job["job_url"] in seen:
                    continue
                seen.add(job["job_url"])
                self._listing_jobs.append(job)
                urls.append(job["job_url"])

            if not urls:
                selectors = [
                    "a.jcs-JobTitle",
                    "a[data-testid='job-title']",
                    ".jobsearch-ResultsList a[id^='job_']",
                ]
                links = []
                for selector in selectors:
                    found = response.css(selector)
                    if found:
                        links.extend(found)
                for link in links:
                    href = link.attrib.get("href", "") if hasattr(link, "attrib") else ""
                    job_url = self.viewjob_url(href)
                    if not job_url or job_url in seen:
                        continue
                    title_text = self.clean_text(self._get_text(link)) or "Untitled"
                    job = {
                        "job_id": self.generate_job_id(title_text, "Unknown", "Pakistan"),
                        "title": title_text,
                        "company": "Unknown",
                        "location": "Pakistan",
                        "job_url": job_url,
                        "board": self.board_name,
                        "description": "",
                        "skills": [],
                        "posted_date": None,
                        "salary": None,
                        "employment_type": None,
                        "experience_required": None,
                        "raw_html": "",
                    }
                    seen.add(job_url)
                    self._listing_jobs.append(job)
                    urls.append(job_url)

            print(
                f"Indeed: Found {len(urls)} job URLs "
                f"({len(self._listing_jobs)} listing cards)"
            )
        except Exception as e:
            print(f"Indeed listing parse error: {e}")

        return urls
    
    def build_search_url(self, query: str, location: str = "Pakistan", page: int = 1) -> str:
        """
        Build Indeed job search URL for Pakistan.
        
        Args:
            query: Search keywords
            location: Location filter
            page: Page number (Indeed uses start index, 10 jobs per page)
            
        Returns:
            Search URL
        """
        import urllib.parse
        query_encoded = urllib.parse.quote(query)
        location_encoded = urllib.parse.quote(location)
        
        # Indeed uses start parameter (10 jobs per page)
        start = (page - 1) * 10
        
        url = (
            f"https://pk.indeed.com/jobs"
            f"?q={query_encoded}"
            f"&l={location_encoded}"
        )
        
        if start > 0:
            url += f"&start={start}"
        
        return url


def parse_indeed_job(response: Any) -> Optional[JobData]:
    """Convenience function for Indeed job parsing."""
    parser = IndeedParser()
    return parser.parse_job(response)
