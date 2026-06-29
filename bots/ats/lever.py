from playwright.sync_api import TimeoutError as PlaywrightTimeout
from .base_ats import BaseATS


class LeverATS(BaseATS):
    name = "lever"

    def apply(self) -> bool:
        try:
            self.page.wait_for_selector("form.application-form, .content-body form", timeout=10000)
        except PlaywrightTimeout:
            return False

        self.delay(1, 2)

        # Full name (Lever uses single name field)
        self.fill_if_empty("input[name='name']", self.profile["name"])

        # Email
        self.fill_if_empty("input[name='email']", self.profile["email"])

        # Phone
        if self.profile.get("phone"):
            self.fill_if_empty("input[name='phone']", self.profile["phone"])

        # Current company/org (optional, leave blank)
        # self.fill_if_empty("input[name='org']", "")

        # Resume upload
        if self.cv_path.exists():
            file_input = self.page.query_selector("input[type='file']")
            if file_input:
                file_input.set_input_files(str(self.cv_path))
                self.delay(1.5, 2.5)

        # LinkedIn URL
        linkedin_field = self.page.query_selector("input[name='urls[LinkedIn]'], input[placeholder*='LinkedIn']")
        if linkedin_field and linkedin_field.is_visible() and not linkedin_field.input_value():
            linkedin_field.fill("https://www.linkedin.com/in/mustafa-hassan-devops")
            self.delay(0.3, 0.6)

        # Submit
        submit = self.page.query_selector("button[type='submit'].postings-btn, button[type='submit']")
        if not submit or not submit.is_visible():
            return False

        submit.click()
        self.delay(2, 4)

        success = (
            "confirmation" in self.page.url
            or "thank" in self.page.url.lower()
            or self.page.query_selector(".thank-you, .confirmation, h2:has-text('Thank you')")
        )
        return bool(success)
