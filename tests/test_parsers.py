"""Parsers + prefilter, run against the fixtures in their native ATS shapes.

No network, no API key. This is the suite that catches the two bugs that cost
me an evening each: Lever's epoch-milliseconds timestamps, and a bare `sde`
regex that silently matches nothing.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hirepilot import mock
from hirepilot.fetch import parse_ashby, parse_greenhouse, parse_lever, strip_html
from hirepilot.mock import fetch_all_mock
from hirepilot.prefilter import prefilter

CONFIG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml")
                        .read_text(encoding="utf-8"))
FILTERS = CONFIG["filters"]


# ------------------------------------------------------------- strip_html ---

def test_strip_html_unescapes_twice():
    """Greenhouse ships HTML-entity-escaped HTML: unescape, strip, unescape."""
    raw = "&lt;p&gt;Go &amp;amp; Java&lt;/p&gt;"
    assert strip_html(raw) == "Go & Java"


def test_strip_html_turns_block_tags_into_newlines():
    out = strip_html("<p>One</p><p>Two</p><ul><li>a</li><li>b</li></ul>")
    assert "One" in out and "Two" in out and "a" in out and "b" in out
    assert "<" not in out


def test_strip_html_handles_none_and_empty():
    assert strip_html(None) == ""
    assert strip_html("") == ""


# ---------------------------------------------------------------- parsers ---

def test_greenhouse_maps_every_field():
    jobs = parse_greenhouse("acme-edge", "Acme Edge", mock.GREENHOUSE["acme-edge"])
    j = next(j for j in jobs if j.title.startswith("Software Engineer II"))
    assert j.job_id == "greenhouse:acme-edge:5501001"
    assert j.ats == "greenhouse"
    assert j.company == "Acme Edge"
    assert j.location == "Bangalore, India"
    assert j.url.startswith("https://boards.greenhouse.io/")
    assert "distributed services" in j.description


def test_lever_concatenates_description_lists_and_additional():
    """The requirements live in lists[], not descriptionPlain. Drop the
    concatenation and every Lever job looks unqualified."""
    jobs = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    j = next(j for j in jobs if j.title == "Backend Engineer (Go)")
    assert "market data pipeline" in j.description      # descriptionPlain
    assert "Requirements" in j.description              # lists[].text
    assert "2-5 years backend experience" in j.description  # lists[].content
    assert "No take-home" in j.description              # additionalPlain


def test_lever_createdAt_is_epoch_milliseconds():
    """1.7e12 is milliseconds. Reading it as seconds dates the post to 1970
    and the freshness filter eats the whole board without a word."""
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    jobs = parse_lever("quantstack", "QuantStack", mock.LEVER["quantstack"])
    j = next(j for j in jobs if j.title == "Backend Engineer (Go)")
    assert j.posted_at == two_days_ago.isoformat()


def test_ashby_skips_unlisted_drafts():
    jobs = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])
    assert all("unlisted" not in j.url for j in jobs)
    assert len(jobs) == 2   # 3 postings, one isListed: false


def test_ashby_reads_compensation_and_html_fallback():
    jobs = parse_ashby("helioscale", "Helioscale", mock.ASHBY["helioscale"])
    networking = next(j for j in jobs if j.title == "Software Engineer, Networking")
    assert networking.salary == "₹32L – ₹48L"
    ds = next(j for j in jobs if j.title == "Data Scientist, Growth")
    assert "Causal inference" in ds.description   # descriptionHtml fallback


def test_job_ids_are_globally_unique_and_namespaced():
    jobs = fetch_all_mock()
    ids = [j.job_id for j in jobs]
    assert len(ids) == len(set(ids))
    assert all(re.match(r"^(greenhouse|lever|ashby):[^:]+:.+$", i) for i in ids)


def test_parsers_take_decoded_json_not_a_response():
    """Parsers are pure: body in, list[Job] out. That is what makes --mock
    exercise the real code path instead of a second implementation."""
    assert parse_greenhouse("x", "X", {}) == []
    assert parse_lever("x", "X", []) == []
    assert parse_ashby("x", "X", {}) == []


# -------------------------------------------------------------- prefilter ---

@pytest.mark.parametrize("title", [
    "Software Engineer II, Distributed Systems",
    "Software Development Engineer, Core Infra",
    "Backend Engineer (Go)",
    "Site Reliability Engineer",
    "SDE II",
])
def test_include_titles_match_real_titles(title):
    inc = FILTERS["include_titles"]
    assert any(re.search(p, title, re.I) for p in inc), title


def test_bare_sde_regex_does_not_match_the_spelled_out_title():
    """The bug: `sde` looks like it covers "Software Development Engineer".
    It does not — they share no substring. \\bsde\\b plus the spelled-out
    variant is why both titles survive the filter."""
    assert not re.search(r"\bsde\b", "Software Development Engineer", re.I)
    assert re.search(r"\bsde\b", "SDE II", re.I)
    inc = FILTERS["include_titles"]
    assert any(re.search(p, "Software Development Engineer, Core Infra", re.I) for p in inc)


@pytest.mark.parametrize("title", [
    "Staff Software Engineer, Storage",       # too senior
    "Engineering Manager, Platform",          # management track
    "Enterprise Account Executive",           # wrong function
    "Frontend Engineer, Design Systems",      # wrong discipline
    "Data Scientist, Growth",                 # wrong discipline
])
def test_junk_titles_are_rejected(title):
    inc, exc = FILTERS["include_titles"], FILTERS["exclude_titles"]
    included = any(re.search(p, title, re.I) for p in inc)
    excluded = any(re.search(p, title, re.I) for p in exc)
    assert excluded or not included, f"{title!r} would have survived"


def test_full_mock_funnel_keeps_only_the_five_real_matches():
    kept = prefilter(fetch_all_mock(), FILTERS)
    titles = sorted(j.title for j in kept)
    assert titles == [
        "Backend Engineer (Go)",
        "Site Reliability Engineer",
        "Software Development Engineer, Core Infra",
        "Software Engineer II, Distributed Systems",
        "Software Engineer, Networking",
    ]


def test_stale_posting_is_dropped_by_freshness_gate():
    kept = prefilter(fetch_all_mock(), FILTERS)
    assert not any("Senior Software Engineer, Platform" == j.title for j in kept)


def test_wrong_city_dropped_but_remote_kept():
    kept = prefilter(fetch_all_mock(), FILTERS)
    assert not any("San Francisco" in (j.location or "") for j in kept)
    assert any("Remote" in (j.location or "") for j in kept)


def test_allow_remote_is_what_lets_an_out_of_region_remote_role_through():
    """"Remote (India)" already matches the `india` location, so it is the
    wrong fixture for this. Use a remote role that names no allowed city."""
    from hirepilot.fetch import Job
    remote = Job(job_id="lever:x:1", ats="lever", company="X",
                 title="Backend Engineer", location="Remote - Global",
                 url="https://example.com", description="Go")

    kept_on = prefilter([remote], dict(FILTERS, allow_remote=True))
    kept_off = prefilter([remote], dict(FILTERS, allow_remote=False))

    assert len(kept_on) == 1
    assert kept_off == []


def test_empty_filters_keep_everything():
    jobs = fetch_all_mock()
    assert len(prefilter(jobs, {})) == len(jobs)


def test_target_locations_matching():
    """Verify Bangalore, Hyderabad, Raipur, and Remote India matching."""
    from hirepilot.fetch import Job
    j_bgr = Job(job_id="g:1", ats="greenhouse", company="A", title="Software Engineer", location="Bengaluru, Karnataka", url="u1", description="d")
    j_hyd = Job(job_id="g:2", ats="greenhouse", company="B", title="Software Engineer", location="Hyderabad, Telangana", url="u2", description="d")
    j_rpr = Job(job_id="g:3", ats="greenhouse", company="C", title="Software Engineer", location="Raipur, Chhattisgarh", url="u3", description="d")
    j_rem = Job(job_id="g:4", ats="greenhouse", company="D", title="Software Engineer", location="Work from home, India", url="u4", description="d")
    j_bad = Job(job_id="g:5", ats="greenhouse", company="E", title="Software Engineer", location="London, UK", url="u5", description="d")

    cfg = {"target_locations": {"primary": ["bangalore", "bengaluru", "hyderabad"], "secondary": ["raipur"], "allow_remote": True}}
    kept = prefilter([j_bgr, j_hyd, j_rpr, j_rem, j_bad], cfg)

    kept_ids = {j.job_id for j in kept}
    assert "g:1" in kept_ids
    assert "g:2" in kept_ids
    assert "g:3" in kept_ids
    assert "g:4" in kept_ids
    assert "g:5" not in kept_ids


def test_deduplication():
    """Verify multi-source deduplication by URL and company+title+location key."""
    from hirepilot.fetch import Job
    from hirepilot.prefilter import deduplicate

    j1 = Job(job_id="g:1", ats="greenhouse", company="Acme", title="Software Engineer", location="Bangalore", url="https://example.com/job1", description="d")
    j2 = Job(job_id="l:1", ats="lever", company="Acme", title="Software Engineer", location="Bangalore", url="https://example.com/job1", description="d")
    j3 = Job(job_id="a:1", ats="ashby", company="Beta", title="Software Engineer", location="Hyderabad", url="https://example.com/job2", description="d")

    unique, dropped = deduplicate([j1, j2, j3])
    assert len(unique) == 2
    assert dropped == 1


def test_wellfound_parser():
    """Verify Wellfound marketplace parser compatibility."""
    from hirepilot.fetch import parse_wellfound
    payload = {"jobs": [{"id": "wf1", "company_name": "AI Startup", "title": "AI Engineer Intern", "location": "Bangalore", "description": "<p>Build LLM agents.</p>", "url": "https://wellfound.com/jobs/wf1"}]}
    jobs = parse_wellfound("ai-startup", "AI Startup", payload)
    assert len(jobs) == 1
    assert jobs[0].ats == "wellfound"
    assert jobs[0].source_type == "marketplace"
    assert jobs[0].company == "AI Startup"
    assert "LLM agents" in jobs[0].description

