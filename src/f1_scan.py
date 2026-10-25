"""Fetch open jobs at Formula 1 teams/governing bodies and filter for
software/tech-adjacent roles matching the candidate's profile.

Each org's ats type/endpoint is pre-verified in config/f1_teams.json (no
URL-pattern guessing here). Where a team has no JSON API, its careers page is
read directly: Red Bull's Oracle HCM REST endpoint, Williams's server-rendered
Attrax tiles, Mercedes's Next.js page data. Orgs still marked ats: "manual"
are skipped with a notice.
"""

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

from src.scan import _get_json, _get_text, _html_to_text, _scan_recruitee, _scan_workable, _scan_workday

# The F1 pipeline has no scorer, so the filter is the whole decision. It used
# to be a single include-list where "engineer" or "student" alone passed,
# which notified every senior mechanical role and HR placement. Now a title
# needs a placement-framing word AND a tech word, and no seniority word.
PLACEMENT_KEYWORDS = [
    "placement", "student", "intern", "internship", "industrial", "undergraduate",
    "year in industry", "sandwich",
]
TECH_KEYWORDS = [
    "software", "it", "developer", "data", "platform", "ai", "machine learning",
    "digital", "cyber", "computing", "computer", "tools", "simulation", "systems",
    "c#", ".net", "web", "cloud", "devops", "network", "technology", "controls",
]
# Excluded even when the title also has a tech word.
EXCLUDE_KEYWORDS = [
    "senior", "lead", "principal", "head", "manager", "graduate", "mechanic",
    "composite", "hospitality", "marketing", "finance", "hr", "summer",
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
        if not any(_kw_match(k, title) for k in PLACEMENT_KEYWORDS):
            continue
        if not any(_kw_match(k, title) for k in TECH_KEYWORDS):
            continue
        if any(_kw_match(k, title) for k in EXCLUDE_KEYWORDS):
            continue
        kept.append(job)
    return kept


# Plain urllib's default UA gets bot-blocked by these careers sites.
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


def _get_page(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _scan_oracle_hcm(host, site, company):
    """Oracle Recruiting Cloud's public candidate-experience REST endpoint, the
    one its own job-search SPA calls."""
    jobs = []
    offset = 0
    while True:
        url = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList"
            f"&finder=findReqs;siteNumber={site},limit=100,offset={offset}"
        )
        data = json.loads(_get_page(url))["items"][0]
        reqs = data.get("requisitionList", [])
        for r in reqs:
            jobs.append(
                {
                    "title": r.get("Title", ""),
                    "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r.get('Id')}",
                    "company": company,
                    "location": r.get("PrimaryLocation", "") or "",
                    "description": r.get("ShortDescriptionStr", "") or "",
                    "posted_date": (r.get("PostedDate") or "")[:10],
                }
            )
        offset += len(reqs)
        if not reqs or offset >= data.get("TotalJobsCount", 0):
            return jobs


_ATTRAX_TILE_RE = re.compile(
    r'attrax-vacancy-tile__title[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
    r'.*?location-freetext.*?item-value">\s*([^<]*?)\s*</p>',
    re.S,
)
ATTRAX_MAX_PAGES = 20


def _scan_attrax(base_url, company):
    """Attrax boards render vacancy tiles server-side, 12 per page."""
    jobs = []
    for page in range(1, ATTRAX_MAX_PAGES + 1):
        tiles = _ATTRAX_TILE_RE.findall(_get_page(f"{base_url}/jobs?page={page}"))
        if not tiles:
            break
        for href, title, location in tiles:
            jobs.append(
                {
                    "title": html.unescape(title.strip()),
                    "url": f"{base_url}{href}",
                    "company": company,
                    "location": html.unescape(location),
                    "description": "",
                    "posted_date": "",
                }
            )
    return jobs


_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
MERCEDES_MAX_PAGES = 20


def _scan_mercedes(base_url, company):
    """mercedesamgf1.com/careers/vacancies embeds each page of vacancies in its
    Next.js __NEXT_DATA__ blob (9 per page)."""
    jobs = []
    seen_ids = set()
    for page in range(1, MERCEDES_MAX_PAGES + 1):
        text = _get_page(f"{base_url}?page={page}")
        props = json.loads(_NEXT_DATA_RE.search(text).group(1))["props"]["pageProps"]
        fresh = [v["fields"] for v in props.get("vacancies", []) if v["fields"]["id"] not in seen_ids]
        if not fresh:
            break
        for f in fresh:
            seen_ids.add(f["id"])
            jobs.append(
                {
                    "title": f.get("title", ""),
                    "url": f"{base_url}/{f['id']}",
                    "company": company,
                    "location": "Brackley, UK",
                    "description": "",
                    "posted_date": (f.get("creationDate") or "")[:10],
                }
            )
    return jobs


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
        if ats == "oracle_hcm":
            return _scan_oracle_hcm(team["host"], team["site"], name)
        if ats == "attrax":
            return _scan_attrax(team["careers_url"].rstrip("/"), name)
        if ats == "mercedes_next":
            return _scan_mercedes(team["careers_url"].rstrip("/"), name)
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
