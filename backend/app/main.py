"""FastAPI application — OBD Trip Platform backend."""
from __future__ import annotations
import gzip
import json
import logging
import math
import re
from datetime import date
from copy import deepcopy
import os
import statistics
import tempfile
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import database as db
from .api_models import SettingsUpdate, RefuelCreate, MaintenanceCreate
from .parsers import csv_parser, myop_parser
from .services import correlator as corr_svc
from .services import fuel as fuel_svc
from .services import insights as insight_svc
from .services import insight_memory, event_log
from .services.watcher import Watcher
from .services import ingestion as ingest

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

OBD_FILES_DIR   = Path(os.getenv("OBD_FILES_DIR",   "/data/obd"))
MYOP_FILES_DIR  = Path(os.getenv("MYOP_FILES_DIR",  "/data/myop"))
DB_PATH         = Path(os.getenv("DB_PATH",         "/data/db/trips.db"))
APP_VERSION    = os.getenv("APP_VERSION", "0.10.0")
APP_REVISION   = os.getenv("APP_REVISION", "unknown")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
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
        value = float(os.getenv(name, "").strip())
        return value if math.isfinite(value) else default
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

# Gzip snapshots preserve every source version. Originals remain available for
# subsequent producer writes. Legacy SOURCE_ARCHIVE=delete also retains sources.
SOURCE_ARCHIVE = os.getenv("SOURCE_ARCHIVE", "gzip").strip().lower()
ARCHIVE_SUBDIR = "archive"
_watcher = Watcher()
_bulk_loading = False
_scan_errors: dict[str, str] = {}
_legacy_rebuild_needed: set[str] = set()
_dashboard_cache: dict = {}
_global_cache: dict = {}
_cache_revision = None
_SOURCE_PARSER_VERSION = {"obd": 2, "myop": 1}


def _content_sha256(path: Path) -> str:
    """Return the stable content hash used by snapshots and the file ledger."""
    return ingest.content_sha256(path)


def _content_size(path: Path) -> int:
    """Count the uncompressed bytes recorded in the ingestion ledger."""
    if not path.name.lower().endswith(".gz"):
        return path.stat().st_size
    with gzip.open(path, "rb") as source:
        return sum(len(chunk) for chunk in iter(lambda: source.read(1 << 20), b""))


def _in_archive(path: Path, base_dir: Path) -> bool:
    """Recognize legacy flat archives and new hash-versioned archive paths."""
    return path.resolve().is_relative_to((base_dir / ARCHIVE_SUBDIR).resolve())


def _archive_source(path: Path, base_dir: Path) -> str | None:
    """Atomically preserve a verified source version without removing the original."""
    if _in_archive(path, base_dir):
        return str(path.relative_to(base_dir))
    if SOURCE_ARCHIVE == "keep":
        return None
    return str(ingest.archive_snapshot(path, base_dir).relative_to(base_dir))


@contextmanager
def _source_snapshot(path: Path, base_dir: Path):
    """Yield an immutable source and its persistent archive path, if enabled."""
    archived = _archive_source(path, base_dir)
    if archived:
        yield base_dir / archived, archived
    else:
        # Even keep mode parses a verified snapshot, never an open producer file.
        with tempfile.TemporaryDirectory(prefix=".ingest-", dir=base_dir) as temporary:
            yield ingest.archive_snapshot(path, Path(temporary)), None


def _legacy_trip_known(trip_id: str) -> bool:
    """Avoid overwriting legacy materialized history until an explicit rebuild."""
    if db.raw_trip_exists(trip_id):
        return False
    known = db.trip_exists(trip_id) or db.trip_is_absorbed(trip_id)
    if known:
        _legacy_rebuild_needed.add(trip_id)
    return known


