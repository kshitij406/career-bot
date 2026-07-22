"""Send Discord notifications for new F1-org job matches (or print in dry-run).

Same webhook as the general career-bot pipeline (DISCORD_WEBHOOK_URL) — no
scoring model here, every job that survives f1_scan.f1_title_filter and the
seen-store dedup is a match, one embed per job.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BATCH_SIZE = 10
USER_AGENT = "career-bot-f1/1.0"


def _build_embed(job):
    location = job.get("location", "")
    posted = job.get("posted_date", "")
    subtitle = f"{job.get('company', '')} · {location}" if location else job.get("company", "")
    if posted:
        subtitle += f" · posted {posted}"
    return {
        "title": job.get("title", ""),
        "url": job.get("url", ""),
        "description": subtitle,
    }


def _is_dead_link(url, timeout=10):
    if not url:
        return False
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return False
    except urllib.error.HTTPError as e:
        return e.code in (404, 410)
    except Exception:  # noqa: BLE001 - network hiccup is not proof the job is dead
        return False


def notify_f1(jobs):
    """POST new F1 job matches to Discord in batches of 10 embeds. Dry-run to stdout if unset."""
    if not jobs:
        return

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if webhook_url:
        alive = []
        for job in jobs:
            if _is_dead_link(job.get("url", "")):
                print(f"info: skipping dead link (404/410): {job.get('title', '')!r} -> {job.get('url', '')}", file=sys.stderr)
                continue
            alive.append(job)
        jobs = alive
        if not jobs:
            return

    for i in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[i : i + BATCH_SIZE]
        embeds = [_build_embed(j) for j in batch]

        if not webhook_url:
            for job, embed in zip(batch, embeds):
                print(f"[dry-run f1 notify] {embed['title']} -> {embed['url']}")
                print(f"    {embed['description']}")
            continue

        body = json.dumps({"embeds": embeds}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except Exception as e:  # noqa: BLE001 - a Discord outage must not lose a scored batch
            print(f"warning: Discord notify failed for a batch: {e}", file=sys.stderr)
