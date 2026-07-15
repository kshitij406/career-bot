"""Fetch job listings from LinkedIn/Indeed alert emails via the Gmail API.

Not scraping: this reads job-alert emails the owner already opted into on
linkedin.com/indeed.com, via the read-only gmail.readonly scope. Alert email
HTML is not a stable API, so the link-scraping here is best-effort — if
LinkedIn/Indeed change their template, update LINKEDIN_JOB_RE/INDEED_JOB_RE
and the two parse_* functions below.
"""

import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

LINKEDIN_JOB_RE = re.compile(r"https://www\.linkedin\.com/jobs/view/(\d+)")
INDEED_JOB_RE = re.compile(r"indeed\.com/(?:viewjob\?jk=|rc/clk\?jk=)([a-zA-Z0-9]+)")


class _LinkCollector(HTMLParser):
    """Collects (href, visible_text) for every <a> tag in an HTML document."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            if text:
                self.links.append((self._href, text))
            self._href = None
            self._text = []


def _collect_links(html):
    parser = _LinkCollector()
    parser.feed(html)
    return parser.links


def parse_linkedin_alert(html):
    """Extract job postings from a LinkedIn job-alert email body."""
    jobs = []
    seen_urls = set()
    for href, text in _collect_links(html):
        if not href or not LINKEDIN_JOB_RE.match(href):
            continue
        clean_url = href.split("?")[0]
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)
        jobs.append(
            {"title": text, "url": clean_url, "company": "", "location": "", "description": ""}
        )
    return jobs


def parse_indeed_alert(html):
    """Extract job postings from an Indeed job-alert email body."""
    jobs = []
    seen_keys = set()
    for href, text in _collect_links(html):
        if not href:
            continue
        m = INDEED_JOB_RE.search(href)
        if not m or m.group(1) in seen_keys:
            continue
        seen_keys.add(m.group(1))
        jobs.append(
            {"title": text, "url": href, "company": "", "location": "", "description": ""}
        )
    return jobs


def _refresh_access_token(client_id, client_secret, refresh_token):
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def _get_json(url, access_token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _list_message_ids(access_token, query):
    url = f"{GMAIL_API}/messages?" + urllib.parse.urlencode({"q": query, "maxResults": 50})
    data = _get_json(url, access_token)
    return [m["id"] for m in data.get("messages", [])]


def _find_html_part(payload):
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return payload["body"]["data"]
    for part in payload.get("parts", []):
        found = _find_html_part(part)
        if found:
            return found
    return None


def _get_message_html(access_token, msg_id):
    data = _get_json(f"{GMAIL_API}/messages/{msg_id}?format=full", access_token)
    encoded = _find_html_part(data.get("payload", {}))
    if not encoded:
        return ""
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")


PARSERS = {"linkedin": parse_linkedin_alert, "indeed": parse_indeed_alert}


def scan_gmail_alerts(cfg):
    """Scan configured LinkedIn/Indeed alert emails. Returns [] if disabled/misconfigured."""
    if not cfg.get("enabled", False):
        return []

    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        print(
            "warning: Gmail alert scanning enabled but credentials are not set — skipping",
            file=sys.stderr,
        )
        return []

    try:
        access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    except Exception as e:  # noqa: BLE001 - a bad token must not kill the whole run
        print(f"warning: Gmail token refresh failed: {e}", file=sys.stderr)
        return []

    lookback = cfg.get("lookback", "2d")
    jobs = []
    for source, sender in cfg.get("senders", {}).items():
        parse = PARSERS.get(source)
        if not parse:
            continue
        try:
            msg_ids = _list_message_ids(access_token, f"from:{sender} newer_than:{lookback}")
        except Exception as e:  # noqa: BLE001
            print(f"warning: Gmail search failed for {source}: {e}", file=sys.stderr)
            continue
        for msg_id in msg_ids:
            try:
                html = _get_message_html(access_token, msg_id)
            except Exception as e:  # noqa: BLE001
                print(f"warning: Gmail fetch failed for message {msg_id}: {e}", file=sys.stderr)
                continue
            jobs.extend(parse(html))
    return jobs
