"""Fetch open jobs from public ATS APIs (Greenhouse, Ashby, Lever,
SmartRecruiters, Workable, Personio, Workday).

No scraping. No Indeed. No LinkedIn. Read-only GETs/POSTs against documented
(or stable first-party) ATS JSON/XML APIs only — the same endpoints each
provider's own embedded job-search widget calls client-side.
"""

import concurrent.futures
import datetime
import html
import json
import re
import sys
import time
import random
import unicodedata
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

# Matches an *escaped* opening tag — &lt;p&gt;, &lt;/div&gt;, &lt;h4 class=…
# The (?:amp;)* handles doubly-escaped payloads (&amp;lt;p&amp;gt;), where the
# literal &lt; only appears after the first unescape pass. Requiring a letter
# or slash after the < keeps prose like "latency &lt; 200ms" from being
# mistaken for markup.
_ESCAPED_TAG_RE = re.compile(r"&(?:amp;)*lt;/?[a-zA-Z]")


def _html_to_text(raw_html):
    """Strip tags and unescape entities — good enough for keyword matching.

    Unescape *before* stripping when the payload looks like escaped markup.
    Greenhouse returns `content` with the tags themselves escaped
    (&lt;div&gt;…), and stripping first is a silent trap: the tag regex finds
    no real tags to remove, then unescape turns the entities into literal
    <div> text that survives into the description. That noise then feeds
    stack-overlap scoring and the AI scoring prompt.

    Looped because a doubly-escaped source needs more than one pass; bounded
    so a pathological input can't spin.
    """
    text = raw_html or ""
    for _ in range(3):
        if not _ESCAPED_TAG_RE.search(text):
            break
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    return html.unescape(_HTML_TAG_RE.sub(" ", text)).strip()


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
                "posted_date": (j.get("updated_at") or "")[:10],
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
                        "posted_date": (j.get("publishedAt") or "")[:10],
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
        created_ms = j.get("createdAt")
        posted_date = (
            datetime.datetime.fromtimestamp(created_ms / 1000, tz=datetime.timezone.utc).date().isoformat()
            if created_ms
            else ""
        )
        jobs.append(
            {
                "title": j.get("text", ""),
                "url": j.get("hostedUrl", ""),
                "company": company,
                "location": (j.get("categories") or {}).get("location", ""),
                "description": j.get("descriptionPlain", "") or "",
                "posted_date": posted_date,
            }
        )
    return jobs


def _scan_smartrecruiters(slug, company):
    # The list endpoint has no description; the real one needs a per-posting
    # GET. Rather than pay that N+1 at scan time for every posting, each job
    # carries the identifiers needed to fetch it later — enrich_descriptions()
    # does that for the handful that survive filtering.
    #
    # Paginated, capped at 5 pages (500 postings). A single limit=100 request
    # silently truncated the large boards — Wise alone publishes ~395, so the
    # scanner was seeing a quarter of it and never knew.
    page_size = 100
    max_pages = 5
    jobs = []
    offset = 0
    for _ in range(max_pages):
        data = _get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            f"?limit={page_size}&offset={offset}",
            timeout=30,
        )
        content = data.get("content", [])
        if not content:
            break
        for j in content:
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
                    "posted_date": (j.get("releasedDate") or "")[:10],
                    "_detail": ("smartrecruiters", slug, j.get("id", "")),
                }
            )
        offset += page_size
        if offset >= data.get("totalFound", 0):
            break
    return jobs


def _scan_workable(slug, company):
    # details=true returns full descriptions for every posting in the same
    # request — no per-posting fetch needed here, unlike SmartRecruiters and
    # Workday. Verified against a live board: 29/29 postings came back with a
    # description, for one request instead of 30.
    data = _get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true", timeout=45
    )
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
                "description": _html_to_text(j.get("description", "")),
                "posted_date": j.get("published_on", "") or "",
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
                # Personio's XML schema doesn't consistently expose a creation
                # date across tenants — best-effort, empty if absent.
                "posted_date": pos.findtext("createdAt", ""),
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
                    # Workday exposes this as relative human text ("Posted
                    # Today", "Posted 30+ Days Ago"), not an ISO date — still
                    # useful signal, so pass it through as-is.
                    "posted_date": j.get("postedOn", ""),
                    "_detail": (
                        "workday",
                        f"https://{tenant}.{wdnum}.myworkdayjobs.com/wday/cxs/{tenant}/{site}",
                        j.get("externalPath", ""),
                    ),
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
                "posted_date": (j.get("published_at") or j.get("created_at") or "")[:10],
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
                "posted_date": (j.get("published_date") or "")[:10],
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