def _process_obd_file(path: Path) -> list[str]:
    """Import a verified CSV revision, retaining raw evidence and legacy history."""
    path = Path(path)
    if path.name.lower().removesuffix(".gz").endswith(".brc"):
        raise ValueError("Formato BRC binario non supportato: esporta il viaggio in CSV da CarScanner")
    with ingest.LOCK:
        if db.source_parse_cached(str(path.resolve()), ingest.signature(path), _SOURCE_PARSER_VERSION["obd"], _content_sha256(path)):
            _scan_errors.pop(str(path), None)
            return []
    with ingest.LOCK, _source_snapshot(path, OBD_FILES_DIR) as (snapshot, archived):
        sha = _content_sha256(snapshot)
        expected_id = csv_parser.trip_id_for_file(snapshot)
        if _legacy_trip_known(expected_id):
            db.record_ingested_file(sha, path.name, "obd", _content_size(snapshot), [expected_id], archived)
            db.record_source_parse(str(path.resolve()), ingest.signature(path), sha, _SOURCE_PARSER_VERSION["obd"], [expected_id])
            return []
        trips = csv_parser.parse_file(snapshot)
        changed_ids: list[str] = []
        for trip in trips:
            if not db.raw_trip_revision_known(trip) and db.save_raw_trip(trip):
                if not db.apply_source_observation(trip):
                    _legacy_rebuild_needed.add(trip["id"])
                changed_ids.append(trip["id"])
        db.record_ingested_file(sha, path.name, "obd", _content_size(snapshot),
                                [trip["id"] for trip in trips], archived)
        db.record_source_parse(str(path.resolve()), ingest.signature(path), sha, _SOURCE_PARSER_VERSION["obd"], [trip["id"] for trip in trips])
        _scan_errors.pop(str(path), None)
        if changed_ids and not _bulk_loading:
            _post_process()
        return changed_ids


def _process_myop_file(path: Path) -> list[str]:
    """Import new MyOpel raw revisions without resurrecting already absorbed legs."""
    path = Path(path)
    with ingest.LOCK:
        if not effective_settings()["myop_enabled"]:
            return []
        if db.source_parse_cached(str(path.resolve()), ingest.signature(path), _SOURCE_PARSER_VERSION["myop"], _content_sha256(path)):
            _scan_errors.pop(str(path), None)
            return []
        with _source_snapshot(path, MYOP_FILES_DIR) as (snapshot, archived):
            sha = _content_sha256(snapshot)
            trips = myop_parser.parse_file(snapshot)
            changed_ids: list[str] = []
            for trip in trips:
                if _legacy_trip_known(trip["id"]):
                    continue
                if not db.raw_trip_revision_known(trip) and db.save_raw_trip(trip):
                    if not db.apply_source_observation(trip):
                        _legacy_rebuild_needed.add(trip["id"])
                    changed_ids.append(trip["id"])
            db.record_ingested_file(sha, path.name, "myop", _content_size(snapshot),
                                    [trip["id"] for trip in trips], archived)
            db.record_source_parse(str(path.resolve()), ingest.signature(path), sha, _SOURCE_PARSER_VERSION["myop"], [trip["id"] for trip in trips])
            _scan_errors.pop(str(path), None)
            if changed_ids and not _bulk_loading:
                _post_process()
            return changed_ids


def _post_process() -> None:
    """Reconcile raw sources and refresh all derived insights under one writer lock."""
    with ingest.LOCK:
        corr_svc.auto_correlate_all()
        _recompute_all_insights()
        _refresh_cross_trip_insights()


