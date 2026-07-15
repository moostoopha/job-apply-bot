#!/usr/bin/env python3
"""
Save StepStone login session.

Opens real Chrome at StepStone login page. Log in with Google or email/password.
Script waits (up to 5 min) for you to finish, then saves cookies and exits.

Next time: cookies load automatically — no login needed.
"""
import json
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_FILE = Path(__file__).parent / "sessions" / "stepstone.json"
SESSION_FILE.parent.mkdir(exist_ok=True)

# Use a persistent temp profile so Google stays signed in between attempts
CHROME_PROFILE = Path("/tmp/chrome-stepstone-profile")
CHROME_PROFILE.mkdir(exist_ok=True)

print("Opening Chrome for StepStone login...")
print("  → Log in with Google or email/password")
print("  → Once you see the StepStone homepage (logged in), wait — script saves automatically")
print()

chrome = subprocess.Popen(
    [
        "/opt/google/chrome/chrome",
        "--remote-debugging-port=9222",
        f"--user-data-dir={CHROME_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.stepstone.de/login",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

time.sleep(4)

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    print("Waiting for you to log in (up to 5 minutes)...")
    logged_in = False
    for i in range(100):
        url = page.url
        on_stepstone = "stepstone.de" in url
        on_login = "login" in url.lower()
        on_google = "accounts.google" in url or "google.com" in url

        if on_stepstone and not on_login and not on_google:
            # Also wait for user menu element to confirm login is done
            try:
                page.wait_for_selector(
                    "[data-testid='header-user-menu'], .at-header-user-menu, "
                    "a[href*='/mein-bereich'], a[href*='/my-account'], "
                    "[aria-label='Profil'], [data-testid='user-menu']",
                    timeout=5000,
                )
                logged_in = True
                print(f"  Logged in at: {url[:70]}")
                break
            except Exception:
                pass

        if i % 5 == 0:
            status = "on Google sign-in" if on_google else url[:60]
            print(f"  Waiting... ({i*3}s) — {status}")
        time.sleep(3)

    if not logged_in:
        print("Timed out — saving whatever StepStone cookies exist anyway")

    time.sleep(2)
    all_cookies = ctx.cookies()
    stepstone_cookies = [c for c in all_cookies if "stepstone" in c.get("domain", "")]

    if stepstone_cookies:
        SESSION_FILE.write_text(json.dumps(stepstone_cookies, indent=2))
        SESSION_FILE.chmod(0o600)
        print(f"\nSaved {len(stepstone_cookies)} StepStone cookies to {SESSION_FILE}")
        print("Run: python3 main.py --platform stepstone")
    else:
        print("\nNo StepStone cookies found — login may not have completed.")

chrome.terminate()
