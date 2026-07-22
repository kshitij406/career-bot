"""Track which notified jobs were actually applied to.

seen.json answers "have I been told about this?" — it is a dedup cache and
nothing more. It cannot answer "did I apply, and what happened?", so that
history lived only in the owner's memory and in Discord scrollback.

This is a separate store on purpose. seen.json is rewritten by the cron
workflow on every run; application state is human-entered and must never be
clobbered by an automated commit. Keeping them apart means a bad scan can
never cost application history.

Deliberately a flat JSON file rather than a database: it is a few hundred
rows at most, it diffs readably in git, and it keeps the repo's stdlib-only
dependency posture.

Manual CLI, never invoked by run.py or the workflow:

    python -m src.applications list [status]
    python -m src.applications add <url> [status]
    python -m src.applications set <url> <status> [note...]
"""

import datetime
import json
import os
import sys

APPLICATIONS_PATH = "applications.json"

USAGE = """usage:
  python -m src.applications list [status]
  python -m src.applications add <url> [status]
  python -m src.applications set <url> <status> [note...]"""

# Open-ended by design — these are the common ones, but `set` accepts any
# string so a status nobody anticipated doesn't require a code change.
KNOWN_STATUSES = ("interested", "applied", "screening", "interview", "offer", "rejected", "withdrawn")


def load_applications(path=APPLICATIONS_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_applications(applications, path=APPLICATIONS_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(applications, f, indent=2, sort_keys=True)


def record_application(job, applications, status="interested", note=""):
    """Add or update an entry, keyed by URL to match seen.json.

    first_seen and the history trail are preserved across updates — the point
    of the store is the trail, so a status change must never erase it.
    """
    url = job["url"]
    existing = applications.get(url, {})
    today = datetime.date.today().isoformat()
    history = list(existing.get("history", []))

    if existing.get("status") != status:
        history.append({"date": today, "status": status, **({"note": note} if note else {})})

    applications[url] = {
        "title": job.get("title", existing.get("title", "")),
        "company": job.get("company", existing.get("company", "")),
        "location": job.get("location", existing.get("location", "")),
        "score": job.get("score", existing.get("score", 0)),
        "status": status,
        "first_seen": existing.get("first_seen", today),
        "updated": today,
        "history": history,
    }
    return applications


def _fail(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _cmd_list(args, applications):
    wanted = args[0] if args else None
    rows = [(u, e) for u, e in applications.items() if wanted is None or e.get("status") == wanted]
    if not rows:
        print("no applications recorded" if wanted is None else f"no applications with status {wanted!r}")
        return
    for url, entry in sorted(rows, key=lambda kv: kv[1].get("updated", ""), reverse=True):
        print(
            f"{entry.get('status', '?'):<10} {entry.get('updated', ''):<12} "
            f"{entry.get('company', ''):<24} {entry.get('title', '')}"
        )
        print(f"{'':<23}{url}")
    counts = {}
    for _, entry in rows:
        counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
    print("\n" + "  ".join(f"{s}={n}" for s, n in sorted(counts.items())))


def _cmd_add(args, applications):
    if not args:
        _fail("usage: python -m src.applications add <url> [status]")
    url = args[0]
    status = args[1] if len(args) > 1 else "interested"
    # Backfill title/company from seen.json so the entry is readable without
    # the owner retyping what the scanner already knows.
    from src.seen import load_seen

    known = load_seen().get(url, {})
    job = {"url": url, "title": known.get("title", ""), "company": known.get("company", ""),
           "score": known.get("score", 0)}
    record_application(job, applications, status)
    save_applications(applications)
    print(f"recorded {status}: {job['company'] or url}")


def _cmd_set(args, applications):
    if len(args) < 2:
        _fail("usage: python -m src.applications set <url> <status> [note...]")
    url, status = args[0], args[1]
    note = " ".join(args[2:])
    if url not in applications:
        _fail(f"{url} is not tracked yet — add it first")
    record_application({"url": url, **applications[url]}, applications, status, note)
    save_applications(applications)
    print(f"{url} -> {status}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=sys.stderr)
        raise SystemExit(1)

    command, args = argv[0], argv[1:]
    applications = load_applications()
    if command == "list":
        _cmd_list(args, applications)
    elif command == "add":
        _cmd_add(args, applications)
    elif command == "set":
        _cmd_set(args, applications)
    else:
        _fail(f"unknown command {command!r} — expected list, add, or set")


if __name__ == "__main__":
    main()
