"""Send Discord notifications for high-scoring jobs (or print in dry-run)."""

import json
import os
import sys
import urllib.error
import urllib.request

BATCH_SIZE = 10
USER_AGENT = "career-bot/1.0"


def _build_embed(job):
    return {
        "title": f"{job.get('score', 0)} — {job.get('title', '')}",
        "url": job.get("url", ""),
        "description": f"{job.get('company', '')} · {job.get('location', '')}\n{job.get('reason', '')}",
    }


def _is_dead_link(url, timeout=10):
    """Best-effort liveness check — a 404/410 means the posting was pulled
    between scan and notify. Any other outcome (200, a 403 from bot
    protection, a timeout) is treated as alive: this must never be the
    reason a real match gets silently dropped, only a confirmed-gone one."""
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


def notify(jobs, threshold):
    """POST matching jobs to Discord in batches of 10 embeds. Dry-run to stdout if unset."""
    matching = [j for j in jobs if j.get("score", 0) >= threshold]
    if not matching:
        return

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    if webhook_url:
        # Only worth the network round-trip when something will actually be
        # sent — dry-run output is for local debugging, not a real alert.
        alive = []
        for job in matching:
            if _is_dead_link(job.get("url", "")):
                print(f"info: skipping dead link (404/410): {job.get('title', '')!r} -> {job.get('url', '')}", file=sys.stderr)
                continue
            alive.append(job)
        matching = alive
        if not matching:
            return

    for i in range(0, len(matching), BATCH_SIZE):
        batch = matching[i : i + BATCH_SIZE]
        embeds = [_build_embed(j) for j in batch]

        if not webhook_url:
            for job, embed in zip(batch, embeds):
                print(f"[dry-run notify] {embed['title']} -> {embed['url']}")
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
