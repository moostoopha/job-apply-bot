"""
ATS detection and form-filling handlers.

Supported: Personio, Greenhouse, Lever, SmartRecruiters, Workday, + Ollama generic fallback.
"""

import asyncio
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

_OLLAMA_LOCK = asyncio.Lock()
_OLLAMA_URL  = "http://localhost:11434/api/generate"


# ── ATS detection ─────────────────────────────────────────────────────────────

def detect_ats(url: str) -> str:
    u = url.lower()
    if "myworkday.com" in u or "wd3.myworkday" in u or "wd5.myworkday" in u:
        return "workday"
    if "personio.de" in u or "personio.com" in u:
        return "personio"
    if "greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    if "bamboohr.com" in u:
        return "bamboohr"
    if "softgarden.de" in u or "softgarden.io" in u:
        return "softgarden"
    if "recruitee.com" in u:
        return "recruitee"
    if "successfactors.com" in u or "sapsf.com" in u:
        return "successfactors"
    if "taleo.net" in u:
        return "taleo"
    return "unknown"


# ── Ollama helper ─────────────────────────────────────────────────────────────

def _call_ollama_sync(prompt: str) -> str:
    payload = json.dumps({
        "model": "mustafa",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 600},
    }).encode()
    req = urllib.request.Request(
        _OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=280) as resp:
            return json.loads(resp.read())["response"].strip()
    except Exception as e:
        return f"ERROR: {e}"


async def _call_ollama(prompt: str) -> str:
    async with _OLLAMA_LOCK:
        return await asyncio.to_thread(_call_ollama_sync, prompt)


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _upload_cv(page, profile: dict) -> bool:
    cv_path = Path(profile.get("cv_path_de", profile.get("cv_path", ""))).expanduser()
    if not cv_path.exists():
        cv_path = Path(profile.get("cv_path", "")).expanduser()
    if cv_path.exists():
        fi = await page.query_selector("input[type='file']")
        if fi:
            await fi.set_input_files(str(cv_path))
            await asyncio.sleep(1.5)
            return True
    return False


def _name_parts(profile: dict):
    parts = profile.get("name", "Mustafa Hassan").split()
    first = parts[0]
    last  = " ".join(parts[1:]) if len(parts) > 1 else ""
    return first, last


async def _fill_if_empty(page, selector: str, value: str):
    try:
        el = await page.query_selector(selector)
        if el and await el.is_visible():
            if not await el.input_value():
                await el.fill(value)
    except Exception:
        pass


# ── Personio ──────────────────────────────────────────────────────────────────

async def fill_personio(page, profile: dict) -> bool:
    await asyncio.sleep(2)
    first, last = _name_parts(profile)

    field_map = [
        ("input[data-testid='first-name'],input[name*='first'],input[placeholder*='Vorname'],input[placeholder*='First']", first),
        ("input[data-testid='last-name'],input[name*='last'],input[placeholder*='Nachname'],input[placeholder*='Last']",   last),
        ("input[type='email'],input[name*='email']",           profile.get("email", "")),
        ("input[type='tel'],input[name*='phone'],input[placeholder*='Telefon'],input[placeholder*='Phone']",
         profile.get("phone", "")),
        ("input[name*='salary'],input[placeholder*='Gehaltsvorstellung'],input[placeholder*='Salary']",
         str(profile.get("salary_default", 70000))),
        ("input[name*='city'],input[placeholder*='Stadt'],input[placeholder*='City']",
         profile.get("city", "Berlin")),
    ]
    for sel, val in field_map:
        await _fill_if_empty(page, sel, val)

    # Notice period dropdown / field
    for sel in ["select[name*='notice'],select[name*='Kündigungsfrist']",
                "input[name*='notice'],input[placeholder*='Kündigungsfrist']"]:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                tag = await el.evaluate("e => e.tagName.toLowerCase()")
                if tag == "select":
                    try:
                        await el.select_option(label=profile.get("notice_period", "sofort"))
                    except Exception:
                        pass
                else:
                    if not await el.input_value():
                        await el.fill(profile.get("notice_period", "sofort"))
                break
        except Exception:
            pass

    await _upload_cv(page, profile)
    await asyncio.sleep(1)

    submit = await page.query_selector(
        "button[type='submit'],button:has-text('Jetzt bewerben'),"
        "button:has-text('Bewerbung abschicken'),button:has-text('Apply now'),"
        "button:has-text('Submit')")
    if submit and await submit.is_visible():
        await submit.click(timeout=8000)
        await asyncio.sleep(3)
        return True
    return False


