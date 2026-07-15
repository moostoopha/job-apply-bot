import asyncio
import re

from playwright.async_api import TimeoutError as PlaywrightTimeout

from .async_base import AsyncBaseBot
from .ats_handlers import fill_ats


class AsyncLinkedInBot(AsyncBaseBot):
    platform  = "linkedin"
    LOGIN_URL = "https://www.linkedin.com/login"
    FEED_URL  = "https://www.linkedin.com/feed/"

    def _search_url(self, keyword: str, location: str) -> str:
        kw  = keyword.replace(" ", "%20")
        loc = location.replace(" ", "%20").replace(",", "%2C")
        # No f_LF=f_AL filter — see ALL jobs, not just Easy Apply
        return (f"https://www.linkedin.com/jobs/search/?"
                f"keywords={kw}&location={loc}&sortBy=DD")

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r'currentJobId=(\d+)|/jobs/view/(\d+)', url)
        if m:
            return m.group(1) or m.group(2)
        return url

    def _job_url(self, job_id: str) -> str:
        return f"https://www.linkedin.com/jobs/view/{job_id}/"

    async def _ensure_logged_in(self):
        await self.load_session()
        await self.page.goto(self.FEED_URL, wait_until="domcontentloaded", timeout=30000)
        # LinkedIn shows a "We're signing you in..." transitional page before
        # redirecting to /feed/ — this can take up to ~9s, well past a fixed
        # short delay, so poll instead of guessing a wait time.
        for _ in range(10):
            if "feed" in self.page.url and "login" not in self.page.url:
                break
            await asyncio.sleep(1.5)
        if "feed" not in self.page.url or "login" in self.page.url:
            raise RuntimeError("LinkedIn session expired. Run setup to re-login.")
        print("  [linkedin] Session loaded OK", flush=True)

    # ── Easy Apply ─────────────────────────────────────────────────────────────

    async def _easy_apply(self, page, job_id: str, title: str, company: str, url: str) -> str:
        # LinkedIn's apply buttons use build-hashed CSS classes that change
        # across deploys (no longer `.jobs-apply-button`), and the external
        # "Apply on company website" button shares those same classes — so
        # match on the stable aria-label/text instead of class.
        # The button mounts asynchronously after `domcontentloaded` fires, so
        # a single immediate query_selector races the page's own JS and
        # misses it intermittently — wait for it explicitly instead.
        apply_sel = "button[aria-label*='Easy Apply' i], button:has-text('Easy Apply')"
        try:
            await page.wait_for_selector(apply_sel, timeout=6000)
        except PlaywrightTimeout:
            return "no-easy-apply"
        btn = await page.query_selector(apply_sel)
        if not btn or not await btn.is_enabled():
            return "no-easy-apply"

        await btn.click(timeout=8000)
        try:
            await page.wait_for_selector("[data-test-modal-container] button", timeout=8000)
        except PlaywrightTimeout:
            return "no-easy-apply"

        await self.human_delay(1, 2)
        modal = "[data-test-modal-container]"

        for step in range(10):
            await self.human_delay(0.5, 1)

            submit = await page.query_selector(f"{modal} button[aria-label='Submit application']")
            if submit:
                await submit.click(timeout=8000)
                await self.human_delay(1, 2)
                self.applied_this_run += 1
                self._applied_ids.add(job_id)
                print(f"      [+] EASY APPLY ({self.applied_this_run})", flush=True)
                await self.log_result(job_id, title, company, url, "applied", "easy-apply")
                await self.human_delay(2, 3)
                # Dismiss success modal
                try:
                    dismiss = await page.query_selector(
                        "button[aria-label='Dismiss'],button[aria-label='Schließen'],"
                        "button[aria-label='Close'],button.artdeco-modal__dismiss")
                    if dismiss and await dismiss.is_visible():
                        await dismiss.click(timeout=5000)
                except Exception:
                    pass
                return "applied"

            review = await page.query_selector(f"{modal} button[aria-label='Review your application']")
            if review:
                await review.click(timeout=5000)
                await self.human_delay(1, 2)
                continue

            nxt = await page.query_selector(f"{modal} button[aria-label='Continue to next step']")
            if nxt:
                await nxt.click(timeout=5000)
                await self.human_delay(1, 2)
                continue

            print(f"      [!] Easy Apply stuck at step {step}", flush=True)
            break

        # Close modal if stuck
        try:
            dismiss = await page.query_selector(
                "button[aria-label='Dismiss'],button[aria-label='Schließen'],"
                "button[aria-label='Close'],button.artdeco-modal__dismiss")
            if dismiss and await dismiss.is_visible():
                await dismiss.click(timeout=5000)
                await self.human_delay(0.5, 1)
                discard = await page.query_selector(
                    "button[aria-label='Discard'],button[aria-label='Verwerfen']")
                if discard and await discard.is_visible():
                    await discard.click(timeout=5000)
        except Exception:
            pass

        await self.log_result(job_id, title, company, url, "failed", "easy-apply-stuck")
        return "failed"

    # ── External ATS apply ────────────────────────────────────────────────────

    async def _external_apply(self, page, job_id: str, title: str, company: str, url: str) -> str:
        """Click the external Apply button, catch new tab or navigation, fill ATS form."""
        # The external apply control's visible text is just generic "Apply" —
        # the distinguishing phrase only lives in aria-label, so match on that.
        # It mounts asynchronously like the Easy Apply button, so wait for it
        # rather than checking once immediately.
        try:
            await page.wait_for_selector("a[aria-label*='apply' i], button[aria-label*='apply' i]", timeout=5000)
        except PlaywrightTimeout:
            pass

        apply_btn = None
        for sel in [
            "a[aria-label='Apply on company website']",
            "a[aria-label='Auf der Website des Unternehmens bewerben']",
            "a[aria-label*='company website' i]",
            "a[aria-label*='bewerben' i]",
            "button[aria-label*='apply' i]",
            "a[aria-label*='apply' i]",
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible() and await btn.is_enabled():
                    # Skip if this is Easy Apply (already tried above)
                    txt = await btn.inner_text()
                    if "Easy Apply" in txt:
                        continue
                    apply_btn = btn
                    break
            except Exception:
                continue

        if not apply_btn:
            await self.log_result(job_id, title, company, url, "skipped", "no apply button")
            return "skipped"

        # Set up new-page listener before clicking
        new_page_ref  = [None]
        new_page_ready = asyncio.Event()

        def _on_page(p):
            new_page_ref[0] = p
            new_page_ready.set()

        page.context.on("page", _on_page)
        try:
            await apply_btn.click(timeout=8000)
        except Exception as e:
            page.context.remove_listener("page", _on_page)
            await self.log_result(job_id, title, company, url, "skipped", f"click err: {e}")
            return "skipped"

        # Wait up to 6s for new tab
        try:
            await asyncio.wait_for(new_page_ready.wait(), timeout=6.0)
            ats_page = new_page_ref[0]
            # Wait for the new tab to navigate away from about:blank
            try:
                await ats_page.wait_for_url(
                    lambda u: u not in ("about:blank", ""), timeout=15000)
            except PlaywrightTimeout:
                pass
            await ats_page.wait_for_load_state("domcontentloaded", timeout=20000)
        except asyncio.TimeoutError:
            ats_page = None
        finally:
            page.context.remove_listener("page", _on_page)

        # Fallback: maybe the same tab navigated away from LinkedIn
        if not ats_page:
            await asyncio.sleep(3)
            if "linkedin.com" not in page.url:
                ats_page = page
            else:
                await self.log_result(job_id, title, company, url, "skipped", "no external page opened")
                return "skipped"

        ats_url = ats_page.url
        print(f"      [ext] Opened: {ats_url[:80]}", flush=True)

        # Skip if page didn't navigate to a real ATS
        if not ats_url or ats_url in ("about:blank", "") or "linkedin.com" in ats_url:
            if ats_page is not page:
                try:
                    await ats_page.close()
                except Exception:
                    pass
            await self.log_result(job_id, title, company, url, "skipped", "no external ATS loaded")
            return "skipped"

        success = await fill_ats(ats_page, self.profile)

        # Close external tab if it was a new one
        if ats_page is not page:
            try:
                await ats_page.close()
            except Exception:
                pass

        if success:
            self.applied_this_run += 1
            self._applied_ids.add(job_id)
            from .ats_handlers import detect_ats
            print(f"      [+] ATS APPLIED ({self.applied_this_run})", flush=True)
            await self.log_result(job_id, title, company, url, "applied",
                                  f"ats:{detect_ats(ats_url)}")
            return "applied"

        await self.log_result(job_id, title, company, url, "failed", f"ats-fill-failed:{ats_url[:60]}")
        return "failed"

    # ── Per-job orchestrator ──────────────────────────────────────────────────

    async def _apply_to_job(self, page, job_id: str,
                             title: str = "Unknown", company: str = "Unknown") -> str:
        url = self._job_url(job_id)
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)

        # Wait for apply button — confirms the job card content is rendered
        try:
            await page.wait_for_selector(
                "button[aria-label*='Easy Apply' i], a[aria-label*='apply' i], "
                "button[aria-label*='apply' i]", timeout=10000)
        except PlaywrightTimeout:
            pass
        await self.human_delay(0.5, 1)

        print(f"  [>] {title} @ {company}", flush=True)

        if self.already_applied(job_id):
            print("      [skip] Already applied", flush=True)
            return "duplicate"

        # 1. Try Easy Apply first (fastest path)
        result = await self._easy_apply(page, job_id, title, company, url)
        if result != "no-easy-apply":
            return result

        # 2. Fall back to external ATS apply
        return await self._external_apply(page, job_id, title, company, url)

    # ── Semaphore wrapper ─────────────────────────────────────────────────────

    async def _apply_with_semaphore(self, job: dict):
        async with self._sem:
            if self.at_limit():
                return
            job_id  = job["job_id"]
            title   = job.get("title", "Unknown")
            company = job.get("company", "Unknown")
            page = await self.ctx.new_page()
            try:
                await self._apply_to_job(page, job_id, title, company)
            except Exception as e:
                print(f"      [error] {e}", flush=True)
                await self.log_result("?", "?", "?", self._job_url(job_id), "error", str(e)[:100])
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            await self.human_delay(1, 2)

    # ── Job collector (id + title + company from list) ────────────────────────

    async def _collect_jobs(self, _retried: bool = False) -> list[dict]:
        """Return list of {job_id, title, company} from the current search results page."""
        try:
            await self.page.wait_for_selector("li[data-occludable-job-id]", timeout=15000)
        except PlaywrightTimeout:
            # Cold first-load on a search page can be slow enough to blow this
            # wait even though results exist. Reload once and retry before
            # concluding there are truly no jobs.
            if not _retried:
                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                await self.human_delay(3, 5)
                return await self._collect_jobs(_retried=True)
            return []

        # LinkedIn virtualizes the results list — cards outside the scroll
        # pane's current viewport are unrendered placeholders (title/company
        # come back empty) until scrolled into view. The pane's class name is
        # a build-hashed string that changes across deploys, so find it by
        # walking up from a card to the nearest scrollable ancestor instead
        # of hardcoding a selector, then walk it top to bottom so every card
        # mounts before we read it.
        await self.page.evaluate("""async () => {
            const firstCard = document.querySelector('li[data-occludable-job-id]');
            if (!firstCard) return;
            let el = firstCard.parentElement;
            let pane = null;
            while (el) {
                const cs = getComputedStyle(el);
                if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
                    el.scrollHeight > el.clientHeight + 50) {
                    pane = el;
                    break;
                }
                el = el.parentElement;
            }
            if (!pane) return;
            const step = Math.max(300, Math.floor(pane.clientHeight * 0.8));
            for (let y = 0; y < pane.scrollHeight; y += step) {
                pane.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 250));
            }
            pane.scrollTo(0, 0);
            await new Promise(r => setTimeout(r, 200));
        }""")
        await self.human_delay(0.5, 1)

        jobs = await self.page.evaluate("""() => {
            const results = [];
            const cards = document.querySelectorAll('li[data-occludable-job-id]');
            cards.forEach(card => {
                const jid = card.getAttribute('data-occludable-job-id');
                if (!jid) return;

                // Title — try several selectors
                const titleEl = card.querySelector(
                    'a.job-card-container__link strong,' +
                    'a.job-card-list__title--link,' +
                    '.job-card-container__link .sr-only,' +
                    '.artdeco-entity-lockup__title,' +
                    'a[class*="job-card"] span:not(.sr-only)'
                );
                const title = titleEl?.innerText?.trim() || 'Unknown';

                // Company
                const compEl = card.querySelector(
                    '.job-card-container__company-name,' +
                    '.artdeco-entity-lockup__subtitle,' +
                    '[class*="company-name"],' +
                    'span[class*="subtitle"]'
                );
                const company = compEl?.innerText?.trim() || 'Unknown';

                results.push({ job_id: jid, title, company });
            });
            return results;
        }""")

        return [j for j in jobs if j["job_id"] not in self._applied_ids]

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self):
        await self.start_browser()
        try:
            await self._ensure_logged_in()
            self._applied_ids = self._load_applied_ids()

            locations = self.search.get("locations", [self.search.get("location", "Germany")])
            for location in locations:
                for keyword in self.search["keywords"]:
                    if self.at_limit():
                        break
                    search_url = self._search_url(keyword, location)
                    print(f"\n[linkedin] {keyword} / {location} [All jobs]", flush=True)
                    await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    await self.human_delay(3, 5)

                    page_num = 0
                    while not self.at_limit():
                        jobs = await self._collect_jobs()
                        if not jobs:
                            print("  [linkedin] No jobs found", flush=True)
                            break

                        print(f"  [linkedin] Page {page_num+1}: {len(jobs)} jobs "
                              f"({self.CONCURRENCY} tabs at once)", flush=True)

                        await asyncio.gather(*[
                            self._apply_with_semaphore(job)
                            for job in jobs
                            if not self.at_limit()
                        ])

                        # Next page
                        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                        await self.human_delay(2, 3)
                        next_btn = await self.page.query_selector("button[aria-label='View next page']")
                        if not next_btn:
                            print("  [linkedin] No more pages", flush=True)
                            break
                        await next_btn.click(timeout=5000)
                        page_num += 1
                        await self.human_delay(3, 5)

        finally:
            await self.close_browser()

        print(f"\n[linkedin] Done. Applied to {self.applied_this_run} jobs.", flush=True)
        return self.applied_this_run
