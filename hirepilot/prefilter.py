"""Deterministic filter that runs BEFORE any LLM call.

This is the whole cost story: ~2000 raw jobs -> ~40 candidates for ~0 rupees,
so Claude only ever reads jobs that already passed title + location + freshness.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .fetch import Job

REMOTE_HINTS = ("remote", "anywhere", "work from home", "wfh", "distributed", "india")


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def deduplicate(jobs: list[Job]) -> tuple[list[Job], int]:
    """Deduplicate jobs across multiple sources by canonical URL, ID, or title+company key."""
    seen_urls, seen_ids, seen_keys = set(), set(), set()
    out = []
    dropped = 0

    for j in jobs:
        url_key = j.url.strip().lower() if j.url else None
        id_key = j.job_id
        comp_key = f"{j.company.strip().lower()}:{j.title.strip().lower()}:{j.location.strip().lower()}"

        if (url_key and url_key in seen_urls) or (id_key in seen_ids) or (comp_key in seen_keys):
            dropped += 1
            continue

        if url_key:
            seen_urls.add(url_key)
        seen_ids.add(id_key)
        seen_keys.add(comp_key)
        out.append(j)

    return out, dropped


def prefilter(jobs: list[Job], cfg: dict) -> list[Job]:
    filters = cfg.get("filters", cfg) or {}
    inc = filters.get("include_titles") or [r"."]
    exc = filters.get("exclude_titles") or []

    # Gather target locations from filters.locations or top-level target_locations
    locs = [l.lower() for l in (filters.get("locations") or [])]
    target_loc_cfg = cfg.get("target_locations") or {}
    if isinstance(target_loc_cfg, dict):
        for k in ("primary", "secondary", "other_india"):
            for l in target_loc_cfg.get(k) or []:
                if l.lower() not in locs:
                    locs.append(l.lower())
        allow_remote = bool(target_loc_cfg.get("allow_remote", filters.get("allow_remote", True)))
    else:
        allow_remote = bool(filters.get("allow_remote", True))

    max_age = filters.get("max_age_days", cfg.get("max_age_days"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age else None

    kept, stats = [], {"title": 0, "location": 0, "age": 0}
    for j in jobs:
        if not _any_match(inc, j.title) or (exc and _any_match(exc, j.title)):
            stats["title"] += 1
            continue

        if locs:
            hay = f"{j.location} {j.title}".lower()
            is_remote = allow_remote and any(h in hay for h in REMOTE_HINTS)
            if not is_remote and not any(l in hay for l in locs):
                stats["location"] += 1
                continue

        if cutoff:
            posted = _parse_date(j.posted_at)
            if posted and posted < cutoff:
                stats["age"] += 1
                continue

        kept.append(j)

    print(f"  prefilter: {len(jobs)} -> {len(kept)} "
          f"(dropped title={stats['title']} location={stats['location']} stale={stats['age']})")
    return kept