def scan_all(portals_cfg, max_workers=8):
    """Scan every company in portals.yml. A failed company is skipped, never crashes.

    Concurrent because this is pure network wait — 119 companies served
    serially is 119 round trips end to end. Workers are capped low: these are
    other people's public APIs and there is no deadline here worth hammering
    them for. Results keep portals.yml order so runs stay comparable.
    """
    companies = portals_cfg.get("companies", [])
    if not companies:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        # executor.map preserves input order and re-raises nothing, since
        # scan_company already swallows per-company failures.
        return [job for jobs in pool.map(scan_company, companies) for job in jobs]


def _fetch_description(detail):
    """Fetch one posting's description. Returns "" on any failure."""
    provider = detail[0]
    try:
        if provider == "smartrecruiters":
            _, slug, posting_id = detail
            if not posting_id:
                return ""
            data = _get_json(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}",
                timeout=30,
            )
            sections = (data.get("jobAd") or {}).get("sections") or {}
            # Concatenated because requirements often carry the stack keywords
            # while jobDescription is mostly company boilerplate.
            parts = [
                (sections.get(key) or {}).get("text", "")
                for key in ("jobDescription", "qualifications", "additionalInformation")
            ]
            return _html_to_text(" ".join(p for p in parts if p))
        if provider == "workday":
            _, base, external_path = detail
            if not external_path:
                return ""
            data = _get_json(f"{base}{external_path}", timeout=30)
            info = data.get("jobPostingInfo") or {}
            return _html_to_text(info.get("jobDescription") or "")
    except Exception:  # noqa: BLE001 - a missing description is not a failure
        return ""
    return ""


def enrich_descriptions(jobs, max_workers=6):
    """Fill in descriptions for providers whose list endpoint omits them.

    Called late — on the jobs that already survived filtering and dedup —
    because this is the one N+1 in the pipeline. Doing it at scan time would
    mean a request per posting across every board; doing it here means a
    request per posting actually worth scoring, which is a handful per run.

    Greenhouse, Ashby, Lever, Workable, Recruitee, Breezy and Personio all
    return descriptions in their list response and never reach this.

    A failed fetch leaves the description empty rather than raising: the
    heuristic scores acceptably on title and location alone, so a flaky detail
    endpoint must not cost a notification.
    """
    pending = [j for j in jobs if j.get("_detail") and not j.get("description")]
    if not pending:
        return jobs
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for job, description in zip(pending, pool.map(_fetch_description, (j["_detail"] for j in pending))):
            if description:
                job["description"] = description
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


_PARENTHETICAL_RE = re.compile(r"\(.*?\)")
_REMOTE_ONLY_RE = re.compile(r"^(fully\s+)?remote$")
_UK_COUNTRY_SUFFIX_RE = re.compile(r",\s*(uk|united kingdom|gb|great britain)\s*$")
_LOCATION_ALIASES = {
    "greater manchester": "manchester",
    "greater london": "london",
}


def _normalize_location(location):
    """case-insensitive, trimmed; strips a UK country suffix ("Manchester,
    UK" -> "manchester") and known city aliases ("Greater Manchester" ->
    "manchester"); collapses any bare-remote phrasing ("Remote", "Remote
    (UK)", "Fully Remote") to one canonical "remote" token, since those
    describe the same non-office reality regardless of exact wording."""
    loc = (location or "").strip().lower()
    if not loc:
        return ""
    loc = _PARENTHETICAL_RE.sub("", loc).strip()
    loc = re.sub(r"\s+", " ", loc)
    if _REMOTE_ONLY_RE.fullmatch(loc):
        return "remote"
    loc = _UK_COUNTRY_SUFFIX_RE.sub("", loc).strip()
    return _LOCATION_ALIASES.get(loc, loc)


