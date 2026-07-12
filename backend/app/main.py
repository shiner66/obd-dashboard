"""FastAPI application — OBD Trip Platform backend."""
from __future__ import annotations
import gzip
import hashlib
import json
import logging
import math
import os
import shutil
import statistics
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import database as db
from .parsers import csv_parser, myop_parser
from .services import correlator as corr_svc
from .services import fuel as fuel_svc
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


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


# Effective settings = DB overrides (set from the UI) on top of these env defaults.
# `myop_enabled=False` runs the platform entirely on OBD data — the MyOpel .myop
# feed is the user's to switch off.
DEFAULT_SETTINGS = {
    "myop_enabled":    _env_bool("MYOP_ENABLED", True),
    "tank_capacity_l": _env_float("TANK_CAPACITY_L", fuel_svc.DEFAULT_TANK_L),
    "fuel_density_gl": _env_float("FUEL_DENSITY_GL", fuel_svc.DEFAULT_DENSITY),
}


def effective_settings() -> dict:
    """DB-stored settings merged over the env-derived defaults."""
    s = dict(DEFAULT_SETTINGS)
    for k, v in db.get_all_settings().items():
        if v is not None:
            s[k] = v
    return s

# What to do with a source file once its content is safely in the DB:
#   gzip   (default) — compress into <watch_dir>/archive/<name>.gz (~90 % smaller),
#                      still re-parsable by future migrations
#   keep             — leave the file untouched
#   delete           — remove it (the DB + ledger become the only copy)
SOURCE_ARCHIVE  = os.getenv("SOURCE_ARCHIVE", "gzip").strip().lower()
ARCHIVE_SUBDIR  = "archive"

_watcher = Watcher()

# During the initial directory scan we save every file first and reconcile once
# at the end. Without this, each of ~100+ files would trigger a full O(n)
# correlation + insight recompute, making a cold re-ingest O(n²) and slow.
_bulk_loading = False


# ── Source archiving & ledger ─────────────────────────────────────────────────
# Once a file's content is in the DB it is recorded in the ingested_files
# ledger (sha256 of the uncompressed content) and, by default, gzipped into
# <watch_dir>/archive/. The archive stays re-parsable: startup scans read
# .gz sources transparently, so parser improvements can always re-ingest.

def _content_sha256(path: Path) -> str:
    """sha256 of the uncompressed content — stable across plain and .gz copies."""
    h = hashlib.sha256()
    opener = gzip.open(path, "rb") if path.name.lower().endswith(".gz") else open(path, "rb")
    with opener as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_size(path: Path) -> int:
    if not path.name.lower().endswith(".gz"):
        return path.stat().st_size
    n = 0
    with gzip.open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            n += len(chunk)
    return n


def _in_archive(path: Path, base_dir: Path) -> bool:
    return path.parent == base_dir / ARCHIVE_SUBDIR


