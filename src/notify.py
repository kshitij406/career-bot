"""Send Discord notifications for high-scoring jobs (or print in dry-run)."""

import json
import os
import urllib.request

BATCH_SIZE = 10


def _build_embed(job):
    return {
        "title": f"{job.get('score', 0)} — {job.get('title', '')}",
        "url": job.get("url", ""),
        "description": f"{job.get('company', '')} · {job.get('location', '')}\n{job.get('reason', '')}",
    }


def notify(jobs, threshold):
    """POST matching jobs to Discord in batches of 10 embeds. Dry-run to stdout if unset."""
    matching = [j for j in jobs if j.get("score", 0) >= threshold]
    if not matching:
        return

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
