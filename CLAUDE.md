# career-bot

What this is: a personal job-search scanner and scorer, not a job board or an auto-apply tool.

## Constraints (do not change without explicit instruction)
- No auto-apply, ever. There is no form-filling or Playwright code in this repo and none may be added.
- No direct scraping of Indeed or LinkedIn. Portal scanning goes only against public ATS JSON APIs (Greenhouse, Ashby, Lever). If Indeed/LinkedIn alerts are ever wanted, the correct path is Gmail API parsing of their alert emails — not scraping.
- Nothing gets submitted anywhere automatically. The bot notifies and scores; the owner decides and acts.
- Discord notification only for jobs at or above the score threshold in `config/profile.yml`.
- `src/tailor.py` and PDF generation are manual-only, never on cron — `src/run.py` and the workflow must never invoke them.
- CV, profile, and personal data stay local. The only outbound endpoints are the three ATS APIs (read-only), api.anthropic.com (scoring/tailoring), and the owner's Discord webhook. No telemetry.
- Generated content must never fabricate experience or metrics, and must never reference any personal relationship.

## Architecture
```
config/profile.yml    owner profile, scoring model + threshold
config/portals.yml    tracked companies (verified ATS slugs) + title keyword pre-filter
cv.md                 canonical CV (owner-maintained)
seen.json             dedup store, committed back by the workflow
src/scan.py           fetch jobs from Greenhouse/Ashby/Lever public APIs; title_filter
src/score.py          Anthropic API scoring (0-100 + reason) against profile + CV
src/seen.py           seen.json load/save/dedup
src/notify.py         Discord webhook (stdout dry-run if webhook unset)
src/run.py            pipeline: scan -> filter -> dedup -> score -> notify -> save seen
src/tailor.py         MANUAL CLI: tailored CV HTML/PDF from a JD file -> output/
templates/cv-template.html  ATS-friendly template used by tailor.py
tests/                offline fixture tests (no network, no API key)
.github/workflows/scan.yml  cron every 6h + workflow_dispatch, commits seen.json
```

## How to run
- `pip install -r requirements.txt` (Python 3.12; deps: anthropic, pyyaml)
- Env vars / GitHub Actions secrets: `ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`
- Full pipeline: `python -m src.run`
- Tests (offline): `python tests/test_pipeline.py`
- Tailored CV (manual): `python -m src.tailor path/to/jd.txt`

## Profile context
Owner: CS placement-year student, University of Kent BSc Computer Science (Hons) with a Year in Industry, Stage 2 starting 28 September 2026. Target: UK-based or UK-remote software placement / year-in-industry / internship roles for 2026-27 — backend, full-stack, DevOps, AI/ML, general SWE (breadth over specialization). Experience weighting: C#/.NET 8, Next.js/React, Go, Python. Keep this in sync with `config/profile.yml`.
