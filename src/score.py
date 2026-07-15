"""Score scanned jobs against the candidate profile using OpenRouter's chat completions API."""

import json
import os
import re
import sys
import time
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT_TEMPLATE = """You score job listings for one specific candidate.

Candidate profile:
{profile_yaml}

Candidate CV:
{cv}

Score each listing 0-100 for fit. Scoring rules, in priority order:
1. Eligibility gate: the candidate needs a UK-based or UK-remote-eligible software placement/internship/year-in-industry role running roughly September 2026 to summer 2027. A listing that is clearly senior, experienced-hire, requires a completed degree now, or has no UK/remote-UK option scores below 25.
2. Breadth over specialization: backend, full-stack, DevOps, AI/ML, and general software engineering all count as strong fits if the candidate plausibly qualifies. Do NOT penalize a listing for being outside a specific specialization — the candidate is deliberately casting a wide net.
3. Weight up (add, don't gate): stack overlap with C#/.NET, React/Next.js, Go, Python; explicit placement/intern/year-in-industry framing; 9-13 month duration; London/Kent/remote-UK location.
4. When the description is missing, score on title, company, and location alone without penalizing the gap.
role_type: one of "placement", "internship", "graduate", "other".
reason: one sentence, concrete, mentioning the decisive factor.

Respond with ONLY a JSON object, no markdown fences, no extra text:
{{"score": <integer 0-100>, "reason": <string>, "role_type": <string>}}"""

DESCRIPTION_TRUNCATE = 4000

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


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
    """Score each job in place (returns a new list) via OpenRouter."""
    model = profile.get("scoring", {}).get("model", "openai/gpt-oss-20b:free")
    api_key = os.environ["OPENROUTER_API_KEY"]
    system_prompt = build_system_prompt(profile, cv_text)

    scored = []
    for i, job in enumerate(jobs):
        if i > 0 and model.endswith(":free"):
            # ponytail: free-tier OpenRouter models cap at ~20 req/min; a flat
            # delay is the simplest way to stay under that. Swap for a token
            # bucket if this ever needs to run faster than one job per 3s.
            time.sleep(3)
        job = dict(job)
        try:
            text = _call_openrouter(model, api_key, system_prompt, _job_user_message(job))
            data = _extract_json(text)
            job["score"] = data["score"]
            job["reason"] = data["reason"]
            job["role_type"] = data["role_type"]
        except Exception as e:  # noqa: BLE001 - one bad job must not kill the run
            print(
                f"warning: scoring failed for {job.get('title', '?')!r}: {e}",
                file=sys.stderr,
            )
            job["score"] = 0
            job["reason"] = "scoring failed"
            job["role_type"] = "other"
        scored.append(job)
    return scored
