"""Manual CLI: re-score everything in seen.json, and drop dead postings.

    python -m src.rescore              # rescore from the cache
    python -m src.rescore --scan       # rescan first, then rescore
    python -m src.rescore --prune      # also remove postings that 404/410

Why this exists: run.py only scores jobs it has never seen. That is correct
for notification (nobody wants the same job twice) but it means every scoring
fix applies only to future finds, while the existing backlog keeps whatever
the rules said the day it was first seen. After a change to the heuristic the
top of the list is stale rather than wrong-in-an-obvious-way, which is worse.

Never invoked by run.py or the workflow. It rewrites seen.json scores in
place and sends no notifications — re-scoring must never re-alert.
"""

import sys

from src.jobcache import load_cache, save_cache, update_cache
from src.notify import _is_dead_link
from src.score import _heuristic_score
from src.seen import load_seen, save_seen


def rescore(seen, cache):
    """Recompute scores for every seen job present in the cache.

    Returns (updated_count, skipped_count, changes) where changes lists the
    biggest movers, so a heuristic change can be sanity-checked before it is
    trusted.
    """
    updated = skipped = 0
    changes = []
    for url, entry in seen.items():
        cached = cache.get(url)
        if not cached:
            # Cache only fills from scans run after it was introduced, so a
            # backlog entry may simply not be there yet. Leave it alone rather
            # than scoring it on title alone and calling that an improvement.
            skipped += 1
            continue
        job = {
            "title": cached.get("title") or entry.get("title", ""),
            "company": cached.get("company") or entry.get("company", ""),
            "location": cached.get("location", ""),
            "description": cached.get("description", ""),
        }
        score, reason, role_type = _heuristic_score(job)
        old = entry.get("score", 0)
        if score != old:
            changes.append((old, score, job["company"], job["title"]))
        entry["score"] = score
        entry["reason"] = reason
        entry["role_type"] = role_type
        updated += 1
    changes.sort(key=lambda c: abs(c[1] - c[0]), reverse=True)
    return updated, skipped, changes


# Hosts whose postings are fully enumerated by a portals scan. If one of these
# URLs is absent from a fresh scan, the posting was taken down — far more
# reliable than an HTTP check, since ATS boards serve a "no longer available"
# page with a 200. Aggregator and Gmail URLs are excluded: a portals scan never
# enumerates them, so absence there means nothing.
_ENUMERABLE_HOSTS = (
    "greenhouse.io", "ashbyhq.com", "lever.co", "smartrecruiters.com",
    "workable.com", "personio.com", "personio.de", "myworkdayjobs.com",
    "recruitee.com", "breezy.hr",
)


def mark_delisted(seen, scanned_urls):
    """Flag seen entries whose posting has disappeared from its board.

    Marked rather than deleted: deleting would re-notify the job if it came
    back, and a delisted entry is still worth seeing in the UI with its
    application history intact.
    """
    delisted = []
    for url, entry in seen.items():
        if not any(host in url for host in _ENUMERABLE_HOSTS):
            continue
        gone = url not in scanned_urls
        if gone and not entry.get("delisted"):
            delisted.append(url)
        entry["delisted"] = gone
    return delisted


def prune_dead(seen, max_checks=200):
    """Remove postings whose URL is confirmed gone (404/410).

    Only confirmed-gone: a timeout or a 403 from bot protection leaves the
    entry alone, exactly as in notify.py. Removing a live job would silently
    re-notify it on the next run.
    """
    dead = []
    for url in list(seen)[:max_checks]:
        if _is_dead_link(url):
            dead.append(url)
    for url in dead:
        del seen[url]
    return dead


def main():
    args = sys.argv[1:]
    cache = load_cache()
    scanned_urls = set()

    if "--scan" in args:
        import yaml

        from src.scan import dedupe_jobs, enrich_descriptions, scan_all
        from src.seen import load_seen as _load

        with open("config/portals.yml", "r", encoding="utf-8") as f:
            portals = yaml.safe_load(f)
        print("scanning portals...", file=sys.stderr)
        jobs = dedupe_jobs(scan_all(portals))
        # Only enrich what's actually in seen.json — enriching 3000 scanned
        # jobs would be thousands of requests for no benefit.
        known = set(_load())
        enrich_descriptions([j for j in jobs if j.get("url") in known])
        cache = update_cache(jobs, cache)
        save_cache(cache)
        print(f"cached {len(cache)} job records", file=sys.stderr)
        scanned_urls = {j.get("url") for j in jobs}

    seen = load_seen()
    updated, skipped, changes = rescore(seen, cache)

    newly_delisted = mark_delisted(seen, scanned_urls) if "--scan" in args else []

    dead = prune_dead(seen) if "--prune" in args else []

    save_seen(seen)
    print(
        f"rescored={updated} skipped(no cache)={skipped} "
        f"delisted={len(newly_delisted)} pruned={len(dead)}"
    )
    if newly_delisted:
        print("\nnewly delisted (gone from the board):")
        for url in newly_delisted[:10]:
            print(f"  {seen[url].get('company','')[:18]:18s} {seen[url].get('title','')[:48]}")
    if changes:
        print("\nbiggest changes:")
        for old, new, company, title in changes[:15]:
            print(f"  {old:3d} -> {new:3d}  {company[:18]:18s} {title[:52]}")
    if dead:
        print("\nremoved (404/410):")
        for url in dead[:10]:
            print(f"  {url}")


if __name__ == "__main__":
    main()
