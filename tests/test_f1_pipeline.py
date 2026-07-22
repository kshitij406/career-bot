"""F1 pipeline tests. Run via pytest, or directly: python tests/test_f1_pipeline.py

No network needed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.f1_scan import f1_title_filter


def test_f1_title_filter_keeps_software_roles():
    jobs = [
        {"title": "Software Engineer Intern"},
        {"title": "IT Support Technician"},
        {"title": "Data Platform Engineer"},
        {"title": ".NET Developer"},
    ]
    kept = f1_title_filter(jobs)
    assert [j["title"] for j in kept] == [j["title"] for j in jobs]
    print("test_f1_title_filter_keeps_software_roles passed")


def test_f1_title_filter_drops_non_software_and_excluded():
    jobs = [
        {"title": "Head Mechanic"},
        {"title": "Composite Technician"},
        {"title": "Aerodynamicist"},
        {"title": "Trackside Systems Support"},  # has "systems" but not an include keyword
        {"title": "Hospitality Coordinator"},
        {"title": "Finance Analyst"},
        {"title": "Marketing Manager"},
    ]
    assert f1_title_filter(jobs) == []
    print("test_f1_title_filter_drops_non_software_and_excluded passed")


def test_f1_title_filter_excluded_word_allowed_alongside_software():
    jobs = [{"title": "Trackside Software Engineer"}]
    kept = f1_title_filter(jobs)
    assert [j["title"] for j in kept] == ["Trackside Software Engineer"]
    print("test_f1_title_filter_excluded_word_allowed_alongside_software passed")


def test_f1_title_filter_it_keyword_needs_word_boundary():
    # "it" must not match inside "Composite" (which is excluded anyway) or
    # any other word containing the letters "it".
    jobs = [{"title": "Composite Design Engineer"}]  # "engineer" makes this pass the include
    # gate, but "composite" is an exclude keyword and "software" isn't present.
    assert f1_title_filter(jobs) == []
    print("test_f1_title_filter_it_keyword_needs_word_boundary passed")


if __name__ == "__main__":
    test_f1_title_filter_keeps_software_roles()
    test_f1_title_filter_drops_non_software_and_excluded()
    test_f1_title_filter_excluded_word_allowed_alongside_software()
    test_f1_title_filter_it_keyword_needs_word_boundary()
    print("all f1 tests passed")
