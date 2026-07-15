# career-bot

A UK placement-year job scanner that monitors ATS portals and scores roles against your CV via an OpenRouter-hosted model.

## How it works

**Automated pipeline** (GitHub Actions cron every 6 hours):
- Scans public ATS APIs (Greenhouse, Ashby, Lever) for companies in `config/portals.yml`
- Parses LinkedIn/Indeed job-alert emails via the read-only Gmail API (`config/gmail.yml`) — not scraping, just reading alerts you already opted into
- Keyword-filters job titles and deduplicates against `seen.json` (committed back to the repo)
- Scores new jobs using your CV (`cv.md`) + profile (`config/profile.yml`) via OpenRouter
- Sends Discord webhook notifications for jobs scoring above your configured threshold

**Manual tools**:
```bash
python -m src.tailor <jd.txt>          # tailored ATS-friendly HTML/PDF CV to output/
python -m src.gmail_auth_setup         # one-time Gmail OAuth consent, prints a refresh token
```

## Setup

**Requirements:** Python 3.12  
**Install:** `pip install -r requirements.txt` (pyyaml — everything else is stdlib)

**Environment variables / GitHub Secrets:**
- `OPENROUTER_API_KEY` — from [openrouter.ai](https://openrouter.ai)
- `DISCORD_WEBHOOK_URL`
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` — only if `config/gmail.yml` has `enabled: true` (see below); otherwise Gmail scanning is skipped with a warning

**Before first run:**
- Replace the placeholder `cv.md` with your actual CV
- Configure `config/profile.yml` (keywords, score threshold, OpenRouter model slug — check current ids at [openrouter.ai/models](https://openrouter.ai/models))
- Add company ATS portals to `config/portals.yml`

**Gmail alerts setup (optional, for LinkedIn/Indeed coverage):**
1. Set up job alerts normally on linkedin.com and indeed.com (emailed to your Gmail).
2. In [Google Cloud Console](https://console.cloud.google.com): create a project, enable the Gmail API, create an OAuth client of type "Desktop app". Note the client ID/secret.
3. Run once locally: `GMAIL_CLIENT_ID=... GMAIL_CLIENT_SECRET=... python -m src.gmail_auth_setup` — opens a browser for consent, prints a refresh token.
4. Save all three (`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`) as GitHub Actions secrets.

**Testing:** `python tests/test_pipeline.py`

## Hard rules

- **No auto-apply.** The bot only notifies and scores — nothing is submitted anywhere.
- **No scraping.** Only uses public ATS APIs plus Gmail alert-email parsing (no Indeed/LinkedIn scraping, no Apify, no headless browser, ever).
- **Data privacy.** CV and personal data sent only to OpenRouter for scoring/tailoring.

## Credits

Adapted from [santifer/career-ops](https://github.com/santifer/career-ops) (ATS scanning approach + CV template idea); everything else custom.
