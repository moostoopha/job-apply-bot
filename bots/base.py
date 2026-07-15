import csv
import json
import random
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

LOG_FILE = Path(__file__).parent.parent / "logs" / "applications.csv"
SESSIONS_DIR = Path(__file__).parent.parent / "sessions"


class BaseBot:
    platform = "base"

    def __init__(self, config: dict, playwright, headless: bool = True):
        self.config = config
        self.profile = config["profile"]
        self.search = config["search"]
        self.limits = config["limits"]
        self.headless = headless
        self.playwright = playwright
        self.browser = None
        self.ctx = None
        self.page = None
        self.applied_this_run = 0
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Browser ──────────────────────────────────────────────────────────────

    def start_browser(self):
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        self.ctx = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.page = self.ctx.new_page()

    def close_browser(self):
        if self.browser:
            self.browser.close()

    # ── Session ───────────────────────────────────────────────────────────────

    def session_file(self) -> Path:
        return SESSIONS_DIR / f"{self.platform}.json"

    def save_session(self):
        cookies = self.ctx.cookies()
        self.session_file().write_text(json.dumps(cookies, indent=2))
        self.session_file().chmod(0o600)
        print(f"  [session] Saved {len(cookies)} cookies for {self.platform}")

    def load_session(self) -> bool:
        if not self.session_file().exists():
            return False
        cookies = json.loads(self.session_file().read_text())
        self.ctx.add_cookies(cookies)
        return True

    def open_browser_for_login(self, login_url: str):
        """Open a visible browser so user can log in manually."""
        vis_browser = self.playwright.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        vis_ctx = vis_browser.new_context(viewport={"width": 1280, "height": 800})
        vis_page = vis_ctx.new_page()
        vis_page.goto(login_url)
        print(f"  [login] Browser opened for {self.platform}. Log in manually...")
        vis_page.wait_for_url("**", timeout=180000)
        # Wait until not on a login-related URL
        for _ in range(60):
            if "login" not in vis_page.url and "signin" not in vis_page.url:
                break
            time.sleep(2)
        cookies = vis_ctx.cookies()
        vis_browser.close()
        self.ctx.add_cookies(cookies)
        self.save_session()
        print(f"  [login] Session saved for {self.platform}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def human_delay(self, lo: float = 1.5, hi: float = 3.5):
        time.sleep(random.uniform(lo, hi))

    def retry(self, fn, attempts: int = 3):
        for i in range(attempts):
            try:
                return fn()
            except Exception as e:
                if i == attempts - 1:
                    raise
                print(f"  [retry {i+1}/{attempts}] {e}")
                self.human_delay(1, 2)

    def dismiss_modal(self):
        """Close any open modal via Dismiss button + discard confirmation."""
        try:
            if not self.page.query_selector("[data-test-modal-container], [role='dialog']"):
                return
            for sel in ["button[aria-label='Dismiss']", "button[aria-label='Schließen']",
                        "button[aria-label='Close']", "button.artdeco-modal__dismiss"]:
                btn = self.page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    self.human_delay(0.5, 1)
                    break
            else:
                self.page.keyboard.press("Escape")
                self.human_delay(0.5, 1)
            for sel in ["button[aria-label='Discard']", "button[aria-label='Verwerfen']",
                        "button[data-control-name='discard_application_confirm_btn']"]:
                btn = self.page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    self.human_delay(0.5, 1)
                    break
        except Exception:
            pass

    # ── Deduplication ─────────────────────────────────────────────────────────

    def applied_job_ids(self) -> set:
        ids = set()
        if not LOG_FILE.exists():
            return ids
        with open(LOG_FILE) as f:
            for row in csv.DictReader(f):
                if row.get("status") == "applied" and row.get("job_id"):
                    ids.add(row["job_id"])
        return ids

    def already_applied(self, job_id: str) -> bool:
        return job_id in self.applied_job_ids()

    # ── Daily limit ───────────────────────────────────────────────────────────

    def daily_applied_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        if not LOG_FILE.exists():
            return 0
        with open(LOG_FILE) as f:
            for row in csv.DictReader(f):
                if row.get("status") == "applied" and row.get("timestamp", "").startswith(today):
                    count += 1
        return count

    def at_limit(self) -> bool:
        if self.applied_this_run >= self.limits["max_per_run"]:
            print(f"[limit] Reached max_per_run ({self.limits['max_per_run']})")
            return True
        daily = self.daily_applied_count()
        if daily >= self.limits["max_per_day"]:
            print(f"[limit] Reached max_per_day ({self.limits['max_per_day']})")
            return True
        return False

    # ── Logging ───────────────────────────────────────────────────────────────

    def log_result(self, job_id: str, title: str, company: str, url: str,
                   status: str, note: str = ""):
        file_exists = LOG_FILE.exists()
        with open(LOG_FILE, "a", newline="") as f:
            w = csv.writer(f)
            if not file_exists:
                w.writerow(["timestamp", "platform", "job_id", "title", "company",
                             "url", "status", "note"])
            w.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                self.platform, job_id, title, company, url, status, note,
            ])

    # ── Abstract ─────────────────────────────────────────────────────────────

    def run(self):
        raise NotImplementedError
