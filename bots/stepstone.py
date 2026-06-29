import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import BaseBot


class StepStoneBot(BaseBot):
    platform = "stepstone"
    LOGIN_URL = "https://www.stepstone.de/login"

    def _search_url(self, keyword: str) -> str:
        kw = keyword.lower().replace(" ", "-")
        loc = self.search["location"].split(",")[0].strip().lower()
        return f"https://www.stepstone.de/jobs/{kw}/in-{loc}"

    def _ensure_logged_in(self):
        if self.load_session():
            self.page.goto("https://www.stepstone.de", wait_until="domcontentloaded", timeout=30000)
            self.human_delay(2, 3)
            # Check if logged in by looking for user menu
            if self.page.query_selector("[data-testid='header-user-menu'], .at-header-user-menu"):
                print("  [stepstone] Session loaded OK")
                return
            print("  [stepstone] Session expired, re-login required")
            self.session_file().unlink(missing_ok=True)
        print("  [stepstone] Opening browser for manual login...")
        self.open_browser_for_login(self.LOGIN_URL)

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r'/(\d+)(?:\?|$)', url)
        return m.group(1) if m else url

    def _accept_cookies(self):
        try:
            btn = self.page.query_selector("button[id='ccmgt_explicit_accept'], button[data-genesis-element='BUTTON']:has-text('Accept')")
            if btn and btn.is_visible():
                btn.click()
                self.human_delay(0.5, 1)
        except Exception:
            pass

    def _apply_to_job(self, card) -> str:
        """Click a job card and attempt quick apply on StepStone."""
        card.click()
        self.human_delay(2, 3)

        title_el = self.page.query_selector("h1[data-testid='job-title'], .at-header-company-job-title")
        company_el = self.page.query_selector("[data-testid='job-company-name'], .at-header-company-name")
        title = title_el.inner_text().strip() if title_el else "Unknown"
        company = company_el.inner_text().strip() if company_el else "Unknown"
        url = self.page.url
        job_id = self._extract_job_id(url)

        print(f"  [>] {title} @ {company}")

        if self.already_applied(job_id):
            print("      [skip] Already applied")
            return "duplicate"

        # Look for quick apply / Schnellbewerbung button
        apply_btn = None
        for sel in [
            "button[data-testid='apply-button']",
            "a[data-testid='apply-button']",
            "button:has-text('Schnellbewerbung')",
            "button:has-text('Jetzt bewerben')",
            "a:has-text('Jetzt bewerben')",
        ]:
            apply_btn = self.page.query_selector(sel)
            if apply_btn and apply_btn.is_visible():
                break
            apply_btn = None

        if not apply_btn:
            self.log_result(job_id, title, company, url, "skipped", "no quick apply button")
            return "skipped"

        apply_btn.click()
        self.human_delay(2, 3)

        # Handle application form
        try:
            self.page.wait_for_selector("form[data-testid='application-form'], .at-application-form", timeout=10000)
        except PlaywrightTimeout:
            self.log_result(job_id, title, company, url, "skipped", "form did not open")
            return "skipped"

        # Fill name if empty
        name_field = self.page.query_selector("input[name='firstName'], input[id*='firstName']")
        if name_field and not name_field.input_value():
            parts = self.profile["name"].split(" ", 1)
            name_field.fill(parts[0])
            self.human_delay(0.3, 0.6)

        last_field = self.page.query_selector("input[name='lastName'], input[id*='lastName']")
        if last_field and not last_field.input_value() and len(self.profile["name"].split()) > 1:
            last_field.fill(self.profile["name"].split(" ", 1)[1])
            self.human_delay(0.3, 0.6)

        # Fill email if empty
        email_field = self.page.query_selector("input[type='email']")
        if email_field and not email_field.input_value():
            email_field.fill(self.profile["email"])
            self.human_delay(0.3, 0.6)

        # Upload CV if file input present
        cv_path = Path(self.profile.get("cv_path", "")).expanduser()
        if cv_path.exists():
            file_input = self.page.query_selector("input[type='file']")
            if file_input:
                file_input.set_input_files(str(cv_path))
                self.human_delay(1, 2)

        # Submit
        submit_btn = None
        for sel in ["button[type='submit']", "button:has-text('Bewerbung absenden')",
                    "button:has-text('Absenden')", "button:has-text('Submit')"]:
            submit_btn = self.page.query_selector(sel)
            if submit_btn and submit_btn.is_visible():
                break
            submit_btn = None

        if not submit_btn:
            self.log_result(job_id, title, company, url, "failed", "no submit button")
            return "failed"

        submit_btn.click()
        self.human_delay(2, 3)
        self.applied_this_run += 1
        print(f"      [+] APPLIED ({self.applied_this_run})")
        self.log_result(job_id, title, company, url, "applied")
        return "applied"

    def run(self):
        self.start_browser()
        try:
            self._ensure_logged_in()

            for keyword in self.search["keywords"]:
                if self.at_limit():
                    break
                search_url = self._search_url(keyword)
                print(f"\n[stepstone] Searching: {keyword} in {self.search['location']}")
                self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                self.human_delay(2, 4)
                self._accept_cookies()

                page_num = 1
                while not self.at_limit():
                    # Job cards
                    cards = self.page.query_selector_all(
                        "article[data-testid='job-item'], .sc-bTAuRo, [data-at='job-item']"
                    )
                    if not cards:
                        print("  [stepstone] No job cards found")
                        break
                    print(f"  [stepstone] Page {page_num}: {len(cards)} jobs")

                    for card in cards:
                        if self.at_limit():
                            break
                        try:
                            self.retry(lambda c=card: self._apply_to_job(c))
                            # Navigate back to search after each job
                            self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                            self.human_delay(1, 2)
                        except Exception as e:
                            print(f"      [error] {e}")
                            self.log_result("?", "?", "?", self.page.url, "error", str(e))
                        self.human_delay(2, 4)

                    # Next page
                    next_btn = self.page.query_selector(
                        "a[data-testid='pagination-next'], button[aria-label='Next page']"
                    )
                    if not next_btn:
                        print("  [stepstone] No more pages")
                        break
                    next_btn.click()
                    page_num += 1
                    self.human_delay(3, 5)

        finally:
            self.close_browser()

        print(f"\n[stepstone] Done. Applied to {self.applied_this_run} jobs this run.")
        return self.applied_this_run
