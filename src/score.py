"""Score scanned jobs against the candidate profile using the Anthropic API."""

import json
import sys

import anthropic

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "reason": {"type": "string"},
        "role_type": {"type": "string"},
    },
    "required": ["score", "reason", "role_type"],
    "additionalProperties": False,
}

SYSTEM_PROMPT_TEMPLATE = """You score job listings for one specific candidate. Return JSON only.

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
reason: one sentence, concrete, mentioning the decisive factor."""

DESCRIPTION_TRUNCATE = 4000


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


def score_jobs(jobs, profile, cv_text):
    """Score each job in place (returns a new list) using the Anthropic API."""
    model = profile.get("scoring", {}).get("model", "claude-opus-4-8")
    system_prompt = build_system_prompt(profile, cv_text)
    client = anthropic.Anthropic()

    scored = []
    for job in jobs:
        job = dict(job)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": _job_user_message(job)}],
            )
            text = next(b.text for b in resp.content if b.type == "text")
            data = json.loads(text)
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
