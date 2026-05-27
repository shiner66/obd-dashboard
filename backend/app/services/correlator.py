"""
Autonomous trip correlation & merging.

Handles four reconciliation problems automatically:

1. OBD-to-OBD overlap dedup — CarScanner reconnects mid-journey and creates a
   new CSV that starts inside an already-running trip.  The recording with fewer
   PIDs is discarded; the richer one (more PIDs, or longer if tied) is kept.

2. OBD-to-OBD chain merge — CarScanner sometimes splits a single real-world
   journey into multiple CSV files (adapter dropout, brief engine restart at
   a gas pump). Consecutive OBD trips within MERGE_GAP_S are merged.

3. OBD-to-MyOpel correlation — Stellantis and CarScanner report the same
   trip independently. We match them on a weighted score (start time +
   distance + duration), then enrich the OBD entry with MyOpel fields
   (fuel cost, alerts, service countdown) and drop the standalone MyOpel.

4. MyOpel-to-MyOpel deduplication — Stellantis occasionally re-issues the
   same trip with a new ID after retroactive updates. Same-day myop trips
   with near-identical start time and distance are deduped.

All operations are idempotent — re-running auto_correlate_all() on a clean
database is a no-op.

Timestamp note — Stellantis raw timestamps:
  Most trips are stored as Italian local time with a spurious Z suffix (Group A).
  Occasional trips are 1 h ahead of true local time due to a Stellantis DST
  double-application bug (Group B).  The parser strips the Z; the scoring
  function tries both raw and DST-adjusted (raw − 1 h) times and picks the
  better time_score, so both groups match correctly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from .. import database as db

log = logging.getLogger(__name__)

_ROME = ZoneInfo("Europe/Rome")

# Tunables — chosen from real-world data, not arbitrary.
MERGE_GAP_S        = 300       # OBD-OBD chain: 5 min between end of A and start of B
MATCH_TIME_WINDOW_S = 3600     # OBD-MyOpel: ±60 min (OBD starts at engine-on, MyOpel at first movement)
MATCH_DISTANCE_TOL  = 0.30     # OBD-MyOpel: ±30% distance
MIN_MATCH_SCORE     = 0.50     # Below this, refuse to correlate
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


def _dst_offset(dt: datetime) -> timedelta:
    """DST offset for Europe/Rome at the given naive local datetime."""
    dst = dt.replace(tzinfo=_ROME).dst()
    return dst if dst else timedelta(0)


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(a: dict, b: dict) -> float:
    """Weighted similarity score in [0, 1]. Higher = more likely the same trip.

    Hard reject (score 0) if distances disagree by more than MATCH_DISTANCE_TOL.
    For time, we try both the raw MyOpel timestamp and the DST-adjusted version
    (raw − 1 h) and take the better time_score.  This handles:
      - Group A trips (majority): raw timestamp is already correct Italian local
      - Group B trips: Stellantis applied DST twice → raw is 1 h ahead of true local
    """
    t_a = _parse_dt(a.get("start"))
    t_b = _parse_dt(b.get("start"))
    if not t_a or not t_b:
        return 0.0

    def _time_score(t1: datetime, t2: datetime) -> float:
        dt = abs((t1 - t2).total_seconds())
        if dt > MATCH_TIME_WINDOW_S:
            return 0.0
        return 1.0 - dt / MATCH_TIME_WINDOW_S

    # For Group B Stellantis trips the raw timestamp is 1 h ahead of true local.
    # DST-adjustment (raw − 1 h) gives a much higher time_score in those cases.
    # We apply the adjustment only when it improves the score by ≥ 0.30 AND the
    # raw score is below 0.90 (i.e. raw was not already a strong match).
    # The time window is a hard constraint: if the chosen score is 0, reject.
    ts_raw     = _time_score(t_a, t_b)
    ts_dst_adj = _time_score(t_a, t_b - _dst_offset(t_b))
    if (ts_dst_adj - ts_raw) >= 0.30 and ts_raw < 0.90:
        time_score = ts_dst_adj
    else:
        time_score = ts_raw
    if time_score == 0.0:
        return 0.0  # outside time window — hard reject

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

    # Distance is the primary signal (OBD and MyOpel measure the same km).
    # Time is secondary — OBD starts at engine-on, MyOpel at first movement,
    # so up to an hour of gap is normal.  Duration is a weak signal
    # (OBD = engine-on time, MyOpel = travel time) so weight it lightly.
    return 0.40 * time_score + 0.50 * dist_score + 0.10 * dur_score


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

    counts = {"obd_overlaps_removed": 0, "obd_chains_merged": 0, "obd_trips_absorbed": 0,
              "myop_correlated": 0, "myop_duplicates_removed": 0,
              "myop_recorrelated": 0}

    # ── Step 0.5: remove overlapping OBD sub-recordings ──────────────────────
    # CarScanner creates a new CSV when the OBD adapter reconnects mid-journey.
    # The new file starts inside an already-running trip (overlap).
    # We keep whichever recording is richer (more PIDs; tie-break: longer duration).
    sorted_obd = sorted(obd_trips, key=lambda t: t.get("start") or "")
    to_remove_ids: set[str] = set()
    for i, trip_a in enumerate(sorted_obd):
        if trip_a["id"] in to_remove_ids:
            continue
        end_a = _parse_dt(trip_a.get("end"))
        if not end_a:
            continue
        for trip_b in sorted_obd[i + 1:]:
            if trip_b["id"] in to_remove_ids:
                continue
            start_b = _parse_dt(trip_b.get("start"))
            if not start_b:
                continue
            if start_b >= end_a:
                break  # sorted by start — no more overlaps possible
            # trip_b starts before trip_a ends — overlapping recording.
            # Keep whichever has more PIDs (richer data); if tied, keep the longer one.
            pids_a = len(trip_a.get("pidCatalog") or [])
            pids_b = len(trip_b.get("pidCatalog") or [])
            dur_a  = trip_a.get("durationMin") or 0
            dur_b  = trip_b.get("durationMin") or 0
            if pids_b > pids_a or (pids_b == pids_a and dur_b > dur_a):
                to_remove_ids.add(trip_a["id"])
                log.info("Removing overlapping OBD sub-recording %s (%d PIDs) in favour of %s (%d PIDs)",
                         trip_a["id"], pids_a, trip_b["id"], pids_b)
                break  # trip_a is gone; outer loop will process trip_b as primary
            else:
                to_remove_ids.add(trip_b["id"])
                log.info("Removing overlapping OBD sub-recording %s (%d PIDs) in favour of %s (%d PIDs)",
                         trip_b["id"], pids_b, trip_a["id"], pids_a)

    for tid in to_remove_ids:
        db.delete_trip(tid)
    counts["obd_overlaps_removed"] += len(to_remove_ids)

    if to_remove_ids:
        trips = db.get_all_trips()
        obd_trips  = [t for t in trips if "obd"    in t.get("sources", [])]
        myop_trips = [t for t in trips if "myopel" in t.get("sources", [])
                      and "obd" not in t.get("sources", [])]

    # 1. OBD-to-OBD chain merge
    chains = _detect_obd_chains(obd_trips)
    for chain in chains:
        primary, *rest = chain
        log.info("Auto-merging OBD chain: %s ← %s", primary, rest)
        db.merge_trips(primary, rest)
        counts["obd_chains_merged"] += 1
        counts["obd_trips_absorbed"] += len(rest)

    # Refresh after merges
    if chains:
        trips = db.get_all_trips()
        obd_trips  = [t for t in trips if "obd"    in t.get("sources", [])]
        myop_trips = [t for t in trips if "myopel" in t.get("sources", [])
                      and "obd" not in t.get("sources", [])]

    # 2. OBD-to-MyOpel correlation
    # First pass: match standalone MyOpel against uncorrelated OBD trips.
    # Skip OBD trips with no meaningful distance — they are artefacts.
    unmatched_obd = [t for t in obd_trips
                     if "myopel" not in t.get("sources", [])
                     and (t.get("distanceKm") or 0) >= 1.0]
    matched_myop_ids: set[str] = set()

    for myop in myop_trips:
        match, score = _best_match(myop, unmatched_obd)
        if match:
            log.info("Correlating myop %s → OBD %s (score=%.2f)", myop["id"], match["id"], score)
            db.enrich_with_myop(match["id"], myop)
            db.delete_trip(myop["id"])
            unmatched_obd = [t for t in unmatched_obd if t["id"] != match["id"]]
            matched_myop_ids.add(myop["id"])
            counts["myop_correlated"] += 1

    # Second pass: standalone MyOpel trips still unmatched may have a better
    # match with an already-correlated OBD trip (wrong previous correlation,
    # e.g. established before a DST fix).  Use a higher score threshold (0.75)
    # to avoid breaking good existing correlations.
    remaining_myop = [m for m in myop_trips if m["id"] not in matched_myop_ids]
    already_correlated_obd = [t for t in obd_trips if "myopel" in t.get("sources", [])]

    for myop in remaining_myop:
        match, score = _best_match(myop, already_correlated_obd)
        if match and score >= 0.75:
            log.info(
                "Re-correlating: myop %s → OBD %s (score=%.2f, replacing existing myop)",
                myop["id"], match["id"], score,
            )
            db.enrich_with_myop(match["id"], myop)
            db.delete_trip(myop["id"])
            already_correlated_obd = [t for t in already_correlated_obd if t["id"] != match["id"]]
            counts["myop_recorrelated"] += 1

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
