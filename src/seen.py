"""Track previously-seen jobs to avoid duplicate notifications."""

import datetime
import json
import os


def load_seen(path="seen.json"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(seen, path="seen.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)


def is_new(job, seen):
    return job["url"] not in seen


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
