"""SQLite persistence — one JSON blob per heavy field to keep schema simple."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

_DB_PATH: Path | None = None


def init(db_path: str | Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(_SCHEMA)


def _conn() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("Database not initialised — call init() first")
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id                  TEXT PRIMARY KEY,
    source              TEXT,          -- "obd_csv" | "myop"
    filename            TEXT,
    start_local         TEXT,
    start_utc           TEXT,
    end_local           TEXT,
    duration_min        REAL,
    distance_km         REAL,
    avg_speed_kmh       REAL,
    max_speed_kmh       REAL,
    avg_rpm             INTEGER,
    max_rpm             INTEGER,
    coolant_max_c       REAL,
    oil_temp_max_c      REAL,
    odometer_km         INTEGER,
    air_temp_c          REAL,
    fuel_consumed_l     REAL,
    consumption_l100km  REAL,
    dpf_soot_pct        REAL,
    dpf_closed_soot     REAL,
    dpf_regen_active    INTEGER,
    dpf_regen_state     TEXT,
    dpf_regen_capability REAL,
    dpf_regen_capability_st REAL,
    dpf_since_regen_km  REAL,
    dpf_avg_regen_km    REAL,
    dpf_replace_km      REAL,
    adblue_vol_l        REAL,
    adblue_range_km     REAL,
    exhaust_before_cat_c REAL,
    exhaust_after_cat_c  REAL,
    nox_cat_temp_max_c   REAL,
    battery_startup_v    REAL,
    oil_dilution_pct     REAL,
    ss_state             INTEGER,
    myop_trip_id         INTEGER,
    myop_fuel_level      INTEGER,
    myop_fuel_autonomy   INTEGER,
    myop_fuel_consumed_l REAL,
    myop_price_fuel      REAL,
    myop_days_to_service INTEGER,
    myop_km_to_service   INTEGER,
    myop_maintenance_passed INTEGER,
    alerts_json          TEXT,        -- JSON array of int
    gps_track_json       TEXT,        -- JSON [[lat,lon],...]
    pid_values_json      TEXT,        -- JSON {slug: stats}
    pid_series_json      TEXT,        -- JSON {slug: [60 values]}
    pid_catalog_json     TEXT,        -- JSON [{slug,name,unit,kind,group}]
    insights_json        TEXT,        -- JSON [{category,level,title,body}]
    created_at           TEXT DEFAULT (datetime('now'))
);
"""


def trip_exists(trip_id: str) -> bool:
    with _conn() as con:
        row = con.execute("SELECT 1 FROM trips WHERE id=?", (trip_id,)).fetchone()
        return row is not None


def delete_trip(trip_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM trips WHERE id=?", (trip_id,))


def save_trip(trip: dict) -> None:
    """Insert or replace a trip dict (as produced by the parsers)."""
    _j = lambda v: json.dumps(v) if v is not None else None
    sources = trip.get("sources", [])
    is_obd  = "obd" in sources
    is_myop = "myopel" in sources

    with _conn() as con:
        con.execute("""
            INSERT OR REPLACE INTO trips (
                id, source, filename, start_local, start_utc, end_local,
                duration_min, distance_km, avg_speed_kmh, max_speed_kmh,
                avg_rpm, max_rpm, coolant_max_c, oil_temp_max_c,
                odometer_km, air_temp_c, fuel_consumed_l, consumption_l100km,
                dpf_soot_pct, dpf_closed_soot, dpf_regen_active, dpf_regen_state,
                dpf_regen_capability, dpf_regen_capability_st,
                dpf_since_regen_km, dpf_avg_regen_km, dpf_replace_km,
                adblue_vol_l, adblue_range_km,
                exhaust_before_cat_c, exhaust_after_cat_c, nox_cat_temp_max_c,
                battery_startup_v, oil_dilution_pct, ss_state,
                myop_trip_id, myop_fuel_level, myop_fuel_autonomy,
                myop_fuel_consumed_l, myop_price_fuel,
                myop_days_to_service, myop_km_to_service, myop_maintenance_passed,
                alerts_json, gps_track_json, pid_values_json,
                pid_series_json, pid_catalog_json, insights_json
            ) VALUES (
                ?,?,?,?,?,?,
                ?,?,?,?,
                ?,?,?,?,
                ?,?,?,?,
                ?,?,?,?,
                ?,?,
                ?,?,?,
                ?,?,
                ?,?,?,
                ?,?,?,
                ?,?,?,
                ?,?,
                ?,?,?,
                ?,?,?,
                ?,?,?
            )
        """, (
            trip.get("id"),
            "myop" if (is_myop and not is_obd) else "obd_csv",
            trip.get("filename"),
            trip.get("start"),
            trip.get("start_utc"),
            trip.get("end"),
            trip.get("durationMin"),
            trip.get("distanceKm"),
            trip.get("avgSpeedKmh"),
            trip.get("maxSpeedKmh"),
            trip.get("avgRpm"),
            trip.get("maxRpm"),
            trip.get("coolantMaxC"),
            trip.get("oilMaxC"),
            trip.get("odometerKm"),
            trip.get("airTempC"),
            trip.get("fuelConsumedL"),
            trip.get("consumptionL100km"),
            trip.get("dpfSootPct"),
            trip.get("dpfClosedSoot"),
            trip.get("dpfRegenActive"),
            trip.get("dpfRegenState"),
            trip.get("dpfRegenCapability"),
            trip.get("dpfRegenCapabilityST"),
            trip.get("dpfSinceRegenKm"),
            trip.get("dpfAvgRegenKm"),
            trip.get("dpfReplaceKm"),
            trip.get("adblueVolL"),
            trip.get("adblueRangeKm"),
            trip.get("exhaustBeforeCatC"),
            trip.get("exhaustAfterCatC"),
            trip.get("noxCatTempMaxC"),
            trip.get("batteryStartupV"),
            trip.get("oilDilutionPct"),
            trip.get("ssState"),
            trip.get("myopId"),
            trip.get("fuelLevel"),
            trip.get("fuelAutonomy"),
            trip.get("fuelConsumedL") if is_myop else None,
            trip.get("priceFuel"),
            trip.get("daysToService"),
            trip.get("kmToService"),
            1 if trip.get("maintenancePassed") else 0,
            _j(trip.get("alerts", [])),
            _j(trip.get("track")),
            _j(trip.get("pidValues")),
            _j(trip.get("pidSeriesFull")),
            _j(trip.get("pidCatalog", [])),
            _j(trip.get("insights", [])),
        ))


def get_all_trips() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM trips ORDER BY start_local DESC"
        ).fetchall()
    return [_row_to_trip(dict(r)) for r in rows]


