"""Pipeline tests. Run via pytest, or directly: python tests/test_pipeline.py

No network and no OPENROUTER_API_KEY needed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scan import title_filter
from src.seen import is_new
from src.score import score_jobs
from src.gmail_scan import parse_linkedin_alert, parse_indeed_alert
from src import notify as notify_module

FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "jobs.json")

TITLE_FILTER_CFG = {
    "title_filter": {
        "positive": ["placement", "intern"],
        "negative": ["senior", "staff"],
    }
}

PROFILE = {
    "candidate": {"summary": "test candidate"},
    "target": {"role_families": ["backend"]},
    "scoring": {"model": "meta-llama/llama-3.3-70b-instruct:free", "threshold": 60},
}


def load_fixtures():
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_title_filter():
    jobs = load_fixtures()
    kept = title_filter(jobs, TITLE_FILTER_CFG)
    kept_titles = {j["title"] for j in kept}
    assert "Software Engineer Industrial Placement" in kept_titles
    assert "Software Engineering Intern" in kept_titles
    assert "Senior Staff Engineer" not in kept_titles
    assert len(kept) == 2


def test_dedup():
    jobs = load_fixtures()
    job1, job2, job3 = jobs
    seen = {job3["url"]: {"first_seen": "2026-01-01", "title": job3["title"],
                           "company": job3["company"], "score": 90}}
    assert is_new(job3, seen) is False
    assert is_new(job1, seen) is True


class _FakeHTTPResponse:
    def __init__(self, payload):
        content = json.dumps({"score": 85, "reason": "Strong stack overlap.", "role_type": "placement"})
        self._body = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_scoring(monkeypatch=None):
    import urllib.request as urllib_request_module

    original_urlopen = urllib_request_module.urlopen
    urllib_request_module.urlopen = lambda req, timeout=60: _FakeHTTPResponse(None)
    os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests"
    try:
        jobs = load_fixtures()
        job1 = jobs[0]
        scored = score_jobs([job1], PROFILE, "fake cv text")
    finally:
        urllib_request_module.urlopen = original_urlopen

    assert len(scored) == 1
    assert scored[0]["score"] == 85
    assert scored[0]["reason"] == "Strong stack overlap."
    assert scored[0]["role_type"] == "placement"


def test_parse_linkedin_alert():
    html = """
    <a href="https://www.linkedin.com/jobs/view/1234567890/?trk=email1">Software Engineer Intern</a>
    <a href="https://www.linkedin.com/jobs/view/1234567890/?trk=email2">Software Engineer Intern</a>
    <a href="https://www.linkedin.com/comm/jobs/settings">Manage job alert</a>
    """
    jobs = parse_linkedin_alert(html)
    assert len(jobs) == 1, "same job linked twice (image + text) must dedup"
    assert jobs[0]["title"] == "Software Engineer Intern"
    assert jobs[0]["url"] == "https://www.linkedin.com/jobs/view/1234567890/"


def test_parse_indeed_alert():
    html = """
    <a href="https://uk.indeed.com/rc/clk?jk=abc123&from=alert">Placement Software Engineer</a>
    <a href="https://uk.indeed.com/viewjob?jk=abc123">Placement Software Engineer</a>
    <a href="https://uk.indeed.com/alertmanagement">Manage alerts</a>
    """
    jobs = parse_indeed_alert(html)
    assert len(jobs) == 1, "same job key (jk) via two link styles must dedup"
    assert jobs[0]["title"] == "Placement Software Engineer"


def test_notify_dry_run(capsys=None):
    os.environ.pop("DISCORD_WEBHOOK_URL", None)
    jobs = [
        {
            "title": "Software Engineer Industrial Placement",
            "company": "Monzo",
            "url": "https://job-boards.greenhouse.io/monzo/jobs/1001",
            "location": "London, UK",
            "score": 85,
            "reason": "Strong stack overlap.",
        }
    ]
    # Should not raise.
    notify_module.notify(jobs, threshold=60)


if __name__ == "__main__":
    test_title_filter()
    print("test_title_filter passed")
    test_dedup()
    print("test_dedup passed")
    test_scoring()
    print("test_scoring passed")
    test_parse_linkedin_alert()
    print("test_parse_linkedin_alert passed")
    test_parse_indeed_alert()
    print("test_parse_indeed_alert passed")
    test_notify_dry_run()
    print("test_notify_dry_run passed")
    print("all tests passed")
