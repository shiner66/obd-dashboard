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
        # Idempotent migrations for columns added after the initial schema.
        existing = {row[1] for row in con.execute("PRAGMA table_info(trips)").fetchall()}
        if "merged_ids" not in existing:
            con.execute("ALTER TABLE trips ADD COLUMN merged_ids TEXT DEFAULT NULL")
        if "vin" not in existing:
            con.execute("ALTER TABLE trips ADD COLUMN vin TEXT DEFAULT NULL")


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
    merged_ids           TEXT DEFAULT NULL,
    vin                  TEXT DEFAULT NULL,
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


def update_insights(trip_id: str, insights: list) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE trips SET insights_json=? WHERE id=?",
            (json.dumps(insights), trip_id),
        )


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
                pid_series_json, pid_catalog_json, insights_json, vin
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
                ?,?,?,?
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
            trip.get("vin"),
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
                alerts_json         = json(?),
                vin                 = COALESCE(vin, ?)
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
            myop_trip.get("vin"),
            obd_trip_id,
        ))


def merge_trips(primary_id: str, secondary_ids: list[str]) -> dict:
    """Merge secondary trips into primary. Returns updated primary trip dict."""
    import json as _json
    with _conn() as con:
        primary = con.execute("SELECT * FROM trips WHERE id=?", (primary_id,)).fetchone()
        if not primary:
            raise ValueError(f"Primary trip {primary_id} not found")
        primary = dict(primary)

        secondaries = []
        for sid in secondary_ids:
            row = con.execute("SELECT * FROM trips WHERE id=?", (sid,)).fetchone()
            if row:
                secondaries.append(dict(row))

        if not secondaries:
            return _row_to_trip(primary)

        # Compute merged end time (latest)
        all_ends = [primary.get("end_local", ""), *[s.get("end_local", "") for s in secondaries]]
        merged_end = max(e for e in all_ends if e)

        # Sum numeric fields
        def _sum(*vals):
            return sum(v for v in vals if v is not None) or None

        # Duration = sum of all durations
        merged_dur = _sum(primary.get("duration_min"), *[s.get("duration_min") for s in secondaries])

        # Distance = sum
        merged_dist = _sum(primary.get("distance_km"), *[s.get("distance_km") for s in secondaries])

        # Fuel = sum
        merged_fuel = _sum(primary.get("fuel_consumed_l"), *[s.get("fuel_consumed_l") for s in secondaries])

        # Recalculate L/100km
        merged_l100 = (merged_fuel / merged_dist * 100) if (merged_dist and merged_fuel and merged_dist > 0) else primary.get("consumption_l100km")

        # Recalculate avg speed from merged totals; take MAX across all rows for max speed
        merged_avg_speed = (
            merged_dist / (merged_dur / 60.0)
            if (merged_dist and merged_dur and merged_dur > 0)
            else primary.get("avg_speed_kmh")
        )
        max_speeds = [primary.get("max_speed_kmh"), *[s.get("max_speed_kmh") for s in secondaries]]
        merged_max_speed = max((v for v in max_speeds if v is not None), default=None)

        # Sources: union (stored as source column — keep primary's)
        # alerts: merge from alerts_json
        def _alerts(row):
            try:
                return _json.loads(row.get("alerts_json") or "[]")
            except Exception:
                return []
        all_alerts = list(set(a for r in [primary, *secondaries] for a in _alerts(r)))

        # Merge myop_trip_id and vin (take first non-null)
        merged_myop_id = primary.get("myop_trip_id")
        merged_vin     = primary.get("vin")
        for s in secondaries:
            if not merged_myop_id and s.get("myop_trip_id"):
                merged_myop_id = s.get("myop_trip_id")
            if not merged_vin and s.get("vin"):
                merged_vin = s.get("vin")

        # GPS tracks: concatenate in chain order so the merged path is continuous
        def _track(row):
            try:
                return _json.loads(row.get("gps_track_json") or "[]")
            except Exception:
                return []
        merged_track = _track(primary)
        for s in secondaries:
            merged_track.extend(_track(s))
        merged_track_json = _json.dumps(merged_track) if merged_track else None

        con.execute("""
            UPDATE trips SET
                end_local           = ?,
                duration_min        = ?,
                distance_km         = ?,
                avg_speed_kmh       = ?,
                max_speed_kmh       = ?,
                fuel_consumed_l     = ?,
                consumption_l100km  = ?,
                alerts_json         = ?,
                gps_track_json      = ?,
                myop_trip_id        = ?,
                vin                 = ?,
                merged_ids          = ?
            WHERE id = ?
        """, (
            merged_end,
            merged_dur,
            merged_dist,
            merged_avg_speed,
            merged_max_speed,
            merged_fuel,
            merged_l100,
            _json.dumps(all_alerts),
            merged_track_json,
            merged_myop_id,
            merged_vin,
            _json.dumps([primary_id, *secondary_ids]),
            primary_id,
        ))

        # Delete secondary trips
        for sid in secondary_ids:
            con.execute("DELETE FROM trips WHERE id=?", (sid,))

        row = con.execute("SELECT * FROM trips WHERE id=?", (primary_id,)).fetchone()
        return _row_to_trip(dict(row))


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
        "fuelConsumedL":         row.get("fuel_consumed_l") or row.get("myop_fuel_consumed_l"),
        "consumptionL100km":     row.get("consumption_l100km") or (
            round(row["myop_fuel_consumed_l"] / row["distance_km"] * 100, 2)
            if row.get("myop_fuel_consumed_l") and row.get("distance_km")
            else None
        ),
        "dpfSootPct":            row.get("dpf_soot_pct"),
        "dpfClosedSoot":         row.get("dpf_closed_soot"),
        "dpfRegenActive":        row.get("dpf_regen_active") or 0,
        "dpfRegenState":         row.get("dpf_regen_state") or (None if row.get("source") == "myop" else "idle"),
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
        "daysToService":         row.get("myop_days_to_service"),
        "kmToService":           row.get("myop_km_to_service"),
        "maintenancePassed":     bool(row.get("myop_maintenance_passed")),
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
        "mergedIds":             _j("merged_ids"),
        "vin":                   row.get("vin"),
    }
