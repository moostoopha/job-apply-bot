import asyncio
import json
import re
import urllib.request
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout

from .async_base import AsyncBaseBot

# Only 1 Ollama call at a time — prevents CPU contention across concurrent tabs
_OLLAMA_LOCK = asyncio.Lock()


class AsyncStepStoneBot(AsyncBaseBot):
    platform = "stepstone"

    def _search_url(self, keyword: str, location: str = "germany") -> str:
        kw  = keyword.lower().replace(" ", "-")
        loc = location.split(",")[0].strip().lower()
        return f"https://www.stepstone.de/jobs/{kw}/in-{loc}"

    def _extract_job_id(self, url: str) -> str:
        m = re.search(r'/(\d+)(?:\?|$)', url)
        return m.group(1) if m else url

    async def _ensure_logged_in(self):
        await self.load_session()
        cookies = await self.ctx.cookies()
        auth = any(c["name"] in ("AuthCA", "AuthCARefresh") for c in cookies)
        if not auth:
            raise RuntimeError("StepStone session expired. Run save_stepstone_session.py first.")
        print("  [stepstone] Session loaded OK (AuthCA found)", flush=True)

    async def _accept_cookies(self, page):
        try:
            btn = await page.query_selector("button[id='ccmgt_explicit_accept']")
            if btn and await btn.is_visible():
                await btn.click()
                await self.human_delay(0.5, 1)
        except Exception:
            pass

    def _extract_salary(self) -> int:
        return self.profile.get("salary_default", 70000)

    # ── Form snapshot ─────────────────────────────────────────────────────────

    async def _extract_form_snapshot(self, page):
        return await page.evaluate("""
            () => {
                const getLabel = (el) => {
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) return lbl.innerText.trim();
                    }
                    const parent = el.closest('li,.form-group,.field,fieldset,[class*="form"],[class*="question"]');
                    if (parent) {
                        const lbl = parent.querySelector('label:not([for]),legend,span.label,.label,p,strong');
                        if (lbl && !lbl.contains(el)) return lbl.innerText.trim().substring(0,120);
                    }
                    return el.placeholder || el.getAttribute('aria-label') || el.name || '';
                };
                const fields = [];
                const sel = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]):not([type="date"]):not([type="datetime-local"]),select,textarea';
                document.querySelectorAll(sel).forEach((el,i) => {
                    if (!el.offsetParent) return;
                    const opts = el.tagName==='SELECT'
                        ? Array.from(el.options).map(o=>o.text.trim()).filter(t=>t) : [];
                    fields.push({index:i, id:el.id||'', name:el.name||'',
                        type:el.type||el.tagName.toLowerCase(),
                        label:getLabel(el), placeholder:el.placeholder||'',
                        options:opts, currentValue:el.value||'', required:el.required});
                });
                document.querySelectorAll('input[type="checkbox"],input[type="radio"]').forEach((el,i)=>{
                    if (!el.offsetParent) return;
                    fields.push({index:2000+i, id:el.id||'', name:el.name||'',
                        type:el.type, label:getLabel(el), options:[],
                        currentValue:el.checked?'checked':'unchecked', required:el.required});
                });
                return fields;
            }
        """)

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _call_ollama_sync(self, prompt: str) -> dict:
        payload = json.dumps({
            "model": "mustafa",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 1500}
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=280) as resp:
            return json.loads(resp.read())

    async def _ai_form_fill(self, page):
        fields = await self._extract_form_snapshot(page)
        if not fields:
            return

        fields = [f for f in fields if not f.get("currentValue") or f["currentValue"] == "unchecked"]
        if not fields:
            return

        prof = self.profile
        profile_text = f"""
Name: {prof.get('name','Mustafa Hassan')}
Email: {prof.get('email','mustufahasan588@gmail.com')}
Phone: {prof.get('phone_country_code','+49')}{prof.get('phone','017666095200')}
Address: {prof.get('address','Dörpfeldstraße 23')}, {prof.get('postcode','12489')} {prof.get('city','Berlin')}
Desired salary: {prof.get('salary_default',70000)} EUR/year
German level: {prof.get('german_level','B2 - Gute Kenntnisse')}
English level: {prof.get('english_level','Verhandlungssicheres Niveau (C2)')}
Other languages: {prof.get('other_languages','Arabisch (Muttersprache), Urdu (Muttersprache)')}
Willing to relocate: Ja, bundesweit
Notice period: sofort verfügbar
Currently employed at this company: Nein
Referral: (none)
"""
        prompt = f"""You are a job application assistant filling a German online application form.

Applicant profile:{profile_text}

Form fields (JSON):
{json.dumps(fields, ensure_ascii=False)}

Return a JSON array of fill instructions. Each item:
  {{"index":<int>,"action":"<fill|select|check>","value":"<string or bool>"}}

Rules:
- fill: text/textarea inputs
- select: native <select> dropdowns — use EXACT option text from "options" array
- check: checkboxes (value: true to check)
- Skip fields with currentValue already set
- salary/Gehalt/Gehaltsspanne: fill "70000"
- Kündigungsfrist/notice: fill "sofort"
- "Bist Du bereits bei uns beschäftigt": select "Nein"
- "Berücksichtigung für andere Jobs": select "Ja"
- relocation/Umzug: select "Ja"
- start date/Eintrittsdatum: skip
- referral/Empfehlung: skip
- language dropdowns: match profile above
- consent/Datenschutz checkboxes: check true
- salutation/Anrede: select "Herr"
- currency: select "EUR"
- country code: select "+49"
- Return ONLY the JSON array, no explanation, no markdown.
"""

        try:
            async with _OLLAMA_LOCK:  # serialize Ollama calls — prevents CPU timeout
                result = await asyncio.to_thread(self._call_ollama_sync, prompt)
            raw = result.get("response", "").strip()
            if raw.startswith("```"):
                raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
            decisions = json.loads(raw)
            print(f"      [ai-form] {len(decisions)} decisions from qwen2.5", flush=True)
        except Exception as e:
            print(f"      [ai-form] Ollama error: {e} — using heuristics", flush=True)
            return

        text_sel = ('input:not([type="hidden"]):not([type="submit"]):not([type="button"])'
                    ':not([type="file"]):not([type="checkbox"]):not([type="radio"])'
                    ':not([type="date"]):not([type="datetime-local"]),select,textarea')
        text_els = await page.query_selector_all(text_sel)
        cb_els   = await page.query_selector_all("input[type='checkbox'],input[type='radio']")

        for dec in decisions:
            try:
                idx    = dec.get("index", -1)
                action = dec.get("action", "fill")
                value  = dec.get("value", "")

                if idx >= 2000:
                    real = idx - 2000
                    if real < len(cb_els):
                        el = cb_els[real]
                        if await el.is_visible():
                            if bool(value) != await el.is_checked():
                                await el.evaluate("el => el.click()")
                                await self.human_delay(0.1, 0.2)
                else:
                    if idx >= len(text_els):
                        continue
                    el = text_els[idx]
                    if not await el.is_visible():
                        continue
                    tag = await el.evaluate("el => el.tagName.toLowerCase()")
                    if action == "fill":
                        await el.fill(str(value))
                    elif action == "select":
                        if tag == "select":
                            try:
                                await el.select_option(label=str(value))
                            except Exception:
                                try:
                                    await el.select_option(str(value))
                                except Exception:
                                    pass
                        else:
                            try:
                                await el.click(timeout=4000)
                                await self.human_delay(0.3, 0.5)
                                opt = await page.query_selector(
                                    f"li:has-text('{value}'),[role='option']:has-text('{value}')")
                                if opt and await opt.is_visible():
                                    await opt.click(timeout=4000)
                                else:
                                    await el.fill(str(value))
                            except Exception:
                                try:
                                    await el.fill(str(value))
                                except Exception:
                                    pass
                    await self.human_delay(0.1, 0.2)
            except Exception as e:
                print(f"      [ai-form] err idx={dec.get('index')}: {e}", flush=True)

    # ── Smart-apply form ──────────────────────────────────────────────────────

    async def _fill_smart_apply_form(self, page) -> bool:
        await self.human_delay(1, 2)

        # 1. AI fill
        await self._ai_form_fill(page)
        await self.human_delay(0.5, 1)

        # 2. Salary fallback
        salary = str(self._extract_salary())
        for inp in await page.query_selector_all(
            "input[name*='EMBEDDED_99904'],input[name*='salary'],input[name*='Salary']"
        ):
            try:
                if await inp.is_visible() and not await inp.input_value():
                    await inp.fill(salary)
                    break
            except Exception:
                pass

        # 3. Other-languages textarea fallback
        other_langs = self.profile.get("other_languages", "")
        if other_langs:
            for ta in await page.query_selector_all("textarea"):
                try:
                    if not await ta.is_visible():
                        continue
                    ctx = await ta.evaluate(
                        "el => el.closest('div,li,fieldset')?.innerText?.substring(0,100)||''")
                    if any(k in ctx.lower() for k in ["weitere sprach","other lang","sonstige sprach"]):
                        if not await ta.input_value():
                            await ta.fill(other_langs)
                except Exception:
                    pass

        # 4. Check remaining unchecked checkboxes
        for cb in await page.query_selector_all("input[type='checkbox']"):
            try:
                if await cb.is_visible() and not await cb.is_checked():
                    await cb.evaluate("el => el.click()")
                    await self.human_delay(0.1, 0.2)
            except Exception:
                pass

        # 5. Upload German CV
        cv_path = Path(self.profile.get("cv_path_de", self.profile.get("cv_path", ""))).expanduser()
        if cv_path.exists():
            fi = await page.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(str(cv_path))
                await self.human_delay(1, 2)

        await self.human_delay(1, 2)

        # 6. Submit
        submit = await page.query_selector(
            "button:has-text('Bewerbung abschicken'),button[type='submit']:visible"
        )
        if submit and await submit.is_visible():
            await submit.click(timeout=8000)
            await self.human_delay(2, 3)
            return True
        return False

    # ── Workday ───────────────────────────────────────────────────────────────

    async def _handle_workday(self, page):
        print("      [workday] Handling Workday ATS...", flush=True)
        try:
            cb = await page.query_selector("button:has-text('Accept Cookies'),button:has-text('Akzeptieren')")
            if cb and await cb.is_visible():
                await cb.click()
                await self.human_delay(0.5, 1)
        except Exception:
            pass

        try:
            last = await page.query_selector(
                "button:has-text('Use My Last Application'),button:has-text('Letzte Bewerbung verwenden')")
            if last and await last.is_visible():
                await last.click()
                await self.human_delay(2, 3)
                print("      [workday] Used last application", flush=True)
                await self._workday_click_through(page)
                return
        except Exception:
            pass

        try:
            af = await page.query_selector(
                "button:has-text('Autofill with Resume'),button:has-text('Mit Lebenslauf ausfüllen')")
            if af and await af.is_visible():
                await af.click()
                await self.human_delay(2, 3)
        except Exception:
            pass

        cv_path = Path(self.profile.get("cv_path_de", self.profile.get("cv_path", ""))).expanduser()
        if cv_path.exists():
            try:
                fi = await page.query_selector("input[type='file']")
                if fi:
                    await fi.set_input_files(str(cv_path))
                    await self.human_delay(2, 3)
                    nxt = await page.query_selector("button:has-text('Next'),button:has-text('Weiter')")
                    if nxt and await nxt.is_visible():
                        await nxt.click()
                        await self.human_delay(2, 3)
            except Exception as e:
                print(f"      [workday] CV upload err: {e}", flush=True)

        for name, val in {
            "addressLine1": self.profile.get("address", "Dörpfeldstraße 23"),
            "postalCode":   self.profile.get("postcode", "12489"),
            "city":         self.profile.get("city", "Berlin"),
        }.items():
            try:
                inp = await page.query_selector(f"input[name='{name}']:visible")
                if inp and not await inp.input_value():
                    await inp.fill(val)
                    await self.human_delay(0.2, 0.4)
            except Exception:
                pass

        await self._workday_click_through(page)

    async def _workday_click_through(self, page):
        for _ in range(15):
            await self.human_delay(2, 3)
            try:
                sub = await page.query_selector(
                    "button:has-text('Submit'),button:has-text('Absenden'),button:has-text('Bewerbung abschicken')")
                if sub and await sub.is_visible() and await sub.is_enabled():
                    await sub.click()
                    await self.human_delay(2, 3)
                    print("      [workday] Submitted!", flush=True)
                    await page.close()
                    return
                nxt = await page.query_selector("button:has-text('Next'),button:has-text('Weiter')")
                if nxt and await nxt.is_visible() and await nxt.is_enabled():
                    await nxt.click()
                else:
                    break
            except Exception:
                break
        try:
            await page.close()
        except Exception:
            pass

    # ── Single job ────────────────────────────────────────────────────────────

    async def _apply_to_job(self, page, job_url: str) -> str:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
        await self.human_delay(1, 2)

        title_el = await page.query_selector("h1")
        title    = (await title_el.inner_text()).strip() if title_el else "Unknown"
        company  = await page.evaluate("""() => {
            const sels = [
                '[data-testid="company-name"]',
                '[data-at="job-company-name"]',
                'a[href*="/cmp/"]',
                '.at-company-name',
                '[class*="company-name"]',
                '[class*="CompanyName"]',
                '[class*="provider-logo-text"]',
                'span[itemprop="name"]',
            ];
            for (const s of sels) {
                const el = document.querySelector(s);
                if (el && el.innerText.trim()) return el.innerText.trim();
            }
            return 'Unknown';
        }""")
        job_id  = self._extract_job_id(job_url)

        print(f"  [>] {title} @ {company}", flush=True)

        if self.already_applied(job_id):
            print("      [skip] Already applied", flush=True)
            return "duplicate"

        apply_btn = None
        for text in ["Ich bin interessiert", "Bewerbung fortsetzen", "Jetzt bewerben", "Bewerben"]:
            btn = await page.query_selector(f"button:has-text('{text}')")
            if btn and await btn.is_visible() and await btn.is_enabled():
                apply_btn = btn
                break

        if not apply_btn:
            await self.log_result(job_id, title, company, job_url, "skipped", "no apply button")
            return "skipped"

        await apply_btn.click(timeout=10000)
        await self.human_delay(2, 3)

        if "smart-apply" in page.url:
            success = await self._fill_smart_apply_form(page)
            if not success:
                await self.log_result(job_id, title, company, job_url, "failed", "smart-apply submit failed")
                return "failed"
        elif "confirmation" in page.url:
            confirm = await page.query_selector(
                "button:has-text('Yes, I want to!'),button:has-text('Ja, ich will!')")
            if confirm and await confirm.is_visible():
                await confirm.click(timeout=8000)
                await self.human_delay(1, 2)
        else:
            await self.human_delay(2, 3)
            if "smart-apply" in page.url:
                await self._fill_smart_apply_form(page)
            elif "confirmation" not in page.url:
                await self.log_result(job_id, title, company, job_url, "failed",
                                      f"unexpected url: {page.url[:60]}")
                return "failed"

        # Handle external ATS tabs
        await self.human_delay(2, 3)
        for p in self.ctx.pages:
            if p != page and "stepstone.de" not in p.url:
                try:
                    if "workday" in p.url or "myworkday" in p.url:
                        await self._handle_workday(p)
                    else:
                        await p.close()
                except Exception as e:
                    print(f"      [ats-err] {e}", flush=True)
                    try:
                        await p.close()
                    except Exception:
                        pass

        self.applied_this_run += 1
        self._applied_ids.add(job_id)
        print(f"      [+] APPLIED ({self.applied_this_run})", flush=True)
        await self.log_result(job_id, title, company, job_url, "applied")
        return "applied"

    # ── Semaphore wrapper ─────────────────────────────────────────────────────

    async def _apply_with_semaphore(self, job_url: str):
        async with self._sem:
            if self.at_limit():
                return
            page = await self.ctx.new_page()
            try:
                await self._apply_to_job(page, job_url)
            except Exception as e:
                print(f"      [error] {e}", flush=True)
                await self.log_result("?", "?", "?", job_url, "error", str(e)[:120])
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
            await self.human_delay(1, 2)

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
                    print(f"\n[stepstone] {keyword} in {location}", flush=True)
                    await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    await self.human_delay(2, 4)
                    await self._accept_cookies(self.page)

                    page_num = 1
                    while not self.at_limit():
                        links = await self.page.query_selector_all("a[href*='/stellenangebote--']")
                        job_urls, seen = [], set()
                        for a in links:
                            href = await a.get_attribute("href") or ""
                            if href and href not in seen:
                                full = href if href.startswith("http") else "https://www.stepstone.de" + href
                                job_urls.append(full)
                                seen.add(href)

                        if not job_urls:
                            print("  [stepstone] No job links found", flush=True)
                            break

                        print(f"  [stepstone] Page {page_num}: {len(job_urls)} jobs "
                              f"({self.CONCURRENCY} tabs at once)", flush=True)

                        # Process all jobs on this page concurrently (bounded by semaphore)
                        await asyncio.gather(*[
                            self._apply_with_semaphore(url)
                            for url in job_urls
                            if not self.at_limit()
                        ])

                        # Pagination
                        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                        await self.human_delay(2, 3)
                        next_link = await self.page.query_selector(f"a[href*='?page={page_num+1}']")
                        if not next_link:
                            print("  [stepstone] No more pages", flush=True)
                            break
                        await self.page.goto(f"{search_url}?page={page_num+1}",
                                             wait_until="domcontentloaded", timeout=30000)
                        page_num += 1
                        await self.human_delay(3, 5)

        finally:
            await self.close_browser()

        print(f"\n[stepstone] Done. Applied to {self.applied_this_run} jobs.", flush=True)
        return self.applied_this_run
