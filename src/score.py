"""Score scanned jobs against the candidate profile.

Primary scoring is a deterministic, local heuristic — keyword/location/stack
overlap, no API key, no network call, no rate limit, always returns a usable
score. AI scoring (via OpenRouter) is optional, off by default: enable with
scoring.ai_enabled: true in config/profile.yml plus OPENROUTER_API_KEY. When
enabled it refines the score/reason, and silently falls back to the
rule-based result if the call fails for any reason (rate-limited, out of
credit, provider down) — a flaky AI provider can never block a notification.
"""

import json
import os
import re
import sys
import time
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DURATION_KEYWORDS = [
    "year in industry", "placement", "sandwich", "industrial", "internship",
]
# Kent's placement year needs 44 weeks of work to satisfy the course — a soft
# preference, not a gate. These are duration mentions long enough to plausibly
# cover that (~10-13 months); shorter/unstated durations still score fine.
LONG_DURATION_KEYWORDS = [
    "44 week", "44-week", "45 week", "46 week", "48 week", "48-week",
    "10 month", "10-month", "11 month", "11-month", "12 month", "12-month",
    "13 month", "13-month", "year long", "year-long", "full year",
]
# Willing to relocate anywhere in the UK for the placement year (onsite,
# hybrid, or remote) — not limited to London/Kent, so any UK location scores
# the same rather than favoring specific cities.
UK_LOCATION_KEYWORDS = [
    # ponytail: bare "remote" deliberately excluded — it's not a UK signal on
    # its own ("Ontario Remote Work" isn't UK just because it says remote).
    "united kingdom", "uk", "london", "kent", "canterbury",
    "manchester", "birmingham", "edinburgh", "glasgow", "bristol", "leeds",
    "liverpool", "sheffield", "newcastle", "cardiff", "belfast",
    "nottingham", "cambridge", "oxford", "reading", "southampton", "brighton",
]
STACK_KEYWORDS = ["c#", ".net", "react", "next.js", "nextjs", "golang", "go developer", "python"]
RED_FLAG_KEYWORDS = ["5+ years", "7+ years", "10+ years", "phd required", "security clearance"]

SYSTEM_PROMPT_TEMPLATE = """You score job listings for one specific candidate.

Candidate profile:
{profile_yaml}

Candidate CV:
{cv}

Score each listing 0-100 for fit. Scoring rules, in priority order:
1. Eligibility gate: the candidate needs a UK-based or UK-remote-eligible software placement/internship/year-in-industry role running roughly July 2027 to 2028. A listing that is clearly senior, experienced-hire, requires a completed degree now, has no UK/remote-UK option, or is for a 2026 start scores below 25.
2. Breadth over specialization: backend, full-stack, DevOps, AI/ML, and general software engineering all count as strong fits if the candidate plausibly qualifies. Do NOT penalize a listing for being outside a specific specialization — the candidate is deliberately casting a wide net.
3. Weight up (add, don't gate): stack overlap with C#/.NET, React/Next.js, Go, Python; explicit placement/intern/year-in-industry framing; any UK location (onsite, hybrid, or remote) — the candidate is willing to relocate anywhere in the UK for the year, so do not favor London/Kent over other UK cities. The candidate's university placement year requires 44 weeks of work to count — a duration of roughly 10-13 months (or explicitly ~44+ weeks) is a soft plus, not a requirement; do not exclude or gate on shorter/unstated durations.
4. When the description is missing, score on title, company, and location alone without penalizing the gap.
role_type: one of "placement", "internship", "graduate", "other".
reason: one sentence, concrete, mentioning the decisive factor.

Respond with ONLY a JSON object, no markdown fences, no extra text:
{{"score": <integer 0-100>, "reason": <string>, "role_type": <string>}}"""