# ── Greenhouse ────────────────────────────────────────────────────────────────

async def fill_greenhouse(page, profile: dict) -> bool:
    await asyncio.sleep(2)
    first, last = _name_parts(profile)

    field_map = [
        ("input#first_name,input[name='first_name'],input[autocomplete='given-name']",   first),
        ("input#last_name,input[name='last_name'],input[autocomplete='family-name']",    last),
        ("input#email,input[name='email'],input[type='email']",                          profile.get("email", "")),
        ("input#phone,input[name='phone'],input[type='tel']",                            profile.get("phone", "")),
        ("input#job_application_location,input[name*='location'],input[name*='city']",  profile.get("city", "Berlin")),
    ]
    for sel, val in field_map:
        await _fill_if_empty(page, sel, val)

    # Resume upload — Greenhouse uses specific IDs
    cv_path = Path(profile.get("cv_path_de", profile.get("cv_path", ""))).expanduser()
    if cv_path.exists():
        for fi_sel in ["input#resume,input[name='resume']", "input[type='file']"]:
            fi = await page.query_selector(fi_sel)
            if fi:
                await fi.set_input_files(str(cv_path))
                await asyncio.sleep(1.5)
                break

    await asyncio.sleep(1)

    submit = await page.query_selector(
        "input#submit_app,button#submit_app,button[type='submit'],"
        "input[value='Submit Application'],button:has-text('Submit Application')")
    if submit and await submit.is_visible():
        await submit.click(timeout=8000)
        await asyncio.sleep(3)
        return True
    return False


# ── Lever ─────────────────────────────────────────────────────────────────────

async def fill_lever(page, profile: dict) -> bool:
    await asyncio.sleep(2)

    field_map = [
        ("input[name='name'],input[placeholder*='Full name'],input[placeholder*='Name']",
         profile.get("name", "")),
        ("input[name='email'],input[type='email']",
         profile.get("email", "")),
        ("input[name='phone'],input[type='tel']",
         profile.get("phone", "")),
        ("input[name='org'],input[placeholder*='Company'],input[placeholder*='Unternehmen']",
         "Currently seeking new opportunities"),
        ("input[name='location'],input[placeholder*='Location'],input[placeholder*='Standort']",
         profile.get("city", "Berlin")),
    ]
    for sel, val in field_map:
        await _fill_if_empty(page, sel, val)

    await _upload_cv(page, profile)
    await asyncio.sleep(1)

    submit = await page.query_selector(
        "button[type='submit'],a.template-btn-submit,"
        "button:has-text('Submit application'),button:has-text('Bewerbung absenden'),"
        "button:has-text('Apply')")
    if submit and await submit.is_visible():
        await submit.click(timeout=8000)
        await asyncio.sleep(3)
        return True
    return False


# ── SmartRecruiters ───────────────────────────────────────────────────────────

async def fill_smartrecruiters(page, profile: dict) -> bool:
    await asyncio.sleep(2)
    first, last = _name_parts(profile)

    field_map = [
        ("input[name='firstName'],input[placeholder*='Vorname'],input[placeholder*='First']",  first),
        ("input[name='lastName'],input[placeholder*='Nachname'],input[placeholder*='Last']",   last),
        ("input[name='email'],input[type='email']",                                             profile.get("email", "")),
        ("input[name='phoneNumber'],input[type='tel']",                                         profile.get("phone", "")),
    ]
    for sel, val in field_map:
        await _fill_if_empty(page, sel, val)

    await _upload_cv(page, profile)
    await asyncio.sleep(1)

    # SmartRecruiters has a multi-step wizard — click through up to 5 steps
    for _ in range(5):
        try:
            nxt = await page.query_selector(
                "button[data-test-id='btn-primary'],button:has-text('Next'),button:has-text('Weiter'),"
                "button:has-text('Continue'),button:has-text('Fortfahren')")
            if nxt and await nxt.is_visible() and await nxt.is_enabled():
                # Check if it's a submit button
                label = (await nxt.inner_text()).strip().lower()
                if any(k in label for k in ["submit", "apply", "absenden", "bewerben"]):
                    await nxt.click(timeout=8000)
                    await asyncio.sleep(3)
                    return True
                await nxt.click(timeout=5000)
                await asyncio.sleep(2)
        except Exception:
            break

    return False