def _scan_directory(directory: Path, process_fn, extensions: tuple[str, ...]) -> None:
    """Read legacy/versioned archives and complete retained uploads, oldest first.

    Fresh root files go through the watcher's stability queue. Existing legacy
    trips remain intact until an explicit rebuild has been validated on a copy.
    """
    candidates: list[Path] = []
    if directory.exists():
        for source in directory.iterdir():
            if source.is_file() and not source.is_symlink() and source.suffix.lower() in extensions:
                if time.time() - source.stat().st_mtime < _watcher._settle:
                    _watcher.enqueue(source, process_fn)
                else:
                    candidates.append(source)
    for folder in (directory / ARCHIVE_SUBDIR, directory / "uploads"):
        if folder.exists():
            endings = extensions + tuple(ext + ".gz" for ext in extensions)
            candidates.extend(source for source in folder.rglob("*")
                              if source.is_file() and not source.is_symlink()
                              and source.name.lower().endswith(endings))
    seen: set[tuple[str, str]] = set()
    for source in sorted(candidates, key=lambda p: (p.stat().st_mtime_ns, str(p))):
        try:
            kind = "obd" if process_fn is _process_obd_file else "myop"
            if db.source_parse_cached(str(source.resolve()), ingest.signature(source), _SOURCE_PARSER_VERSION[kind], _content_sha256(source)):
                process_fn(source)
                continue
            key = (csv_parser.source_stem(source), _content_sha256(source))
            if key in seen:
                continue
            seen.add(key)
            process_fn(source)
        except Exception as error:
            _scan_errors[str(source)] = str(error)
            log.exception("Error scanning %s", source)


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize durable state and restartable observers while preserving legacy data."""
    global _bulk_loading, _watcher
    _watcher = Watcher()
    app.state.ready = False
    _scan_errors.clear()
    _legacy_rebuild_needed.clear()
    db.init(DB_PATH)
    myop_on = effective_settings()["myop_enabled"]
    try:
        with ingest.LOCK:
            _bulk_loading = True
            _watcher.watch(OBD_FILES_DIR, _process_obd_file, (".csv", ".brc"))
            if myop_on:
                _watcher.watch(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
            _watcher.start()
            _scan_directory(OBD_FILES_DIR, _process_obd_file, (".csv", ".brc"))
            if myop_on:
                _scan_directory(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
            _bulk_loading = False
            _post_process()
            app.state.ready = True
        yield
    finally:
        _bulk_loading = False
        app.state.ready = False
        _watcher.stop()


def _recompute_all_insights() -> None:
    try:
        trips = _effective_trip_metrics(db.get_trip_summaries(include_insight_features=True))
        ctx = insight_svc.build_context(trips, events=db.get_maintenance())
        updated = 0
        for trip in trips:
            if "obd" not in trip.get("sources", []):
                continue
            db.update_insights(trip["id"], insight_svc.per_trip(trip, ctx))
            updated += 1
        log.info("Recomputed per-trip insights for %d OBD trips", updated)
    except Exception:
        log.exception("Per-trip insight recompute failed")
        raise


def _refresh_cross_trip_insights() -> None:
    try:
        trips = _effective_trip_metrics(db.get_trip_summaries(include_insight_features=True))
        if trips:
            ct = insight_svc.cross_trip(trips, history=trips, events=db.get_maintenance())
            db.save_insight_reconciliation(insight_memory.reconcile(db.get_insight_states(), ct))
            log.info("Generated %d cross-trip insights", len(ct))
            # Store on a synthetic key in the app state (returned via data.js)
            app.state.trend_insights = ct
        else:
            app.state.trend_insights = []
    except Exception:
        log.exception("Cross-trip insight generation failed")
        raise


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
        "observedAt":    latest.get("start"),
        "obdObservedAt": latest_obd.get("start"),
        "myopObservedAt": latest_myop.get("start"),
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


def _platform_meta() -> dict:
    """Return release identity and visible ingestion/rebuild status."""
    status = _watcher.status()
    status["errors"] += [{"file": Path(path).name, "error": error}
                         for path, error in _scan_errors.items()]
    legacy = sorted(set(db.legacy_rebuild_ids()) | _legacy_rebuild_needed)
    return {"version": APP_VERSION, "revision": APP_REVISION, "ingestion": status,
            "legacyRebuildNeeded": len(legacy), "legacyRebuildIds": legacy,
            "legacyRebuildReason": "Sorgenti mancanti o insufficienti: storico preservato, escluso dai confronti" if legacy else None}


def _validate_period(from_date: str | None, to_date: str | None) -> tuple[str | None, str | None]:
    """Validate exact inclusive local-calendar dates before any data is read."""
    for value in (from_date, to_date):
        if value is not None:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(status_code=422, detail="Date richieste nel formato YYYY-MM-DD")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(status_code=422, detail="La data iniziale deve precedere o coincidere con quella finale")
    return from_date, to_date


def _in_period(timestamp: str | None, from_date: str | None, to_date: str | None) -> bool:
    """Match a local date, excluding undated observations only when filtering."""
    if not from_date and not to_date:
        return True
    if not timestamp:
        return False
    day = timestamp[:10]
    return (not from_date or day >= from_date) and (not to_date or day <= to_date)


def _period_aggregates(trips: list[dict]) -> dict:
    """Aggregate whole-period activity and comparable measured consumption separately."""
    valid = [t for t in trips if insight_svc.usable_for_analysis(t)]
    consumed = [t for t in valid if t.get("fuelConsumedL") is not None and t.get("fuelDistanceKm")]
    liters = sum(t["fuelConsumedL"] for t in consumed)
    fuel_km = sum(t["fuelDistanceKm"] for t in consumed)
    costed = [t for t in valid if t.get("costEur") is not None and t.get("costDistanceKm")]
    observed = [t for t in valid if t.get("durationMin") is not None]
    return {"tripCount": len(trips), "obdTripCount": sum("obd" in t.get("sources", []) for t in trips),
            "myopTripCount": sum("myopel" in t.get("sources", []) for t in trips),
            "totalKm": round(sum(t.get("distanceKm") or 0 for t in trips), 2),
            "observedMinutes": round(sum(t["durationMin"] for t in observed), 1),
            "observedTripCount": len(observed), "costTripCount": len(costed),
            "fuelLiters": round(liters, 3), "fuelDistanceKm": round(fuel_km, 2),
            "fuelTripCount": len(consumed),
            "consumptionKmL": round(fuel_km / liters, 2) if liters else None,
            "consumptionL100km": round(liters / fuel_km * 100, 2) if fuel_km else None,
            "costEur": round(sum(t["costEur"] for t in costed), 2),
            "costDistanceKm": round(sum(t["costDistanceKm"] for t in costed), 2),
            "excludedFromComparisons": len(trips) - len(valid)}


def _effective_trip_metrics(trips: list[dict], settings: dict | None = None) -> list[dict]:
    """Apply the selected fuel density consistently across summaries, details and exports."""
    density = (settings or effective_settings())["fuel_density_gl"]
    return [{**t, **fuel_svc.select_consumption(t, density)} for t in trips]


def _ensure_global_cache() -> dict:
    """Cache global causal history and persist only genuinely changed measured states.

    Callers hold the ingestion lock. Selecting a period never writes a different
    diagnostic state: reconciliation always uses the complete available history.
    """
    global _cache_revision
    if db.data_revision() != _cache_revision:
        _dashboard_cache.clear()
        _global_cache.clear()
    if not _global_cache:
        settings = effective_settings()
        trips = _effective_trip_metrics(db.get_trip_summaries(include_insight_features=True), settings)
        SettingsUpdate.model_validate({key: settings[key] for key in DEFAULT_SETTINGS})
        refuels, events = db.get_refuels(), db.get_maintenance()
        fuel = fuel_svc.fuel_summary(refuels, trips, settings, db.get_raw_trips("myopel"))
        cards = insight_svc.cross_trip(trips, history=trips, events=events)
        result = insight_memory.reconcile(db.get_insight_states(), cards)
        db.save_insight_reconciliation(result)
        _global_cache.update(trips=trips, settings=settings, refuels=refuels, fuel=fuel,
                             catalog=db.get_pid_catalog(), vehicle=_build_vehicle(trips, settings, fuel),
                             events=events, context=insight_svc.build_context(trips, events=events),
                             states={state["ruleId"]: state for state in result["states"]}, cards=cards)
        _cache_revision = db.data_revision()
    return _global_cache


def _with_lifecycle(cards: list[dict], states: dict) -> list[dict]:
    """Attach current memory only to its exact observation, clearly marking historical views."""
    out = []
    for card in cards:
        state = states.get(card.get("ruleId"))
        if state and card.get("finding") != "information":
            lifecycle = {key: state.get(key) for key in ("state", "firstSeen", "lastSeen", "observations", "unconfirmed")}
            if card.get("observedAt") != state.get("lastObservationAt"):
                lifecycle = {"state": "historical", "unconfirmed": True}
            out.append({**card, "lifecycle": lifecycle})
        else:
            out.append(card)
    return out


def _fresh_trip_insights(trips: list[dict]) -> list[dict]:
    """Return current per-trip analysis without rewriting imported trip measurements."""
    cached = _ensure_global_cache()
    features = {trip["id"]: trip for trip in cached["trips"]}
    return [{**trip, "insights": insight_svc.per_trip(features.get(trip["id"], trip), cached["context"])}
            for trip in _effective_trip_metrics(trips, cached["settings"])]


def _dashboard_payload(from_date: str | None = None, to_date: str | None = None) -> dict:
    """Serve cached lightweight period data while keeping vehicle/tank state global."""
    _validate_period(from_date, to_date)
    with ingest.LOCK:
        _ensure_global_cache()
        key = (from_date, to_date)
        if key not in _dashboard_cache:
            all_trips = _global_cache["trips"]
            trips = [t for t in all_trips if _in_period(t.get("start"), from_date, to_date)]
            ctx = _global_cache["context"]
            slim = [{**{k: v for k, v in t.items() if k != "pidValues"},
                     "insights": insight_svc.per_trip(t, ctx)} for t in trips]
            refuels = [r for r in _global_cache["refuels"] if _in_period(r.get("ts"), from_date, to_date)]
            fuel = deepcopy(_global_cache["fuel"])
            # Full-to-full intervals were computed over the entire ledger first.
            fuel["tankToTank"] = [interval for interval in fuel["tankToTank"] if _in_period(interval.get("date"), from_date, to_date)]
            fuel["refuels"] = refuels
            fuel["period"] = {"refuelCount": len(refuels), "liters": round(sum(r.get("liters") or 0 for r in refuels), 3),
                              "costEur": round(sum((r.get("liters") or 0) * (r.get("pricePerL") or 0) for r in refuels), 2),
                              "undatedRefuels": sum(not r.get("ts") for r in _global_cache["refuels"])}
            fuel["fcSuspects"] = [t for t in fuel["fcSuspects"] if _in_period(t.get("start"), from_date, to_date)]
            catalog = _global_cache["catalog"]
            scope = {"fromDate": from_date, "toDate": to_date,
                     "trips": "selected_period" if from_date or to_date else "all",
                     "trendInsights": "diagnostics_prior_90_days_and_period_summaries",
                     "diagnosticAsOf": max((t.get("start") or "" for t in trips), default=None),
                     "diagnosticLookbackDays": 90, "tripBaselineLookbackDays": 180,
                     "vehicle": "latest_global", "fuelLevel": "latest_global",
                     "tankToTank": "interval_end_in_period", "refuels": "refuel_date_in_period"}
            payload = {"vehicle": _global_cache["vehicle"], "trips": slim,
                       "alerts": myop_parser.ALERT_DICT, "trendInsights": _with_lifecycle(
                           insight_svc.cross_trip(trips, history=all_trips, events=_global_cache["events"]), _global_cache["states"]),
                       "pidCatalog": catalog, "pidGroups": _build_pid_groups(catalog),
                       "settings": _global_cache["settings"], "fuel": fuel,
                       "aggregates": _period_aggregates(trips), "scope": scope}
            if len(_dashboard_cache) >= 16:
                _dashboard_cache.pop(next(iter(_dashboard_cache)))
            _dashboard_cache[key] = _safe(payload)
        # Worker errors/status may change without a database mutation.
        payload = _dashboard_cache[key]
        return {**payload, "meta": {**_platform_meta(), "scope": payload["scope"]}}


@app.get("/api/v1/dashboard")
def dashboard(from_date: str | None = None, to_date: str | None = None):
    """Return date-scoped summaries and explicit global state, with retryable failures."""
    try:
        return JSONResponse(_dashboard_payload(from_date, to_date), headers={"Cache-Control": "no-store"})
    except HTTPException:
        raise
    except Exception:
        log.exception("Dashboard data generation failed")
        raise HTTPException(status_code=503, detail="Dati temporaneamente non disponibili. Riprova o consulta lo stato importazioni.")


@app.get("/api/v1/data.js", response_class=Response)
def data_js():
    """Retain legacy globals while making bootstrap failures explicit to the UI."""
    try:
        data = _dashboard_payload()
        names = {"VEHICLE": "vehicle", "TRIPS": "trips", "ALERTS": "alerts",
                 "TREND_INSIGHTS": "trendInsights", "PID_CATALOG": "pidCatalog",
                 "PID_GROUPS": "pidGroups", "SETTINGS": "settings", "FUEL": "fuel"}
        js = "// Auto-generated dashboard bootstrap\n"
        js += "\n".join(f"var {name} = {json.dumps(data[key], ensure_ascii=False, allow_nan=False)};"
                        for name, key in names.items())
        js += "\nvar POINTS = {}; var DATA_LOAD_ERROR = null;\n"
        return Response(content=js, media_type="application/javascript", headers={"Cache-Control": "no-store"})
    except Exception:
        log.exception("Dashboard bootstrap failed")
        return Response(content='var DATA_LOAD_ERROR = "Dati non disponibili"; window.DASHBOARD_BOOTSTRAP_ERROR = true;',
                        status_code=503, media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/v1/trips")
def list_trips(from_date: str | None = None, to_date: str | None = None, summary: bool = False):
    """Export full trips or light summaries using the dashboard's local-day bounds."""
    _validate_period(from_date, to_date)
    with ingest.LOCK:
        return _fresh_trip_insights(db.get_trip_summaries(from_date, to_date) if summary else db.get_all_trips(from_date, to_date))


