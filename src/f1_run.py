"""F1 job monitor pipeline entrypoint: scan -> filter -> dedup -> notify -> save.

Separate from the general career-bot pipeline (src/run.py): its own team
list (config/f1_teams.json), its own dedup store (seen_f1.json), no scoring
model — every job that passes f1_title_filter is a keyword match and gets
notified. Reuses the general pipeline's ATS fetch layer, dedupe_jobs, and
seen-store mechanics rather than duplicating them.

Run as: python -m src.f1_run
"""

import json

from src.f1_notify import notify_f1
from src.f1_scan import f1_title_filter, scan_all_teams
from src.scan import dedupe_jobs
from src.seen import is_new, load_seen, record, save_seen

SEEN_PATH = "seen_f1.json"


def load_teams():
    with open("config/f1_teams.json", "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    teams_cfg = load_teams()

    scanned = scan_all_teams(teams_cfg)
    scanned = dedupe_jobs(scanned)
    filtered = f1_title_filter(scanned)

    seen = load_seen(SEEN_PATH)
    new_jobs = [j for j in filtered if is_new(j, seen)]

    notify_f1(new_jobs)

    for job in new_jobs:
        record(job, seen)
    save_seen(seen, SEEN_PATH)

    print(f"scanned={len(scanned)} filtered={len(filtered)} new={len(new_jobs)} notified={len(new_jobs)}")


if __name__ == "__main__":
    main()
