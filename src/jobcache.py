"""Local cache of full job records.

seen.json is the committed dedup store and deliberately tiny — url, title,
company, score. That was fine while Discord was the only surface, but it
means two things are impossible:

  - Rescoring. Scores are frozen at first sight, because seen.json keeps no
    location or description to re-run the heuristic against. Every scoring
    fix therefore only ever applies to jobs found *after* it, and the existing
    backlog keeps whatever the old rules said.
  - Filling the job description into the tailor pane. The text was thrown away
    right after scoring.

So the full record is cached here instead of being pushed into seen.json.
jobs_cache.json is gitignored: it holds every description the scanner has
fetched, which would add megabytes to a file the workflow commits on every
run, and none of it is worth version-controlling.
"""

import json
import os

CACHE_PATH = "jobs_cache.json"

# Descriptions can be tens of KB. Keep enough for tailoring and scoring, drop
# the rest — no JD needs 20k characters of benefits boilerplate to tailor a CV.
MAX_DESCRIPTION = 12000


def load_cache(path=CACHE_PATH):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        # A corrupt cache is a rebuildable inconvenience, never a failed run.
        return {}


def save_cache(cache, path=CACHE_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def update_cache(jobs, cache):
    """Record each job by URL. Later scans overwrite earlier ones."""
    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        entry = {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "description": (job.get("description") or "")[:MAX_DESCRIPTION],
            "posted_date": job.get("posted_date", ""),
            "source": job.get("source", "ats"),
        }
        existing = cache.get(url)
        # Don't let a later scan blank a description we already have: the
        # SmartRecruiters/Workday list endpoints return empty descriptions,
        # and enrichment only runs for jobs about to be scored.
        if existing and existing.get("description") and not entry["description"]:
            entry["description"] = existing["description"]
        cache[url] = entry
    return cache