DESCRIPTION_TRUNCATE = 4000

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _heuristic_score(job):
    """Deterministic 0-100 score from keyword/location/stack overlap. No network call."""
    title = (job.get("title") or "").lower()
    location = (job.get("location") or "").lower()
    description = (job.get("description") or "").lower()
    text = f"{title} {location} {description}"

    score = 40  # baseline: already passed the title_filter's keyword gate
    signals = []

    if any(kw in text for kw in DURATION_KEYWORDS):
        score += 10
        signals.append("placement/year-in-industry framing")

    if any(kw in text for kw in LONG_DURATION_KEYWORDS):
        score += 6
        signals.append("duration likely covers the 44-week requirement")

    if any(kw in location for kw in UK_LOCATION_KEYWORDS):
        score += 10
        signals.append("UK location (onsite/hybrid/remote, relocation OK)")

    stack_hits = [kw for kw in STACK_KEYWORDS if kw in text]
    if stack_hits:
        score += min(len(stack_hits) * 6, 24)
        signals.append(f"stack overlap ({', '.join(stack_hits[:3])})")

    flags = [kw for kw in RED_FLAG_KEYWORDS if kw in text]
    if flags:
        score -= 25
        signals.append(f"experience-level red flag ({flags[0]})")

    score = max(0, min(100, score))

    if "placement" in title or "year in industry" in title or "sandwich" in title:
        role_type = "placement"
    elif "intern" in title:
        role_type = "internship"
    elif "graduate" in title:
        role_type = "graduate"
    else:
        role_type = "other"

    reason = "Rule-based: " + (
        ", ".join(signals) if signals else "keyword-filter match only, no additional signals"
    )
    return score, reason, role_type


def build_system_prompt(profile, cv_text):
    profile_yaml = json.dumps(
        {"candidate": profile.get("candidate"), "target": profile.get("target")},
        indent=2,
    )
    return SYSTEM_PROMPT_TEMPLATE.format(profile_yaml=profile_yaml, cv=cv_text)


def _job_user_message(job):
    description = (job.get("description") or "")[:DESCRIPTION_TRUNCATE]
    return (
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Description: {description}"
    )


def _extract_json(text):
    """Some models wrap JSON in markdown fences despite instructions — strip if present."""
    text = text.strip()
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    return json.loads(text)


def _call_openrouter(model, api_key, system_prompt, user_message):
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 1024,
            # Best-effort: honored by many but not all OpenRouter providers.
            # The system-prompt instruction + _extract_json fallback cover the rest.
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/career-bot",
            "X-Title": "career-bot",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def score_jobs(jobs, profile, cv_text):
    """Score each job in place (returns a new list). Rule-based by default;
    AI refines it on top when scoring.ai_enabled is true and a key is set."""
    scoring_cfg = profile.get("scoring", {})
    api_key = os.environ.get("OPENROUTER_API_KEY")
    use_ai = scoring_cfg.get("ai_enabled", False) and bool(api_key)

    model = scoring_cfg.get("model", "openai/gpt-oss-20b:free")
    system_prompt = build_system_prompt(profile, cv_text) if use_ai else None

    scored = []
    for i, job in enumerate(jobs):
        job = dict(job)
        score, reason, role_type = _heuristic_score(job)

        if use_ai:
            if i > 0 and model.endswith(":free"):
                # ponytail: free-tier OpenRouter models cap at ~20 req/min; a
                # flat delay is the simplest way to stay under that.
                time.sleep(3)
            try:
                text = _call_openrouter(model, api_key, system_prompt, _job_user_message(job))
                data = _extract_json(text)
                score, reason, role_type = data["score"], data["reason"], data["role_type"]
            except Exception as e:  # noqa: BLE001 - AI is optional, never a blocker
                print(
                    f"warning: AI scoring failed for {job.get('title', '?')!r}, "
                    f"using rule-based score instead: {e}",
                    file=sys.stderr,
                )

        job["score"] = score
        job["reason"] = reason
        job["role_type"] = role_type
        scored.append(job)
    return scored
