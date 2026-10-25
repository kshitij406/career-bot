"""F1 pipeline tests. Run via pytest, or directly: python tests/test_f1_pipeline.py

No network needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.f1_scan import f1_title_filter


def test_f1_title_filter_keeps_tech_placements():
    jobs = [
        {"title": "Software Engineer Intern"},
        {"title": "AI Engineering Student Placement"},
        {"title": "IT Service Desk Student Placement"},
        {"title": "Aero Development Tools Student Placement"},
        {"title": "Data Platform Industrial Placement"},
    ]
    kept = f1_title_filter(jobs)
    assert [j["title"] for j in kept] == [j["title"] for j in jobs]
    print("test_f1_title_filter_keeps_tech_placements passed")


def test_f1_title_filter_drops_non_placements_and_non_tech():
    jobs = [
        {"title": "Senior Software Engineer"},          # no placement framing
        {"title": "Platform Engineer"},                 # no placement framing
        {"title": "Graduate Electronics Engineer"},     # graduate
        {"title": "HR Student Placement"},              # no tech word
        {"title": "Mechanical Design Student Placement"},
        {"title": "Software Lead Intern"},              # seniority word
        {"title": "Composite Design Engineer"},
    ]
    assert f1_title_filter(jobs) == []
    print("test_f1_title_filter_drops_non_placements_and_non_tech passed")


def test_f1_title_filter_it_keyword_needs_word_boundary():
    # "it" must not match inside "Composite"/"Recruitment".
    assert f1_title_filter([{"title": "Recruitment Student Placement"}]) == []
    print("test_f1_title_filter_it_keyword_needs_word_boundary passed")


if __name__ == "__main__":
    test_f1_title_filter_keeps_tech_placements()
    test_f1_title_filter_drops_non_placements_and_non_tech()
    test_f1_title_filter_it_keyword_needs_word_boundary()
    print("all f1 tests passed")
