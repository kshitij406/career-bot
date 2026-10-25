"""Track previously-seen jobs to avoid duplicate notifications."""

import datetime
import json
import os
import re

from src.scan import _company_key, _title_key

# Adzuna's redirect_url carries a per-request session token (?se=...&v=...),
# so the same posting gets a fresh URL every run. Audited 2026-10-25: 811 of
# 1097 seen.json keys were /land/ad/ URLs, one JPMorgan placement alone under
# 174 of them — every one re-notified. The numeric ad id is the stable part.
_ADZUNA_ID_RE = re.compile(r"adzuna\.[a-z.]+/jobs/(?:land/ad|details)/(\d+)")


def load_seen(path="seen.json"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(seen, path="seen.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


def _url_key(url):
    m = _ADZUNA_ID_RE.search(url or "")
    return f"adzuna:{m.group(1)}" if m else url


def _fingerprint(title, company):
    return (_title_key(title), _company_key(company))


def is_new(job, seen):
    """New iff neither its URL (session params stripped) nor its normalized
    (title, company) has been recorded before.

    The title/company check catches reposts under a fresh ad id — recruiter
    feeds (Hackajob) relist the same role every few days. Location is
    deliberately ignored: once a role has been notified, the same title at the
    same company in another city is not worth a second ping.
    """
    url = _url_key(job["url"])
    fingerprint = _fingerprint(job.get("title", ""), job.get("company", ""))
    for seen_url, entry in seen.items():
        if _url_key(seen_url) == url:
            return False
        if _fingerprint(entry.get("title", ""), entry.get("company", "")) == fingerprint:
            return False
    return True


def record(job, seen):
    """Add/update a job entry in seen, keyed by URL."""
    seen[job["url"]] = {
        "first_seen": seen.get(job["url"], {}).get(
            "first_seen", datetime.date.today().isoformat()
        ),
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "score": job.get("score", 0),
    }
    return seen
