# Job Apply Bot

Automated job application bot for LinkedIn and StepStone. Supports LinkedIn Easy Apply, external ATS platforms (Greenhouse, Lever, Personio), and logs every application to a CSV.

## Features

- **LinkedIn Easy Apply** — steps through multi-page modals automatically
- **External ATS support** — Greenhouse, Lever, Personio
- **StepStone** — quick apply with CV upload
- **Deduplication** — skips jobs already applied to (tracked by job ID)
- **Daily limits** — configurable max applications per run and per day
- **Multi-keyword × multi-location** — runs every combination automatically
- **Session cookies** — avoids re-login on every run

## Setup

```bash
git clone <repo-url>
cd job-apply-bot
pip install -r requirements.txt
playwright install chromium
```

Run the setup wizard to create your config:

```bash
python3 main.py --setup
```

This creates `config.json` with your profile, search keywords, locations, and limits.

### Manual login (first time)

LinkedIn requires a real browser session to avoid bot detection. Run once with `--visible` to log in manually:

```bash
python3 main.py --platform linkedin --visible
```

The session is saved to `sessions/linkedin.json` and reused on future runs.

## Usage

```bash
# Run all enabled platforms
python3 main.py

# Run a specific platform
python3 main.py --platform linkedin
python3 main.py --platform stepstone

# Open browser window (useful for debugging)
python3 main.py --visible

# Show application stats
python3 main.py --stats

# Re-run setup wizard
python3 main.py --setup
```

## Configuration

Copy `config.example.json` to `config.json` and fill in your details:

```json
{
  "profile": {
    "name": "Your Name",
    "email": "your@email.com",
    "phone": "+49123456789",
    "cv_path": "~/Documents/your_cv.pdf"
  },
  "search": {
    "keywords": ["DevOps Engineer", "Platform Engineer", "SRE"],
    "locations": ["Germany", "Europe"],
    "remote": true
  },
  "platforms": {
    "linkedin": true,
    "stepstone": true
  },
  "limits": {
    "max_per_run": 100,
    "max_per_day": 200
  }
}
```

## Project Structure

```
job-apply-bot/
├── main.py                  # Entry point
├── config.json              # Your config (gitignored)
├── config.example.json      # Safe template
├── requirements.txt
├── bots/
│   ├── base.py              # Shared logic (session, logging, retry, limits)
│   ├── linkedin.py          # LinkedIn Easy Apply + external ATS
│   ├── stepstone.py         # StepStone quick apply
│   └── ats/
│       ├── greenhouse.py    # Greenhouse ATS handler
│       ├── lever.py         # Lever ATS handler
│       └── personio.py      # Personio ATS handler
├── sessions/                # Saved login cookies (gitignored)
└── logs/
    └── applications.csv     # Application log (gitignored)
```

## Logs

All applications are logged to `logs/applications.csv`:

| timestamp | platform | job_id | title | company | url | status | note |
|-----------|----------|--------|-------|---------|-----|--------|------|

Statuses: `applied`, `skipped`, `failed`, `duplicate`, `error`

## Notes

- `config.json`, `sessions/`, and `logs/` are gitignored — never committed
- LinkedIn session expires periodically — re-run with `--visible` to refresh it
- StepStone requires a separate manual login session
