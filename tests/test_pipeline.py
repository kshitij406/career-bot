"""Pipeline tests. Run via pytest, or directly: python tests/test_pipeline.py

No network and no OPENROUTER_API_KEY needed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scan import _html_to_text, dedupe_jobs, title_filter
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


def test_html_to_text_handles_escaped_markup():
    # Greenhouse returns `content` with the tags themselves escaped. Stripping
    # before unescaping left the raw markup in the description verbatim, which
    # then fed stack-overlap scoring and the AI prompt. Shape below is copied
    # from a live boards-api.greenhouse.io response.
    greenhouse = (
        "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;&lt;strong&gt;"
        "We want &lt;em&gt;Go&lt;/em&gt; and C# devs.&lt;/strong&gt;&lt;/p&gt;&lt;/div&gt;"
    )
    text = _html_to_text(greenhouse)
    assert "<" not in text and ">" not in text, text
    assert "class=" not in text, text
    assert "Go" in text and "C#" in text

    # Doubly-escaped payloads need more than one unescape pass.
    assert "<" not in _html_to_text("&amp;lt;p&amp;gt;Backend role&amp;lt;/p&amp;gt;")

    # Ordinary markup still works.
    assert _html_to_text("<p>Python &amp; Go</p>").strip() == "Python & Go"

    # A bare comparison in prose is not markup and must survive intact.
    assert "200ms" in _html_to_text("latency &lt; 200ms")


def test_api_base_is_configurable_and_local_needs_no_key():
    import urllib.request as urllib_request_module
    from src.score import _resolve_api_base, _is_openrouter

    assert _resolve_api_base({}) == "https://openrouter.ai/api/v1"
    assert _resolve_api_base({"api_base": "http://localhost:11434/v1/"}) == "http://localhost:11434/v1"
    os.environ["CAREER_BOT_API_BASE"] = "http://localhost:1234/v1"
    try:
        # env wins over profile.yml, so a local run can redirect scoring
        # without editing committed config
        assert _resolve_api_base({"api_base": "https://openrouter.ai/api/v1"}) == "http://localhost:1234/v1"
    finally:
        os.environ.pop("CAREER_BOT_API_BASE", None)
    assert _is_openrouter("https://openrouter.ai/api/v1")
    assert not _is_openrouter("http://localhost:11434/v1")

    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["referer"] = req.headers.get("Http-referer")
        return _FakeHTTPResponse(None)

    original = urllib_request_module.urlopen
    urllib_request_module.urlopen = fake_urlopen
    os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        local_profile = dict(AI_PROFILE)
        local_profile["scoring"] = dict(AI_PROFILE["scoring"], api_base="http://localhost:11434/v1")
        scored = score_jobs(load_fixtures()[:1], local_profile, "fake cv text")
    finally:
        urllib_request_module.urlopen = original

    # ai_enabled must not silently no-op against a local server just because
    # OPENROUTER_API_KEY is unset — that key is meaningless to localhost.
    assert scored[0]["score"] == 85
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["auth"] is None, "no key exists, so no Authorization header"
    assert captured["referer"] is None, "OpenRouter-specific headers must not go to a local server"


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


def test_dedupe_jobs_keeps_same_company_same_title_different_office():
    # Real data from the 2026-07-16 live audit: Monzo posts "Engineering
    # Manager" separately in Barcelona and in Cardiff/London/Remote — two
    # genuinely different open reqs that a location-blind (title, company)
    # key was wrongly collapsing.
    barcelona = {
        "title": "Engineering Manager",
        "company": "Monzo",
        "url": "https://job-boards.greenhouse.io/monzo/jobs/6945371",
        "location": "Barcelona",
    }
    cardiff_london_remote = {
        "title": "Engineering Manager",
        "company": "Monzo",
        "url": "https://job-boards.greenhouse.io/monzo/jobs/5018066",
        "location": "Cardiff, London or Remote (UK)",
    }
    deduped = dedupe_jobs([barcelona, cardiff_london_remote])
    assert len(deduped) == 2, "different real offices for the same title must not collapse"


def test_dedupe_jobs_collapses_cross_source_despite_incompatible_location_text():
    # Real data from the same audit: AJ Bell "Software Engineer" posted
    # independently to Reed and Adzuna. Reed's location is a bare postcode
    # ("M53EE"), Adzuna's is a city name ("Manchester, Greater Manchester") —
    # incompatible text with no cheap way to reconcile, so cross-source
    # matches must still collapse on (title, company) alone.
    reed_posting = {
        "title": "Software Engineer",
        "company": "AJ Bell",
        "url": "https://www.reed.co.uk/jobs/software-engineer/57122279",
        "location": "M53EE",
        "source": "reed",
    }
    adzuna_posting = {
        "title": "Software Engineer",
        "company": "AJ Bell",
        "url": "https://www.adzuna.co.uk/jobs/land/ad/5801491743",
        "location": "Manchester, Greater Manchester",
        "source": "adzuna",
    }
    deduped = dedupe_jobs([reed_posting, adzuna_posting])
    assert len(deduped) == 1, "same job across sources must still collapse despite postcode vs city name"


def test_dedupe_jobs_normalizes_company_legal_suffix_and_title_parenthetical():
    # Sources disagree on legal form and on parenthetical qualifiers for what
    # is unambiguously one posting. Before normalization these stayed separate
    # and both got notified.
    ats = {
        "title": "Software Engineer",
        "company": "AJ Bell",
        "url": "https://ats.example/1",
        "location": "Manchester",
    }
    reed = {
        "title": "Software Engineer (Remote)",
        "company": "AJ Bell plc",
        "url": "https://www.reed.co.uk/jobs/1",
        "location": "M53EE",
        "source": "reed",
    }
    deduped = dedupe_jobs([ats, reed])
    assert len(deduped) == 1, "legal suffix + parenthetical must not hide a duplicate"

    # Accents and punctuation fold too.
    a = {"title": "Backend Engineer", "company": "Société Générale", "url": "u1", "location": "London"}
    b = {"title": "Backend Engineer", "company": "Societe Generale", "url": "u2",
         "location": "London", "source": "adzuna"}
    assert len(dedupe_jobs([a, b])) == 1

    # Genuinely different employers must still stay separate.
    x = {"title": "Software Engineer", "company": "Monzo", "url": "u3", "location": "London"}
    y = {"title": "Software Engineer", "company": "Starling Bank", "url": "u4", "location": "London"}
    assert len(dedupe_jobs([x, y])) == 2


def test_dedupe_jobs_prefers_authoritative_source_regardless_of_order():
    ats = {
        "title": "Software Engineer",
        "company": "AJ Bell",
        "url": "https://ats.example/real-application-form",
        "location": "Manchester",
    }
    reed = {
        "title": "Software Engineer",
        "company": "AJ Bell",
        "url": "https://www.reed.co.uk/jobs/redirect",
        "location": "M53EE",
        "source": "reed",
    }
    # The ATS copy must win from either input order — callers should not have
    # to remember to list ATS sources first.
    assert dedupe_jobs([ats, reed])[0]["url"] == ats["url"]
    assert dedupe_jobs([reed, ats])[0]["url"] == ats["url"], "aggregator-first order must still yield the ATS copy"

    # Position in the output is preserved when the incumbent is replaced, so
    # unrelated jobs around it keep their order.
    other = {"title": "Data Analyst", "company": "Ocado", "url": "u9", "location": "Hatfield"}
    out = dedupe_jobs([reed, other, ats])
    assert [j["url"] for j in out] == [ats["url"], "u9"]


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
    test_api_base_is_configurable_and_local_needs_no_key()
    print("test_api_base_is_configurable_and_local_needs_no_key passed")
    test_html_to_text_handles_escaped_markup()
    print("test_html_to_text_handles_escaped_markup passed")
    test_dedupe_jobs_collapses_cross_source_duplicate()
    print("test_dedupe_jobs_collapses_cross_source_duplicate passed")
    test_dedupe_jobs_keeps_same_company_same_title_different_office()
    print("test_dedupe_jobs_keeps_same_company_same_title_different_office passed")
    test_dedupe_jobs_collapses_cross_source_despite_incompatible_location_text()
    print("test_dedupe_jobs_collapses_cross_source_despite_incompatible_location_text passed")
    test_dedupe_jobs_normalizes_company_legal_suffix_and_title_parenthetical()
    print("test_dedupe_jobs_normalizes_company_legal_suffix_and_title_parenthetical passed")
    test_dedupe_jobs_prefers_authoritative_source_regardless_of_order()
    print("test_dedupe_jobs_prefers_authoritative_source_regardless_of_order passed")
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
