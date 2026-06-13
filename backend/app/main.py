"""FastAPI application — OBD Trip Platform backend."""
from __future__ import annotations
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import database as db
from .parsers import csv_parser, myop_parser
from .services import correlator as corr_svc
from .services import insights as insight_svc
from .services.watcher import Watcher

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

OBD_FILES_DIR   = Path(os.getenv("OBD_FILES_DIR",   "/data/obd"))
MYOP_FILES_DIR  = Path(os.getenv("MYOP_FILES_DIR",  "/data/myop"))
DB_PATH         = Path(os.getenv("DB_PATH",         "/data/db/trips.db"))
VEHICLE_NAME    = os.getenv("VEHICLE_NAME",    "Opel Corsa F Elegance")
VEHICLE_ECU     = os.getenv("VEHICLE_ECU",     "MD1CS003 — 1.5d BlueHDi")
VEHICLE_ADAPTER = os.getenv("VEHICLE_ADAPTER", "BTLE IOS-Vlink")

_watcher = Watcher()

# During the initial directory scan we save every file first and reconcile once
# at the end. Without this, each of ~100+ files would trigger a full O(n)
# correlation + insight recompute, making a cold re-ingest O(n²) and slow.
_bulk_loading = False


# ── File processing ───────────────────────────────────────────────────────────
# Files are persisted as-is. Cross-trip reconciliation (OBD chain merge,
# OBD↔MyOpel correlation, MyOpel dedupe) is delegated to corr_svc and runs
# after every batch — see _post_process().

def _process_obd_file(path: Path) -> list[str]:
    """Parse an OBD CSV/BRC file, save new trips, run per-trip insights. Returns new trip IDs."""
    # Skip the full CSV parse when this file's trip is already in the DB. The
    # trip id is derived purely from the filename, so on warm restarts (and for
    # the space/underscore duplicate files) we avoid re-reading thousands of rows.
    expected_id = csv_parser.trip_id_for_file(path)
    if expected_id and db.trip_exists(expected_id):
        return []

    trips = csv_parser.parse_file(path)
    # During bulk load, insights are recomputed once at the end — skip the
    # per-file context build (which would scan the whole DB on every file).
    ctx = None if _bulk_loading else insight_svc.build_context(db.get_all_trips())
    new_ids: list[str] = []
    for trip in trips:
        if db.trip_exists(trip["id"]):
            log.info("Trip %s already in DB, skipping", trip["id"])
            continue
        trip["insights"] = [] if _bulk_loading else insight_svc.per_trip(trip, ctx)
        db.save_trip(trip)
        new_ids.append(trip["id"])
        log.info("Saved OBD trip %s (%.1f km)", trip["id"], trip.get("distanceKm") or 0)
    if new_ids and not _bulk_loading:
        _post_process()
    return new_ids


def _process_myop_file(path: Path) -> list[str]:
    """Parse a .myop file, save new trips. Returns new trip IDs."""
    trips = myop_parser.parse_file(path)
    new_ids: list[str] = []
    for trip in trips:
        if db.trip_exists(trip["id"]):
            continue
        db.save_trip(trip)
        new_ids.append(trip["id"])
        log.info("Saved myop trip %s", trip["id"])
    if new_ids and not _bulk_loading:
        _post_process()
    return new_ids


def _post_process() -> None:
    """Run autonomous correlation + refresh insights. Safe to call repeatedly."""
    try:
        counts = corr_svc.auto_correlate_all()
    except Exception:
        log.exception("auto_correlate_all failed")
        counts = {}
    # Recompute per-trip insights when correlation actually changed something
    # (merges can absorb fields that affect rules like soot threshold or EGT).
    if any(counts.values()):
        _recompute_all_insights()
    _refresh_cross_trip_insights()