def get_trip(trip_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    return _row_to_trip(dict(row)) if row else None


def enrich_with_myop(obd_trip_id: str, myop_trip: dict) -> None:
    """Retroactively add myop fields to an existing OBD trip."""
    with _conn() as con:
        con.execute("""
            UPDATE trips SET
                myop_trip_id        = ?,
                myop_fuel_level     = ?,
                myop_fuel_autonomy  = ?,
                myop_fuel_consumed_l= ?,
                myop_price_fuel     = ?,
                myop_days_to_service= ?,
                myop_km_to_service  = ?,
                myop_maintenance_passed = ?,
                alerts_json         = json(?)
            WHERE id = ?
        """, (
            myop_trip.get("myopId"),
            myop_trip.get("fuelLevel"),
            myop_trip.get("fuelAutonomy"),
            myop_trip.get("fuelConsumedL"),
            myop_trip.get("priceFuel"),
            myop_trip.get("daysToService"),
            myop_trip.get("kmToService"),
            1 if myop_trip.get("maintenancePassed") else 0,
            json.dumps(myop_trip.get("alerts", [])),
            obd_trip_id,
        ))


def _row_to_trip(row: dict) -> dict:
    _j = lambda k: json.loads(row[k]) if row.get(k) else None

    sources: list[str] = []
    if row.get("source") in ("obd_csv", "obd_brc"):
        sources.append("obd")
    if row.get("myop_trip_id") is not None:
        sources.append("myopel")
    if not sources:
        sources = ["myopel" if row.get("source") == "myop" else "obd"]

    return {
        "id":                    row["id"],
        "sources":               sources,
        "filename":              row.get("filename"),
        "myopId":                row.get("myop_trip_id"),
        "start":                 row.get("start_local"),
        "start_utc":             row.get("start_utc"),
        "end":                   row.get("end_local"),
        "durationMin":           row.get("duration_min"),
        "distanceKm":            row.get("distance_km"),
        "avgSpeedKmh":           row.get("avg_speed_kmh"),
        "maxSpeedKmh":           row.get("max_speed_kmh"),
        "avgRpm":                row.get("avg_rpm"),
        "maxRpm":                row.get("max_rpm"),
        "coolantMaxC":           row.get("coolant_max_c"),
        "oilMaxC":               row.get("oil_temp_max_c"),
        "odometerKm":            row.get("odometer_km"),
        "airTempC":              row.get("air_temp_c"),
        "fuelConsumedL":         row.get("fuel_consumed_l"),
        "consumptionL100km":     row.get("consumption_l100km"),
        "dpfSootPct":            row.get("dpf_soot_pct"),
        "dpfClosedSoot":         row.get("dpf_closed_soot"),
        "dpfRegenActive":        row.get("dpf_regen_active") or 0,
        "dpfRegenState":         row.get("dpf_regen_state") or "idle",
        "dpfRegenCapability":    row.get("dpf_regen_capability"),
        "dpfRegenCapabilityST":  row.get("dpf_regen_capability_st"),
        "dpfSinceRegenKm":       row.get("dpf_since_regen_km"),
        "dpfAvgRegenKm":         row.get("dpf_avg_regen_km"),
        "dpfReplaceKm":          row.get("dpf_replace_km"),
        "adblueVolL":            row.get("adblue_vol_l"),
        "adblueRangeKm":         row.get("adblue_range_km"),
        "exhaustBeforeCatC":     row.get("exhaust_before_cat_c"),
        "exhaustAfterCatC":      row.get("exhaust_after_cat_c"),
        "noxCatTempMaxC":        row.get("nox_cat_temp_max_c"),
        "batteryStartupV":       row.get("battery_startup_v"),
        "oilDilutionPct":        row.get("oil_dilution_pct"),
        "ssState":               row.get("ss_state"),
        "fuelLevel":             row.get("myop_fuel_level"),
        "fuelAutonomy":          row.get("myop_fuel_autonomy"),
        "priceFuel":             row.get("myop_price_fuel"),
        "costEur":               (row["myop_fuel_consumed_l"] * row["myop_price_fuel"]
                                   if row.get("myop_fuel_consumed_l") and row.get("myop_price_fuel")
                                   else None),
        "alerts":                _j("alerts_json") or [],
        "track":                 _j("gps_track_json"),
        "pidValues":             _j("pid_values_json"),
        "pidSeriesFull":         _j("pid_series_json"),
        "pidCatalog":            _j("pid_catalog_json") or [],
        "insights":              _j("insights_json") or [],
    }
