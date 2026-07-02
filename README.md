# career-bot

A UK placement-year job scanner that monitors ATS portals and scores roles against your CV via the Anthropic API.

## How it works

**Automated pipeline** (GitHub Actions cron every 6 hours):
- Scans public ATS APIs (Greenhouse, Ashby, Lever) for companies in `config/portals.yml`
- Keyword-filters job titles and deduplicates against `seen.json` (committed back to the repo)
- Scores new jobs using your CV (`cv.md`) + profile (`config/profile.yml`) via the Anthropic API
- Sends Discord webhook notifications for jobs scoring above your configured threshold

**Manual tool**:
```bash
python -m src.tailor <jd.txt>
```
Generates a tailored ATS-friendly HTML/PDF CV to `output/` for a specific job description.

## Setup

**Requirements:** Python 3.12  
**Install:** `pip install -r requirements.txt` (anthropic, pyyaml)

**Environment variables / GitHub Secrets:**
- `ANTHROPIC_API_KEY`
- `DISCORD_WEBHOOK_URL`

**Before first run:**
- Replace the placeholder `cv.md` with your actual CV
- Configure `config/profile.yml` (keywords, score threshold)
- Add company ATS portals to `config/portals.yml`

**Testing:** `python tests/test_pipeline.py`

## Hard rules

- **No auto-apply.** The bot only notifies and scores — nothing is submitted anywhere.
- **No scraping.** Only uses public ATS APIs (no Indeed, LinkedIn, or hidden portals).
- **Data privacy.** CV and personal data sent only to Anthropic API for scoring/tailoring.

## Credits

Adapted from [santifer/career-ops](https://github.com/santifer/career-ops) (ATS scanning approach + CV template idea); everything else custom.
