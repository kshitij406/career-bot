"""Live check for src/aggregators.py — hits the real Reed/Adzuna APIs.

MANUAL ONLY. Unlike test_pipeline.py this needs network + real API keys
(REED_API_KEY, and ADZUNA_APP_ID/ADZUNA_API_KEY for the Adzuna half) and is
never run by CI. Run directly: python tests/test_live_aggregators.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.aggregators import fetch_adzuna_jobs, fetch_reed_jobs

REQUIRED_KEYS = {"title", "url", "company", "location", "description"}


def _check_shape(jobs, label):
    assert isinstance(jobs, list)
    for job in jobs:
        missing = REQUIRED_KEYS - job.keys()
        assert not missing, f"{label} job missing keys: {missing}"
    print(f"{label}: {len(jobs)} jobs, shape OK")


if __name__ == "__main__":
    if not os.environ.get("REED_API_KEY"):
        print("REED_API_KEY not set, skipping Reed live check")
    else:
        reed_jobs = fetch_reed_jobs("software engineer placement", "UK")
        _check_shape(reed_jobs, "reed")
        assert reed_jobs, "expected at least one real Reed result for this query"

    if not os.environ.get("ADZUNA_APP_ID") or not os.environ.get("ADZUNA_API_KEY"):
        print("ADZUNA_APP_ID/ADZUNA_API_KEY not set, skipping Adzuna live check")
    else:
        adzuna_jobs = fetch_adzuna_jobs("software engineer placement", "UK")
        _check_shape(adzuna_jobs, "adzuna")
        assert adzuna_jobs, "expected at least one real Adzuna result for this query"

    print("done")
