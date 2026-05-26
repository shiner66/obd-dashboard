"""
Autonomous trip correlation & merging.

Handles three reconciliation problems automatically:

1. OBD-to-OBD chain merge — CarScanner sometimes splits a single real-world
   journey into multiple CSV files (adapter dropout, brief engine restart at
   a gas pump). Consecutive OBD trips within MERGE_GAP_S are merged.

2. OBD-to-MyOpel correlation — Stellantis and CarScanner report the same
   trip independently. We match them on a weighted score (start time +
   distance + duration), then enrich the OBD entry with MyOpel fields
   (fuel cost, alerts, service countdown) and drop the standalone MyOpel.

3. MyOpel-to-MyOpel deduplication — Stellantis occasionally re-issues the
   same trip with a new ID after retroactive updates. Same-day myop trips
   with near-identical start time and distance are deduped.

All operations are idempotent — re-running auto_correlate_all() on a clean
database is a no-op.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from .. import database as db

log = logging.getLogger(__name__)

# Tunables — chosen from real-world data, not arbitrary.
MERGE_GAP_S        = 300       # OBD-OBD chain: 5 min between end of A and start of B
MATCH_TIME_WINDOW_S = 1200     # OBD-MyOpel: ±20 min start time
MATCH_DISTANCE_TOL  = 0.30     # OBD-MyOpel: ±30% distance
MIN_MATCH_SCORE     = 0.55     # Below this, refuse to correlate
DEDUPE_TIME_S       = 600      # MyOpel-MyOpel dedupe: ±10 min start
DEDUPE_DISTANCE_TOL = 0.10     # MyOpel-MyOpel dedupe: ±10% distance


# ── Datetime parsing ──────────────────────────────────────────────────────────

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 4 if "%f" in fmt else 19], fmt)
        except ValueError:
            continue
    return None


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(a: dict, b: dict) -> float:
    """Weighted similarity score in [0, 1]. Higher = more likely the same trip.

    Hard reject (score 0) if start times differ by more than MATCH_TIME_WINDOW_S
    or distances disagree by more than MATCH_DISTANCE_TOL.
    """
    t_a = _parse_dt(a.get("start"))
    t_b = _parse_dt(b.get("start"))
    if not t_a or not t_b:
        return 0.0

    dt = abs((t_a - t_b).total_seconds())
    if dt > MATCH_TIME_WINDOW_S:
        return 0.0
    time_score = 1.0 - dt / MATCH_TIME_WINDOW_S

    d_a = a.get("distanceKm") or 0
    d_b = b.get("distanceKm") or 0
    if d_a > 0 and d_b > 0:
        ratio = abs(d_a - d_b) / max(d_a, d_b)
        if ratio > MATCH_DISTANCE_TOL:
            return 0.0
        dist_score = 1.0 - ratio / MATCH_DISTANCE_TOL
    else:
        dist_score = 0.5  # no info — neutral

    dur_a = a.get("durationMin") or 0
    dur_b = b.get("durationMin") or 0
    if dur_a > 0 and dur_b > 0:
        ratio = abs(dur_a - dur_b) / max(dur_a, dur_b)
        dur_score = max(0.0, 1.0 - ratio)
    else:
        dur_score = 0.5

    # Time is the strongest signal; distance is a strong tiebreaker.
    return 0.5 * time_score + 0.35 * dist_score + 0.15 * dur_score


def _best_match(target: dict, candidates: Iterable[dict]) -> tuple[dict | None, float]:
    """Return (best_trip, score) or (None, 0) if no candidate clears MIN_MATCH_SCORE."""
    best, best_score = None, 0.0
    for c in candidates:
        s = _score(target, c)
        if s > best_score:
            best, best_score = c, s
    if best_score < MIN_MATCH_SCORE:
        return None, 0.0
    return best, best_score


# ── OBD-to-OBD chain detection ────────────────────────────────────────────────

def _detect_obd_chains(obd_trips: list[dict]) -> list[list[str]]:
    """Group OBD trips into chains where each consecutive pair has a small gap.

    Returns list of chains, each chain is a list of trip IDs (in start order).
    Singleton chains are omitted — only multi-trip chains are returned.
    """
    sorted_trips = sorted(obd_trips, key=lambda t: t.get("start") or "")
    chains: list[list[str]] = []
    current: list[dict] = []

    for trip in sorted_trips:
        if not current:
            current = [trip]
            continue
        prev = current[-1]
        end_dt   = _parse_dt(prev.get("end"))
        start_dt = _parse_dt(trip.get("start"))
        if end_dt and start_dt:
            gap = (start_dt - end_dt).total_seconds()
        else:
            gap = float("inf")

        if 0 <= gap <= MERGE_GAP_S:
            current.append(trip)
        else:
            if len(current) > 1:
                chains.append([t["id"] for t in current])
            current = [trip]

    if len(current) > 1:
        chains.append([t["id"] for t in current])

    return chains


# ── Public entry point ────────────────────────────────────────────────────────

def auto_correlate_all() -> dict:
    """Reconcile all trips in the DB. Returns operation counts.

    Idempotent — safe to call after every upload and at startup.
    """
    trips = db.get_all_trips()
    obd_trips  = [t for t in trips if "obd"    in t.get("sources", [])]
    myop_trips = [t for t in trips if "myopel" in t.get("sources", [])
                  and "obd" not in t.get("sources", [])]

    counts = {"obd_chains_merged": 0, "obd_trips_absorbed": 0,
              "myop_correlated": 0, "myop_duplicates_removed": 0}

    # 1. OBD-to-OBD chain merge
    chains = _detect_obd_chains(obd_trips)
    for chain in chains:
        primary, *rest = chain
        log.info("Auto-merging OBD chain: %s ← %s", primary, rest)
        db.merge_trips(primary, rest)
        counts["obd_chains_merged"] += 1
        counts["obd_trips_absorbed"] += len(rest)

    # Refresh after merges — IDs of absorbed trips are gone
    if chains:
        trips = db.get_all_trips()
        obd_trips  = [t for t in trips if "obd"    in t.get("sources", [])]
        myop_trips = [t for t in trips if "myopel" in t.get("sources", [])
                      and "obd" not in t.get("sources", [])]

    # 2. OBD-to-MyOpel correlation (orphan myop → matching OBD)
    available_obd = [t for t in obd_trips if "myopel" not in t.get("sources", [])]
    for myop in myop_trips:
        match, score = _best_match(myop, available_obd)
        if match:
            log.info("Auto-correlating myop %s → OBD %s (score=%.2f)",
                     myop["id"], match["id"], score)
            db.enrich_with_myop(match["id"], myop)
            db.delete_trip(myop["id"])
            available_obd = [t for t in available_obd if t["id"] != match["id"]]
            counts["myop_correlated"] += 1

    # 3. MyOpel-to-MyOpel dedupe (same trip resent with new ID)
    trips = db.get_all_trips()
    myop_only = sorted(
        [t for t in trips if "myopel" in t.get("sources", [])
         and "obd" not in t.get("sources", [])],
        key=lambda t: t.get("start") or "",
    )
    keep: list[dict] = []
    for trip in myop_only:
        is_dup = False
        for kept in keep:
            t_k = _parse_dt(kept.get("start"))
            t_t = _parse_dt(trip.get("start"))
            if not t_k or not t_t:
                continue
            if abs((t_k - t_t).total_seconds()) > DEDUPE_TIME_S:
                continue
            d_k = kept.get("distanceKm") or 0
            d_t = trip.get("distanceKm") or 0
            if d_k > 0 and d_t > 0:
                ratio = abs(d_k - d_t) / max(d_k, d_t)
                if ratio > DEDUPE_DISTANCE_TOL:
                    continue
            is_dup = True
            break
        if is_dup:
            log.info("Removing duplicate myop trip %s (matches %s)", trip["id"], keep[-1]["id"])
            db.delete_trip(trip["id"])
            counts["myop_duplicates_removed"] += 1
        else:
            keep.append(trip)

    if any(counts.values()):
        log.info("auto_correlate_all summary: %s", counts)
    return counts
