"""Keyword/location job search aggregators (Reed, Adzuna, TargetJobs). Reed and
Adzuna are official, key-based public APIs; TargetJobs has no API, so it comes
in through a paid Apify actor. All search across many employers by keyword,
unlike scan.py's per-company ATS scanners, so results feed through the same
title_filter as everything else and get deduped against ATS-sourced postings
afterward.
"""

import datetime

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "career-bot/1.0"

REED_URL = "https://www.reed.co.uk/api/1.0/search"
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
APIFY_BASE = "https://api.apify.com/v2"
TARGETJOBS_ACTOR = "getascraper~targetjobs-uk-scraper"

REED_PAGE_SIZE = 100
ADZUNA_PAGE_SIZE = 50
# ponytail: cap total pages fetched per run; raise if a real UK/placement
# listing turns out to be buried past it.
#
# Reed publishes a hard 1000 requests/day/key limit. The workflow cron runs
# every 6h (4 runs/day; see .github/workflows/scan.yml), so worst case here
# is 4 * MAX_PAGES = 12 Reed calls/day — under 2% of budget with room to spare.
#
# Adzuna publishes no numeric quota anywhere in its docs, ToS, or API-tracker
# listings (checked 2026-07-16) — so there's no fixed number to code against.
# _get_json_with_retry's 429 backoff is the actual enforcement: back off on
# whatever the server tells us, rather than hardcode a guessed limit.
MAX_PAGES = 3
PAGE_DELAY_SECONDS = 1  # be a good citizen between paginated requests regardless of any documented limit


def _get_json_with_retry(req, timeout=30, max_retries=3):
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(2**attempt * 2)
                continue
            raise


def fetch_reed_jobs(keywords, location="UK"):
    """Reed search API. Basic auth: API key as username, blank password."""
    api_key = os.environ.get("REED_API_KEY")
    if not api_key:
        return []

    auth = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {auth}", "User-Agent": USER_AGENT}

    jobs = []
    skip = 0
    for _ in range(MAX_PAGES):
        params = urllib.parse.urlencode(
            {"keywords": keywords, "locationName": location, "resultsToTake": REED_PAGE_SIZE, "resultsToSkip": skip}
        )
        req = urllib.request.Request(f"{REED_URL}?{params}", headers=headers)
        try:
            data = _get_json_with_retry(req)
        except Exception as e:  # noqa: BLE001 - one aggregator failing must not break the pipeline
            print(f"warning: reed search failed: {e}", file=sys.stderr)
            return jobs
        results = data.get("results", [])
        for r in results:
            jobs.append(
                {
                    "title": r.get("jobTitle", ""),
                    "url": r.get("jobUrl", ""),
                    "company": r.get("employerName", ""),
                    "location": r.get("locationName", ""),
                    "description": r.get("jobDescription", "") or "",
                    "source": "reed",
                }
            )
        skip += REED_PAGE_SIZE
        if skip >= data.get("totalResults", 0):
            break
        time.sleep(PAGE_DELAY_SECONDS)
    return jobs


def fetch_adzuna_jobs(keywords, location="UK"):
    """Adzuna search API. Auth: app_id + app_key as query params."""
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_API_KEY")
    if not app_id or not app_key:
        print("warning: adzuna skipped, ADZUNA_APP_ID and/or ADZUNA_API_KEY not set", file=sys.stderr)
        return []

    jobs = []
    for page in range(1, MAX_PAGES + 1):
        params = urllib.parse.urlencode(
            {
                "app_id": app_id,
                "app_key": app_key,
                "what": keywords,
                "where": location,
                "results_per_page": ADZUNA_PAGE_SIZE,
            }
        )
        url = ADZUNA_URL.format(page=page) + f"?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            data = _get_json_with_retry(req)
        except Exception as e:  # noqa: BLE001 - one aggregator failing must not break the pipeline
            print(f"warning: adzuna search failed: {e}", file=sys.stderr)
            return jobs
        results = data.get("results", [])
        if not results:
            break
        for r in results:
            jobs.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("redirect_url", ""),
                    "company": (r.get("company") or {}).get("display_name", ""),
                    "location": (r.get("location") or {}).get("display_name", ""),
                    "description": r.get("description", "") or "",
                    "source": "adzuna",
                }
            )
        if page < MAX_PAGES:
            time.sleep(PAGE_DELAY_SECONDS)
    return jobs


def _apify_request(path, token, payload=None, timeout=60):
    req = urllib.request.Request(
        f"{APIFY_BASE}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST" if payload is not None else "GET",
    )
    return _get_json_with_retry(req, timeout=timeout)


def _last_targetjobs_run(token):
    """The actor's most recent successful run, or None if it has never run."""
    try:
        return _apify_request(f"/acts/{TARGETJOBS_ACTOR}/runs/last?status=SUCCEEDED", token)["data"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fetch_targetjobs_jobs(cfg):
    """TargetJobs UK via the getascraper/targetjobs-uk-scraper Apify actor.

    The actor bills per job returned (~$2.50/1000, more with scrapeDetails),
    and the cron runs every 6h. So a fresh run is only started when the last
    successful one is older than min_hours_between_runs; in between, that
    run's dataset is re-read, which is free. Apify keeps the state, so there's
    no local file to commit. Repeats are harmless: seen.json drops them.
    """
    token = os.environ.get("APIFY_TOKEN")
    if not token or not cfg.get("enabled", True):
        return []

    try:
        last = _last_targetjobs_run(token)
        min_age = datetime.timedelta(hours=cfg.get("min_hours_between_runs", 24))
        started = (
            datetime.datetime.fromisoformat(last["startedAt"].replace("Z", "+00:00")) if last else None
        )
        if started and datetime.datetime.now(datetime.timezone.utc) - started < min_age:
            items = _apify_request(f"/datasets/{last['defaultDatasetId']}/items?clean=true", token)
        else:
            run_input = {
                "searchTerms": cfg.get("search_terms", ["software"]),
                "opportunityTypes": cfg.get("opportunity_types", []),
                "sectors": cfg.get("sectors", []),
                "scrapeDetails": cfg.get("scrape_details", True),
                "maxItems": cfg.get("max_items", 40),
                "maxPages": cfg.get("max_pages", 3),
            }
            # run-sync caps at 300s server-side; the client timeout sits just past it.
            items = _apify_request(
                f"/acts/{TARGETJOBS_ACTOR}/run-sync-get-dataset-items?timeout=290",
                token,
                payload=run_input,
                timeout=310,
            )
    except Exception as e:  # noqa: BLE001 - one aggregator failing must not break the pipeline
        print(f"warning: targetjobs (apify) failed: {e}", file=sys.stderr)
        return []

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("jobUrl", "") or r.get("applicationUrl", ""),
            "company": r.get("company", ""),
            "location": r.get("location", ""),
            "description": r.get("description", "") or "",
            "posted_date": (r.get("postedAt") or "")[:10],
            "source": "targetjobs",
        }
        for r in items
        if r.get("jobUrl") or r.get("applicationUrl")
    ]
