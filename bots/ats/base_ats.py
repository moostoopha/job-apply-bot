import time
import random
from pathlib import Path


class BaseATS:
    name = "base"

    def __init__(self, page, profile: dict):
        self.page = page
        self.profile = profile
        self.cv_path = Path(profile.get("cv_path", "")).expanduser()

    def delay(self, lo=0.5, hi=1.5):
        time.sleep(random.uniform(lo, hi))

    def fill_if_empty(self, selector: str, value: str):
        el = self.page.query_selector(selector)
        if el and el.is_visible() and not el.input_value():
            el.fill(value)
            self.delay(0.3, 0.7)

    def first_name(self) -> str:
        return self.profile["name"].split()[0]

    def last_name(self) -> str:
        parts = self.profile["name"].split()
        return parts[-1] if len(parts) > 1 else ""

    def upload_cv(self, selector: str = "input[type='file']"):
        if not self.cv_path.exists():
            return False
        el = self.page.query_selector(selector)
        if el:
            el.set_input_files(str(self.cv_path))
            self.delay(1, 2)
            return True
        return False

    def apply(self) -> bool:
        raise NotImplementedError