@app.get("/api/v1/trips/{trip_id}")
def get_trip(trip_id: str):
    """Fetch full evidence even outside the selected dashboard period."""
    with ingest.LOCK:
        trip = db.get_trip(trip_id)
        if trip is None:
            raise HTTPException(status_code=404, detail="Viaggio non trovato")
        return _fresh_trip_insights([trip])[0]


@app.get("/api/v1/tracks")
def all_tracks(from_date: str | None = None, to_date: str | None = None):
    """GPS tracks in the selected local-date range, loaded when the map opens."""
    _validate_period(from_date, to_date)
    return db.get_all_tracks(from_date, to_date)


def _upload_name(filename: str | None, extensions: tuple[str, ...]) -> str:
    """Accept only ordinary supported basenames inside the configured upload root."""
    if (not filename or len(filename) > 200 or Path(filename).name != filename
            or "\\" in filename or filename.startswith(".")
            or any(ord(char) < 32 for char in filename)):
        raise HTTPException(status_code=400, detail="Nome file non valido")
    suffix = Path(filename).suffix.lower()
    if suffix == ".brc":
        raise HTTPException(status_code=400, detail="Formato BRC binario non supportato: esporta in CSV da CarScanner")
    if suffix not in extensions:
        raise HTTPException(status_code=400, detail="Formato file non supportato")
    return filename