def _archive_source(path: Path, base_dir: Path) -> str | None:
    """Apply the SOURCE_ARCHIVE policy. Returns the archived path (relative
    to base_dir) or None. Never touches files already under archive/."""
    if _in_archive(path, base_dir):
        return str(path.relative_to(base_dir))
    if SOURCE_ARCHIVE == "keep":
        return None
    if SOURCE_ARCHIVE == "delete":
        path.unlink(missing_ok=True)
        log.info("Deleted ingested source %s (SOURCE_ARCHIVE=delete)", path.name)
        return None
    # default: gzip
    adir = base_dir / ARCHIVE_SUBDIR
    adir.mkdir(parents=True, exist_ok=True)
    target = adir / (path.name + ".gz")
    if not target.exists():
        with open(path, "rb") as src, gzip.open(target, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
    path.unlink(missing_ok=True)
    log.info("Archived %s → %s (%.0f%% smaller)", path.name, target.name,
             (1 - target.stat().st_size / max(_content_size(target), 1)) * 100)
    return str(target.relative_to(base_dir))


# ── File processing ───────────────────────────────────────────────────────────
# Cross-trip reconciliation (OBD chain merge, OBD↔MyOpel correlation, MyOpel
# dedupe) is delegated to corr_svc and runs after every batch — _post_process().

def _process_obd_file(path: Path) -> list[str]:
    """Parse an OBD CSV/BRC file, save new trips, run per-trip insights. Returns new trip IDs."""
    # Skip the full CSV parse when this file's trip is already in the DB. The
    # trip id is derived purely from the filename, so on warm restarts (and for
    # the space/underscore duplicate files) we avoid re-reading thousands of rows.
    expected_id = csv_parser.trip_id_for_file(path)
    if expected_id and db.trip_exists(expected_id):
        if not _in_archive(path, OBD_FILES_DIR):
            # Content already ingested under this id — ledger + archive policy.
            sha = _content_sha256(path)
            size = _content_size(path)
            archived = _archive_source(path, OBD_FILES_DIR)
            db.record_ingested_file(sha, path.name, "obd", size, [expected_id], archived)
        return []

    sha = _content_sha256(path)
    size = _content_size(path)
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
    # Ledger + archive also when parse produced no trips (sub-1 km recordings):
    # otherwise the file would be re-parsed at every startup forever.
    archived = _archive_source(path, OBD_FILES_DIR)
    db.record_ingested_file(sha, path.name, "obd", size,
                            new_ids or [t["id"] for t in trips], archived)
    if new_ids and not _bulk_loading:
        _post_process()
    return new_ids


def _process_myop_file(path: Path) -> list[str]:
    """Parse a .myop file, save new trips. Returns new trip IDs.

    .myop files are always re-parsed (never skipped via the ledger): each one
    is cumulative, and re-adding absorbed legs at startup is what lets the
    correlator rebuild its grouping after a migration reset.
    """
    # Honour a runtime MyOpel switch-off even if a file is dropped in the watch dir.
    if not effective_settings()["myop_enabled"]:
        log.info("MyOpel disabled — ignoring dropped file %s", path.name)
        return []
    sha = _content_sha256(path)
    size = _content_size(path)
    trips = myop_parser.parse_file(path)
    new_ids: list[str] = []
    for trip in trips:
        if db.trip_exists(trip["id"]):
            continue
        db.save_trip(trip)
        new_ids.append(trip["id"])
        log.info("Saved myop trip %s", trip["id"])
    archived = _archive_source(path, MYOP_FILES_DIR)
    db.record_ingested_file(sha, path.name, "myop", size, new_ids, archived)
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
    """Scan a directory (and its archive/ subdir, .gz included) for files not yet in the DB."""
    candidates: list[Path] = []
    if directory.exists():
        candidates += [f for f in sorted(directory.iterdir())
                       if f.is_file() and f.suffix.lower() in extensions]
    adir = directory / ARCHIVE_SUBDIR
    if adir.exists():
        gz_exts = tuple(e + ".gz" for e in extensions)
        candidates += [f for f in sorted(adir.iterdir())
                       if f.is_file() and (f.suffix.lower() in extensions
                                           or f.name.lower().endswith(gz_exts))]
    for f in candidates:
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

    myop_on = effective_settings()["myop_enabled"]
    log.info("MyOpel source %s", "ENABLED" if myop_on else "DISABLED (OBD-only mode)")

    # Bulk mode: save all files first, reconcile once at the end (avoids O(n²)).
    _bulk_loading = True
    _scan_directory(OBD_FILES_DIR,  _process_obd_file,  (".csv", ".brc"))
    if myop_on:
        _scan_directory(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
    _bulk_loading = False

    # Single reconciliation pass after the initial scan — chains, overlaps,
    # OBD↔MyOpel grouping and dedupe, all at once.
    try:
        corr_svc.auto_correlate_all()
    except Exception:
        log.exception("Startup auto_correlate_all failed")

    _watcher.watch(OBD_FILES_DIR,  _process_obd_file,  (".csv", ".brc"))
    if myop_on:
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

def _recent_kml(trips: list[dict]) -> float | None:
    """Median km/L over the most recent trips with a consumption figure."""
    vals = [t.get("consumptionKmL") for t in
            sorted(trips, key=lambda t: t.get("start") or "", reverse=True)[:15]
            if t.get("consumptionKmL")]
    return statistics.median(vals) if vals else None


def _build_vehicle(trips: list[dict], settings: dict, fuel: dict) -> dict:
    """Build VEHICLE global from latest trip data.

    Fuel level and service countdown come from MyOpel when the source is enabled
    and available, otherwise from OBD-native fields + the refuel ledger, so the
    dashboard stays populated with the .myop feed switched off.
    """
    obd_trips = [t for t in trips if "obd" in t.get("sources", [])]
    myop_trips = [t for t in trips if "myopel" in t.get("sources", [])]

    latest_obd  = obd_trips[0]  if obd_trips  else {}
    latest_myop = myop_trips[0] if myop_trips else {}
    latest      = trips[0]      if trips       else {}

    myop_on = bool(settings.get("myop_enabled"))
    level   = fuel.get("level") or {}

    # ── Fuel level & autonomy ────────────────────────────────────────────────
    if myop_on and latest_myop.get("fuelLevel") is not None:
        fuel_level, fuel_autonomy, fuel_source = (
            latest_myop.get("fuelLevel"), latest_myop.get("fuelAutonomy"), "myopel")
    else:
        fuel_level  = level.get("pct")
        fuel_source = level.get("source")            # "ledger" | "obd" | None
        liters, kml = level.get("liters"), _recent_kml(trips)
        fuel_autonomy = round(liters * kml) if (liters and kml) else None

    # ── Service countdown ────────────────────────────────────────────────────
    if myop_on and (latest_myop.get("kmToService") is not None
                    or latest_myop.get("daysToService") is not None):
        days_svc     = latest_myop.get("daysToService")
        km_svc       = latest_myop.get("kmToService")
        maint_passed = latest_myop.get("maintenancePassed") or False
        svc_source   = "myopel"
    else:
        # OBD proxy: distance to the next oil change (no day countdown available).
        km_svc = next((t.get("oilKmToService") for t in obd_trips
                       if t.get("oilKmToService") is not None), None)
        days_svc, maint_passed = None, False
        svc_source = "obd" if km_svc is not None else None

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
        "fuelSource":    fuel_source,
        "fuelLiters":    level.get("liters"),
        "fuelCapacityL": fuel.get("capacityL"),
        "serviceSource": svc_source,
        "myopEnabled":   myop_on,
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
    "odometer:null,fuelLevel:null,fuelAutonomy:null,fuelSource:null,myopEnabled:true,adblueRange:null,"
    "nextService:{days:null,km:null,passed:false},"
    "dpfSoot:null,dpfAvgRegenKm:null,dpfSinceRegenKm:null,battery:null};\n"
    "var TRIPS=[];\nvar ALERTS={};\nvar TREND_INSIGHTS=[];\n"
    "var PID_CATALOG=[];\nvar PID_GROUPS={};\nvar POINTS={};\n"
    "var SETTINGS={myop_enabled:true,tank_capacity_l:43.5,fuel_density_gl:835};\n"
    "var FUEL={level:{},tankToTank:[],fcSuspects:[],capacityL:43.5};\n"
)


@app.get("/api/v1/data.js", response_class=Response)
def data_js():
    try:
        trips = db.get_all_trips()
        settings = effective_settings()
        fuel = fuel_svc.fuel_summary(db.get_refuels(), trips, settings)
        vehicle = _safe(_build_vehicle(trips, settings, fuel))
        catalog = _safe(db.get_pid_catalog())
        groups  = _safe(_build_pid_groups(catalog))
        trend_insights = _safe(getattr(app.state, "trend_insights", []))
        # data.js carries only trip *summaries*. The heavy per-trip payload —
        # pidValues, pidSeriesFull and the GPS track — is loaded lazily via
        # /api/v1/trips/{id} (and /tracks for the map) so the initial
        # dashboard load stays small.
        _HEAVY = ("pidValues", "pidSeriesFull", "track")
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
            f"var SETTINGS = {json.dumps(_safe(settings), ensure_ascii=False)};\n\n"
            f"var FUEL = {json.dumps(_safe(fuel), ensure_ascii=False)};\n\n"
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


# ── Settings ──────────────────────────────────────────────────────────────────

_ALLOWED_SETTINGS = {
    "myop_enabled":    bool,
    "tank_capacity_l": float,
    "fuel_density_gl": float,
}


@app.get("/api/v1/settings")
def get_settings():
    """Effective settings (DB overrides merged over env defaults) + their defaults."""
    return {"settings": effective_settings(), "defaults": DEFAULT_SETTINGS}


@app.put("/api/v1/settings")
def put_settings(payload: dict):
    """Update one or more settings. Only whitelisted keys are accepted."""
    applied = {}
    for key, caster in _ALLOWED_SETTINGS.items():
        if key not in payload:
            continue
        try:
            val = caster(payload[key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Invalid value for {key}")
        db.set_setting(key, val)
        applied[key] = val
    # Toggling the MyOpel source changes what the watcher scans and what the
    # vehicle summary prefers — re-run reconciliation and refresh insights.
    _post_process()
    return {"ok": True, "applied": applied, "settings": effective_settings()}


# ── Refuel ledger + fuel model ────────────────────────────────────────────────

@app.get("/api/v1/fuel")
def get_fuel():
    """Tank level (ledger − consumption or OBD sender), tank-to-tank economy,
    the refuel ledger, and any stale-fuelConsumption suspects."""
    trips = db.get_all_trips()
    settings = effective_settings()
    summary = fuel_svc.fuel_summary(db.get_refuels(), trips, settings)
    summary["refuels"] = db.get_refuels()
    return summary


@app.post("/api/v1/refuels")
def add_refuel(payload: dict):
    """Record a refuel. Required: liters. Recommended: odometerKm (anchors the
    fuel-level model) and ts. Optional: pricePerL, fuelType, fullTank, note."""
    if not payload.get("liters"):
        raise HTTPException(status_code=400, detail="liters required")
    try:
        entry = {
            "ts":         payload.get("ts"),
            "odometerKm": float(payload["odometerKm"]) if payload.get("odometerKm") is not None else None,
            "liters":     float(payload["liters"]),
            "pricePerL":  float(payload["pricePerL"]) if payload.get("pricePerL") is not None else None,
            "fuelType":   payload.get("fuelType"),
            "fullTank":   bool(payload.get("fullTank", True)),
            "note":       payload.get("note"),
        }
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid numeric field")
    return {"ok": True, "refuel": db.add_refuel(entry)}


@app.delete("/api/v1/refuels/{refuel_id}")
def remove_refuel(refuel_id: int):
    db.delete_refuel(refuel_id)
    return {"ok": True}


def _dir_bytes(d: Path, recursive: bool = False) -> int:
    if not d.exists():
        return 0
    it = d.rglob("*") if recursive else d.iterdir()
    return sum(f.stat().st_size for f in it if f.is_file())


@app.get("/api/v1/admin/storage")
def admin_storage():
    """Disk usage breakdown: DB, source dirs, archives, and ledger savings."""
    db_bytes = sum(p.stat().st_size for p in
                   (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"),
                    DB_PATH.with_name(DB_PATH.name + "-shm")) if p.exists())
    obd_arch  = _dir_bytes(OBD_FILES_DIR / ARCHIVE_SUBDIR)
    myop_arch = _dir_bytes(MYOP_FILES_DIR / ARCHIVE_SUBDIR)
    obd_raw   = _dir_bytes(OBD_FILES_DIR)
    myop_raw  = _dir_bytes(MYOP_FILES_DIR)
    ledger = db.ledger_stats()
    return {
        "archive_mode":       SOURCE_ARCHIVE,
        "db_bytes":           db_bytes,
        "obd_pending_bytes":  obd_raw,      # not yet archived (watch dir root)
        "myop_pending_bytes": myop_raw,
        "obd_archive_bytes":  obd_arch,
        "myop_archive_bytes": myop_arch,
        "total_bytes":        db_bytes + obd_raw + myop_raw + obd_arch + myop_arch,
        "ledger_files":       ledger["files"],
        "ledger_archived":    ledger["archived"],
        "ledger_original_bytes": ledger["original_bytes"],
    }


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

    # Audit existing correlations: coverage = MyOpel km / OBD km should sit
    # near 1. Far below → Stellantis recorded only part of the session (or a
    # wrong leg was absorbed); far above → a foreign leg slipped in.
    suspects = []
    for t in trips:
        if "obd" not in t.get("sources", []) or t.get("myopId") is None:
            continue
        obd_km  = t.get("distanceKm") or 0
        myop_km = t.get("myopDistanceKm")
        if not obd_km or myop_km is None:
            continue
        coverage = myop_km / obd_km
        if 0.6 <= coverage <= 1.35:
            continue
        suspects.append({
            "id":        t["id"],
            "start":     t.get("start"),
            "obd_km":    round(obd_km, 1),
            "myop_km":   round(myop_km, 1),
            "coverage":  round(coverage, 2),
            "leg_ids":   t.get("myopLegIds") or [],
            "reason":    ("copertura parziale — Stellantis ha registrato solo parte della sessione"
                          if coverage < 0.6 else
                          "tratte MyOpel oltre la distanza OBD — possibile tratta estranea"),
        })
    suspects.sort(key=lambda s: s["coverage"])

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return {
        "standalone_obd":  len(obd_trips),
        "standalone_myop": len(myop_trips),
        "candidates":      candidates,
        "suspects":        suspects,
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
        settings = effective_settings()
        fuel = fuel_svc.fuel_summary(db.get_refuels(), trips, settings)
        vehicle = _build_vehicle(trips, settings, fuel)
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
