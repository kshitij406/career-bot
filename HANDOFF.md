# career-bot — handoff

Personal job-search scanner and scorer for one specific candidate. Not a job
board, not an auto-apply tool. It notifies; the owner decides and acts.

## Who this is for

Kshitij, CS placement-year student (University of Kent, BSc Computer Science
(Hons) with a Year in Industry). Stage 2 starts September 2026; the placement
year itself starts around/after July 2027, once Stage 2 concludes. Target:
UK-based (onsite/hybrid/remote, willing to relocate anywhere in the UK)
software placement/internship/year-in-industry role for the 2027-28 cycle —
backend, full-stack, DevOps, AI/ML, general SWE. Stack weighting: C#/.NET 8,
Next.js/React, Go, Python. Full detail in `config/profile.yml` and `cv.md`.

## How it works

```
scan (9 ATS providers + Gmail alerts + Reed/Adzuna) -> dedupe -> title_filter
  -> dedup vs seen.json -> score -> notify (Discord, if >= threshold) -> save seen.json
```

Runs on a GitHub Actions cron every 6h (`.github/workflows/scan.yml`), plus
`workflow_dispatch` for manual runs. `seen.json` is committed back by the
workflow so the dedup cache persists across runs.

## Sources

**Per-company ATS scanners** (`src/scan.py`, provider auto-detected from each
company's `careers_url` in `config/portals.yml`, currently ~63 companies):
Greenhouse, Ashby, Lever, SmartRecruiters, Workable, Personio, Workday,
Recruitee (incl. a custom-domain auto-probe fallback), Breezy HR. All are
public/official JSON or XML APIs — the same endpoints each provider's own
embedded job-search widget calls, not scraping. Evaluated and excluded:
Teamtailor and Comeet/Spark Hire Recruit both need an employer-issued
key/token that isn't derivable from `careers_url`, breaking the auto-detect
model.

**Aggregator search** (`src/aggregators.py`, keywords/location in
`config/aggregators.yml`): Reed and Adzuna, official key-based keyword search
across many employers — different in kind from the per-company scanners.
Both skip gracefully (return `[]`) if their keys aren't set. Reed publishes a
hard 1000 requests/day/key limit (cron volume is ~12/day, comfortably under
it); Adzuna publishes no numeric quota anywhere in its docs/ToS, so the real
enforcement is reactive 429 backoff, not a coded number. Both paginate with a
per-run page cap and a pause between pages.

**Gmail alerts** (`src/gmail_scan.py`): parses LinkedIn/Indeed job-alert
emails the owner already opted into, via the read-only `gmail.readonly`
scope — this is how LinkedIn/Indeed enter the pipeline at all, since direct
scraping of either is a hard no (see Constraints below). Silently returns
`[]` if the Gmail OAuth env vars aren't set.

## Scoring

Deterministic local heuristic is the **default and always runs** — keyword/
location/stack-overlap scoring, no API key, no network call, no rate limit
(`src/score.py::_heuristic_score`). AI scoring via OpenRouter is optional
(`scoring.ai_enabled: true` in `config/profile.yml` + `OPENROUTER_API_KEY`)
and only *refines* the heuristic score on top — if the AI call fails for any
reason (rate-limited, out of credit, provider down), the heuristic result is
kept untouched. This was a deliberate pivot away from an AI-dependent design
earlier in the project's life, after repeatedly hitting free-tier rate limits
across OpenRouter, Gemini, and multiple API keys.

Threshold for a Discord notification: `config/profile.yml -> scoring.threshold`
(currently 60).

## Known limitations / accepted risks

- **Cross-source dedupe gap** (`src/scan.py::dedupe_jobs`): same-source
  duplicates (one ATS company posting the same title in different cities)
  are correctly kept separate, requiring location agreement. Cross-source
  duplicates (e.g. the same job on both Reed and Adzuna) fall back to
  `(title, company)` alone, ignoring location — Reed returns bare postcodes,
  Adzuna returns city names, and there's no cheap way to reconcile a postcode
  against a place name without a lookup table. This means a genuinely
  different req in a different city, picked up by two different sources,
  **will** incorrectly collapse to one. Only one cross-source overlap case
  has been observed live so far (AJ Bell, correctly a true duplicate) — this
  hasn't been proven safe in general. Every time it fires, an `info` log line
  records both entries' title/company/source/raw location for later audit.
- **Workday/Reed/Adzuna pagination caps**: each fetch is capped at a fixed
  number of pages per run (see `MAX_PAGES` in `src/scan.py` and
  `src/aggregators.py`) since none of these expose reliable server-side
  keyword filtering. A relevant listing buried past that cap won't surface.
- **SmartRecruiters/Workable/Workday/Breezy list endpoints omit full
  descriptions** for some providers (would need an extra per-posting request
  per job — not done, to avoid N+1 request costs at scan time). The heuristic
  scores fine on title+location alone when description is missing.
- Gmail alert parsing returned 0 results in the last live test run — may just
  mean no matching alerts existed in the 2-day lookback window, not
  necessarily a broken token; worth a manual check if it stays at 0.

## Constraints (see `CLAUDE.md` for the authoritative list)

- No auto-apply, ever — no form-filling, no Playwright, anywhere in this repo.
- No direct scraping of Indeed or LinkedIn, ever — no Apify, no headless
  browser, no unofficial API. They only enter via Gmail alert parsing.
- Nothing is ever submitted anywhere automatically.
- `src/tailor.py` (manual tailored-CV generation) and `src/gmail_auth_setup.py`
  (one-time OAuth consent) are never invoked by `src/run.py` or the cron
  workflow.
- Generated content must never fabricate experience/metrics or reference any
  personal relationship.

## Running it

```
pip install -r requirements.txt          # Python 3.12; only new dep is pyyaml
python -m src.run                        # full pipeline
python tests/test_pipeline.py            # offline test suite, no network/keys needed
python tests/test_live_aggregators.py    # manual: hits real Reed/Adzuna APIs
python tests/test_live_providers.py      # manual: hits real Recruitee/Breezy companies
python -m src.tailor path/to/jd.txt      # manual: tailored CV HTML/PDF
python -m src.gmail_auth_setup           # manual, one-time: Gmail OAuth refresh token
```

Env vars / GitHub Actions secrets (all already configured on
`kshitij406/career-bot`): `OPENROUTER_API_KEY`, `DISCORD_WEBHOOK_URL`,
`REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_API_KEY`, `GMAIL_CLIENT_ID`,
`GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`.

## Repo

`kshitij406/career-bot` (private). `main` currently at `3dfebdd`.
