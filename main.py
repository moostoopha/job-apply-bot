#!/usr/bin/env python3
"""
Job Application Bot
Usage:
  python3 main.py                    # run all enabled platforms (async multi-tab)
  python3 main.py --platform linkedin
  python3 main.py --platform stepstone
  python3 main.py --setup            # re-run setup wizard
  python3 main.py --stats            # show application stats
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

CONFIG_FILE = Path(__file__).parent / "config.json"
LOG_FILE    = Path(__file__).parent / "logs" / "applications.csv"


# ── Setup wizard ─────────────────────────────────────────────────────────────

def run_setup():
    print("\n=== Job Bot Setup ===\n")
    config = {}

    config["profile"] = {
        "name":     input("Your full name: ").strip(),
        "email":    input("Email: ").strip(),
        "phone":    input("Phone number (optional): ").strip(),
        "cv_path":  input("CV path [~/Documents/my_updated_resume/Mustafa_Hassan_CV_EN.docx]: ").strip()
                    or "~/Documents/my_updated_resume/Mustafa_Hassan_CV_EN.docx",
    }

    keywords_input = input("Job keywords (comma-separated) [DevOps Engineer,Platform Engineer,SRE]: ").strip()
    config["search"] = {
        "keywords": [k.strip() for k in keywords_input.split(",")] if keywords_input
                    else ["DevOps Engineer", "Platform Engineer", "SRE"],
        "location": input("Location [Berlin, Germany]: ").strip() or "Berlin, Germany",
        "remote":   input("Include remote? [Y/n]: ").strip().lower() != "n",
    }

    config["platforms"] = {
        "linkedin":  input("Enable LinkedIn? [Y/n]: ").strip().lower() != "n",
        "stepstone": input("Enable StepStone? [Y/n]: ").strip().lower() != "n",
    }

    max_run = input("Max applications per run [50]: ").strip()
    max_day = input("Max applications per day [100]: ").strip()
    config["limits"] = {
        "max_per_run": int(max_run) if max_run.isdigit() else 50,
        "max_per_day": int(max_day) if max_day.isdigit() else 100,
    }

    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print(f"\n[+] Config saved to {CONFIG_FILE}")
    return config


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print("[!] No config found. Running setup wizard...\n")
        return run_setup()
    return json.loads(CONFIG_FILE.read_text())


# ── Stats ─────────────────────────────────────────────────────────────────────

def show_stats():
    if not LOG_FILE.exists():
        print("No applications logged yet.")
        return
    import csv
    from collections import Counter
    rows = list(csv.DictReader(open(LOG_FILE)))
    total = len(rows)
    applied = [r for r in rows if r["status"] == "applied"]
    by_platform = Counter(r["platform"] for r in applied)
    print(f"\n=== Application Stats ===")
    print(f"Total applied : {len(applied)}")
    for platform, count in by_platform.items():
        print(f"  {platform:12}: {count}")
    print(f"Total logged  : {total} (includes skipped/errors)")
    if applied:
        print(f"\nLast 5 applications:")
        for r in applied[-5:]:
            print(f"  {r['timestamp']}  {r['title']} @ {r['company']} [{r['platform']}]")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Job Application Bot")
    parser.add_argument("--platform", choices=["linkedin", "stepstone"], help="Run only this platform")
    parser.add_argument("--setup", action="store_true", help="Re-run setup wizard")
    parser.add_argument("--stats", action="store_true", help="Show stats and exit")
    parser.add_argument("--visible", action="store_true", help="Show browser window (useful for debugging)")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if args.setup:
        run_setup()
        print("Setup complete. Run `python3 main.py` to start applying.")
        return

    config = load_config()
    headless = not args.visible

    # Determine which platforms to run
    platforms_to_run = []
    if args.platform:
        platforms_to_run = [args.platform]
    else:
        platforms_to_run = [p for p, enabled in config["platforms"].items() if enabled]

    if not platforms_to_run:
        print("[!] No platforms enabled. Edit config.json or run --setup")
        sys.exit(1)

    locations = config["search"].get("locations", [config["search"].get("location", "Germany")])
    print(f"\n[bot] Starting — platforms: {', '.join(platforms_to_run)}")
    print(f"[bot] Keywords : {', '.join(config['search']['keywords'])}")
    print(f"[bot] Locations: {', '.join(locations)}")
    print(f"[bot] Limits   : {config['limits']['max_per_run']} per run / {config['limits']['max_per_day']} per day\n")

    async def _run_async():
        total = 0
        async with async_playwright() as pw:
            if "linkedin" in platforms_to_run:
                from bots.async_linkedin import AsyncLinkedInBot
                bot = AsyncLinkedInBot(config, pw, headless=headless)
                total += await bot.run()

            if "stepstone" in platforms_to_run:
                from bots.async_stepstone import AsyncStepStoneBot
                bot = AsyncStepStoneBot(config, pw, headless=headless)
                total += await bot.run()
        return total

    total_applied = asyncio.run(_run_async())
    print(f"\n[done] Total applied this run: {total_applied}")
    show_stats()


if __name__ == "__main__":
    main()
