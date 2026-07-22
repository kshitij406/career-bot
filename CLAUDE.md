# career-bot

What this is: a personal job-search scanner and scorer, not a job board or an auto-apply tool.

## Constraints (do not change without explicit instruction)
- No auto-apply, ever. There is no form-filling or Playwright code in this repo and none may be added.
- No direct scraping of Indeed or LinkedIn, ever — no Apify, no headless browser, no unofficial API. Portal scanning goes against public ATS JSON/XML APIs (Greenhouse, Ashby, Lever, SmartRecruiters, Workable, Personio, Workday, Recruitee, Breezy HR) — the same endpoints each provider's own embedded job-search widget calls. Indeed/LinkedIn listings enter the pipeline only via `src/gmail_scan.py`, which parses job-alert emails the owner already opted into, through the read-only `gmail.readonly` Gmail API scope.
- Reed and Adzuna are a separate category from the per-company ATS scanners: official, key-based keyword-search APIs (not scraping) that search across many employers. They live in `src/aggregators.py`, feed through the same `title_filter` as everything else, and get deduped against ATS-sourced postings before scoring.
- Nothing gets submitted anywhere automatically. The bot notifies and scores; the owner decides and acts.
- Discord notification only for jobs at or above the score threshold in `config/profile.yml`.
- `src/tailor.py` and PDF generation are manual-only, never on cron — `src/run.py` and the workflow must never invoke them. Same rule for `src/gmail_auth_setup.py` (one-time OAuth consent).
- CV, profile, and personal data stay local. The only outbound endpoints are the ATS APIs (read-only), the Reed and Adzuna search APIs (read-only, key-based), the Gmail API (read-only, gmail.readonly scope), openrouter.ai (scoring/tailoring, routed to whatever model `config/profile.yml` picks), and the owner's Discord webhook. No telemetry.
- Generated content must never fabricate experience or metrics, and must never reference any personal relationship.

## Architecture
```
config/profile.yml    owner profile, scoring model (OpenRouter slug) + threshold
config/portals.yml    tracked companies (verified ATS slugs) + title keyword pre-filter
config/gmail.yml      LinkedIn/Indeed alert-sender addresses + lookback window
config/aggregators.yml  Reed/Adzuna search keywords + location default
cv.md                 canonical CV (owner-maintained)
seen.json             dedup store, committed back by the workflow
src/scan.py           fetch jobs from Greenhouse/Ashby/Lever/SmartRecruiters/Workable/Personio/Workday/Recruitee/Breezy public APIs; title_filter; dedupe_jobs
src/aggregators.py     Reed/Adzuna keyword search across employers (official APIs, not scraping)
src/gmail_scan.py     parse LinkedIn/Indeed job-alert emails via read-only Gmail API
src/gmail_auth_setup.py  MANUAL, one-time: get a Gmail OAuth refresh token
src/score.py          OpenRouter chat-completions scoring (0-100 + reason) against profile + CV
src/seen.py           seen.json load/save/dedup
src/notify.py         Discord webhook (stdout dry-run if webhook unset)
src/run.py            pipeline: scan -> filter -> dedup -> score -> notify -> save seen
src/tailor.py         MANUAL CLI: tailored CV HTML/PDF from a JD file -> output/
templates/cv-template.html  ATS-friendly template used by tailor.py
tests/                offline fixture tests (no network, no API key)
.github/workflows/scan.yml  cron every 6h + workflow_dispatch, commits seen.json

config/f1_teams.json   2026-grid F1 teams + F1/FOM + FIA: confirmed careers_url + ats type
seen_f1.json           F1 pipeline's own dedup store (separate from seen.json)
src/f1_scan.py         fetch jobs from each F1 org per its ats type in f1_teams.json (reuses
                        src/scan.py's recruitee/workable/workday fetchers + 3 new ones:
                        Pinpoint, Haas's Drupal JSON:API, FIA's RSS feed); f1_title_filter
                        (single include-keyword list + exclude-unless-"software" list, no
                        scoring model); orgs with ats:"manual" (no public API found — Ferrari/
                        SAP SuccessFactors, Red Bull/Oracle HCM, Mercedes/custom, Williams/
                        Attrax, Audi-Sauber/custom, Racing Bulls/Red Bull portal) are skipped
                        with a stderr notice, never scraped
src/f1_notify.py        Discord webhook for F1 matches (reuses DISCORD_WEBHOOK_URL), one embed
                        per job, dead-link (404/410) check before posting
src/f1_run.py           F1 pipeline: scan -> dedupe_jobs -> f1_title_filter -> dedup vs
                        seen_f1.json -> notify -> save seen_f1.json
.github/workflows/f1_scan.yml  cron every 12h + workflow_dispatch, commits seen_f1.json
```

## How to run
- `pip install -r requirements.txt` (Python 3.12; deps: pyyaml — everything else is stdlib `urllib`)
- Env vars / GitHub Actions secrets: `OPENROUTER_API_KEY`, `DISCORD_WEBHOOK_URL`, `REED_API_KEY`, `ADZUNA_APP_ID` + `ADZUNA_API_KEY` (aggregators skip gracefully if unset), and (if `config/gmail.yml` is enabled) `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`
- Full pipeline: `python -m src.run`
- F1 org pipeline (separate scanner, own dedup store, no scoring model): `python -m src.f1_run`
- Tests (offline): `python tests/test_pipeline.py`
- Tailored CV (manual): `python -m src.tailor path/to/jd.txt`
- Gmail OAuth setup (manual, one-time): `python -m src.gmail_auth_setup`

## Profile context
Owner: CS placement-year student, University of Kent BSc Computer Science (Hons) with a Year in Industry, Stage 2 starting September 2026. The placement year itself starts around/after July 2027, once Stage 2 concludes. Target: UK-based or UK-remote software placement / year-in-industry / internship roles for the 2027-28 cycle — backend, full-stack, DevOps, AI/ML, general SWE (breadth over specialization). Experience weighting: C#/.NET 8, Next.js/React, Go, Python. Keep this in sync with `config/profile.yml`.
