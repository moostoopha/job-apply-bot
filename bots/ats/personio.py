from playwright.sync_api import TimeoutError as PlaywrightTimeout
from .base_ats import BaseATS


class PersonioATS(BaseATS):
    name = "personio"

    def apply(self) -> bool:
        try:
            self.page.wait_for_selector("form, .application-form", timeout=10000)
        except PlaywrightTimeout:
            return False

        self.delay(1, 2)

        # Personio field names vary — try common patterns
        for sel in ["input[id*='first_name'], input[name*='first_name'], input[placeholder*='Vorname'], input[placeholder*='First name']"]:
            self.fill_if_empty(sel, self.first_name())

        for sel in ["input[id*='last_name'], input[name*='last_name'], input[placeholder*='Nachname'], input[placeholder*='Last name']"]:
            self.fill_if_empty(sel, self.last_name())

        # Some Personio forms have a single full name field
        self.fill_if_empty("input[id*='full_name'], input[name*='full_name']", self.profile["name"])

        # Email
        for sel in ["input[type='email']", "input[id*='email']", "input[name*='email']"]:
            self.fill_if_empty(sel, self.profile["email"])

        # Phone
        if self.profile.get("phone"):
            for sel in ["input[id*='phone'], input[name*='phone'], input[placeholder*='Telefon'], input[placeholder*='Phone']"]:
                self.fill_if_empty(sel, self.profile["phone"])

        # Upload CV
        if self.cv_path.exists():
            file_input = self.page.query_selector("input[type='file']")
            if file_input:
                file_input.set_input_files(str(self.cv_path))
                self.delay(1.5, 2.5)

        # Accept privacy/GDPR checkbox (required in Germany)
        for sel in ["input[type='checkbox'][id*='privacy'], input[type='checkbox'][id*='gdpr'], input[type='checkbox'][name*='privacy']"]:
            cb = self.page.query_selector(sel)
            if cb and cb.is_visible() and not cb.is_checked():
                cb.click()
                self.delay(0.3, 0.5)

        # Submit
        submit = None
        for sel in [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Bewerbung absenden')",
            "button:has-text('Jetzt bewerben')",
            "button:has-text('Submit application')",
            "button:has-text('Apply now')",
        ]:
            submit = self.page.query_selector(sel)
            if submit and submit.is_visible():
                break
            submit = None

        if not submit:
            return False

        submit.click()
        self.delay(2, 4)

        success = (
            "confirmation" in self.page.url
            or "success" in self.page.url
            or "thank" in self.page.url.lower()
            or self.page.query_selector(".success, .confirmation, h1:has-text('Danke'), h1:has-text('Thank you')")
        )
        return bool(success)