# ── Workday ───────────────────────────────────────────────────────────────────

async def fill_workday(page, profile: dict) -> bool:
    await asyncio.sleep(3)

    # Accept cookies
    try:
        cb = await page.query_selector(
            "button:has-text('Accept Cookies'),button:has-text('Akzeptieren'),button:has-text('Accept')")
        if cb and await cb.is_visible():
            await cb.click(timeout=5000)
            await asyncio.sleep(1)
    except Exception:
        pass

    # Use last application if available (fastest path)
    try:
        last = await page.query_selector(
            "button:has-text('Use My Last Application'),button:has-text('Letzte Bewerbung verwenden')")
        if last and await last.is_visible():
            await last.click(timeout=5000)
            await asyncio.sleep(3)
            return await _workday_click_through(page)
    except Exception:
        pass

    # Autofill with resume
    cv_path = Path(profile.get("cv_path_de", profile.get("cv_path", ""))).expanduser()
    if cv_path.exists():
        try:
            af = await page.query_selector(
                "button:has-text('Autofill with Resume'),button:has-text('Mit Lebenslauf ausfüllen')")
            if af and await af.is_visible():
                await af.click(timeout=5000)
                await asyncio.sleep(2)
        except Exception:
            pass
        try:
            fi = await page.query_selector("input[type='file']")
            if fi:
                await fi.set_input_files(str(cv_path))
                await asyncio.sleep(2)
                nxt = await page.query_selector("button:has-text('Next'),button:has-text('Weiter')")
                if nxt and await nxt.is_visible():
                    await nxt.click(timeout=5000)
                    await asyncio.sleep(2)
        except Exception:
            pass

    return await _workday_click_through(page)


async def _workday_click_through(page) -> bool:
    for _ in range(15):
        await asyncio.sleep(2)
        try:
            sub = await page.query_selector(
                "button:has-text('Submit'),button:has-text('Absenden'),"
                "button:has-text('Bewerbung abschicken')")
            if sub and await sub.is_visible() and await sub.is_enabled():
                await sub.click(timeout=8000)
                await asyncio.sleep(2)
                print("      [workday] Submitted!", flush=True)
                return True
            nxt = await page.query_selector("button:has-text('Next'),button:has-text('Weiter')")
            if nxt and await nxt.is_visible() and await nxt.is_enabled():
                await nxt.click(timeout=5000)
            else:
                break
        except Exception:
            break
    return False


# ── BambooHR ──────────────────────────────────────────────────────────────────

async def fill_bamboohr(page, profile: dict) -> bool:
    await asyncio.sleep(2)
    first, last = _name_parts(profile)

    field_map = [
        ("input[id*='firstName'],input[name*='firstName']", first),
        ("input[id*='lastName'],input[name*='lastName']",   last),
        ("input[type='email']",                              profile.get("email", "")),
        ("input[type='tel'],input[id*='phone']",             profile.get("phone", "")),
    ]
    for sel, val in field_map:
        await _fill_if_empty(page, sel, val)

    await _upload_cv(page, profile)
    await asyncio.sleep(1)

    submit = await page.query_selector(
        "button[type='submit'],input[type='submit'],button:has-text('Submit'),button:has-text('Apply')")
    if submit and await submit.is_visible():
        await submit.click(timeout=8000)
        await asyncio.sleep(3)
        return True
    return False


# ── Softgarden ────────────────────────────────────────────────────────────────

async def fill_softgarden(page, profile: dict) -> bool:
    await asyncio.sleep(2)
    first, last = _name_parts(profile)

    field_map = [
        ("input[name*='firstName'],input[placeholder*='Vorname']", first),
        ("input[name*='lastName'],input[placeholder*='Nachname']",  last),
        ("input[type='email'],input[name*='email']",                profile.get("email", "")),
        ("input[type='tel'],input[name*='phone']",                  profile.get("phone", "")),
    ]
    for sel, val in field_map:
        await _fill_if_empty(page, sel, val)

    await _upload_cv(page, profile)
    await asyncio.sleep(1)

    submit = await page.query_selector("button[type='submit'],button:has-text('Bewerben'),button:has-text('Apply')")
    if submit and await submit.is_visible():
        await submit.click(timeout=8000)
        await asyncio.sleep(3)
        return True
    return False


