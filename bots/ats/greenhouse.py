from playwright.sync_api import TimeoutError as PlaywrightTimeout
from .base_ats import BaseATS


class GreenhouseATS(BaseATS):
    name = "greenhouse"

    def apply(self) -> bool:
        try:
            self.page.wait_for_selector("#application_form, form#application", timeout=10000)
        except PlaywrightTimeout:
            return False

        self.delay(1, 2)

        # Name fields
        self.fill_if_empty("#first_name", self.first_name())
        self.fill_if_empty("#last_name", self.last_name())

        # Email
        self.fill_if_empty("#email", self.profile["email"])
        self.fill_if_empty("#email_confirm", self.profile["email"])

        # Phone
        if self.profile.get("phone"):
            self.fill_if_empty("#phone", self.profile["phone"])

        # Resume upload — Greenhouse uses a specific attach button or file input
        if self.cv_path.exists():
            # Try direct file input first
            file_input = self.page.query_selector("input[type='file'][name='resume']")
            if not file_input:
                file_input = self.page.query_selector("input[type='file']")
            if file_input:
                file_input.set_input_files(str(self.cv_path))
                self.delay(1.5, 2.5)

        # LinkedIn URL field (common in Greenhouse)
        linkedin_field = self.page.query_selector("#job_application_answers_attributes_0_text_value, input[name*='linkedin']")
        if linkedin_field and linkedin_field.is_visible() and not linkedin_field.input_value():
            linkedin_field.fill("https://www.linkedin.com/in/mustafa-hassan-devops")
            self.delay(0.3, 0.6)

        # Submit
        submit = self.page.query_selector("#submit_app, button[type='submit']")
        if not submit or not submit.is_visible():
            return False

        submit.click()
        self.delay(2, 4)

        # Confirm success by URL change or success message
        success = (
            "confirmation" in self.page.url
            or "success" in self.page.url
            or self.page.query_selector(".confirmation, .success-message, h2:has-text('Application submitted')")
        )
        return bool(success)