# Legal-form suffixes that differ between sources for the same employer:
# Reed says "AJ Bell plc", the ATS board says "AJ Bell". Stripping them is
# safe because no two distinct employers differ only by legal form.
_COMPANY_NOISE_RE = re.compile(
    r"\b(?:inc|llc|ltd|limited|corp|corporation|gmbh|bv|nv|sa|ag|plc|co|company|group|holdings)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _fold(value):
    """Lowercase, strip accents, collapse to alphanumerics — so "Société
    Générale" and "societe generale" compare equal."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub("", stripped.lower())


def _company_key(company):
    return _fold(_COMPANY_NOISE_RE.sub("", company or ""))


def _title_key(title):
    # Parenthetical qualifiers vary by source for what is unambiguously the
    # same posting — "(Remote)", "(12 Month Placement)", "(f/m/d)".
    return _fold(_PARENTHETICAL_RE.sub("", title or ""))


# Lower wins when the same job turns up from two sources. The employer's own
# ATS board is canonical: its URL is the real application form rather than an
# aggregator redirect, and its description is usually the fullest.
SOURCE_PRIORITY = {"ats": 10, "gmail": 20, "reed": 30, "adzuna": 30}
DEFAULT_SOURCE_PRIORITY = 50


def _source_rank(source):
    return SOURCE_PRIORITY.get(source, DEFAULT_SOURCE_PRIORITY)


def dedupe_jobs(jobs):
    """Collapse duplicate postings, keyed on normalized (title, company) plus
    location — but location is only compared *within* the same source.

    Audited on a live run (2026-07-16): the measured false positives were all
    a single ATS company posting the same title in genuinely different
    offices (Monzo "Engineering Manager" in Barcelona vs Cardiff/London/
    Remote; GoCardless "Data Science Manager" in Riga/London/Lisbon) — same
    source, different real postings, wrongly collapsed by a location-blind
    key. Requiring a location match within a source fixes that.

    The one confirmed-working cross-source collapse from that same run (AJ
    Bell "Software Engineer", posted independently to Reed and Adzuna) has
    incompatible location text between sources — Reed returned the bare
    postcode "M53EE", Adzuna returned "Manchester, Greater Manchester" — with
    no cheap, maintainable way to reconcile a postcode against a city name
    (would need a postcode-to-place lookup table). So cross-source matches
    fall back to (title, company) alone, same as before this fix: two
    different sources reporting the same title at the same company are
    treated as the same real job regardless of how each formats location.

    Cross-source collapses that couldn't check location agreement are logged
    (not blocked) so it's possible to audit them periodically as more
    companies turn up on multiple sources — see the comment on that branch.

    Title and company are matched on normalized keys (_title_key/_company_key),
    so "AJ Bell plc" matches "AJ Bell" and "Software Engineer (Remote)" matches
    "Software Engineer". This catches true duplicates that formatting
    differences used to hide — but note it also makes the cross-source branch
    below fire more often, since looser matching means more collapses. The
    log line there is the audit trail for exactly that.

    When the same job appears from two sources, the one from the
    highest-priority source wins (SOURCE_PRIORITY: the employer's own ATS
    board over an aggregator), regardless of the order callers pass them in.
    """
    # base_key -> {source: {normalized_location: (raw_location, index in kept)}}
    seen_sources_by_key = {}
    kept = []
    for job in jobs:
        title = _title_key(job.get("title", ""))
        company = _company_key(job.get("company", ""))
        source = job.get("source", "ats")
        raw_location = job.get("location", "")
        location = _normalize_location(raw_location)
        base_key = (title, company)

        locations_by_source = seen_sources_by_key.setdefault(base_key, {})

        if source in locations_by_source:
            if location in locations_by_source[source]:
                # same source, same (normalized) location -> duplicate
                continue
        elif locations_by_source:
            # ACCEPTED RISK, not a solved case: we cannot check location
            # agreement here. Reed returns bare postcodes ("M53EE"), Adzuna
            # returns city names ("Manchester, Greater Manchester") — the
            # same real office, but with no cheap/maintainable way to
            # reconcile a postcode against a place name (would need a
            # postcode-to-place lookup table). So a genuinely different req
            # in a different city, picked up by two different sources,
            # *will* incorrectly collapse here, exactly the failure mode
            # just fixed for same-source — this branch has no structural
            # fix, only the log line below for visibility.
            other_source, other_locations = next(iter(locations_by_source.items()))
            other_raw_location, other_index = next(iter(other_locations.values()))
            print(
                "info: dedupe_jobs collapsed cross-source with no location agreement: "
                f"{job.get('title', '')!r} @ {job.get('company', '')!r} "
                f"[{source} loc={raw_location!r}] vs [{other_source} loc={other_raw_location!r}]",
                file=sys.stderr,
            )
            # A different source already reported this title+company, so it's
            # treated as the same real job — but keep whichever copy comes
            # from the more authoritative source, not whichever arrived first.
            if _source_rank(source) < _source_rank(other_source):
                kept[other_index] = job
                del locations_by_source[other_source]
                locations_by_source[source] = {location: (raw_location, other_index)}
            continue

        locations_by_source.setdefault(source, {})[location] = (raw_location, len(kept))
        kept.append(job)
    return kept
