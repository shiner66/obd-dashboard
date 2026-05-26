"""FastAPI application — OBD Trip Platform backend."""
from __future__ import annotations
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import database as db
from .parsers import csv_parser, myop_parser
from .services import insights as insight_svc
from .services.watcher import Watcher

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

OBD_FILES_DIR   = Path(os.getenv("OBD_FILES_DIR",   "/data/obd"))
MYOP_FILES_DIR  = Path(os.getenv("MYOP_FILES_DIR",  "/data/myop"))
DB_PATH         = Path(os.getenv("DB_PATH",         "/data/db/trips.db"))
VEHICLE_NAME    = os.getenv("VEHICLE_NAME",    "Peugeot 308 SW")
VEHICLE_ECU     = os.getenv("VEHICLE_ECU",     "MD1CS003 — 1.5 BlueHDi")
VEHICLE_ADAPTER = os.getenv("VEHICLE_ADAPTER", "BTLE IOS-Vlink")

_watcher = Watcher()


# ── Trip correlation ──────────────────────────────────────────────────────────

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt[:len(fmt)])
        except ValueError:
            continue
    return None


def _trips_match(obd: dict, myop: dict) -> bool:
    """Return True if an OBD trip and a myop trip are the same real-world trip."""
    t1 = _parse_dt(obd.get("start"))
    t2 = _parse_dt(myop.get("start"))
    if t1 is None or t2 is None:
        return False
    if abs((t1 - t2).total_seconds()) > 600:   # ±10 min
        return False
    d1 = obd.get("distanceKm") or 0
    d2 = myop.get("distanceKm") or 0
    if d1 > 0 and d2 > 0:
        ratio = abs(d1 - d2) / max(d1, d2)
        if ratio > 0.25:                        # ±25 % tolerance
            return False
    return True


def _correlate_new_obd(obd_trip: dict) -> None:
    """After saving an OBD trip, check if any unmatched myop trip matches it.

    Picks the closest match by start-time delta rather than the first result.
    """
    all_trips = db.get_all_trips()
    candidates: list[tuple[float, dict]] = []
    for t in all_trips:
        if "myopel" not in t.get("sources", []):
            continue
        if "obd" in t.get("sources", []):
            continue
        if _trips_match(obd_trip, t):
            t1 = _parse_dt(obd_trip.get("start"))
            t2 = _parse_dt(t.get("start"))
            delta = abs((t1 - t2).total_seconds()) if t1 and t2 else float("inf")
            candidates.append((delta, t))

    if not candidates:
        return

    _, best = min(candidates, key=lambda x: x[0])
    log.info("Correlating OBD %s → myop %s", obd_trip["id"], best["id"])
    db.enrich_with_myop(obd_trip["id"], best)
    if best.get("id") and db.trip_exists(best["id"]):
        db.delete_trip(best["id"])
        log.info("Deleted absorbed myop entry %s", best["id"])


def _correlate_new_myop(myop_trip: dict) -> bool:
    """After saving a myop trip, check if any OBD trip matches it.

    Returns True if a match was found and the OBD trip was enriched.
    Also deletes the standalone myop entry from DB when it is absorbed.
    """
    all_trips = db.get_all_trips()
    for t in all_trips:
        if "obd" not in t.get("sources", []):
            continue
        if _trips_match(t, myop_trip):
            log.info("Correlating myop %s → OBD %s", myop_trip["id"], t["id"])
            db.enrich_with_myop(t["id"], myop_trip)
            # Remove the now-redundant standalone myop entry so it doesn't
            # appear as a duplicate alongside the enriched OBD trip.
            if db.trip_exists(myop_trip["id"]):
                db.delete_trip(myop_trip["id"])
                log.info("Deleted absorbed myop entry %s", myop_trip["id"])
            return True
    return False


# ── File processing ───────────────────────────────────────────────────────────

def _process_obd_file(path: Path) -> list[str]:
    """Parse an OBD CSV/BRC file, save trips, run insights, correlate. Returns new trip IDs."""
    trips = csv_parser.parse_file(path)
    new_ids: list[str] = []
    for trip in trips:
        if db.trip_exists(trip["id"]):
            log.info("Trip %s already in DB, skipping", trip["id"])
            continue
        trip["insights"] = insight_svc.per_trip(trip)
        db.save_trip(trip)
        _correlate_new_obd(trip)
        new_ids.append(trip["id"])
        log.info("Saved OBD trip %s (%.1f km)", trip["id"], trip.get("distanceKm") or 0)
    return new_ids