# ── Generic Ollama fallback ───────────────────────────────────────────────────

async def fill_generic(page, profile: dict) -> bool:
    """Use Ollama to read the form and fill it intelligently."""
    await asyncio.sleep(2)
    # Some consent managers inject the modal after this initial settle time,
    # i.e. after fill_ats()'s own dismissal already ran — check again right
    # before we read/touch any field.
    await _dismiss_cookie_banner(page, attempts=4)

    snapshot = await page.evaluate("""() => {
        const results = [];
        const els = document.querySelectorAll(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]),'
            + 'select,textarea'
        );

        // For radio/checkbox inputs, the per-option <label> only names that one
        // option (e.g. "No") — find the enclosing question text separately so
        // the model can tell "No" (employed?) apart from "No" (relocate?).
        function groupQuestion(input) {
            const container = input.closest('fieldset, [role="radiogroup"], [role="group"]')
                || input.closest('div');
            if (container) {
                const legend = container.querySelector('legend, h1, h2, h3, h4, h5, h6');
                if (legend) return legend.innerText.trim();
            }
            let el = input.closest('div');
            for (let i = 0; i < 5 && el; i++) {
                const prev = el.previousElementSibling;
                if (prev && prev.innerText && prev.innerText.trim().length > 3
                    && prev.innerText.trim().length < 200) {
                    return prev.innerText.trim();
                }
                el = el.parentElement;
            }
            return '';
        }

        els.forEach((el, i) => {
            if (!el.offsetParent) return;
            const ownLabel = document.querySelector(`label[for="${el.id}"]`)?.innerText?.trim()
                || el.placeholder?.trim() || el.name?.trim()
                || el.getAttribute('aria-label')?.trim() || '';

            let label = ownLabel;
            if (el.type === 'radio' || el.type === 'checkbox') {
                const question = groupQuestion(el);
                label = question ? `${question} — ${ownLabel}` : ownLabel;
            }

            const options = el.tagName === 'SELECT'
                ? Array.from(el.options).map(o => o.text.trim()).filter(Boolean)
                : [];
            results.push({
                index: i, label, type: el.type || el.tagName.toLowerCase(), options,
                checked: (el.type === 'radio' || el.type === 'checkbox') ? el.checked : undefined,
            });
        });
        return results;
    }""")

    if not snapshot:
        return False

    profile_text = json.dumps({
        "full_name":      profile.get("name", ""),
        "first_name":     profile.get("name", "").split()[0],
        "last_name":      " ".join(profile.get("name", "").split()[1:]),
        "email":          profile.get("email", ""),
        "phone":          profile.get("phone", ""),
        "city":           profile.get("city", "Berlin"),
        "country":        "Germany",
        "salary":         profile.get("salary_default", 70000),
        "notice_period":  profile.get("notice_period", "sofort"),
        "german_level":   profile.get("german_level", "B2"),
        "english_level":  profile.get("english_level", "C2"),
        "relocation":     profile.get("relocation", "Ja"),
    })

    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""Fill this job application form using the profile below.

Profile: {profile_text}
Today's date: {today}

Form fields (JSON):
{json.dumps(snapshot[:40], indent=2)}

Return ONLY a JSON array of fill actions, no explanation:
[{{"index": 0, "action": "fill", "value": "Mustafa"}}, ...]

Rules:
- action "fill" for text/textarea/number inputs
- action "fill" for type "date" — value MUST be ISO format YYYY-MM-DD (use today's
  date above for "available immediately" / "notice period" questions)
- action "select" for select dropdowns (use exact option text from "options")
- action "check" for type "radio" or "checkbox" — set it on the ONE index whose
  label (formatted as "question — option") best matches the profile; value can be
  anything truthy. Do not emit "check" for options you don't want selected.
- For a data-protection / profile-visibility radio group, prefer the option that
  keeps the candidate visible for other/future roles unless a narrower option is
  clearly required.
