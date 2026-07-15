import re
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .base import BaseBot


class StepStoneBot(BaseBot):
    platform = "stepstone"
    LOGIN_URL = "https://www.stepstone.de/login"

    def _search_url(self, keyword: str, location: str = "germany") -> str:
        kw = keyword.lower().replace(" ", "-")
        loc = location.split(",")[0].strip().lower()
        return f"https://www.stepstone.de/jobs/{kw}/in-{loc}"

    def _ensure_logged_in(self):
        if self.load_session():
            # Check for auth cookie — more reliable than DOM selector
            cookies = self.ctx.cookies()
            auth = any(c["name"] in ("AuthCA", "AuthCARefresh") for c in cookies)
            if auth:
                print("  [stepstone] Session loaded OK (AuthCA found)")
                return
            print("  [stepstone] Session expired — run: python3 save_stepstone_session.py")
            self.session_file().unlink(missing_ok=True)
            raise RuntimeError("StepStone session expired. Run save_stepstone_session.py first.")

        raise RuntimeError(
            "No StepStone session found. Run: python3 save_stepstone_session.py"
        )

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

    def _extract_salary_from_page(self) -> int:
        """Try to find salary range on page and return midpoint, else default."""
        try:
            text = self.page.inner_text("body")
            # Match patterns like "60.000 - 80.000" or "60000 - 80000" or "60k - 80k"
            m = re.search(r'(\d[\d\.]+)\s*(?:k|K|\.000)?\s*[-–]\s*(\d[\d\.]+)\s*(?:k|K|\.000)?', text)
            if m:
                lo = float(m.group(1).replace(".", ""))
                hi = float(m.group(2).replace(".", ""))
                if lo < 1000:  # was "k" notation
                    lo *= 1000
                    hi *= 1000
                mid = int((lo + hi) / 2)
                if 20000 < mid < 200000:  # sanity check
                    return mid
        except Exception:
            pass
        return self.profile.get("salary_default", 70000)

    def _extract_form_snapshot(self, page=None):
        """Extract all visible form fields with labels and options from the page."""
        p = page or self.page
        return p.evaluate("""
            () => {
                const getLabel = (el) => {
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) return lbl.innerText.trim();
                    }
                    const parent = el.closest('li, .form-group, .field, fieldset, [class*="form"], [class*="question"]');
                    if (parent) {
                        const lbl = parent.querySelector('label:not([for]), legend, span.label, .label, p, strong');
                        if (lbl && !lbl.contains(el)) return lbl.innerText.trim().substring(0, 120);
                    }
                    return el.placeholder || el.getAttribute('aria-label') || el.name || '';
                };
                const fields = [];
                const sel = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), select, textarea';
                document.querySelectorAll(sel).forEach((el, i) => {
                    if (!el.offsetParent) return;
                    const opts = el.tagName === 'SELECT'
                        ? Array.from(el.options).map(o => o.text.trim()).filter(t => t)
                        : [];
                    fields.push({ index: i, id: el.id || '', name: el.name || '',
                        type: el.type || el.tagName.toLowerCase(),
                        label: getLabel(el), placeholder: el.placeholder || '',
                        options: opts, currentValue: el.value || '', required: el.required });
                });
                // Checkboxes / radios
                document.querySelectorAll('input[type="checkbox"], input[type="radio"]').forEach((el, i) => {
                    if (!el.offsetParent) return;
                    fields.push({ index: 2000 + i, id: el.id || '', name: el.name || '',
                        type: el.type, label: getLabel(el), options: [],
                        currentValue: el.checked ? 'checked' : 'unchecked', required: el.required });
                });
                return fields;
            }
        """)

    def _ai_form_fill(self, page=None):
        """Use local Ollama qwen2.5:3b to intelligently decide how to fill any form."""
        import json
        import urllib.request

        p = page or self.page
        fields = self._extract_form_snapshot(p)
        if not fields:
            return

        # Only send unfilled fields to keep prompt small and fast
        fields = [f for f in fields if not f.get("currentValue") or f["currentValue"] == "unchecked"]
        if not fields:
            return

        prof = self.profile
        profile_text = f"""
Name: {prof.get('name', 'Mustafa Hassan')}
Email: {prof.get('email', 'mustufahasan588@gmail.com')}
Phone: {prof.get('phone_country_code', '+49')}{prof.get('phone', '017666095200')}
Address: {prof.get('address', 'Dörpfeldstraße 23')}, {prof.get('postcode', '12489')} {prof.get('city', 'Berlin')}
Desired salary: {prof.get('salary_default', 70000)} EUR/year
German level: {prof.get('german_level', 'B2 - Gute Kenntnisse')}
English level: {prof.get('english_level', 'Verhandlungssicheres Niveau (C2)')}
Other languages: {prof.get('other_languages', 'Arabisch (Muttersprache), Urdu (Muttersprache)')}
Willing to relocate: Ja, bundesweit
Notice period: sofort verfügbar
Currently employed at this company: Nein
Referral: (none)
"""

        fields_json = json.dumps(fields, ensure_ascii=False, indent=2)

        prompt = f"""You are a job application assistant filling a German online application form automatically.

Applicant profile:
{profile_text}

Form fields (JSON):
{fields_json}

Return a JSON array of fill instructions. Each item:
  {{"index": <int>, "action": "<fill|select|check|uncheck>", "value": "<string or bool>"}}

Rules:
- "fill": use for text inputs and textareas. Set value to a string.
- "select": use for <select> dropdowns. Set value to the EXACT option text from the "options" array.
- "check": use for checkboxes (value: true to check, false to uncheck).
- Skip fields where currentValue is already set (not empty/unchecked), UNLESS required and value looks wrong.
- For salary / Gehalt / Gehaltsspanne fields: fill "70000"
- For notice period / Kündigungsfrist: fill "sofort"
- For "Bist Du bereits bei uns beschäftigt": select "Nein" or closest option
- For "Berücksichtigung für andere Jobs": select "Ja" or closest option
- For relocation / Umzug: select "Ja" or closest yes option
- For start date / Eintrittsdatum / Verfügbar ab: leave empty (skip)
- For referral / Empfehlung: skip (leave empty)
- For language level dropdowns: match profile values above
- For consent/Datenschutz checkboxes: check them (true)
- For "salutation" / "Anrede": select "Herr"
- For currency dropdowns: select "EUR"
- For country code / Ländervorwahl: select "+49"
- Return ONLY the JSON array — no explanation, no markdown, no code fences.
"""

        try:
            payload = json.dumps({
                "model": "qwen2.5:3b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 1500}
            }).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
            raw = result.get("response", "").strip()
            if raw.startswith("```"):
                raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
            decisions = json.loads(raw)
            print(f"      [ai-form] {len(decisions)} fill decisions from qwen2.5", flush=True)
        except Exception as e:
            print(f"      [ai-form] Ollama error: {e} — falling back to heuristics", flush=True)
            return

        text_sel = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), select, textarea'
        text_els = p.query_selector_all(text_sel)
        cb_els = p.query_selector_all("input[type='checkbox'], input[type='radio']")

        for dec in decisions:
            try:
                idx = dec.get("index", -1)
                action = dec.get("action", "fill")
                value = dec.get("value", "")

                if idx >= 2000:
                    real = idx - 2000
                    if real < len(cb_els):
                        el = cb_els[real]
                        if el.is_visible():
                            checked = el.is_checked()
                            want = bool(value)
                            if want != checked:
                                el.evaluate("el => el.click()")
                                self.human_delay(0.1, 0.2)
                else:
                    if idx >= len(text_els):
                        continue
                    el = text_els[idx]
                    if not el.is_visible():
                        continue
                    tag = el.evaluate("el => el.tagName.toLowerCase()")
                    if action == "fill":
                        el.fill(str(value))
                    elif action == "select":
                        if tag == "select":
                            try:
                                el.select_option(label=str(value))
                            except Exception:
                                try:
                                    el.select_option(str(value))
                                except Exception:
                                    pass
                        else:
                            # Custom dropdown — click to open then pick matching option
                            try:
                                el.click()
                                self.human_delay(0.3, 0.5)
                                opt = p.query_selector(f"li:has-text('{value}'), [role='option']:has-text('{value}')")
                                if opt and opt.is_visible():
                                    opt.click()
                                else:
                                    el.fill(str(value))
                            except Exception:
                                try:
                                    el.fill(str(value))
                                except Exception:
                                    pass
                    self.human_delay(0.1, 0.3)
            except Exception as e:
                print(f"      [ai-form] err idx={dec.get('index')}: {e}", flush=True)

    def _fill_smart_apply_form(self):
        """Fill the StepStone smart-apply form and submit."""
        from pathlib import Path
        self.human_delay(1, 2)

        # 1. AI-powered fill — handles any form, any company
        self._ai_form_fill()
        self.human_delay(0.5, 1)

        # 2. Heuristic fallback for known StepStone-specific fields that AI may miss
        salary = self._extract_salary_from_page()
        for inp in self.page.query_selector_all(
            "input[name*='EMBEDDED_99904'], input[name*='salary'], input[name*='Salary']"
        ):
            try:
                if inp.is_visible() and not inp.input_value():
                    inp.fill(str(salary))
                    break
            except Exception:
                pass

        # Other language textarea fallback
        other_langs = self.profile.get("other_languages", "")
        if other_langs:
            for ta in self.page.query_selector_all("textarea"):
                try:
                    if not ta.is_visible():
                        continue
                    ctx = ta.evaluate("el => el.closest('div,li,fieldset')?.innerText?.substring(0,100) || ''")
                    if any(k in ctx.lower() for k in ["weitere sprach", "other lang", "sonstige sprach"]):
                        if not ta.input_value():
                            ta.fill(other_langs)
                except Exception:
                    pass

        # 3. Check all remaining visible unchecked checkboxes (location cities, consents)
        for cb in self.page.query_selector_all("input[type='checkbox']"):
            try:
                if cb.is_visible() and not cb.is_checked():
                    cb.evaluate("el => el.click()")
                    self.human_delay(0.1, 0.2)
            except Exception:
                pass

        # 4. Upload German CV to any file input
        cv_path = Path(self.profile.get("cv_path_de", self.profile.get("cv_path", ""))).expanduser()
        if cv_path.exists():
            file_input = self.page.query_selector("input[type='file']")
            if file_input:
                file_input.set_input_files(str(cv_path))
                self.human_delay(1, 2)

        self.human_delay(1, 2)

        # 5. Submit
        submit = self.page.query_selector(
            "button:has-text('Bewerbung abschicken'), button[type='submit']:visible"
        )
        if submit and submit.is_visible():
            submit.click()
            self.human_delay(2, 3)
            return True
        return False

    def _handle_workday(self, page):
        """Handle Workday ATS that opens in a new tab after StepStone apply."""
        from pathlib import Path
        print("      [workday] Handling Workday ATS...", flush=True)

        # Accept cookies if banner present
        try:
            cookie_btn = page.query_selector("button:has-text('Accept Cookies'), button:has-text('Akzeptieren')")
            if cookie_btn and cookie_btn.is_visible():
                cookie_btn.click()
                self.human_delay(0.5, 1)
        except Exception:
            pass

        # Click "Use My Last Application" if available (fastest)
        try:
            last_app = page.query_selector(
                "button:has-text('Use My Last Application'), "
                "button:has-text('Letzte Bewerbung verwenden')"
            )
            if last_app and last_app.is_visible():
                last_app.click()
                self.human_delay(2, 3)
                print("      [workday] Used last application", flush=True)
                self._workday_click_through(page)
                return
        except Exception:
            pass

        # Otherwise: Autofill with Resume
        try:
            autofill = page.query_selector(
                "button:has-text('Autofill with Resume'), "
                "button:has-text('Mit Lebenslauf ausfüllen')"
            )
            if autofill and autofill.is_visible():
                autofill.click()
                self.human_delay(2, 3)
        except Exception:
            pass

        # Upload German CV
        cv_path = Path(self.profile.get("cv_path_de", self.profile.get("cv_path", ""))).expanduser()
        if cv_path.exists():
            try:
                file_input = page.query_selector("input[type='file']")
                if file_input:
                    file_input.set_input_files(str(cv_path))
                    self.human_delay(2, 3)
                    # Click Next after upload
                    next_btn = page.query_selector("button:has-text('Next'), button:has-text('Weiter')")
                    if next_btn and next_btn.is_visible():
                        next_btn.click()
                        self.human_delay(2, 3)
            except Exception as e:
                print(f"      [workday] CV upload err: {e}", flush=True)

        # Fill personal info fields that may be empty
        fields = {
            "addressLine1": self.profile.get("address", "Adlershof"),
            "postalCode":   self.profile.get("postcode", "12489"),
            "city":         self.profile.get("city", "Berlin"),
        }
        for name, value in fields.items():
            try:
                inp = page.query_selector(f"input[name='{name}']:visible")
                if inp and not inp.input_value():
                    inp.fill(value)
                    self.human_delay(0.2, 0.4)
            except Exception:
                pass

        self._workday_click_through(page)

    def _workday_click_through(self, page):
        """Click Next/Submit through remaining Workday pages."""
        for _ in range(15):
            self.human_delay(2, 3)
            try:
                # Submit if available
                submit = page.query_selector(
                    "button:has-text('Submit'), button:has-text('Absenden'), "
                    "button:has-text('Bewerbung abschicken')"
                )
                if submit and submit.is_visible() and submit.is_enabled():
                    submit.click()
                    self.human_delay(2, 3)
                    print("      [workday] Submitted!", flush=True)
                    page.close()
                    return

                # Next page
                nxt = page.query_selector(
                    "button:has-text('Next'), button:has-text('Weiter')"
                )
                if nxt and nxt.is_visible() and nxt.is_enabled():
                    nxt.click()
                else:
                    break
            except Exception:
                break
        try:
            page.close()
        except Exception:
            pass

    def _apply_to_job(self, job_url: str) -> str:
        """Navigate to a job URL and apply via one-click or smart-apply form."""
        self.page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        self.human_delay(1, 2)

        title_el = self.page.query_selector("h1")
        company_el = self.page.query_selector("a[href*='/cmp/']")
        title = title_el.inner_text().strip() if title_el else "Unknown"
        company = company_el.inner_text().strip() if company_el else "Unknown"
        job_id = self._extract_job_id(job_url)

        print(f"  [>] {title} @ {company}")

        if self.already_applied(job_id):
            print("      [skip] Already applied")
            return "duplicate"

        # Try all possible apply buttons
        apply_btn = None
        for text in ["Ich bin interessiert", "Bewerbung fortsetzen", "Jetzt bewerben", "Bewerben"]:
            btn = self.page.query_selector(f"button:has-text('{text}')")
            if btn and btn.is_visible():
                apply_btn = btn
                break

        if not apply_btn:
            self.log_result(job_id, title, company, job_url, "skipped", "no apply button")
            return "skipped"

        apply_btn.click()
        self.human_delay(2, 3)

        # Flow 1: smart-apply form
        if "smart-apply" in self.page.url:
            success = self._fill_smart_apply_form()
            if not success:
                self.log_result(job_id, title, company, job_url, "failed", "smart-apply submit failed")
                return "failed"

        # Flow 2: confirmation page (one-click)
        elif "confirmation" in self.page.url:
            confirm_btn = self.page.query_selector(
                "button:has-text('Yes, I want to!'), button:has-text('Ja, ich will!')"
            )
            if confirm_btn and confirm_btn.is_visible():
                confirm_btn.click()
                self.human_delay(1, 2)

        else:
            # Wait a bit more — page might still be loading
            self.human_delay(2, 3)
            if "smart-apply" in self.page.url:
                self._fill_smart_apply_form()
            elif "confirmation" not in self.page.url:
                self.log_result(job_id, title, company, job_url, "failed", f"unexpected url: {self.page.url[:60]}")
                return "failed"

        # Handle external ATS tabs that opened
        self.human_delay(2, 3)
        for p in self.ctx.pages:
            if p != self.page and "stepstone.de" not in p.url:
                try:
                    if "workday" in p.url or "myworkday" in p.url:
                        self._handle_workday(p)
                    else:
                        p.close()
                except Exception as e:
                    print(f"      [ats-err] {e}")
                    try:
                        p.close()
                    except Exception:
                        pass

        self.applied_this_run += 1
        print(f"      [+] APPLIED ({self.applied_this_run})")
        self.log_result(job_id, title, company, job_url, "applied")
        return "applied"

    def run(self):
        self.start_browser()
        try:
            self._ensure_logged_in()

            locations = self.search.get("locations", [self.search.get("location", "Germany")])
            for location in locations:
                for keyword in self.search["keywords"]:
                    if self.at_limit():
                        break
                    search_url = self._search_url(keyword, location)
                    print(f"\n[stepstone] Searching: {keyword} in {location}")
                    self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                    self.human_delay(2, 4)
                    self._accept_cookies()

                    page_num = 1
                    while not self.at_limit():
                        # Collect all job URLs first — avoids stale element errors
                        job_links = self.page.query_selector_all("a[href*='/stellenangebote--']")
                        job_urls = []
                        seen = set()
                        for a in job_links:
                            href = a.get_attribute("href") or ""
                            if href and href not in seen and "/stellenangebote--" in href:
                                full = href if href.startswith("http") else "https://www.stepstone.de" + href
                                # Normalize: strip -inline suffix variants
                                job_urls.append(full)
                                seen.add(href)

                        if not job_urls:
                            print("  [stepstone] No job links found")
                            break
                        print(f"  [stepstone] Page {page_num}: {len(job_urls)} jobs")

                        for job_url in job_urls:
                            if self.at_limit():
                                break
                            try:
                                self.retry(lambda u=job_url: self._apply_to_job(u))
                            except Exception as e:
                                print(f"      [error] {e}")
                                self.log_result("?", "?", "?", job_url, "error", str(e)[:120])
                            self.human_delay(2, 4)

                        # Go back to search page for pagination
                        self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                        self.human_delay(2, 3)

                        next_url = f"{search_url}?page={page_num + 1}"
                        next_link = self.page.query_selector(f"a[href*='?page={page_num + 1}']")
                        if not next_link:
                            print("  [stepstone] No more pages")
                            break
                        self.page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                        page_num += 1
                        self.human_delay(3, 5)

        finally:
            self.close_browser()

        print(f"\n[stepstone] Done. Applied to {self.applied_this_run} jobs this run.")
        return self.applied_this_run
