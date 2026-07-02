"""Fetch open jobs from public ATS APIs (Greenhouse, Ashby, Lever).

No scraping. No Indeed. No LinkedIn. Read-only GETs against the three
documented ATS JSON APIs only.
"""

import json
import re
import sys
import time
import random
import urllib.error
import urllib.request

USER_AGENT = "career-bot/1.0"

GREENHOUSE_RE = re.compile(r"job-boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)")
ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([^/?#]+)")


def _get_json(url, timeout):
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _scan_greenhouse(slug, company):
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=30
    )
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(
            {
                "title": j.get("title", ""),
                "url": j.get("absolute_url", ""),
                "company": company,
                "location": (j.get("location") or {}).get("name", ""),
                "description": "",
            }
        )
    return jobs


def _scan_ashby(slug, company):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    last_err = None
    for attempt in range(2):
        try:
            data = _get_json(url, timeout=45)
            jobs = []
            for j in data.get("jobs", []):
                jobs.append(
                    {
                        "title": j.get("title", ""),
                        "url": j.get("jobUrl", ""),
                        "company": company,
                        "location": j.get("location", ""),
                        "description": "",
                    }
                )
            return jobs
        except Exception as e:  # noqa: BLE001 - retry once then give up
            last_err = e
            if attempt == 0:
                time.sleep(2 + random.uniform(0, 2))
    raise last_err


def _scan_lever(slug, company):
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}", timeout=30)
    jobs = []
    for j in data:
        jobs.append(
            {
                "title": j.get("text", ""),
                "url": j.get("hostedUrl", ""),
                "company": company,
                "location": (j.get("categories") or {}).get("location", ""),
                "description": j.get("descriptionPlain", "") or "",
            }
        )
    return jobs


def scan_company(company):
    """Detect provider from careers_url and fetch its jobs. Returns [] on failure."""
    careers_url = company.get("careers_url", "")
    name = company.get("name", careers_url)

    m = GREENHOUSE_RE.search(careers_url)
    if m:
        try:
            return _scan_greenhouse(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: greenhouse scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = ASHBY_RE.search(careers_url)
    if m:
        try:
            return _scan_ashby(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: ashby scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = LEVER_RE.search(careers_url)
    if m:
        try:
            return _scan_lever(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: lever scan failed for {name}: {e}", file=sys.stderr)
            return []

    print(f"warning: unrecognized provider for {name}: {careers_url}", file=sys.stderr)
    return []


def scan_all(portals_cfg):
    """Scan every company in portals.yml. A failed company is skipped, never crashes."""
    jobs = []
    for company in portals_cfg.get("companies", []):
        jobs.extend(scan_company(company))
    return jobs


def _kw_match(keyword, title):
    # Word-boundary match so "intern" doesn't hit "International" or "Internal".
    return re.search(rf"\b{re.escape(keyword)}\b", title) is not None


def title_filter(jobs, cfg):
    """Keep a job iff its title has >=1 positive keyword and 0 negative keywords."""
    filt = cfg.get("title_filter", {})
    positive = [p.lower() for p in filt.get("positive", [])]
    negative = [n.lower() for n in filt.get("negative", [])]

    kept = []
    for job in jobs:
        title = job.get("title", "").lower()
        if not any(_kw_match(p, title) for p in positive):
            continue
        if any(_kw_match(n, title) for n in negative):
            continue
        kept.append(job)
    return kept