- Skip unknown fields
- Use profile values that best match each field label
- Return valid JSON only"""

    response = await _call_ollama(prompt)

    try:
        m = re.search(r'\[.*?\]', response, re.DOTALL)
        if not m:
            return False
        decisions = json.loads(m.group())
    except Exception:
        return False

    all_els = await page.query_selector_all(
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]),'
        'select,textarea'
    )

    for dec in decisions:
        try:
            idx    = dec.get("index", -1)
            action = dec.get("action", "")
            value  = str(dec.get("value", ""))
            if idx < 0 or idx >= len(all_els) or (action != "check" and not value):
                continue
            el = all_els[idx]
            if not await el.is_visible():
                continue
            tag = await el.evaluate("e => e.tagName.toLowerCase()")
            input_type = await el.evaluate("e => e.type || ''")
            if action == "check" and input_type in ("radio", "checkbox"):
                await el.check(timeout=5000)
            elif action == "fill":
                await el.fill(value)
            elif action == "select" and tag == "select":
                try:
                    await el.select_option(label=value)
                except Exception:
                    try:
                        await el.select_option(value)
                    except Exception:
                        pass
            await asyncio.sleep(0.1)
        except Exception:
            pass

    await _upload_cv(page, profile)
    await asyncio.sleep(1)

    # A cookie modal can appear mid-flow (lazy-loaded consent scripts) even
    # after the initial dismissal, silently blocking the submit click.
    await _dismiss_cookie_banner(page, attempts=1)

    submit = await page.query_selector(
        "button[type='submit'],input[type='submit'],"
        "button:has-text('Submit'),button:has-text('Apply'),"
        "button:has-text('Absenden'),button:has-text('Bewerben'),"
        "button:has-text('Senden')")
    if submit and await submit.is_visible() and await submit.is_enabled():
        await submit.click(timeout=8000)
        await asyncio.sleep(3)
        return True

    return False


# ── Cookie consent ───────────────────────────────────────────────────────────

async def _dismiss_cookie_banner(page, attempts: int = 3) -> None:
    """GDPR cookie modals are near-universal on German career sites and sit on
    top of the whole form (blocking every input). Some consent managers
    inject the modal a beat after the rest of the page (e.g. after a
    third-party script loads), so poll a few times rather than checking once —
    a single missed banner blocks every field interaction and the final
    submit click silently."""
    # Accessibility-role matching (name is the computed accessible name, not
    # raw textContent) finds buttons that CSS :has-text()/text= selectors can
    # miss entirely on some consent widgets, even with no shadow DOM or
    # iframe involved — seen in practice on a Recruitee-hosted form. Try the
    # "accept everything" phrasing before narrower "necessary only" ones.
    role_name_patterns = [
        r"alle[ns]?\s*(akzeptieren|zustimmen)",
        r"accept\s*all",
        r"akzeptieren",
        r"zustimmen",
        r"einverstanden",
        r"^accept$",
    ]
    selectors = [
        "button:has-text('Alles akzeptieren')",
        "button:has-text('Allen zustimmen')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept')",
        "button:has-text('Zustimmen')",
        "button:has-text('Einverstanden')",
        "#onetrust-accept-btn-handler",
        "button[aria-label*='Accept' i]",
        "button[aria-label*='akzeptieren' i]",
        "button[aria-label*='zustimmen' i]",
    ]
    for _ in range(attempts):
        for pattern in role_name_patterns:
            try:
                loc = page.get_by_role("button", name=re.compile(pattern, re.I))
                if await loc.count():
                    await loc.first.click(timeout=3000)
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible() and await btn.is_enabled():
                    await btn.click(timeout=3000)
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue
        await asyncio.sleep(1)


# ── Router ────────────────────────────────────────────────────────────────────

async def fill_ats(page, profile: dict) -> bool:
    """Detect the ATS from page URL and route to the correct handler."""
    await _dismiss_cookie_banner(page)
    ats = detect_ats(page.url)
    print(f"      [ats:{ats}] {page.url[:70]}", flush=True)

    handlers = {
        "personio":       fill_personio,
        "greenhouse":     fill_greenhouse,
        "lever":          fill_lever,
        "smartrecruiters": fill_smartrecruiters,
        "workday":        fill_workday,
        "bamboohr":       fill_bamboohr,
        "softgarden":     fill_softgarden,
    }

    handler = handlers.get(ats, fill_generic)
    try:
        return await handler(page, profile)
    except Exception as e:
        print(f"      [ats] Error in {ats} handler: {e}", flush=True)
        # Last resort: try generic
        if ats != "unknown":
            try:
                return await fill_generic(page, profile)
            except Exception:
                pass
        return False
