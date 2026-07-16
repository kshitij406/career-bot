"""Pipeline tests. Run via pytest, or directly: python tests/test_pipeline.py

No network and no OPENROUTER_API_KEY needed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scan import dedupe_jobs, title_filter
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
    "scoring": {"threshold": 60},
}

AI_PROFILE = {
    "candidate": {"summary": "test candidate"},
    "target": {"role_families": ["backend"]},
    "scoring": {"ai_enabled": True, "model": "openai/gpt-oss-20b:free", "threshold": 60},
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


def test_dedupe_jobs_collapses_cross_source_duplicate():
    ats_job = {
        "title": "Software Engineer Industrial Placement",
        "company": "Monzo",
        "url": "https://job-boards.greenhouse.io/monzo/jobs/1001",
        "location": "London, UK",
    }
    reed_copy = {
        "title": "Software Engineer Industrial Placement",
        "company": "Monzo",
        "url": "https://www.reed.co.uk/jobs/software-engineer-industrial-placement/9999999",
        "location": "London",
        "source": "reed",
    }
    unrelated = {
        "title": "Data Analyst Placement",
        "company": "Ocado Group",
        "url": "https://job-boards.greenhouse.io/ocadogroup/jobs/2002",
        "location": "Hatfield, UK",
    }
    deduped = dedupe_jobs([ats_job, reed_copy, unrelated])
    assert len(deduped) == 2
    assert deduped[0]["url"] == ats_job["url"], "ATS-sourced copy must win over the aggregator duplicate"


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


def test_heuristic_scoring_needs_no_network():
    """Default path: ai_enabled is unset/false, so no API key and no network call."""
    os.environ.pop("OPENROUTER_API_KEY", None)
    jobs = load_fixtures()
    job1 = jobs[0]  # "Software Engineer Industrial Placement", London
    scored = score_jobs([job1], PROFILE, "fake cv text")

    assert len(scored) == 1
    assert 0 <= scored[0]["score"] <= 100
    assert scored[0]["reason"].startswith("Rule-based:")
    assert scored[0]["role_type"] == "placement"


def test_ai_scoring_refines_and_overrides_heuristic(monkeypatch=None):
    import urllib.request as urllib_request_module

    original_urlopen = urllib_request_module.urlopen
    urllib_request_module.urlopen = lambda req, timeout=60: _FakeHTTPResponse(None)
    os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests"
    try:
        jobs = load_fixtures()
        job1 = jobs[0]
        scored = score_jobs([job1], AI_PROFILE, "fake cv text")
    finally:
        urllib_request_module.urlopen = original_urlopen
        os.environ.pop("OPENROUTER_API_KEY", None)

    assert len(scored) == 1
    assert scored[0]["score"] == 85
    assert scored[0]["reason"] == "Strong stack overlap."
    assert scored[0]["role_type"] == "placement"


def test_ai_scoring_falls_back_to_heuristic_on_failure():
    import urllib.request as urllib_request_module

    def _raise(*args, **kwargs):
        raise urllib_request_module.URLError("rate limited")

    original_urlopen = urllib_request_module.urlopen
    urllib_request_module.urlopen = _raise
    os.environ["OPENROUTER_API_KEY"] = "fake-key-for-tests"
    try:
        jobs = load_fixtures()
        job1 = jobs[0]
        scored = score_jobs([job1], AI_PROFILE, "fake cv text")  # must not raise
    finally:
        urllib_request_module.urlopen = original_urlopen
        os.environ.pop("OPENROUTER_API_KEY", None)

    assert len(scored) == 1
    assert scored[0]["reason"].startswith("Rule-based:")


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


def test_notify_survives_discord_failure():
    import urllib.request as urllib_request_module

    def _raise(*args, **kwargs):
        raise urllib_request_module.URLError("boom")

    os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/fake/fake"
    original_urlopen = urllib_request_module.urlopen
    urllib_request_module.urlopen = _raise
    try:
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
        notify_module.notify(jobs, threshold=60)  # must not raise
    finally:
        urllib_request_module.urlopen = original_urlopen
        os.environ.pop("DISCORD_WEBHOOK_URL", None)


if __name__ == "__main__":
    test_title_filter()
    print("test_title_filter passed")
    test_dedupe_jobs_collapses_cross_source_duplicate()
    print("test_dedupe_jobs_collapses_cross_source_duplicate passed")
    test_dedup()
    print("test_dedup passed")
    test_heuristic_scoring_needs_no_network()
    print("test_heuristic_scoring_needs_no_network passed")
    test_ai_scoring_refines_and_overrides_heuristic()
    print("test_ai_scoring_refines_and_overrides_heuristic passed")
    test_ai_scoring_falls_back_to_heuristic_on_failure()
    print("test_ai_scoring_falls_back_to_heuristic_on_failure passed")
    test_parse_linkedin_alert()
    print("test_parse_linkedin_alert passed")
    test_parse_indeed_alert()
    print("test_parse_indeed_alert passed")
    test_notify_dry_run()
    print("test_notify_dry_run passed")
    test_notify_survives_discord_failure()
    print("test_notify_survives_discord_failure passed")
    print("all tests passed")