def _process_myop_file(path: Path) -> list[str]:
    """Parse a .myop file, save trips, correlate. Returns new trip IDs."""
    trips = myop_parser.parse_file(path)
    new_ids: list[str] = []
    for trip in trips:
        if db.trip_exists(trip["id"]):
            # Already in DB: still try to correlate in case a matching OBD trip
            # was uploaded after the myop import. _correlate_new_myop will also
            # delete the standalone entry if a match is found.
            _correlate_new_myop(trip)
            continue
        # Try correlation first; only persist as standalone if no OBD match.
        matched = _correlate_new_myop(trip)
        if not matched:
            db.save_trip(trip)
            new_ids.append(trip["id"])
            log.info("Saved myop trip %s", trip["id"])
        else:
            log.info("myop trip %s absorbed into OBD trip, not saved standalone", trip["id"])
    return new_ids


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
    db.init(DB_PATH)
    log.info("Database initialised at %s", DB_PATH)

    _scan_directory(OBD_FILES_DIR,  _process_obd_file,  (".csv", ".brc"))
    _scan_directory(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))

    # Idempotent: remove any standalone myop entries absorbed into an OBD trip
    # (handles historical duplicates created before correlation was fixed)
    with db._conn() as _con:
        _res = _con.execute(
            """DELETE FROM trips
               WHERE source = 'myop'
                 AND CAST(SUBSTR(id, 6) AS INTEGER) IN (
                     SELECT myop_trip_id FROM trips
                     WHERE source IN ('obd_csv', 'obd_brc')
                       AND myop_trip_id IS NOT NULL
                 )"""
        )
        if _res.rowcount:
            log.info("Startup cleanup: removed %d absorbed standalone myop entries", _res.rowcount)

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
        updated = 0
        for trip in trips:
            if "obd" not in trip.get("sources", []):
                continue
            db.update_insights(trip["id"], insight_svc.per_trip(trip))
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
    """Merge PID catalogs from all trips; deduplicate by slug."""
    seen: dict[str, dict] = {}
    for trip in trips:
        for entry in (trip.get("pidCatalog") or []):
            slug = entry.get("slug")
            if slug and slug not in seen:
                seen[slug] = entry
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
        slim_trips = _safe(trips)

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


@app.post("/api/v1/upload/obd")
async def upload_obd(file: UploadFile):
    dest = OBD_FILES_DIR / file.filename
    OBD_FILES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(await file.read())
    try:
        new_ids = _process_obd_file(dest)
        _refresh_cross_trip_insights()
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
        _refresh_cross_trip_insights()
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


@app.post("/api/v1/admin/fix-myop-timestamps")
def fix_myop_timestamps():
    """Fix myop timestamps and remove absorbed duplicate entries.

    Step 1: add +2 h to start_local/end_local of all myop-source trips
    (the original parser wrongly subtracted 2 h from the Stellantis timestamp
    which is already in local Italian time).

    Step 2: delete standalone myop entries whose myop_trip_id already appears
    on an OBD trip — those are duplicates created before correlation worked.

    Call once after upgrading. Not idempotent — do not call again.
    """
    with db._conn() as con:
        fix_result = con.execute(
            """UPDATE trips
               SET start_local = datetime(start_local, '+2 hours'),
                   end_local   = datetime(end_local,   '+2 hours')
               WHERE source = 'myop'"""
        )
        ts_fixed = fix_result.rowcount

        del_result = con.execute(
            """DELETE FROM trips
               WHERE source = 'myop'
                 AND CAST(SUBSTR(id, 6) AS INTEGER) IN (
                     SELECT myop_trip_id FROM trips
                     WHERE source IN ('obd_csv', 'obd_brc')
                       AND myop_trip_id IS NOT NULL
                 )"""
        )
        duplicates_removed = del_result.rowcount

    log.info("fix-myop-timestamps: %d timestamps fixed, %d duplicates removed",
             ts_fixed, duplicates_removed)
    return {"timestamps_fixed": ts_fixed, "duplicates_removed": duplicates_removed}


@app.post("/api/v1/admin/recompute-insights")
def recompute_insights():
    """Recompute per-trip insights for all OBD trips using current rules.

    Safe to call multiple times. Use after updating insight/DPF logic to
    refresh the stored insights without re-uploading CSV files.
    """
    trips = db.get_all_trips()
    updated = 0
    for trip in trips:
        if "obd" not in trip.get("sources", []):
            continue
        new_insights = insight_svc.per_trip(trip)
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
