"""Fetch open jobs at Formula 1 teams/governing bodies and filter for
software/tech-adjacent roles matching the candidate's profile.

Same no-scraping rule as src/scan.py: public JSON/XML ATS APIs only, each
org's ats type/endpoint pre-verified in config/f1_teams.json (no URL-pattern
guessing here — the teams file already records what each org was confirmed
to run). Orgs with no public API (ats: "manual") are skipped with a notice
rather than scraped.
"""

import re
import sys
import xml.etree.ElementTree as ET

from src.scan import _get_json, _get_text, _html_to_text, _scan_recruitee, _scan_workable, _scan_workday

# Single include-list (not the two-tier role+positive split career-bot uses
# for general roles) — matches the exact list requested for F1 org scanning.
INCLUDE_KEYWORDS = [
    "software", "it", "developer", "engineer", "data", "platform",
    "c#", ".net", "placement", "student", "intern", "graduate",
]
# Excluded unless "software" also appears in the same title.
EXCLUDE_KEYWORDS = [
    "mechanic", "composite", "aerodynamicist", "trackside",
    "hospitality", "marketing", "finance",
]


def _kw_match(keyword, text):
    # Plain alnum keywords need word boundaries ("it" must not hit
    # "composite"); keywords with punctuation (c#, .net) are already
    # distinctive enough that a bare substring check is safe and avoids the
    # regex \b-around-punctuation trap ("c#\b" doesn't match "C# Developer"
    # since '#' is a non-word char with nothing to bound against).
    if keyword.isalnum():
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword in text


def f1_title_filter(jobs):
    kept = []
    for job in jobs:
        title = job.get("title", "").lower()
        if not any(_kw_match(k, title) for k in INCLUDE_KEYWORDS):
            continue
        excluded = any(_kw_match(k, title) for k in EXCLUDE_KEYWORDS)
        if excluded and not _kw_match("software", title):
            continue
        kept.append(job)
    return kept


def _scan_pinpoint(slug, company):
    data = _get_json(f"https://{slug}.pinpointhq.com/postings.json", timeout=30)
    jobs = []
    for j in data.get("data", []):
        loc = j.get("location") or {}
        jobs.append(
            {
                "title": j.get("title", ""),
                "url": j.get("url", ""),
                "company": company,
                "location": loc.get("name") or loc.get("city") or "",
                "description": _html_to_text(j.get("description", "")),
                "posted_date": "",
            }
        )
    return jobs


def _scan_haas_drupal(api_url, company):
    data = _get_json(api_url, timeout=30)
    jobs = []
    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        path = attrs.get("path") or {}
        alias = path.get("alias", "")
        url = attrs.get("field_bamboohr_url") or (
            f"https://www.haasf1team.com{alias}" if alias else ""
        )
        body = attrs.get("body") or {}
        jobs.append(
            {
                "title": attrs.get("title", ""),
                "url": url,
                "company": company,
                "location": attrs.get("field_location_city_country", "") or "",
                "description": _html_to_text(body.get("value", "")) if isinstance(body, dict) else "",
                "posted_date": (attrs.get("created") or "")[:10],
            }
        )
    return jobs


def _scan_fia_rss(url, company):
    text = _get_text(url, timeout=30)
    root = ET.fromstring(text)
    jobs = []
    for item in root.iter("item"):
        jobs.append(
            {
                "title": item.findtext("title", "") or "",
                "url": item.findtext("link", "") or "",
                "company": company,
                "location": item.findtext("Location", "") or "",
                "description": "",
                "posted_date": "",
            }
        )
    return jobs


def scan_team(team):
    """Detect provider from team['ats'] and fetch its jobs. Returns [] on failure."""
    name = team.get("name", team.get("careers_url", ""))
    ats = team.get("ats", "")

    if ats == "manual":
        print(f"notice: {name} has no public jobs API — check manually: {team.get('careers_url', '')}", file=sys.stderr)
        return []

    try:
        if ats == "recruitee":
            return _scan_recruitee(f"{team['slug']}.recruitee.com", name)
        if ats == "workable":
            return _scan_workable(team["slug"], name)
        if ats == "workday":
            return _scan_workday(team["tenant"], team["wdnum"], team["site"], name)
        if ats == "pinpoint":
            return _scan_pinpoint(team["slug"], name)
        if ats == "haas_drupal":
            return _scan_haas_drupal(team["api_url"], name)
        if ats == "fia_rss":
            return _scan_fia_rss(team["api_url"], name)
        print(f"warning: unrecognized ats {ats!r} for {name}", file=sys.stderr)
        return []
    except Exception as e:  # noqa: BLE001 - one org's site being down must not kill the run
        print(f"warning: {ats} scan failed for {name}: {e}", file=sys.stderr)
        return []


def scan_all_teams(teams_cfg):
    jobs = []
    for team in teams_cfg.get("teams", []):
        jobs.extend(scan_team(team))
    return jobs