def _publish_upload(temporary: Path, destination: Path) -> None:
    """Flush a completed upload to disk before atomically publishing its basename."""
    with temporary.open("rb") as source:
        os.fsync(source.fileno())
    os.replace(temporary, destination)
    descriptor = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def _upload_source(file: UploadFile, directory: Path, extensions: tuple[str, ...], process_fn):
    """Stream into an isolated staging directory, then import one immutable version."""
    filename = _upload_name(file.filename, extensions)
    directory.mkdir(parents=True, exist_ok=True)
    upload_dir = directory / "uploads" / uuid.uuid4().hex
    upload_dir.mkdir(parents=True)
    destination = upload_dir / filename
    if not destination.resolve().is_relative_to(directory.resolve()):
        raise HTTPException(status_code=400, detail="Percorso file non valido")
    temporary = upload_dir / (filename + ".part")
    total = 0
    try:
        async with aiofiles.open(temporary, "xb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File troppo grande: massimo 100 MB")
                await target.write(chunk)
            await target.flush()
        if total == 0:
            raise HTTPException(status_code=400, detail="Il file è vuoto")
        await run_in_threadpool(_publish_upload, temporary, destination)
        try:
            changed_ids = await run_in_threadpool(process_fn, destination)
            return {"status": "ok", "new_trips": changed_ids, "file": filename}
        except Exception as error:
            _scan_errors[str(destination)] = str(error)
            log.exception("Uploaded source could not be imported: %s", filename)
            raise HTTPException(status_code=422, detail=f"Importazione non riuscita: {error}")
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()
        if not destination.exists():
            upload_dir.rmdir()


