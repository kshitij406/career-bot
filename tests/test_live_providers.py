"""Live check for the Recruitee/Breezy HR scanners in src/scan.py — hits real
company boards to confirm the JSON shape and that missing optional fields
(Breezy's department/salary) don't break the parser.

MANUAL ONLY. Needs network and isn't run by CI. Run directly:
python tests/test_live_providers.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scan import scan_company

REQUIRED_KEYS = {"title", "url", "company", "location", "description"}

LIVE_COMPANIES = [
    ("BCN Group", "https://bcngroup.recruitee.com"),
    ("The Config Team", "https://theconfigteamcareers.recruitee.com"),
    ("Reincubate", "https://reincubate.breezy.hr"),
    ("Quantios", "https://quantios.breezy.hr"),
]


if __name__ == "__main__":
    for name, url in LIVE_COMPANIES:
        jobs = scan_company({"name": name, "careers_url": url})
        assert jobs, f"expected at least one real posting for {name}"
        for job in jobs:
            missing = REQUIRED_KEYS - job.keys()
            assert not missing, f"{name} job missing keys: {missing}"
        print(f"{name}: {len(jobs)} jobs, shape OK")

    print("done")
