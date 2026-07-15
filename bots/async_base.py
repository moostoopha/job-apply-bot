import asyncio
import csv
import json
import random
import re
import subprocess
from datetime import datetime
from pathlib import Path

LOG_FILE    = Path(__file__).parent.parent / "logs" / "applications.csv"
SESSIONS_DIR = Path(__file__).parent.parent / "sessions"
_LOG_LOCK   = asyncio.Lock()


class AsyncBaseBot:
    platform    = "base"
    CONCURRENCY = 2  # parallel tabs — 3 caused StepStone rate-limiting

    def __init__(self, config: dict, playwright, headless: bool = True):
        self.config   = config
        self.profile  = config["profile"]
        self.search   = config["search"]
        self.limits   = config["limits"]
        self.headless = headless
        self.playwright = playwright
        self.browser  = None
        self.ctx      = None
        self.page     = None   # navigation/search page
        self.applied_this_run = 0
        self._applied_ids: set = set()   # in-memory dedup cache
        self._sem = asyncio.Semaphore(self.CONCURRENCY)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # ── Browser ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_viewport() -> dict:
        """In visible mode, size the viewport to the real screen so
        fixed-position elements (e.g. sticky submit bars) stay reachable —
        a viewport taller than the actual display gets silently clipped by
        the window manager with no way to scroll the missing part into view."""
        default = {"width": 1280, "height": 800}
        try:
            out = subprocess.run(
                ["xrandr"], capture_output=True, text=True, timeout=5
            ).stdout
            m = re.search(r"current (\d+) x (\d+)", out)
            if not m:
                return default
            width, height = int(m.group(1)), int(m.group(2))
            # Leave room for window chrome / panels / taskbar.
            return {"width": max(width - 20, 800), "height": max(height - 90, 600)}
        except Exception:
            return default

    async def start_browser(self):
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        viewport = {"width": 1280, "height": 800} if self.headless else self._detect_viewport()
        self.ctx  = await self.browser.new_context(viewport=viewport)
        self.page = await self.ctx.new_page()

    async def close_browser(self):
        if self.browser:
            await self.browser.close()

    # ── Session ───────────────────────────────────────────────────────────────

    def session_file(self) -> Path:
        return SESSIONS_DIR / f"{self.platform}.json"

    async def load_session(self) -> bool:
        if not self.session_file().exists():
            return False
        cookies = json.loads(self.session_file().read_text())
        await self.ctx.add_cookies(cookies)
        return True

    async def save_session(self):
        cookies = await self.ctx.cookies()
        self.session_file().write_text(json.dumps(cookies, indent=2))
        self.session_file().chmod(0o600)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def human_delay(self, lo: float = 1.0, hi: float = 2.5):
        await asyncio.sleep(random.uniform(lo, hi))

    # ── Deduplication ─────────────────────────────────────────────────────────

    def _load_applied_ids(self) -> set:
        ids = set()
        if not LOG_FILE.exists():
            return ids
        with open(LOG_FILE) as f:
            for row in csv.DictReader(f):
                if row.get("status") == "applied" and row.get("job_id"):
                    ids.add(row["job_id"])
        return ids

    def already_applied(self, job_id: str) -> bool:
        return job_id in self._applied_ids

    # ── Limits ────────────────────────────────────────────────────────────────

    def daily_applied_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        if not LOG_FILE.exists():
            return 0
        with open(LOG_FILE) as f:
            return sum(
                1 for row in csv.DictReader(f)
                if row.get("status") == "applied" and row.get("timestamp", "").startswith(today)
            )

    def at_limit(self) -> bool:
        if self.applied_this_run >= self.limits["max_per_run"]:
            print(f"[limit] Reached max_per_run ({self.limits['max_per_run']})", flush=True)
            return True
        if self.daily_applied_count() >= self.limits["max_per_day"]:
            print(f"[limit] Reached max_per_day ({self.limits['max_per_day']})", flush=True)
            return True
        return False

    # ── Logging ───────────────────────────────────────────────────────────────

    async def log_result(self, job_id: str, title: str, company: str,
                         url: str, status: str, note: str = ""):
        async with _LOG_LOCK:
            file_exists = LOG_FILE.exists()
            with open(LOG_FILE, "a", newline="") as f:
                w = csv.writer(f)
                if not file_exists:
                    w.writerow(["timestamp", "platform", "job_id", "title",
                                "company", "url", "status", "note"])
                w.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                    self.platform, job_id, title, company, url, status, note,
                ])

    # ── Abstract ─────────────────────────────────────────────────────────────

    async def run(self):
        raise NotImplementedError
