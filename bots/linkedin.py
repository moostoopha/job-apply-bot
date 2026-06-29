import re

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import BaseBot
from .ats import detect_ats


class LinkedInBot(BaseBot):
    platform = "linkedin"
    LOGIN_URL = "https://www.linkedin.com/login"
    FEED_URL  = "https://www.linkedin.com/feed/"

    def _search_url(self, keyword: str, location: str) -> str:
        kw  = keyword.replace(" ", "%20")
        loc = location.replace(" ", "%20").replace(",", "%2C")
        return (
            f"https://www.linkedin.com/jobs/search/?"
            f"keywords={kw}&location={loc}&sortBy=DD"
        )

    def _search_url_easy_apply(self, keyword: str, location: str) -> str:
        return self._search_url(keyword, location) + "&f_LF=f_AL"

    def _ensure_logged_in(self):
        if self.load_session():
            self.page.goto(self.FEED_URL, wait_until="domcontentloaded", timeout=30000)
            self.human_delay(2, 3)
            if "feed" in self.page.url:
                print("  [linkedin] Session loaded OK")
                return
            print("  [linkedin] Session expired, re-login required")
            self.session_file().unlink(missing_ok=True)
        print("  [linkedin] Opening browser for manual login...")
        self.open_browser_for_login(self.LOGIN_URL)
        self.page.goto(self.FEED_URL, wait_until="domcontentloaded", timeout=30000)

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r'currentJobId=(\d+)|/jobs/view/(\d+)', url)
        if m:
            return m.group(1) or m.group(2)
        return url

    def _try_easy_apply(self, apply_btn, job_id: str, title: str, company: str, url: str) -> str:
        """Handle LinkedIn Easy Apply modal."""
        apply_btn.click()
        try:
            self.page.wait_for_selector("[data-test-modal-container] button", timeout=8000)
        except PlaywrightTimeout:
            self.log_result(job_id, title, company, url, "skipped", "modal did not open")
            return "skipped"

        self.human_delay(1, 2)
        submitted = False

        for step in range(10):
            self.human_delay(0.5, 1)
            modal = "[data-test-modal-container]"

            submit = self.page.query_selector(f"{modal} button[aria-label='Submit application']")
            if submit:
                submit.click()
                self.human_delay(1, 2)
                submitted = True
                self.applied_this_run += 1
                print(f"      [+] APPLIED via Easy Apply ({self.applied_this_run})")
                self.log_result(job_id, title, company, url, "applied", "easy-apply")
                self.human_delay(2, 3)
                break

            review = self.page.query_selector(f"{modal} button[aria-label='Review your application']")
            if review:
                review.click()
                self.human_delay(1, 2)
                continue

            nxt = self.page.query_selector(f"{modal} button[aria-label='Continue to next step']")
            if nxt:
                nxt.click()
                self.human_delay(1, 2)
                continue

            print(f"      [!] Stuck at step {step}")
            break

        self.dismiss_modal()
        if not submitted:
            self.log_result(job_id, title, company, url, "failed", "easy-apply-stuck")
        return "applied" if submitted else "failed"

    def _try_external_apply(self, apply_btn, job_id: str, title: str, company: str, url: str) -> str:
        """Handle external apply via ATS detection."""
        # Open in new tab by intercepting popup
        with self.page.expect_popup(timeout=10000) as popup_info:
            apply_btn.click()

        try:
            ext_page = popup_info.value
        except PlaywrightTimeout:
            # No popup — maybe same-tab navigation
            self.human_delay(2, 3)
            ext_page = self.page

        ext_url = ext_page.url
        self.human_delay(2, 3)

        ats_class = detect_ats(ext_url)
        if not ats_class:
            print(f"      [skip] Unknown ATS: {ext_url[:60]}")
            self.log_result(job_id, title, company, url, "skipped", f"unknown-ats:{ext_url[:80]}")
            if ext_page != self.page:
                ext_page.close()
            return "skipped"

        print(f"      [ats] Detected: {ats_class.name}")
        ats = ats_class(ext_page, self.profile)
        success = ats.apply()

        if ext_page != self.page:
            ext_page.close()

        if success:
            self.applied_this_run += 1
            print(f"      [+] APPLIED via {ats_class.name} ({self.applied_this_run})")
            self.log_result(job_id, title, company, url, "applied", ats_class.name)
            return "applied"
        else:
            print(f"      [!] {ats_class.name} apply failed")
            self.log_result(job_id, title, company, url, "failed", f"{ats_class.name}-failed")
            return "failed"

    def _apply_to_job(self, card, search_url: str) -> str:
        card.click()
        self.human_delay(1.5, 2.5)

        title_el   = self.page.query_selector(".job-details-jobs-unified-top-card__job-title")
        company_el = self.page.query_selector(".job-details-jobs-unified-top-card__company-name")
        title   = title_el.inner_text().strip()   if title_el   else "Unknown"
        company = company_el.inner_text().strip() if company_el else "Unknown"
        url     = self.page.url
        job_id  = self._extract_job_id(url)

        print(f"  [>] {title} @ {company}")

        if self.already_applied(job_id):
            print("      [skip] Already applied")
            return "duplicate"

        apply_btn = self.page.query_selector("button.jobs-apply-button")
        if not apply_btn:
            self.log_result(job_id, title, company, url, "skipped", "no apply button")
            return "skipped"

        btn_text = apply_btn.inner_text().strip()

        if "Easy Apply" in btn_text:
            return self._try_easy_apply(apply_btn, job_id, title, company, url)
        elif "Apply" in btn_text:
            try:
                return self._try_external_apply(apply_btn, job_id, title, company, url)
            except PlaywrightTimeout:
                self.log_result(job_id, title, company, url, "skipped", "external-popup-timeout")
                return "skipped"
        else:
            self.log_result(job_id, title, company, url, "skipped", btn_text)
            return "skipped"

    def _run_search(self, keyword: str, location: str, easy_apply_only: bool = False):
        search_url = (
            self._search_url_easy_apply(keyword, location)
            if easy_apply_only
            else self._search_url(keyword, location)
        )
        label = f"{keyword} / {location}" + (" [Easy Apply]" if easy_apply_only else " [All]")
        print(f"\n[linkedin] Searching: {label}")

        self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        self.human_delay(3, 5)

        page_num = 0
        while not self.at_limit():
            try:
                self.page.wait_for_selector("li[data-occludable-job-id]", timeout=15000)
            except PlaywrightTimeout:
                print("  [linkedin] No jobs found on this page")
                break

            cards = self.page.query_selector_all("li[data-occludable-job-id]")
            print(f"  [linkedin] Page {page_num + 1}: {len(cards)} jobs")

            for card in cards:
                if self.at_limit():
                    break
                try:
                    self.retry(lambda c=card: self._apply_to_job(c, search_url))
                except Exception as e:
                    print(f"      [error] {e}")
                    self.log_result("?", "?", "?", self.page.url, "error", str(e)[:100])
                    self.dismiss_modal()
                self.human_delay(2, 4)

            # Navigate back and go to next page
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            self.human_delay(2, 3)

            next_btn = self.page.query_selector("button[aria-label='View next page']")
            if not next_btn:
                print("  [linkedin] No more pages")
                break
            next_btn.click()
            page_num += 1
            self.human_delay(3, 5)

    def run(self):
        self.start_browser()
        try:
            self._ensure_logged_in()

            locations = self.search.get("locations", [self.search.get("location", "Germany")])

            for location in locations:
                for keyword in self.search["keywords"]:
                    if self.at_limit():
                        break
                    # First pass: Easy Apply (fast, reliable)
                    self._run_search(keyword, location, easy_apply_only=True)
                    if self.at_limit():
                        break
                    # Second pass: All jobs including external ATS
                    self._run_search(keyword, location, easy_apply_only=False)

        finally:
            self.close_browser()

        print(f"\n[linkedin] Done. Applied to {self.applied_this_run} jobs this run.")
        return self.applied_this_run