@app.post("/api/v1/upload/obd")
async def upload_obd(file: UploadFile):
    """Upload a completed CarScanner CSV without overwriting another source."""
    return await _upload_source(file, OBD_FILES_DIR, (".csv",), _process_obd_file)


@app.post("/api/v1/upload/myop")
async def upload_myop(file: UploadFile):
    """Upload MyOpel data only while its source is enabled."""
    if not effective_settings()["myop_enabled"]:
        raise HTTPException(status_code=409, detail="Sorgente MyOpel disattivata: riattivala nelle impostazioni")
    return await _upload_source(file, MYOP_FILES_DIR, (".myop", ".json"), _process_myop_file)


@app.post("/api/v1/trips/merge")
async def merge_trips(payload: dict):
    """Merge trips: { primary_id: str, secondary_ids: [str] }"""
    primary_id = payload.get("primary_id")
    secondary_ids = payload.get("secondary_ids", [])
    if not primary_id or not secondary_ids:
        raise HTTPException(status_code=400, detail="primary_id and secondary_ids required")
    try:
        with ingest.LOCK:
            result = db.merge_trips(primary_id, secondary_ids)
            _recompute_all_insights()
            _refresh_cross_trip_insights()
            return {"ok": True, "merged": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/health")
def health():
    """Check usable dashboard data and the worker, with release identity for deploys."""
    try:
        db.check_readiness()
        SettingsUpdate.model_validate({key: effective_settings()[key] for key in DEFAULT_SETTINGS})
        if not getattr(app.state, "ready", False) or not _watcher.status()["running"]:
            raise RuntimeError("Avvio o arresto in corso")
        return {"status": "ok", **_platform_meta()}
    except Exception:
        log.exception("Readiness check failed")
        return JSONResponse({"status": "error", "version": APP_VERSION, "revision": APP_REVISION,
                             "detail": "Dashboard non pronta"}, status_code=503)


@app.get("/api/v1/admin/ingestion")
def ingestion_status():
    """Show pending sources, failures and legacy history requiring explicit recovery."""
    return _platform_meta()


@app.get("/api/v1/settings")
def get_settings():
    """Effective settings (DB overrides merged over env defaults) and defaults."""
    return {"settings": effective_settings(), "defaults": DEFAULT_SETTINGS}


@app.put("/api/v1/settings")
def put_settings(payload: SettingsUpdate):
    """Validate all fields before one atomic write and apply source toggles live."""
    applied = payload.model_dump(exclude_unset=True)
    with ingest.LOCK:
        before = effective_settings()
        db.set_settings(applied)
        after = effective_settings()
        if before["myop_enabled"] != after["myop_enabled"]:
            if after["myop_enabled"]:
                _watcher.watch(MYOP_FILES_DIR, _process_myop_file, (".myop", ".json"))
                # Files dropped while disabled are queued by watch(); archives and
                # retained HTTP uploads can be resumed without blocking this request.
                for folder in (MYOP_FILES_DIR / ARCHIVE_SUBDIR, MYOP_FILES_DIR / "uploads"):
                    if folder.exists():
                        for source in folder.rglob("*"):
                            if source.is_file() and source.name.lower().endswith((".myop", ".json", ".myop.gz", ".json.gz")):
                                _watcher.enqueue(source, _process_myop_file)
            else:
                _watcher.unwatch(MYOP_FILES_DIR)
        _post_process()
        return {"ok": True, "applied": applied, "settings": after}


# ── Refuel ledger + fuel model ────────────────────────────────────────────────

@app.get("/api/v1/fuel")
def get_fuel(from_date: str | None = None, to_date: str | None = None):
    """Date-scoped refuels and intervals, keeping the current tank estimate global."""
    return _dashboard_payload(from_date, to_date)["fuel"]


@app.get("/api/v1/maintenance")
def list_maintenance(from_date: str | None = None, to_date: str | None = None, include_archived: bool = True):
    """List manually recorded interventions, including restorable archived records."""
    _validate_period(from_date, to_date)
    with ingest.LOCK:
        return {"events": [event for event in db.get_maintenance(include_archived)
                           if _in_period(event["ts"], from_date, to_date)]}


@app.post("/api/v1/maintenance")
def add_maintenance(payload: MaintenanceCreate):
    """Record an explicitly entered intervention without changing vehicle measurements."""
    with ingest.LOCK:
        return db.save_maintenance(payload.model_dump())


@app.put("/api/v1/maintenance/{event_id}")
def edit_maintenance(event_id: int, payload: MaintenanceCreate):
    """Replace a validated event, including explicit restoration of an archived row."""
    with ingest.LOCK:
        event = db.save_maintenance(payload.model_dump(), event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Intervento non trovato")
        return event


@app.delete("/api/v1/maintenance/{event_id}")
def archive_maintenance(event_id: int):
    """Archive rather than erase user-entered maintenance history."""
    with ingest.LOCK:
        if not db.archive_maintenance(event_id):
            raise HTTPException(status_code=404, detail="Intervento non trovato")
        return {"id": event_id, "archived": True}


@app.get("/api/v1/events")
def list_events(from_date: str | None = None, to_date: str | None = None):
    """Return a date-scoped timeline; reading or filtering never invents observations."""
    _validate_period(from_date, to_date)
    with ingest.LOCK:
        cached = _ensure_global_cache()
        maintenance = db.get_maintenance(include_archived=True)
        timeline = event_log.build_timeline(maintenance, cached["refuels"], cached["trips"], db.get_insight_history())
        return {"events": [event for event in timeline if _in_period(event["ts"], from_date, to_date)],
                "maintenance": [event for event in maintenance if _in_period(event["ts"], from_date, to_date)],
                "scope": {"fromDate": from_date, "toDate": to_date, "events": "event_date_in_period"}}


@app.post("/api/v1/refuels")
def add_refuel(payload: RefuelCreate):
    """Record a validated refuel atomically, preserving local timestamp semantics."""
    with ingest.LOCK:
        return {"ok": True, "refuel": db.add_refuel(payload.model_dump())}


@app.put("/api/v1/refuels/{refuel_id}")
def edit_refuel(refuel_id: int, payload: RefuelCreate):
    """Replace one validated ledger entry atomically; a missing ID remains a 404."""
    with ingest.LOCK:
        result = db.update_refuel(refuel_id, payload.model_dump())
        if result is None:
            raise HTTPException(status_code=404, detail="Rifornimento non trovato")
        return {"ok": True, "refuel": result}


@app.delete("/api/v1/refuels/{refuel_id}")
def remove_refuel(refuel_id: int):
    """Remove one ledger entry under the shared mutation lock."""
    with ingest.LOCK:
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
    obd_arch  = _dir_bytes(OBD_FILES_DIR / ARCHIVE_SUBDIR, recursive=True)
    myop_arch = _dir_bytes(MYOP_FILES_DIR / ARCHIVE_SUBDIR, recursive=True)
    obd_raw   = _dir_bytes(OBD_FILES_DIR) + _dir_bytes(OBD_FILES_DIR / "uploads", recursive=True)
    myop_raw  = _dir_bytes(MYOP_FILES_DIR) + _dir_bytes(MYOP_FILES_DIR / "uploads", recursive=True)
    ledger = db.ledger_stats()
    return {
        "archive_mode":       "keep" if SOURCE_ARCHIVE == "keep" else "gzip",
        "originals_retained": True,
        "db_bytes":           db_bytes,
        "obd_pending_bytes":  obd_raw,      # compatibility name: includes retained originals
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
    with ingest.LOCK:
        counts = corr_svc.auto_correlate_all()
        _recompute_all_insights()
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
    with ingest.LOCK:
        _recompute_all_insights()
        _refresh_cross_trip_insights()
        updated = sum("obd" in trip.get("sources", []) for trip in db.get_trip_summaries())
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
        fuel = fuel_svc.fuel_summary(db.get_refuels(), trips, settings, db.get_raw_trips("myopel"))
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
