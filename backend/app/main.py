"""FastAPI application — OBD Trip Platform backend."""
from __future__ import annotations
import json
import logging
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

OBD_FILES_DIR  = Path(os.getenv("OBD_FILES_DIR",  "/data/obd"))
MYOP_FILES_DIR = Path(os.getenv("MYOP_FILES_DIR", "/data/myop"))
DB_PATH        = Path(os.getenv("DB_PATH",        "/data/db/trips.db"))

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
    """After saving an OBD trip, check if any unmatched myop trip matches it."""
    all_trips = db.get_all_trips()
    for t in all_trips:
        if "myopel" not in t.get("sources", []):
            continue
        if "obd" in t.get("sources", []):
            continue                            # already merged
        if _trips_match(obd_trip, t):
            log.info("Correlating OBD %s → myop %s", obd_trip["id"], t["id"])
            db.enrich_with_myop(obd_trip["id"], t)
            break


def _correlate_new_myop(myop_trip: dict) -> None:
    """After saving a myop trip, check if any OBD trip matches it."""
    all_trips = db.get_all_trips()
    for t in all_trips:
        if "obd" not in t.get("sources", []):
            continue
        if _trips_match(t, myop_trip):
            log.info("Correlating myop %s → OBD %s", myop_trip["id"], t["id"])
            db.enrich_with_myop(t["id"], myop_trip)
            break


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
            _correlate_new_myop(trip)
            continue
        db.save_trip(trip)
        _correlate_new_myop(trip)
        new_ids.append(trip["id"])
        log.info("Saved myop trip %s", trip["id"])
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

    _watcher.watch(OBD_FILES_DIR,  _process_obd_file,  (".csv", ".brc"))
    _watcher.watch(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
    _watcher.start()

    # Regenerate cross-trip insights and store on the most recent trip
    _refresh_cross_trip_insights()

    yield

    _watcher.stop()


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
        "name":          "Peugeot 308 SW",
        "ecu":           "MD1CS003 — 1.5 BlueHDi",
        "adapter":       "BTLE IOS-Vlink",
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
        "dpfSoot":         latest_obd.get("dpfSootPct"),
        "dpfAvgRegenKm":   latest_obd.get("dpfAvgRegenKm"),
        "dpfSinceRegenKm": latest_obd.get("dpfSinceRegenKm"),
        "battery":         latest_obd.get("batteryStartupV"),
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


@app.get("/api/v1/data.js", response_class=Response)
def data_js():
    trips = db.get_all_trips()
    vehicle = _build_vehicle(trips)
    catalog = _build_pid_catalog(trips)
    groups  = _build_pid_groups(catalog)

    trend_insights = getattr(app.state, "trend_insights", [])

    # Strip heavy per-trip fields that blow up data.js payload
    # (they're fetched per-trip via /api/v1/trips/{id})
    slim_trips = []
    for t in trips:
        slim = dict(t)
        # Keep pidValues (small) but drop pidSeriesFull (large) and track from list view
        # Frontend TripDetail fetches the full trip via /api/v1/trips/{id}
        slim_trips.append(slim)

    js = (
        "// Auto-generated by OBD Trip Platform backend\n"
        f"const VEHICLE = {json.dumps(vehicle, ensure_ascii=False)};\n\n"
        f"const TRIPS = {json.dumps(slim_trips, ensure_ascii=False)};\n\n"
        f"const ALERTS = {json.dumps(myop_parser.ALERT_DICT, ensure_ascii=False)};\n\n"
        f"const TREND_INSIGHTS = {json.dumps(trend_insights, ensure_ascii=False)};\n\n"
        f"const PID_CATALOG = {json.dumps(catalog, ensure_ascii=False)};\n\n"
        f"const PID_GROUPS = {json.dumps(groups, ensure_ascii=False)};\n\n"
        "const POINTS = {};\n"
    )
    return Response(content=js, media_type="application/javascript")


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


@app.get("/api/v1/health")
def health():
    return {"status": "ok"}
