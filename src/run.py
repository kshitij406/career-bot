"""Pipeline entrypoint: scan -> filter -> dedup -> score -> notify -> save.

Run as: python -m src.run
"""

import yaml

from src.gmail_scan import scan_gmail_alerts
from src.notify import notify
from src.scan import scan_all, title_filter
from src.score import score_jobs
from src.seen import is_new, load_seen, record, save_seen


def load_config():
    with open("config/profile.yml", "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)
    with open("config/portals.yml", "r", encoding="utf-8") as f:
        portals = yaml.safe_load(f)
    with open("config/gmail.yml", "r", encoding="utf-8") as f:
        gmail_cfg = yaml.safe_load(f)
    with open("cv.md", "r", encoding="utf-8") as f:
        cv_text = f.read()
    return profile, portals, gmail_cfg, cv_text


def main():
    profile, portals, gmail_cfg, cv_text = load_config()

    scanned = scan_all(portals) + scan_gmail_alerts(gmail_cfg)
    filtered = title_filter(scanned, portals)

    seen = load_seen()
    new_jobs = [j for j in filtered if is_new(j, seen)]

    scored = score_jobs(new_jobs, profile, cv_text) if new_jobs else []

    threshold = profile.get("scoring", {}).get("threshold", 60)
    notify(scored, threshold)

    for job in scored:
        record(job, seen)
    save_seen(seen)

    notified = len([j for j in scored if j.get("score", 0) >= threshold])
    print(
        f"scanned={len(scanned)} filtered={len(filtered)} new={len(new_jobs)} "
        f"scored={len(scored)} notified={notified}"
    )


if __name__ == "__main__":
    main()