def _scan_directory(directory: Path, process_fn, extensions: tuple[str, ...]) -> None:
    """Scan a directory for existing files not yet in the DB."""
    if not directory.exists():
        return
    for f in sorted(directory.iterdir()):
        if f.suffix.lower() in extensions:
            try:
                process_fn(f)
            except Exception:
                log.exception("Error scanning %s", f)


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bulk_loading
    db.init(DB_PATH)
    log.info("Database initialised at %s", DB_PATH)

    # Bulk mode: save all files first, reconcile once at the end (avoids O(n²)).
    _bulk_loading = True
    _scan_directory(OBD_FILES_DIR,  _process_obd_file,  (".csv", ".brc"))
    _scan_directory(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
    _bulk_loading = False

    # Single reconciliation pass after the initial scan — chains, overlaps,
    # OBD↔MyOpel grouping and dedupe, all at once.
    try:
        corr_svc.auto_correlate_all()
    except Exception:
        log.exception("Startup auto_correlate_all failed")

    _watcher.watch(OBD_FILES_DIR,  _process_obd_file,  (".csv", ".brc"))
    _watcher.watch(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
    _watcher.start()

    _recompute_all_insights()
    _refresh_cross_trip_insights()

    yield

    _watcher.stop()


def _recompute_all_insights() -> None:
    try:
        trips = db.get_all_trips()
        ctx = insight_svc.build_context(trips)
        updated = 0
        for trip in trips:
            if "obd" not in trip.get("sources", []):
                continue
            db.update_insights(trip["id"], insight_svc.per_trip(trip, ctx))
            updated += 1
        log.info("Recomputed per-trip insights for %d OBD trips", updated)
    except Exception:
        log.exception("Per-trip insight recompute failed")


def _refresh_cross_trip_insights() -> None:
    try:
        trips = db.get_all_trips()
        if trips:
            ct = insight_svc.cross_trip(trips)
            log.info("Generated %d cross-trip insights", len(ct))
            # Store on a synthetic key in the app state (returned via data.js)
            app.state.trend_insights = ct
        else:
            app.state.trend_insights = []
    except Exception:
        log.exception("Cross-trip insight generation failed")
        app.state.trend_insights = []


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="OBD Trip Platform", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── data.js endpoint ──────────────────────────────────────────────────────────

def _build_vehicle(trips: list[dict]) -> dict:
    """Build VEHICLE global from latest trip data."""
    obd_trips = [t for t in trips if "obd" in t.get("sources", [])]
    myop_trips = [t for t in trips if "myopel" in t.get("sources", [])]

    latest_obd  = obd_trips[0]  if obd_trips  else {}
    latest_myop = myop_trips[0] if myop_trips else {}
    latest      = trips[0]      if trips       else {}

    # Prefer myop data for service/fuel; OBD for DPF/battery
    fuel_level    = latest_myop.get("fuelLevel")    or latest_obd.get("fuelLevel")
    fuel_autonomy = latest_myop.get("fuelAutonomy") or latest_obd.get("fuelAutonomy")
    days_svc      = latest_myop.get("daysToService") or latest_obd.get("daysToService")
    km_svc        = latest_myop.get("kmToService")   or latest_obd.get("kmToService")
    maint_passed  = latest_myop.get("maintenancePassed") or latest_obd.get("maintenancePassed") or False

    # VIN from any myop trip
    vin = next((t.get("vin", "") for t in myop_trips if t.get("vin")), "")

    return {
        "name":          VEHICLE_NAME,
        "ecu":           VEHICLE_ECU,
        "adapter":       VEHICLE_ADAPTER,
        "vin":           vin,
        "odometer":      latest.get("odometerKm"),
        "fuelLevel":     fuel_level,
        "fuelAutonomy":  fuel_autonomy,
        "adblueRange":   latest_obd.get("adblueRangeKm"),
        "nextService": {
            "days":   days_svc,
            "km":     km_svc,
            "passed": bool(maint_passed),
        },
        "dpfSoot":            latest_obd.get("dpfClosedSoot"),
        "dpfClosedSoot":      latest_obd.get("dpfClosedSoot"),
        "dpfAvgRegenKm":      latest_obd.get("dpfAvgRegenKm"),
        "dpfSinceRegenKm":    latest_obd.get("dpfSinceRegenKm"),
        "dpfReplaceKm":       latest_obd.get("dpfReplaceKm"),
        "dpfRegenCapability": latest_obd.get("dpfRegenCapability"),
        "dpfRegenState":      latest_obd.get("dpfRegenState"),
        "oilDilutionPct":     latest_obd.get("oilDilutionPct"),
        "battery":            latest_obd.get("batteryStartupV"),
    }


def _build_pid_catalog(trips: list[dict]) -> list[dict]:
    """Merge PID catalogs from all trips; deduplicate by slug.

    A PID is marked `useful` if it is useful in *any* trip — a signal that sits
    constant in most sessions but actually moves in a few (e.g. EGT, regen flags)
    is surfaced rather than hidden. This is the dynamic half of PID curation.
    """
    seen: dict[str, dict] = {}
    for trip in trips:
        for entry in (trip.get("pidCatalog") or []):
            slug = entry.get("slug")
            if not slug:
                continue
            if slug not in seen:
                seen[slug] = dict(entry)
            elif entry.get("useful") and not seen[slug].get("useful"):
                seen[slug] = dict(entry)        # prefer the entry that flags it useful
    return list(seen.values())


def _build_pid_groups(catalog: list[dict]) -> dict[str, list[str]]:
    """Group slugs by group name → {group: [slug, ...]}."""
    groups: dict[str, list[str]] = {}
    for entry in catalog:
        g = entry.get("group", "Other")
        groups.setdefault(g, []).append(entry["slug"])
    return groups


def _safe(obj):
    """Recursively replace NaN/Infinity floats with None so json.dumps never raises."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe(v) for v in obj]
    return obj


_EMPTY_JS = (
    "// data.js — empty fallback (backend error)\n"
    "var VEHICLE={name:'OBD Trip Platform',ecu:'',adapter:'',vin:'',"
    "odometer:null,fuelLevel:null,fuelAutonomy:null,adblueRange:null,"
    "nextService:{days:null,km:null,passed:false},"
    "dpfSoot:null,dpfAvgRegenKm:null,dpfSinceRegenKm:null,battery:null};\n"
    "var TRIPS=[];\nvar ALERTS={};\nvar TREND_INSIGHTS=[];\n"
    "var PID_CATALOG=[];\nvar PID_GROUPS={};\nvar POINTS={};\n"
)


@app.get("/api/v1/data.js", response_class=Response)
def data_js():
    try:
        trips = db.get_all_trips()
        vehicle = _safe(_build_vehicle(trips))
        catalog = _safe(_build_pid_catalog(trips))
        groups  = _safe(_build_pid_groups(catalog))
        trend_insights = _safe(getattr(app.state, "trend_insights", []))
        # data.js carries only trip *summaries*. The heavy per-trip payload —
        # pidCatalog (redundant with global), pidValues, pidSeriesFull and the
        # GPS track — is loaded lazily via /api/v1/trips/{id} (and /tracks for
        # the map) so the initial dashboard load stays small.
        _HEAVY = ("pidCatalog", "pidValues", "pidSeriesFull", "track")
        slim_trips = _safe([
            {**{k: v for k, v in t.items() if k not in _HEAVY},
             "hasTrack": bool(t.get("track")),
             "pidCount": len(t.get("pidValues") or {})}
            for t in trips
        ])

        js = (
            "// Auto-generated by OBD Trip Platform backend\n"
            f"var VEHICLE = {json.dumps(vehicle, ensure_ascii=False)};\n\n"
            f"var TRIPS = {json.dumps(slim_trips, ensure_ascii=False)};\n\n"
            f"var ALERTS = {json.dumps(myop_parser.ALERT_DICT, ensure_ascii=False)};\n\n"
            f"var TREND_INSIGHTS = {json.dumps(trend_insights, ensure_ascii=False)};\n\n"
            f"var PID_CATALOG = {json.dumps(catalog, ensure_ascii=False)};\n\n"
            f"var PID_GROUPS = {json.dumps(groups, ensure_ascii=False)};\n\n"
            "var POINTS = {};\n"
        )
        return Response(content=js, media_type="application/javascript")
    except Exception:
        log.exception("data_js serialization failed — returning empty fallback")
        return Response(content=_EMPTY_JS, media_type="application/javascript")


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/v1/trips")
def list_trips():
    return db.get_all_trips()


@app.get("/api/v1/trips/{trip_id}")
def get_trip(trip_id: str):
    trip = db.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@app.get("/api/v1/tracks")
def all_tracks():
    """All GPS tracks keyed by trip id — loaded once when the map view opens."""
    return db.get_all_tracks()


@app.post("/api/v1/upload/obd")
async def upload_obd(file: UploadFile):
    dest = OBD_FILES_DIR / file.filename
    OBD_FILES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(await file.read())
    try:
        new_ids = _process_obd_file(dest)
        return {"status": "ok", "new_trips": new_ids}
    except Exception as e:
        log.exception("Error processing uploaded OBD file")
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/v1/upload/myop")
async def upload_myop(file: UploadFile):
    dest = MYOP_FILES_DIR / file.filename
    MYOP_FILES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(await file.read())
    try:
        new_ids = _process_myop_file(dest)
        return {"status": "ok", "new_trips": new_ids}
    except Exception as e:
        log.exception("Error processing uploaded myop file")
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/v1/trips/merge")
async def merge_trips(payload: dict):
    """Merge trips: { primary_id: str, secondary_ids: [str] }"""
    primary_id = payload.get("primary_id")
    secondary_ids = payload.get("secondary_ids", [])
    if not primary_id or not secondary_ids:
        raise HTTPException(status_code=400, detail="primary_id and secondary_ids required")
    try:
        result = db.merge_trips(primary_id, secondary_ids)
        return {"ok": True, "merged": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/admin/correlate")
def admin_correlate():
    """Force an autonomous correlation pass (merge chains, correlate, dedupe).

    Normally runs automatically on every upload and at startup. Use this
    endpoint after manual DB edits or to verify reconciliation is up-to-date.
    """
    counts = corr_svc.auto_correlate_all()
    _refresh_cross_trip_insights()
    return counts


@app.get("/api/v1/admin/uncorrelated")
def admin_uncorrelated():
    """Return candidate trip pairs that may be the same journey but are not yet correlated.

    Reports every (OBD, MyOpel) pair whose score exceeds 0.25 (well below the
    0.50 auto-merge threshold), sorted descending by score.  Pairs at or above
    0.50 were already correlated automatically; pairs below indicate either
    genuine separate trips or a data quality issue worth investigating.
    """
    trips = db.get_all_trips()
    obd_trips  = [t for t in trips if "obd"    in t.get("sources", [])
                  and "myopel" not in t.get("sources", [])]
    myop_trips = [t for t in trips if "myopel" in t.get("sources", [])
                  and "obd"    not in t.get("sources", [])]

    candidates = []
    for myop in myop_trips:
        for obd in obd_trips:
            score = corr_svc._score(obd, myop)
            if score >= 0.25:
                candidates.append({
                    "score":          round(score, 3),
                    "auto_threshold": corr_svc.MIN_MATCH_SCORE,
                    "would_correlate": score >= corr_svc.MIN_MATCH_SCORE,
                    "obd": {
                        "id":    obd["id"],
                        "start": obd.get("start"),
                        "end":   obd.get("end"),
                        "km":    obd.get("distanceKm"),
                        "min":   obd.get("durationMin"),
                    },
                    "myop": {
                        "id":    myop["id"],
                        "start": myop.get("start"),
                        "end":   myop.get("end"),
                        "km":    myop.get("distanceKm"),
                        "min":   myop.get("durationMin"),
                    },
                })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return {
        "standalone_obd":  len(obd_trips),
        "standalone_myop": len(myop_trips),
        "candidates":      candidates,
    }


@app.post("/api/v1/admin/recompute-insights")
def recompute_insights():
    """Recompute per-trip insights for all OBD trips using current rules.

    Safe to call multiple times. Use after updating insight/DPF logic to
    refresh the stored insights without re-uploading CSV files.
    """
    trips = db.get_all_trips()
    ctx = insight_svc.build_context(trips)
    updated = 0
    for trip in trips:
        if "obd" not in trip.get("sources", []):
            continue
        new_insights = insight_svc.per_trip(trip, ctx)
        db.update_insights(trip["id"], new_insights)
        updated += 1
    log.info("recompute-insights: updated %d OBD trips", updated)
    return {"updated": updated}


@app.get("/api/v1/debug/data-error")
def debug_data_error():
    """Diagnose why data.js fails: find the first trip field that can't be serialized."""
    import traceback
    trips = db.get_all_trips()
    results = []

    # Try serializing VEHICLE
    try:
        vehicle = _build_vehicle(trips)
        json.dumps(vehicle)
        results.append({"section": "VEHICLE", "ok": True})
    except Exception as e:
        results.append({"section": "VEHICLE", "ok": False, "error": str(e),
                        "trace": traceback.format_exc()})

    # Try serializing TREND_INSIGHTS
    try:
        ti = getattr(app.state, "trend_insights", [])
        json.dumps(ti)
        results.append({"section": "TREND_INSIGHTS", "ok": True})
    except Exception as e:
        results.append({"section": "TREND_INSIGHTS", "ok": False, "error": str(e)})

    # Try serializing each trip individually to pin down the bad one
    bad_trips = []
    for t in trips:
        try:
            json.dumps(t)
        except Exception as e:
            # Find the bad field
            bad_fields = []
            for k, v in t.items():
                try:
                    json.dumps(v)
                except Exception as fe:
                    bad_fields.append({"field": k, "type": type(v).__name__,
                                       "value": repr(v)[:200], "error": str(fe)})
            bad_trips.append({"trip_id": t.get("id"), "bad_fields": bad_fields})

    if bad_trips:
        results.append({"section": "TRIPS", "ok": False, "bad_trips": bad_trips})
    else:
        results.append({"section": "TRIPS", "ok": True, "count": len(trips)})

    return results
