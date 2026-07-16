"""Fetch open jobs from public ATS APIs (Greenhouse, Ashby, Lever,
SmartRecruiters, Workable, Personio, Workday).

No scraping. No Indeed. No LinkedIn. Read-only GETs/POSTs against documented
(or stable first-party) ATS JSON/XML APIs only — the same endpoints each
provider's own embedded job-search widget calls client-side.
"""

import html
import json
import re
import sys
import time
import random
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "career-bot/1.0"

GREENHOUSE_RE = re.compile(r"job-boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)")
ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([^/?#]+)")
SMARTRECRUITERS_RE = re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([^/?#]+)")
WORKABLE_RE = re.compile(r"apply\.workable\.com/([^/?#]+)")
PERSONIO_RE = re.compile(r"([a-z0-9\-]+\.jobs\.personio\.(?:com|de))")
WORKDAY_RE = re.compile(
    r"([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-zA-Z]{2}-[A-Z]{2}/)?([^/?#]+)"
)
RECRUITEE_RE = re.compile(r"([a-z0-9\-]+\.recruitee\.com)")
BREEZY_RE = re.compile(r"([a-z0-9\-]+\.breezy\.hr)")

# Evaluated and excluded — both require an employer-issued key/token that
# isn't derivable from careers_url alone, breaking the auto-detect model this
# file relies on. Don't re-add expecting the same no-auth pattern as above:
#   Teamtailor:            needs a company-issued API key (Public/Internal/Admin scoped)
#   Comeet / Spark Hire Recruit: needs a company UID plus a company-issued token

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html):
    """Strip tags and unescape entities — good enough for keyword matching."""
    return html.unescape(_HTML_TAG_RE.sub(" ", raw_html or "")).strip()


def _get_json(url, timeout):
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_text(url, timeout):
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _post_json(url, payload, timeout):
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS URL: {url}")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _scan_greenhouse(slug, company):
    data = _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", timeout=30
    )
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(
            {
                "title": j.get("title", ""),
                "url": j.get("absolute_url", ""),
                "company": company,
                "location": (j.get("location") or {}).get("name", ""),
                "description": _html_to_text(j.get("content", "")),
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
                        "description": j.get("descriptionPlain", "") or "",
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


def _scan_smartrecruiters(slug, company):
    # ponytail: list endpoint has no description field; a real one needs a
    # per-posting GET. Skipped — heuristic scores fine on title+location alone.
    data = _get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", timeout=30
    )
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        location = ", ".join(p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p)
        if loc.get("remote"):
            location = f"{location} (Remote)" if location else "Remote"
        jobs.append(
            {
                "title": j.get("name", ""),
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}",
                "company": company,
                "location": location,
                "description": "",
            }
        )
    return jobs


def _scan_workable(slug, company):
    # ponytail: same as SmartRecruiters — list endpoint omits description.
    data = _get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", timeout=30)
    jobs = []
    for j in data.get("jobs", []):
        location = ", ".join(p for p in (j.get("city"), j.get("state"), j.get("country")) if p)
        if j.get("telecommuting"):
            location = f"{location} (Remote)" if location else "Remote"
        jobs.append(
            {
                "title": j.get("title", ""),
                "url": j.get("url", ""),
                "company": company,
                "location": location,
                "description": "",
            }
        )
    return jobs


def _scan_personio(host, company):
    text = _get_text(f"https://{host}/xml", timeout=30)
    root = ET.fromstring(text)
    jobs = []
    for pos in root.findall("position"):
        offices = [pos.findtext("office", "")] + [
            o.text for o in pos.findall("additionalOffices/office") if o.text
        ]
        description = _html_to_text(
            " ".join(jd.findtext("value", "") for jd in pos.findall("jobDescriptions/jobDescription"))
        )
        jobs.append(
            {
                "title": pos.findtext("name", ""),
                "url": f"https://{host}/job/{pos.findtext('id', '')}",
                "company": company,
                "location": ", ".join(o for o in offices if o),
                "description": description,
            }
        )
    return jobs


def _scan_workday(tenant, wdnum, site, company):
    # ponytail: capped at 5 pages (100 postings) per tenant — Workday's
    # searchText param doesn't reliably filter server-side and facet IDs
    # vary by tenant, so there's no cheap way to narrow further. Raise
    # max_pages if a real UK/placement listing turns out to be buried past it.
    url = f"https://{tenant}.{wdnum}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    page_size = 20
    max_pages = 5
    jobs = []
    offset = 0
    for _ in range(max_pages):
        data = _post_json(
            url, {"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": ""}, timeout=30
        )
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            jobs.append(
                {
                    "title": j.get("title", ""),
                    "url": f"https://{tenant}.{wdnum}.myworkdayjobs.com/{site}{j.get('externalPath', '')}",
                    "company": company,
                    "location": j.get("locationsText", ""),
                    "description": "",
                }
            )
        offset += page_size
        if offset >= data.get("total", 0):
            break
    return jobs


def _scan_recruitee(host, company):
    data = _get_json(f"https://{host}/api/offers/", timeout=30)
    jobs = []
    for j in data.get("offers", []):
        location = ", ".join(p for p in (j.get("city"), j.get("country")) if p) or j.get("location", "")
        jobs.append(
            {
                "title": j.get("title", ""),
                "url": f"https://{host}/o/{j.get('slug', '')}",
                "company": company,
                "location": location,
                "description": _html_to_text(j.get("description", "")),
            }
        )
    return jobs


def _try_recruitee_custom_domain(careers_url):
    """Some Recruitee customers front their board with a custom domain instead
    of <slug>.recruitee.com. Same API, different host — probe for it."""
    parsed = urllib.parse.urlparse(careers_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    try:
        data = _get_json(f"https://{parsed.netloc}/api/offers/", timeout=15)
    except Exception:  # noqa: BLE001 - just a probe, any failure means "not Recruitee"
        return None
    return parsed.netloc if isinstance(data, dict) and "offers" in data else None


def _scan_breezy(host, company):
    # verbose=true is mandatory — without it postings come back with no description.
    data = _get_json(f"https://{host}/json?verbose=true", timeout=30)
    jobs = []
    for j in data:
        loc = j.get("location") or {}
        location = loc.get("name", "")
        if loc.get("is_remote") and location and "remote" not in location.lower():
            location = f"{location} (Remote)"
        jobs.append(
            {
                "title": j.get("name", ""),
                "url": j.get("url", ""),
                "company": company,
                "location": location,
                "description": _html_to_text(j.get("description", "")),
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

    m = SMARTRECRUITERS_RE.search(careers_url)
    if m:
        try:
            return _scan_smartrecruiters(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: smartrecruiters scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = WORKABLE_RE.search(careers_url)
    if m:
        try:
            return _scan_workable(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: workable scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = PERSONIO_RE.search(careers_url)
    if m:
        try:
            return _scan_personio(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: personio scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = WORKDAY_RE.search(careers_url)
    if m:
        try:
            return _scan_workday(m.group(1), m.group(2), m.group(3), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: workday scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = RECRUITEE_RE.search(careers_url)
    if m:
        try:
            return _scan_recruitee(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: recruitee scan failed for {name}: {e}", file=sys.stderr)
            return []

    m = BREEZY_RE.search(careers_url)
    if m:
        try:
            return _scan_breezy(m.group(1), name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: breezy scan failed for {name}: {e}", file=sys.stderr)
            return []

    # Last resort: some Recruitee customers front their board with a custom
    # domain instead of <slug>.recruitee.com — probe for it before giving up.
    custom_host = _try_recruitee_custom_domain(careers_url)
    if custom_host:
        try:
            return _scan_recruitee(custom_host, name)
        except Exception as e:  # noqa: BLE001
            print(f"warning: recruitee (custom domain) scan failed for {name}: {e}", file=sys.stderr)
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
    """Keep a job iff its title has >=1 role keyword (if configured), >=1
    positive/framing keyword, and 0 negative keywords."""
    filt = cfg.get("title_filter", {})
    role_keywords = [r.lower() for r in filt.get("role_keywords", [])]
    positive = [p.lower() for p in filt.get("positive", [])]
    negative = [n.lower() for n in filt.get("negative", [])]

    kept = []
    for job in jobs:
        title = job.get("title", "").lower()
        if role_keywords and not any(_kw_match(r, title) for r in role_keywords):
            continue
        if not any(_kw_match(p, title) for p in positive):
            continue
        if any(_kw_match(n, title) for n in negative):
            continue
        kept.append(job)
    return kept


def dedupe_jobs(jobs):
    """Collapse cross-source duplicates (e.g. the same Greenhouse posting
    showing up again via Reed/Adzuna aggregation), keyed on normalized
    (title, company). First occurrence wins, so callers should list
    ATS/Gmail sources before aggregator sources."""
    seen_keys = set()
    kept = []
    for job in jobs:
        key = (job.get("title", "").strip().lower(), job.get("company", "").strip().lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        kept.append(job)
    return kept
