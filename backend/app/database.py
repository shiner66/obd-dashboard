"""SQLite persistence — one JSON blob per heavy field to keep schema simple.

Heavy JSON columns (GPS track, PID stats, PID series) are stored
zlib-compressed; readers accept both compressed blobs and legacy plain text.
"""
from __future__ import annotations
import json
import hashlib
import math
import logging
import re
import sqlite3
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_DB_PATH: Path | None = None
_DATA_REVISION = 0
_ROME = ZoneInfo("Europe/Rome")
log = logging.getLogger(__name__)


def init(db_path: str | Path) -> None:
    global _DB_PATH, _DATA_REVISION
    _DB_PATH = Path(db_path)
    _DATA_REVISION += 1
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(_SCHEMA)
        con.executescript(_INSIGHT_SCHEMA)
        # Idempotent column migrations
        existing = {row[1] for row in con.execute("PRAGMA table_info(trips)").fetchall()}
        if "merged_ids" not in existing:
            con.execute("ALTER TABLE trips ADD COLUMN merged_ids TEXT DEFAULT NULL")
        if "vin" not in existing:
            con.execute("ALTER TABLE trips ADD COLUMN vin TEXT DEFAULT NULL")
        if "myop_leg_ids" not in existing:
            con.execute("ALTER TABLE trips ADD COLUMN myop_leg_ids TEXT DEFAULT NULL")
        if "myop_distance_km" not in existing:
            con.execute("ALTER TABLE trips ADD COLUMN myop_distance_km REAL DEFAULT NULL")
        # OBD-native replacements for MyOpel fields + briefing §2/§3 metrics.
        for col, decl in _EXTRA_TRIP_COLUMNS:
            if col not in existing:
                con.execute(f"ALTER TABLE trips ADD COLUMN {col} {decl}")
    _run_data_migrations()


_INSIGHT_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    odometer_km REAL,
    note TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS maintenance_event_time ON maintenance_events(ts);
CREATE TABLE IF NOT EXISTS insight_states (
    rule_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS insight_history (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS insight_history_time ON insight_history(observed_at);
"""


def _maintenance_row(row) -> dict:
    """Expose one persisted intervention through the camelCase API contract."""
    return {"id": row["id"], "ts": row["ts"], "type": row["type"],
            "odometerKm": row["odometer_km"], "note": row["note"], "archived": bool(row["archived"])}


def get_maintenance(include_archived: bool = False) -> list[dict]:
    """Read manual history; archived rows never influence diagnostic baselines."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM maintenance_events " +
                           ("" if include_archived else "WHERE archived=0 ") + "ORDER BY ts DESC,id DESC").fetchall()
    return [_maintenance_row(row) for row in rows]


def save_maintenance(entry: dict, event_id: int | None = None) -> dict | None:
    """Insert or fully replace a validated intervention in one transaction."""
    values = (entry["ts"], entry["type"], entry.get("odometerKm"), entry.get("note", ""), int(entry.get("archived", False)))
    with _conn() as con:
        if event_id is None:
            cursor = con.execute("INSERT INTO maintenance_events(ts,type,odometer_km,note,archived) VALUES(?,?,?,?,?)", values)
            event_id = cursor.lastrowid
        else:
            cursor = con.execute("UPDATE maintenance_events SET ts=?,type=?,odometer_km=?,note=?,archived=? WHERE id=?", (*values, event_id))
            if not cursor.rowcount:
                return None
        row = con.execute("SELECT * FROM maintenance_events WHERE id=?", (event_id,)).fetchone()
    return _maintenance_row(row)


def archive_maintenance(event_id: int) -> bool:
    """Archive an existing intervention without destroying its editable history."""
    with _conn() as con:
        row = con.execute("SELECT archived FROM maintenance_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            return False
        if not row["archived"]:
            con.execute("UPDATE maintenance_events SET archived=1 WHERE id=?", (event_id,))
    return True


def get_insight_states() -> list[dict]:
    """Load the last measured state of each diagnostic rule across restarts."""
    with _conn() as con:
        return [json.loads(row[0]) for row in con.execute("SELECT payload FROM insight_states ORDER BY rule_id")]


def save_insight_reconciliation(result: dict) -> None:
    """Commit changed rule states and deduplicated historical observations atomically."""
    with _conn() as con:
        for state in result.get("changes", []):
            payload = json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False)
            con.execute("INSERT INTO insight_states(rule_id,payload) VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET payload=excluded.payload WHERE payload<>excluded.payload", (state["ruleId"], payload))
        for event in result.get("history", []):
            payload = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
            fingerprint = hashlib.sha256(payload.encode()).hexdigest()
            observed = event.get("lastObservationAt") or event.get("observedAt") or event.get("lastSeen")
            if observed:
                con.execute("INSERT OR IGNORE INTO insight_history(id,rule_id,observed_at,payload) VALUES(?,?,?,?)", (fingerprint, event["ruleId"], observed, payload))


def get_insight_history() -> list[dict]:
    """Read real diagnostic observations in chronological order, with stable identifiers."""
    with _conn() as con:
        return [{**json.loads(row["payload"]), "historyId": row["id"], "observedAt": row["observed_at"]}
                for row in con.execute("SELECT * FROM insight_history ORDER BY observed_at,id")]


# Columns added after the original schema — OBD-native fuel/service fields and
# the briefing's derived per-trip metrics (§2 mass-based fuel, §3 idle / speed
# bands / wheel-vs-GPS ratio). Kept as an idempotent ALTER list so an existing
# DB upgrades in place without a destructive migration.
_EXTRA_TRIP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("fuel_level_obd",      "REAL"),      # [ECM] Fuel tank level, % — OBD twin of myop_fuel_level
    ("oil_km_to_service",   "REAL"),      # [ECM] Distance until next oil change — OBD twin of kmToService
    ("fuel_mass_g",         "REAL"),      # §2 injector-mass integral, grams
    ("g_per_km",            "REAL"),      # §2 mass consumption, density-independent
    ("fuel_validation_gl",  "REAL"),      # fuel_mass_g / litres → should sit near 835 g/L
    ("ac_current_ma",       "REAL"),      # §2 mean A/C compressor solenoid current
    ("ac_active_pct",       "REAL"),      # §2 share of samples with A/C engaged (>300 mA)
    ("idle_seconds",        "REAL"),      # §3 seconds spent below ~2 km/h with engine on
    ("idle_share_pct",      "REAL"),      # §3 idle share of engine-on time
    ("idle_fuel_g",         "REAL"),      # §3 grams burned at idle
    ("speed_bands_json",    "TEXT"),      # §3 {band: km} distance split by speed band
    ("ratio_wg",            "REAL"),      # §3 median wheel-speed / GPS-speed (tyre monitor)
    ("fc_suspect",          "INTEGER"),   # §1 stale MyOpel fuelConsumption flag
    ("metadata_json",       "TEXT"),     # versioned provenance, coverage and time axes
    ("myop_fuel_ul",        "INTEGER"),   # §1 raw MyOpel fuelConsumption, microlitres
)


# ── Data migrations (tracked via PRAGMA user_version) ─────────────────────────

def _run_data_migrations() -> None:
    """Apply pending one-time data migrations in version order."""
    with _conn() as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        _migrate_v1_fix_myop_dst()
        with _conn() as con:
            con.execute("PRAGMA user_version = 1")

    if version < 2:
        _migrate_v2_restore_obd_and_reset_correlations()
        with _conn() as con:
            con.execute("PRAGMA user_version = 2")

    if version < 3:
        _migrate_v3_remove_sub1km_obd()
        with _conn() as con:
            con.execute("PRAGMA user_version = 3")

    if version < 4:
        _migrate_v4_fix_distance_and_reset_correlation()
        with _conn() as con:
            con.execute("PRAGMA user_version = 4")

    if version < 5:
        _migrate_v5_reset_myop_correlations()
        with _conn() as con:
            con.execute("PRAGMA user_version = 5")

    if version < 6:
        _migrate_v6_compact_storage()
        with _conn() as con:
            con.execute("PRAGMA user_version = 6")

    if version < 7:
        _migrate_v7_compress_blobs()
        with _conn() as con:
            con.execute("PRAGMA user_version = 7")

    if version < 8:
        _migrate_v8_fix_empty_starts()
        with _conn() as con:
            con.execute("PRAGMA user_version = 8")


    if version < 9:
        # Additive migration: keep every original row, including compressed blobs.
        # Rebuilding derived sessions from source files is an explicit offline job.
        with _conn() as con:
            con.execute("CREATE TABLE IF NOT EXISTS trips_before_v9 AS SELECT * FROM trips")
            con.execute("PRAGMA user_version = 9")


def _migrate_v1_fix_myop_dst() -> None:
    """Migration 1: fix myop timestamps in CEST — standalone myop entries only.

    NOTE: v1 was originally buggy and also modified OBD+myop trips (wrong).
    This corrected version skips OBD trips.  The v2 migration undoes the damage
    from the original v1 on OBD trips.
    """
    trips = get_all_trips()
    fixed = 0
    for trip in trips:
        if "obd" in trip.get("sources", []):
            continue  # OBD timestamps come from CarScanner — already correct
        if "myopel" not in trip.get("sources", []):
            continue
        start = trip.get("start")
        if not start:
            continue
        try:
            dt_s = datetime.fromisoformat(start)
            dst = dt_s.replace(tzinfo=_ROME).dst()
            if not dst or dst.total_seconds() == 0:
                continue
            new_start = (dt_s - dst).isoformat()
            end = trip.get("end") or ""
            new_end = end
            if end:
                try:
                    new_end = (datetime.fromisoformat(end) - dst).isoformat()
                except ValueError:
                    pass
            with _conn() as con:
                con.execute(
                    "UPDATE trips SET start_local=?, end_local=? WHERE id=?",
                    (new_start, new_end, trip["id"]),
                )
            fixed += 1
        except Exception:
            log.exception("migrate_v1: error on trip %s", trip.get("id"))
    log.info("migrate_v1_fix_myop_dst: corrected %d standalone myop trips", fixed)


_OBD_ID_RE = re.compile(r"^obd-(\d{4}-\d{2}-\d{2})[_ ](\d{2})-(\d{2})-(\d{2})$")


def _id_to_start(trip_id: str) -> datetime | None:
    """Recover the correct start datetime from the OBD trip ID (= CarScanner filename timestamp)."""
    m = _OBD_ID_RE.match(trip_id)
    if not m:
        return None
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}")
    except ValueError:
        return None


def _migrate_v2_restore_obd_and_reset_correlations() -> None:
    """Migration 2: restore OBD trip timestamps and reset all myop correlations.

    Migration v1 (buggy version) wrongly subtracted 1 h from OBD trips that
    had already been correlated with MyOpel data.  The correct OBD start time
    is always encoded in the trip ID (derived from the CarScanner filename, which
    uses Italian local time).

    We also clear all myop_trip_id references so that auto_correlate_all() can
    re-correlate from scratch with the corrected timestamps and the improved
    scoring function (which handles both raw and DST-adjusted MyOpel times).
    """
    with _conn() as con:
        rows = con.execute("SELECT id, start_local, end_local FROM trips WHERE source='obd_csv'").fetchall()

    restored = 0
    for row in rows:
        tid = row["id"]
        correct_start = _id_to_start(tid)
        if correct_start is None:
            continue
        try:
            db_start = datetime.fromisoformat(row["start_local"][:19])
        except (ValueError, TypeError):
            continue

        delta = correct_start - db_start
        if abs(delta.total_seconds()) < 60:
            continue  # already correct

        # Shift end_local by the same delta
        new_end = row["end_local"]
        if new_end:
            try:
                new_end = (datetime.fromisoformat(new_end[:26]) + delta).isoformat()
            except (ValueError, TypeError):
                pass

        with _conn() as con:
            con.execute(
                "UPDATE trips SET start_local=?, end_local=? WHERE id=?",
                (correct_start.isoformat(), new_end, tid),
            )
        restored += 1

    # Reset all myop correlations — auto_correlate_all() re-runs from scratch
    with _conn() as con:
        con.execute("UPDATE trips SET myop_trip_id=NULL WHERE source='obd_csv'")

    log.info(
        "migrate_v2: restored timestamps for %d OBD trips; reset all myop correlations",
        restored,
    )


def _migrate_v3_remove_sub1km_obd() -> None:
    """Migration 3: delete OBD trips shorter than 1 km that slipped in before the filter."""
    with _conn() as con:
        res = con.execute(
            "DELETE FROM trips WHERE source='obd_csv' AND (distance_km IS NULL OR distance_km < 1.0)"
        )
    if res.rowcount:
        log.info("migrate_v3: deleted %d sub-1km OBD trips", res.rowcount)


def _migrate_v4_fix_distance_and_reset_correlation() -> None:
    """Migration 4: re-ingest OBD trips with corrected distance and reset correlation.

    The previous parser computed OBD trip distance from the noisy GPS-speed
    integral, which diverged badly from the true distance, broke correlation,
    and left sub-1 km "phantom" trips (long idle recordings) in the DB that the
    odometer-based distance now correctly rejects.

    Rather than patch each stored row, we delete all OBD trips so the startup
    directory scan re-parses them from the source CSV/BRC files (the persistent
    source of truth in the watched /data/obd folder) with the corrected logic.
    Standalone MyOpel legs absorbed by earlier correlations are likewise restored
    when the cumulative .myop file is re-scanned. auto_correlate_all() then
    regroups everything from scratch with the new session-grouping algorithm.

    If the source files are unavailable the OBD aggregates are rebuilt on the
    next file drop; MyOpel data is untouched.
    """
    with _conn() as con:
        n_obd = con.execute("SELECT COUNT(*) FROM trips WHERE source='obd_csv'").fetchone()[0]
        con.execute("DELETE FROM trips WHERE source='obd_csv'")
        # Drop the standalone-MyOpel rows too: the cumulative .myop file re-adds
        # every leg on the next scan, so this guarantees a clean, complete regroup
        # instead of a mix of old correlated state and freshly parsed OBD trips.
        con.execute("DELETE FROM trips WHERE source='myop'")
    log.info("migrate_v4: cleared %d OBD trips for re-ingest with corrected distances", n_obd)


def _migrate_v5_reset_myop_correlations() -> None:
    """Migration 5: reset all OBD↔MyOpel correlations for the hardened grouper.

    The previous session-grouping tried the DST-adjusted (raw − 1 h) timestamp
    for *every* leg and never checked distances, so separate MyOpel drives were
    occasionally absorbed into the wrong OBD session. Clearing the enrichment
    lets auto_correlate_all() regroup from scratch with the raw-first two-pass
    matching and the distance budget. Absorbed legs are restored automatically:
    the cumulative .myop file in the watch directory re-adds every missing leg
    on the next startup scan.

    OBD alerts always come from MyOpel legs, so they are reset together with
    the other enrichment fields.
    """
    with _conn() as con:
        res = con.execute("""
            UPDATE trips SET
                myop_trip_id = NULL, myop_leg_ids = NULL, myop_distance_km = NULL,
                myop_fuel_level = NULL, myop_fuel_autonomy = NULL,
                myop_fuel_consumed_l = NULL, myop_price_fuel = NULL,
                myop_days_to_service = NULL, myop_km_to_service = NULL,
                myop_maintenance_passed = 0, alerts_json = '[]'
            WHERE source = 'obd_csv' AND myop_trip_id IS NOT NULL
        """)
    if res.rowcount:
        log.info("migrate_v5: reset myop correlation on %d OBD trips", res.rowcount)


def _migrate_v6_compact_storage() -> None:
    """Migration 6: move PID catalogs to the global table and compact JSON blobs.

    Before: every OBD trip stored its own ~36 KB pid_catalog_json (≈3 MB of
    duplication), full-precision floats in the series, and 13+ decimal GPS
    coordinates. This migration:
      • merges all per-trip catalogs into the pid_catalog table, then clears
        pid_catalog_json
      • rounds series values to 2 decimals and drops constant series (their
        sparkline is a flat line — the stats row already tells the story)
      • rounds GPS coordinates to 5 decimals (≈1.1 m, below GPS accuracy)
      • VACUUMs to reclaim the freed pages
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT id, pid_catalog_json, pid_series_json, gps_track_json "
            "FROM trips WHERE pid_catalog_json IS NOT NULL OR pid_series_json IS NOT NULL "
            "OR gps_track_json IS NOT NULL"
        ).fetchall()

    compacted = 0
    for row in rows:
        tid = row["id"]
        try:
            catalog = json.loads(row["pid_catalog_json"]) if row["pid_catalog_json"] else []
            if catalog:
                upsert_pid_catalog(catalog)

            series = json.loads(row["pid_series_json"]) if row["pid_series_json"] else None
            if series:
                slim = {}
                for slug, vals in series.items():
                    if not vals:
                        continue
                    nums = [v for v in vals if isinstance(v, (int, float))]
                    if nums and min(nums) == max(nums):
                        continue  # constant — nothing to plot
                    slim[slug] = [round(v, 2) if isinstance(v, float) else v for v in vals]
                series = slim or None

            track = json.loads(row["gps_track_json"]) if row["gps_track_json"] else None
            if track:
                track = [[round(p[0], 5), round(p[1], 5)] for p in track if len(p) >= 2]

            with _conn() as con:
                con.execute(
                    "UPDATE trips SET pid_catalog_json=NULL, pid_series_json=?, gps_track_json=? WHERE id=?",
                    (json.dumps(series) if series else None,
                     json.dumps(track) if track else None,
                     tid),
                )
            compacted += 1
        except Exception:
            log.exception("migrate_v6: error compacting trip %s", tid)

    _vacuum()
    log.info("migrate_v6: compacted %d trips, catalog moved to global table", compacted)


def _migrate_v7_compress_blobs() -> None:
    """Migration 7: zlib-compress the heavy JSON columns and VACUUM.

    JSON is extremely repetitive (the same stat keys per PID per trip), so
    zlib at level 6 typically shrinks these columns by 70-85 % for negligible
    CPU. Readers are format-agnostic (compressed blob or legacy text).
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT id, gps_track_json, pid_values_json, pid_series_json FROM trips"
        ).fetchall()

    before = after = 0
    for row in rows:
        updates: dict[str, bytes] = {}
        for col in ("gps_track_json", "pid_values_json", "pid_series_json"):
            val = row[col]
            if isinstance(val, str) and val:
                packed = zlib.compress(val.encode("utf-8"), 6)
                before += len(val)
                after += len(packed)
                updates[col] = packed
        if updates:
            sets = ", ".join(f"{c}=?" for c in updates)
            with _conn() as con:
                con.execute(f"UPDATE trips SET {sets} WHERE id=?",
                            (*updates.values(), row["id"]))

    _vacuum()
    if before:
        log.info("migrate_v7: JSON blobs %0.1f MB → %0.1f MB (−%d%%)",
                 before / 1e6, after / 1e6, round((1 - after / before) * 100))


_OBD_ID_COMPACT_RE = re.compile(r"^obd-(\d{4})(\d{2})(\d{2})[_ ](\d{2})(\d{2})(\d{2})$")


def _migrate_v8_fix_empty_starts() -> None:
    """Migration 8: recover start/end for OBD trips saved with an empty start.

    CarScanner's newer export names files ``YYYYMMDD_HHMMSS.csv``; the old
    parser didn't recognise that pattern and stored start_local="", which
    breaks chronological sorting and every trend rule. The timestamp is
    recoverable from the trip id.
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT id, duration_min FROM trips "
            "WHERE source='obd_csv' AND (start_local IS NULL OR start_local='')"
        ).fetchall()
    fixed = 0
    for row in rows:
        m = _OBD_ID_COMPACT_RE.match(row["id"])
        if not m:
            continue
        try:
            start = datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]))
        except ValueError:
            continue
        end = start + timedelta(minutes=row["duration_min"] or 0)
        with _conn() as con:
            con.execute("UPDATE trips SET start_local=?, end_local=? WHERE id=?",
                        (start.isoformat(), end.isoformat(), row["id"]))
        fixed += 1
    if fixed:
        log.info("migrate_v8: recovered timestamps for %d trips with empty start", fixed)


# ── Compressed JSON helpers ───────────────────────────────────────────────────

def _pack_json(obj) -> bytes | None:
    """Serialize + zlib-compress an object for a heavy JSON column."""
    if obj is None:
        return None
    return zlib.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), 6)


def _unpack_json(data):
    """Read a JSON column that may be a zlib blob (new) or plain text (legacy)."""
    if data is None:
        return None
    if isinstance(data, bytes):
        try:
            data = zlib.decompress(data).decode("utf-8")
        except zlib.error:
            data = data.decode("utf-8", "replace")
    return json.loads(data) if data else None


@contextmanager
def _conn():
    """Connection context: commit on success, rollback on error, always close.

    `with sqlite3.connect(...)` alone only commits — it leaves the connection
    open until GC, which keeps the WAL pinned and prevents VACUUM from ever
    truncating the main file.
    """
    if _DB_PATH is None:
        raise RuntimeError("Database not initialised — call init() first")
    global _DATA_REVISION
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
        if con.total_changes:
            _DATA_REVISION += 1
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _vacuum() -> None:
    """Checkpoint the WAL and rebuild the database file to its minimal size."""
    try:
        con = sqlite3.connect(_DB_PATH)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    except sqlite3.Error:
        log.exception("VACUUM failed (non-fatal)")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_trip_revisions (
    trip_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    parser_version INTEGER NOT NULL DEFAULT 1,
    payload BLOB NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (trip_id, revision)
);
CREATE INDEX IF NOT EXISTS raw_trip_kind ON raw_trip_revisions(source_kind);
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
    myop_leg_ids         TEXT,        -- JSON array of MyOpel leg ids absorbed
    myop_distance_km     REAL,        -- sum of absorbed MyOpel leg distances
    alerts_json          TEXT,        -- JSON array of int
    gps_track_json       TEXT,        -- JSON [[lat,lon],...]
    pid_values_json      TEXT,        -- JSON {slug: stats}
    pid_series_json      TEXT,        -- JSON {slug: [60 values]}
    pid_catalog_json     TEXT,        -- legacy, superseded by the pid_catalog table
    insights_json        TEXT,        -- JSON [{category,level,title,body}]
    merged_ids           TEXT DEFAULT NULL,
    vin                  TEXT DEFAULT NULL,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS trips_start_local_idx ON trips(start_local);
CREATE TABLE IF NOT EXISTS trip_summaries (
    trip_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trips_summary_update AFTER UPDATE ON trips
BEGIN DELETE FROM trip_summaries WHERE trip_id=OLD.id; END;
CREATE TRIGGER IF NOT EXISTS trips_summary_delete AFTER DELETE ON trips
BEGIN DELETE FROM trip_summaries WHERE trip_id=OLD.id; END;
CREATE TRIGGER IF NOT EXISTS trips_summary_insert AFTER INSERT ON trips
BEGIN DELETE FROM trip_summaries WHERE trip_id=NEW.id; END;
CREATE TABLE IF NOT EXISTS source_parse_cache (
    source_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    parser_version INTEGER NOT NULL,
    signature_json TEXT,
    trip_ids TEXT NOT NULL
);

-- Global PID catalog, deduplicated by slug (previously duplicated per trip).
CREATE TABLE IF NOT EXISTS pid_catalog (
    slug    TEXT PRIMARY KEY,
    name    TEXT,
    short   TEXT,
    unit    TEXT,
    kind    TEXT,
    grp     TEXT,
    useful  INTEGER DEFAULT 0
);

-- Ledger of ingested source files, keyed by sha256 of the *uncompressed*
-- content (stable whether the file sits in the watch dir or gzipped in
-- archive/). Lets the app archive/dedupe sources safely after ingestion.
CREATE TABLE IF NOT EXISTS ingested_files (
    sha256      TEXT PRIMARY KEY,
    filename    TEXT,
    kind        TEXT,              -- "obd" | "myop"
    size_bytes  INTEGER,           -- original (uncompressed) size
    trip_ids    TEXT,              -- JSON array of trip ids produced
    archived_as TEXT,              -- path relative to the watch dir, if archived
    ingested_at TEXT DEFAULT (datetime('now'))
);

-- Simple key/value app settings (JSON-encoded values). Lets the user flip
-- runtime options — e.g. disable the MyOpel source — without a restart.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,              -- JSON-encoded
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Manual refuel ledger. The fuel-level-by-subtraction engine (services/fuel.py)
-- walks these fills together with OBD-measured consumption to reconstruct the
-- tank level and true tank-to-tank economy — no MyOpel required.
CREATE TABLE IF NOT EXISTS refuels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,              -- ISO local datetime of the fill
    odometer_km REAL,              -- odometer reading at the pump
    liters      REAL,              -- litres dispensed (pump ground truth)
    price_per_l REAL,              -- €/L, optional
    fuel_type   TEXT,              -- 'B7' | 'HVO' | free text
    full_tank   INTEGER DEFAULT 1, -- 1 = filled to full (anchors the tank model)
    note        TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""


def file_ingested(sha256: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM ingested_files WHERE sha256=?", (sha256,)).fetchone()
    return dict(row) if row else None


def record_ingested_file(sha256: str, filename: str, kind: str, size_bytes: int,
                         trip_ids: list[str], archived_as: str | None) -> None:
    with _conn() as con:
        con.execute("""
            INSERT INTO ingested_files (sha256, filename, kind, size_bytes, trip_ids, archived_as)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(sha256) DO UPDATE SET
                filename=excluded.filename,
                archived_as=COALESCE(excluded.archived_as, ingested_files.archived_as)
        """, (sha256, filename, kind, size_bytes, json.dumps(trip_ids), archived_as))


def ledger_stats() -> dict:
    with _conn() as con:
        row = con.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS original_bytes,
                   SUM(CASE WHEN archived_as IS NOT NULL THEN 1 ELSE 0 END) AS archived
            FROM ingested_files
        """).fetchone()
    return {"files": row["n"], "original_bytes": row["original_bytes"],
            "archived": row["archived"] or 0}


# ── Settings (key/value, JSON-encoded) ────────────────────────────────────────

def get_setting(key: str, default=None):
    """Return a JSON-decoded setting value, or `default` if unset/unparseable."""
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None or row["value"] is None:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


def set_setting(key: str, value) -> None:
    """Store a setting as a JSON-encoded value (upsert)."""
    with _conn() as con:
        con.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, json.dumps(value)))


def get_all_settings() -> dict:
    """All stored settings as a {key: decoded_value} dict."""
    with _conn() as con:
        rows = con.execute("SELECT key, value FROM settings").fetchall()
    out: dict = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"]) if r["value"] is not None else None
        except (ValueError, TypeError):
            out[r["key"]] = None
    return out


# ── Refuel ledger ─────────────────────────────────────────────────────────────

def add_refuel(entry: dict) -> dict:
    """Insert a refuel (camelCase dict from the API) and return the stored row."""
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO refuels (ts, odometer_km, liters, price_per_l, fuel_type, full_tank, note)
            VALUES (?,?,?,?,?,?,?)
        """, (
            entry.get("ts"),
            entry.get("odometerKm"),
            entry.get("liters"),
            entry.get("pricePerL"),
            entry.get("fuelType"),
            1 if entry.get("fullTank", True) else 0,
            entry.get("note"),
        ))
        rid = cur.lastrowid
        row = con.execute("SELECT * FROM refuels WHERE id=?", (rid,)).fetchone()
    return _row_to_refuel(dict(row))


def get_refuels() -> list[dict]:
    """All refuels ordered by odometer (then time) — the order the fuel model walks."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM refuels ORDER BY odometer_km, ts").fetchall()
    return [_row_to_refuel(dict(r)) for r in rows]


def update_refuel(refuel_id: int, entry: dict) -> dict | None:
    """Atomically replace the validated editable fields of an existing refuel."""
    with _conn() as con:
        cursor = con.execute("""UPDATE refuels SET ts=?,odometer_km=?,liters=?,price_per_l=?,
            fuel_type=?,full_tank=?,note=? WHERE id=?""", (entry.get("ts"), entry.get("odometerKm"),
            entry["liters"], entry.get("pricePerL"), entry.get("fuelType"),
            int(entry.get("fullTank", True)), entry.get("note"), refuel_id))
        if not cursor.rowcount:
            return None
        row = con.execute("SELECT * FROM refuels WHERE id=?", (refuel_id,)).fetchone()
    return _row_to_refuel(dict(row))


def delete_refuel(refuel_id: int) -> None:
    """Remove a refuel from the ledger by id."""
    with _conn() as con:
        con.execute("DELETE FROM refuels WHERE id=?", (refuel_id,))


def _row_to_refuel(row: dict) -> dict:
    return {
        "id":         row["id"],
        "ts":         row.get("ts"),
        "odometerKm": row.get("odometer_km"),
        "liters":     row.get("liters"),
        "pricePerL":  row.get("price_per_l"),
        "fuelType":   row.get("fuel_type"),
        "fullTank":   bool(row.get("full_tank")),
        "note":       row.get("note"),
        "createdAt":  row.get("created_at"),
    }


def upsert_pid_catalog(entries: list[dict]) -> None:
    """Merge catalog entries into the global table.

    `useful` is sticky: a PID that moves in *any* trip stays surfaced even if
    it sits constant in most sessions (e.g. EGT spikes only during regens).
    """
    if not entries:
        return
    with _conn() as con:
        con.executemany("""
            INSERT INTO pid_catalog (slug, name, short, unit, kind, grp, useful)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                useful = MAX(pid_catalog.useful, excluded.useful)
        """, [
            (e.get("slug"), e.get("name"), e.get("short"), e.get("unit"),
             e.get("kind"), e.get("group"), 1 if e.get("useful") else 0)
            for e in entries if e.get("slug")
        ])


def get_pid_catalog() -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM pid_catalog ORDER BY name").fetchall()
    return [{
        "slug":   r["slug"],
        "name":   r["name"] or r["slug"],
        "short":  r["short"] or r["name"] or r["slug"],
        "unit":   r["unit"] or "",
        "kind":   r["kind"] or "number",
        "group":  r["grp"] or "Sensori",
        "useful": bool(r["useful"]),
    } for r in rows]


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


def save_trip(trip: dict, *, record_raw: bool = True) -> None:
    """Store a materialized trip and preserve immutable parser revisions by default.

    Derived merges pass record_raw=False so they cannot overwrite original legs.
    """
    if record_raw:
        save_raw_trip(trip)
    _j = lambda v: json.dumps(v) if v is not None else None
    sources = trip.get("sources", [])
    is_obd  = "obd" in sources
    is_myop = "myopel" in sources

    # The catalog is global — one row per slug, not one copy per trip.
    upsert_pid_catalog(trip.get("pidCatalog") or [])

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
                pid_series_json, insights_json, vin
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
            trip.get("obdFuelConsumedL", trip.get("fuelConsumedL")) if is_obd else trip.get("fuelConsumedL"),
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
            _pack_json(trip.get("track")),
            _pack_json(trip.get("pidValues")),
            _pack_json(trip.get("pidSeriesFull")),
            _j(trip.get("insights", [])),
            trip.get("vin"),
        ))

        con.execute("UPDATE trips SET metadata_json=?,merged_ids=?,myop_leg_ids=?,myop_distance_km=? WHERE id=?",
                    (_pack_json({k: trip.get(k) for k in _METADATA_FIELDS if k in trip}),
                     _j(trip.get("mergedIds")), _j(trip.get("myopLegIds")), trip.get("myopDistanceKm"), trip["id"]))
        if trip.get("myopFuelConsumedL") is not None:
            con.execute("UPDATE trips SET myop_fuel_consumed_l=? WHERE id=?", (trip["myopFuelConsumedL"], trip["id"]))

        # OBD-native + derived metrics (added after the base schema).
        con.execute("""
            UPDATE trips SET
                fuel_level_obd=?, oil_km_to_service=?, fuel_mass_g=?, g_per_km=?,
                fuel_validation_gl=?, ac_current_ma=?, ac_active_pct=?,
                idle_seconds=?, idle_share_pct=?, idle_fuel_g=?, speed_bands_json=?,
                ratio_wg=?, fc_suspect=?, myop_fuel_ul=?
            WHERE id=?
        """, (
            trip.get("fuelLevelObd"),
            trip.get("oilKmToService"),
            trip.get("fuelMassG"),
            trip.get("gPerKm"),
            trip.get("fuelValidationGL"),
            trip.get("acCurrentMa"),
            trip.get("acActivePct"),
            trip.get("idleSeconds"),
            trip.get("idleSharePct"),
            trip.get("idleFuelG"),
            _j(trip.get("speedBandsKm")),
            trip.get("ratioWg"),
            1 if trip.get("fcSuspect") else (0 if trip.get("fcSuspect") is not None else None),
            trip.get("myopFuelUl"),
            trip.get("id"),
        ))


_METADATA_FIELDS = (
    "parserVersion", "distanceSource", "fuelRateCoveragePct", "fuelMassCoveragePct",
    "fuelSource", "fuelCoveragePct", "qualityWarnings", "elapsedDurationMin",
    "recordingGapSeconds", "engineOnDurationMin", "pidSeriesTimes",
    "observedWindows", "dpfEndObservedActive", "dpfQualityWarnings",
    "fuelDensityGL", "myopFuelConsumedL",
)


def save_raw_trip(trip: dict) -> bool:
    """Append an immutable source revision if its meaningful payload changed."""
    clean = {k: v for k, v in trip.items() if k not in ("insights", "filename")}
    encoded = json.dumps(clean, sort_keys=True, ensure_ascii=False, allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with _conn() as con:
        old = con.execute("SELECT revision, content_hash FROM raw_trip_revisions WHERE trip_id=? ORDER BY revision DESC LIMIT 1", (trip["id"],)).fetchone()
        if old and old["content_hash"] == digest:
            return False
        con.execute("INSERT INTO raw_trip_revisions(trip_id,revision,content_hash,source_kind,parser_version,payload) VALUES(?,?,?,?,?,?)",
                    (trip["id"], old["revision"] + 1 if old else 1, digest,
                     "obd" if "obd" in trip.get("sources", []) else "myopel",
                     trip.get("parserVersion", 1), _pack_json(trip)))
    return True


def raw_trip_revision_known(trip: dict) -> bool:
    """Recognize any archived revision without promoting it over the chosen latest."""
    clean = {k: v for k, v in trip.items() if k not in ("insights", "filename")}
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with _conn() as con:
        return con.execute("SELECT 1 FROM raw_trip_revisions WHERE trip_id=? AND content_hash=? LIMIT 1", (trip["id"], digest)).fetchone() is not None


def raw_trip_exists(trip_id: str) -> bool:
    """Whether a parser observation exists, including absorbed segments."""
    with _conn() as con:
        return con.execute("SELECT 1 FROM raw_trip_revisions WHERE trip_id=? LIMIT 1", (trip_id,)).fetchone() is not None


def get_raw_trip(trip_id: str) -> dict | None:
    """Return the latest immutable parser observation for an original trip ID."""
    with _conn() as con:
        row = con.execute("SELECT payload FROM raw_trip_revisions WHERE trip_id=? ORDER BY revision DESC LIMIT 1", (trip_id,)).fetchone()
    return _unpack_json(row["payload"]) if row else None


def get_raw_trips(kind: str | None = None) -> list[dict]:
    """Latest source observations, optionally restricted to obd or myopel."""
    with _conn() as con:
        rows = con.execute("""SELECT r.payload FROM raw_trip_revisions r
            JOIN (SELECT trip_id, MAX(revision) revision FROM raw_trip_revisions GROUP BY trip_id) latest
            USING(trip_id,revision) WHERE (? IS NULL OR r.source_kind=?)""", (kind, kind)).fetchall()
    return [_unpack_json(r["payload"]) for r in rows]


def set_settings(mapping: dict) -> None:
    """Atomically store a prevalidated batch of settings."""
    with _conn() as con:
        con.executemany("""INSERT INTO settings(key,value,updated_at) VALUES(?,?,datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                        [(key, json.dumps(value, allow_nan=False)) for key, value in mapping.items()])


def apply_source_observation(trip: dict) -> bool:
    """Apply a new raw revision without dropping the other segments of a session.

    Call after save_raw_trip. An incomplete legacy session stays materialized as
    before and is repaired only by the explicit offline rebuild.
    """
    affected = next((t for t in get_all_trips() if trip["id"] in (t.get("mergedIds") or []) and len(t["mergedIds"]) > 1), None)
    if affected is None:
        save_trip(trip, record_raw=False)
        return True
    member_ids = affected["mergedIds"]
    members = [get_raw_trip(tid) for tid in member_ids]
    legs = affected.get("myopLegIds") or ([affected["myopId"]] if affected.get("myopId") is not None else [])
    if any(member is None for member in members) or any(not raw_trip_exists(f"myop-{i}") for i in legs):
        log.warning("Raw revision retained; legacy session %s requires complete source rebuild", affected["id"])
        return False
    for member in members:
        save_trip(member, record_raw=False)
    merge_trips(affected["id"], [tid for tid in member_ids if tid != affected["id"]])
    return True


def rebuild_materialized_from_raw() -> dict:
    """Explicit offline rebuild after parsing *all* source copies into raw revisions.

    This is never invoked by init/startup. Legacy rows without complete original
    coverage remain in place; their overlapping source observations are withheld.
    Call the correlator afterwards to rebuild sessions and MyOpel enrichment.
    """
    def canonical(tid):
        """Normalize only the known duplicate-export suffix, preserving identity."""
        return re.sub(r"__zip_[0-9a-fA-F]+$", "", tid)
    raw = {t["id"]: t for t in get_raw_trips()}
    existing = get_all_trips()
    replace_ids, protected, held_raw = [], [], set()
    for trip in existing:
        ids = {canonical(tid) for tid in (trip.get("mergedIds") or [trip["id"]])}
        # An OBD row can also contain legacy MyOpel enrichment: require those legs
        # before replacing it so a partial source copy cannot lose that history.
        legs = trip.get("myopLegIds") or ([trip["myopId"]] if trip.get("myopId") is not None else [])
        needed = ids | {f"myop-{i}" for i in legs}
        if needed <= raw.keys():
            replace_ids.append(trip["id"])
        else:
            protected.append(trip["id"])
            held_raw.update(needed & raw.keys())
    with _conn() as con:
        # Copy every pre-rebuild row, including latest settings/refuels elsewhere.
        con.execute("CREATE TABLE IF NOT EXISTS trips_before_rebuild AS SELECT * FROM trips")
        con.executemany("DELETE FROM trips WHERE id=?", [(tid,) for tid in replace_ids])
    written = 0
    for tid, observation in raw.items():
        if tid not in held_raw:
            save_trip(observation, record_raw=False)
            written += 1
    return {"replaced": len(replace_ids), "materializedRaw": written,
            "preservedLegacy": protected, "withheldRawIds": sorted(held_raw)}


_INSIGHT_SLUGS = {"rpm", "fuel_p_d", "ecm_measured_high_pressure_common_rail_fuel_pressure",
    "boost", "oil_dil", "regen_st", "regen_dist", "regen_avg", "soot_cl", "bat_v",
    "coolant", "coolant_c", "urea_km", "fuel_rate", "inj_q", "speed", "speed_v",
    "ac_amp", "ss_state", "egt_a", "egt_dpf_i", "egt_dpf_o", "nox_t"}


def data_revision() -> tuple[str, int]:
    """A process-local cache token changes after every committed database mutation."""
    return str(_DB_PATH), _DATA_REVISION


def _date_filter(from_date: str | None, to_date: str | None, column="start_local") -> tuple[str, list]:
    """Build parameterized inclusive local-day bounds for an ISO timestamp column."""
    clauses, values = [], []
    if from_date:
        clauses.append(f"{column} >= ?")
        values.append(from_date)
    if to_date:
        if to_date == "9999-12-31":
            # datetime cannot represent the next day after its maximum year.
            clauses.append(f"{column} <= ?")
            values.append("9999-12-31T23:59:59.999999")
        else:
            clauses.append(f"{column} < ?")
            values.append((datetime.fromisoformat(to_date) + timedelta(days=1)).date().isoformat())
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", values


def get_trip_summaries(from_date: str | None = None, to_date: str | None = None,
                       *, include_insight_features: bool = False) -> list[dict]:
    """Read summaries without decompressing tracks/PID blobs after each row's first read.

    SQLite triggers invalidate only changed trips. Compact rule input statistics
    are cached with each summary and never exposed by the summary API by default.
    """
    where, params = _date_filter(from_date, to_date, "t.start_local")
    with _conn() as con:
        dirty_where = where + (" AND " if where else " WHERE ") + "s.trip_id IS NULL"
        rows = con.execute("SELECT t.* FROM trips t LEFT JOIN trip_summaries s ON s.trip_id=t.id" + dirty_where, params).fetchall()
        updates = []
        for row in rows:
            trip = _row_to_trip(dict(row))
            summary = {key: value for key, value in trip.items()
                       if key not in {"pidValues", "pidSeriesFull", "pidSeriesTimes", "track"}}
            summary["hasTrack"] = bool(trip.get("track"))
            track = trip.get("track") or []
            valid_points = [point for point in track if isinstance(point, list) and len(point) >= 2 and
                            all(isinstance(v, (int, float)) and math.isfinite(v) for v in point[:2]) and
                            -90 <= point[0] <= 90 and -180 <= point[1] <= 180 and point[:2] != [0, 0]]
            summary["routeStart"] = valid_points[0][:2] if len(valid_points) >= 2 else None
            summary["routeEnd"] = valid_points[-1][:2] if len(valid_points) >= 2 else None
            summary["pidCount"] = len(trip.get("pidValues") or {})
            summary["_insightPidValues"] = {slug: {key: stat.get(key) for key in ("min", "max", "mean", "mode", "samples")}
                for slug, stat in (trip.get("pidValues") or {}).items() if slug in _INSIGHT_SLUGS}
            updates.append((trip["id"], json.dumps(summary, ensure_ascii=False, allow_nan=False)))
        if updates:
            con.executemany("INSERT OR REPLACE INTO trip_summaries(trip_id,payload) VALUES(?,?)", updates)
        rows = con.execute("SELECT s.payload FROM trips t JOIN trip_summaries s ON s.trip_id=t.id" + where + " ORDER BY t.start_local DESC", params).fetchall()
    summaries = []
    for row in rows:
        summary = json.loads(row["payload"])
        features = summary.pop("_insightPidValues", {})
        if include_insight_features:
            summary["pidValues"] = features
        summaries.append(summary)
    return summaries


def legacy_rebuild_ids() -> list[str]:
    """Read durable recovery flags without decoding every summary on health checks."""
    with _conn() as con:
        dirty = con.execute("SELECT t.id FROM trips t LEFT JOIN trip_summaries s ON s.trip_id=t.id WHERE s.trip_id IS NULL LIMIT 1").fetchone()
    if dirty:
        get_trip_summaries()
    with _conn() as con:
        rows = con.execute("SELECT trip_id FROM trip_summaries WHERE json_extract(payload,'$.legacyIncomplete')=1 ORDER BY trip_id").fetchall()
    return [row["trip_id"] for row in rows]


def check_readiness() -> None:
    """Verify the query used by the dashboard can access its database and schema."""
    with _conn() as con:
        con.execute("SELECT id,start_local FROM trips LIMIT 1").fetchall()
        con.execute("SELECT trip_id FROM trip_summaries LIMIT 1").fetchall()


def source_parse_cached(source_key: str, signature: tuple, parser_version: int,
                        content_hash: str | None = None) -> dict | None:
    """Reuse only a successful parse of the same file version and parser version."""
    with _conn() as con:
        row = con.execute("SELECT * FROM source_parse_cache WHERE source_key=? AND parser_version=?",
                          (source_key, parser_version)).fetchone()
    if row is None:
        return None
    row = dict(row)
    if content_hash is None or row["content_hash"] != content_hash:
        return None
    ids = json.loads(row["trip_ids"])
    # Cache alone is never proof data survived a restore/recovery of the DB.
    if any(not (raw_trip_exists(tid) or trip_exists(tid) or trip_is_absorbed(tid)) for tid in ids):
        return None
    return row


def record_source_parse(source_key: str, signature: tuple, content_hash: str,
                        parser_version: int, trip_ids: list[str]) -> None:
    """Record a successful immutable parse only after its rows are durable."""
    with _conn() as con:
        con.execute("""INSERT INTO source_parse_cache VALUES(?,?,?,?,?)
            ON CONFLICT(source_key) DO UPDATE SET content_hash=excluded.content_hash,
            parser_version=excluded.parser_version,signature_json=excluded.signature_json,
            trip_ids=excluded.trip_ids""", (source_key, content_hash, parser_version,
              json.dumps(list(signature)), json.dumps(trip_ids)))


def get_all_trips(from_date: str | None = None, to_date: str | None = None) -> list[dict]:
    """Return full trip details, optionally filtered by inclusive local dates."""
    where, params = _date_filter(from_date, to_date)
    with _conn() as con:
        rows = con.execute("SELECT * FROM trips" + where + " ORDER BY start_local DESC", params).fetchall()
    return [_row_to_trip(dict(r)) for r in rows]


def get_trip(trip_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    return _row_to_trip(dict(row)) if row else None


def get_all_tracks(from_date: str | None = None, to_date: str | None = None) -> dict[str, list]:
    """Lightweight: just id → GPS track, without deserializing the PID blobs."""
    where, params = _date_filter(from_date, to_date)
    where += (" AND " if where else " WHERE ") + "gps_track_json IS NOT NULL"
    with _conn() as con:
        rows = con.execute("SELECT id,gps_track_json FROM trips" + where, params).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        try:
            track = _unpack_json(r["gps_track_json"])
            if track:
                out[r["id"]] = track
        except (ValueError, TypeError):
            continue
    return out


def enrich_with_myop(obd_trip_id: str, myop_trip: dict) -> None:
    """Retroactively add myop fields to an existing OBD trip."""
    leg_ids = myop_trip.get("myopLegIds")
    with _conn() as con:
        con.execute("""
            UPDATE trips SET
                myop_trip_id        = ?,
                myop_fuel_ul        = ?,
                myop_leg_ids        = ?,
                myop_distance_km    = ?,
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
            myop_trip.get("myopFuelUl"),
            json.dumps(leg_ids) if leg_ids else None,
            myop_trip.get("myopDistanceKm"),
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


def clear_myop_enrichment(trip_id: str) -> None:
    """Clear a derived association only when original legs exist for regrouping."""
    with _conn() as con:
        con.execute("""UPDATE trips SET myop_trip_id=NULL,myop_leg_ids=NULL,myop_distance_km=NULL,
            myop_fuel_level=NULL,myop_fuel_autonomy=NULL,myop_fuel_consumed_l=NULL,
            myop_price_fuel=NULL,myop_days_to_service=NULL,myop_km_to_service=NULL,
            myop_maintenance_passed=NULL,myop_fuel_ul=NULL,alerts_json='[]' WHERE id=?""", (trip_id,))


def trip_is_absorbed(trip_id: str) -> bool:
    """Check legacy and current session references without resurrecting segments."""
    with _conn() as con:
        rows = con.execute("SELECT merged_ids,myop_leg_ids,myop_trip_id FROM trips WHERE merged_ids IS NOT NULL OR myop_trip_id IS NOT NULL").fetchall()
    myop_id = trip_id.removeprefix("myop-")
    for row in rows:
        if trip_id in (_unpack_json(row["merged_ids"]) or []):
            return True
        ids = _unpack_json(row["myop_leg_ids"]) or [row["myop_trip_id"]]
        if trip_id.startswith("myop-") and any(str(i) == myop_id for i in ids):
            return True
    return False


def merge_trips(primary_id: str, secondary_ids: list[str]) -> dict:
    """Aggregate original segments by field semantics while retaining raw revisions.

    A legacy merged row without all original observations is left intact and must
    be rebuilt explicitly from archived source copies before extending its chain.
    """
    if primary_id in secondary_ids or len(set(secondary_ids)) != len(secondary_ids):
        raise ValueError("Gli ID da unire devono essere distinti")
    material = [get_trip(tid) for tid in [primary_id, *secondary_ids]]
    if any(t is None for t in material):
        raise ValueError("Viaggio da unire non trovato")
    raw_ids = list(dict.fromkeys(tid for t in material for tid in (t.get("mergedIds") or [t["id"]])))
    parts = []
    for tid in raw_ids:
        raw = get_raw_trip(tid)
        if raw is None:
            candidate = next((t for t in material if t["id"] == tid and not t.get("mergedIds")), None)
            if candidate is None:
                raise ValueError("Sessione legacy: ricostruire i segmenti dai sorgenti prima del merge")
            raw = candidate
            save_raw_trip(raw)
        parts.append(raw)
    parts.sort(key=lambda t: t.get("start") or "")
    if any(not t.get("start") or not t.get("end") for t in parts):
        raise ValueError("Date mancanti: impossibile unire in modo affidabile")
    merged = dict(parts[0])
    merged["id"] = primary_id
    merged["mergedIds"] = raw_ids
    merged["end"] = max(t["end"] for t in parts)
    merged["durationMin"] = sum(t.get("durationMin") or 0 for t in parts)
    merged["elapsedDurationMin"] = (datetime.fromisoformat(merged["end"]) - datetime.fromisoformat(merged["start"])).total_seconds() / 60
    merged["recordingGapSeconds"] = max(0, (merged["elapsedDurationMin"] - merged["durationMin"]) * 60)
    merged["track"] = [point for t in parts for point in (t.get("track") or [])]
    merged["alerts"] = sorted({a for t in parts for a in (t.get("alerts") or [])})
    merged["qualityWarnings"] = sorted({w for t in parts for w in (t.get("qualityWarnings") or [])})
    # Additive quantities retain null when every segment lacks a measurement.
    for key in ("distanceKm", "fuelMassG", "idleSeconds", "idleFuelG", "engineOnDurationMin"):
        values = [t.get(key) for t in parts if t.get(key) is not None]
        merged[key] = sum(values) if values else None
    last_keys = ("odometerKm", "dpfSootPct", "dpfClosedSoot", "dpfSinceRegenKm", "dpfAvgRegenKm",
                 "dpfReplaceKm", "dpfRegenCapability", "dpfRegenCapabilityST", "adblueVolL",
                 "adblueRangeKm", "oilDilutionPct", "ssState", "fuelLevelObd", "oilKmToService",
                 "fuelLevel", "fuelAutonomy", "kmToService", "daysToService", "dpfEndObservedActive")
    for key in last_keys:
        merged[key] = next((t[key] for t in reversed(parts) if t.get(key) is not None), None)
    for key in ("maxSpeedKmh", "maxRpm", "coolantMaxC", "oilMaxC", "exhaustBeforeCatC", "exhaustAfterCatC", "noxCatTempMaxC"):
        merged[key] = max((t[key] for t in parts if t.get(key) is not None), default=None)
    for key in ("avgRpm", "acCurrentMa", "acActivePct", "ratioWg", "fuelRateCoveragePct", "fuelMassCoveragePct"):
        weights = [(t.get(key), t.get("durationMin") or 0) for t in parts]
        is_coverage = key.endswith("CoveragePct")
        total = sum(w for v, w in weights if is_coverage or v is not None)
        merged[key] = sum((v or 0) * w for v, w in weights) / total if total and any(v is not None for v, _ in weights) else None
    duration = merged.get("durationMin") or 0
    km = merged.get("distanceKm") or 0
    merged["avgSpeedKmh"] = km / duration * 60 if duration else None
    merged["gPerKm"] = merged["fuelMassG"] / km if merged.get("fuelMassG") is not None and km and (merged.get("fuelMassCoveragePct") or 0) >= 90 else None
    if merged.get("fuelMassG") is not None and (merged.get("fuelMassCoveragePct") or 0) < 90:
        merged["qualityWarnings"].append("Consumo in massa parziale: confronto g/km non disponibile.")
    engine_s = (merged.get("engineOnDurationMin") or 0) * 60
    merged["idleSharePct"] = (merged.get("idleSeconds") or 0) / engine_s * 100 if engine_s else None
    rates = [t.get("obdFuelConsumedL", t.get("fuelConsumedL")) for t in parts]
    merged["obdFuelConsumedL"] = sum(v for v in rates if v is not None) if any(v is not None for v in rates) else None
    merged["fuelConsumedL"] = merged["obdFuelConsumedL"]
    merged["consumptionL100km"] = merged["fuelConsumedL"] / km * 100 if merged["fuelConsumedL"] and km else None
    merged["fuelValidationGL"] = merged["fuelMassG"] / merged["fuelConsumedL"] if merged.get("fuelMassG") and merged["fuelConsumedL"] else None
    bands = {}
    for t in parts:
        for band, value in (t.get("speedBandsKm") or {}).items():
            bands[band] = bands.get(band, 0) + value
    merged["speedBandsKm"] = bands or None
    states = [t.get("dpfRegenState") for t in parts]
    merged["dpfRegenState"] = states[-1]
    if states[-1] not in ("active", "requested") and "completed" in states:
        merged["dpfRegenState"] = "completed"
    merged["dpfRegenActive"] = 1 if any(t.get("dpfRegenActive") for t in parts) else 0
    base = datetime.fromisoformat(merged["start"])
    merged["pidValues"], merged["pidSeriesFull"], merged["pidSeriesTimes"] = {}, {}, {}
    merged["observedWindows"] = []
    slugs = {slug for t in parts for slug in (t.get("pidValues") or {})}
    for t in parts:
        shift = (datetime.fromisoformat(t["start"]) - base).total_seconds()
        windows = t.get("observedWindows") or [[0, (t.get("durationMin") or 0) * 60]]
        merged["observedWindows"].extend([[a + shift, b + shift] for a, b in windows])
        for slug, values in (t.get("pidSeriesFull") or {}).items():
            times = (t.get("pidSeriesTimes") or {}).get(slug)
            if not times or len(times) != len(values):
                continue  # no invented time axis for legacy values
            merged["pidSeriesFull"].setdefault(slug, []).extend(values)
            merged["pidSeriesTimes"].setdefault(slug, []).extend([v + shift for v in times])
    for slug in slugs:
        values = [(t, (t.get("pidValues") or {})[slug]) for t in parts if slug in (t.get("pidValues") or {})]
        first_t, first = values[0]
        last_t, last = values[-1]
        stat = dict(first)
        stat["first"], stat["last"] = first.get("first"), last.get("last")
        for key, fn in (("min", min), ("max", max)):
            stat[key] = fn((v[key] for _, v in values if v.get(key) is not None), default=None)
        samples = sum(v.get("samples") or 0 for _, v in values)
        stat["samples"] = samples
        stat["mean"] = sum((v.get("mean") or 0) * (v.get("samples") or 0) for _, v in values) / samples if samples else None
        modes = {v.get("mode") for _, v in values}
        stat["mode"] = modes.pop() if len(modes) == 1 else None
        stat["first_seen_s"] = (datetime.fromisoformat(first_t["start"]) - base).total_seconds() + (first.get("first_seen_s") or 0)
        stat["last_seen_s"] = (datetime.fromisoformat(last_t["start"]) - base).total_seconds() + (last.get("last_seen_s") or 0)
        stat["age_from_trip_end_s"] = merged["elapsedDurationMin"] * 60 - stat["last_seen_s"]
        stat["is_stale"] = stat["age_from_trip_end_s"] > 60
        stat["coverage_pct"] = sum((v.get("coverage_pct") or 0) * (t.get("durationMin") or 0) for t, v in values) / duration if duration else 0
        stat["sample_rate_hz"] = samples / (duration * 60) if duration else None
        merged["pidValues"][slug] = stat
    merged["pidCatalog"] = list({p["slug"]: p for t in parts for p in (t.get("pidCatalog") or [])}.values())
    # Source MyOpel legs are reattached by the correlator after this merge.
    known_legs = list(dict.fromkeys(i for t in material for i in (t.get("myopLegIds") or ([t["myopId"]] if t.get("myopId") is not None else []))))
    merged["myopLegIds"] = known_legs or None
    merged["myopId"] = known_legs[0] if known_legs else None
    save_trip(merged, record_raw=False)
    with _conn() as con:
        con.executemany("DELETE FROM trips WHERE id=?", [(sid,) for sid in secondary_ids])
    return get_trip(primary_id)


def _row_to_trip(row: dict) -> dict:
    _j = lambda k: _unpack_json(row.get(k))

    sources: list[str] = []
    if row.get("source") in ("obd_csv", "obd_brc"):
        sources.append("obd")
    if row.get("myop_trip_id") is not None:
        sources.append("myopel")
    if not sources:
        sources = ["myopel" if row.get("source") == "myop" else "obd"]

    metadata = _j("metadata_json") or {}
    if "obd" in sources and metadata.get("parserVersion", 0) < 2:
        metadata.setdefault("qualityWarnings", []).append("Dati legacy: copertura non verificata; ricostruzione dai sorgenti necessaria.")
    from .services.fuel import select_consumption
    selected = select_consumption({
        **metadata,
        "sources": sources, "distanceKm": row.get("distance_km"),
        "obdFuelConsumedL": row.get("fuel_consumed_l") if "obd" in sources else None,
        "myopFuelConsumedL": row.get("myop_fuel_consumed_l"),
        "myopDistanceKm": row.get("myop_distance_km") if "obd" in sources else row.get("distance_km"),
        "fuelMassG": row.get("fuel_mass_g"),
    })
    _l100 = selected["consumptionL100km"]
    _kml = 100.0 / _l100 if _l100 and _l100 > 0 else None

    return {
        "id":                    row["id"],
        "legacyIncomplete":      "obd" in sources and metadata.get("parserVersion", 0) < 2,
        "sources":               sources,
        "filename":              row.get("filename"),
        "myopId":                row.get("myop_trip_id"),
        "myopLegIds":            _j("myop_leg_ids"),
        "myopDistanceKm":        row.get("myop_distance_km"),
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
        "fuelConsumedL":         selected["fuelConsumedL"],
        "obdFuelConsumedL":      row.get("fuel_consumed_l") if "obd" in sources else None,
        "myopFuelConsumedL":     row.get("myop_fuel_consumed_l"),
        "consumptionL100km":     round(_l100, 2) if _l100 else None,
        "consumptionKmL":        _kml,
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
        # OBD-native replacements for MyOpel fields + briefing §2/§3 metrics
        "fuelLevelObd":          row.get("fuel_level_obd"),
        "oilKmToService":        row.get("oil_km_to_service"),
        "fuelMassG":             row.get("fuel_mass_g"),
        "gPerKm":                row.get("g_per_km"),
        "fuelValidationGL":      row.get("fuel_validation_gl"),
        "acCurrentMa":           row.get("ac_current_ma"),
        "acActivePct":           row.get("ac_active_pct"),
        "idleSeconds":           row.get("idle_seconds"),
        "idleSharePct":          row.get("idle_share_pct"),
        "idleFuelG":             row.get("idle_fuel_g"),
        "speedBandsKm":          _j("speed_bands_json"),
        "ratioWg":               row.get("ratio_wg"),
        "fcSuspect":             (bool(row["fc_suspect"]) if row.get("fc_suspect") is not None else None),
        "myopFuelUl":            row.get("myop_fuel_ul"),
        "costDistanceKm":        row.get("myop_distance_km") if "obd" in sources else row.get("distance_km"),
        "costEur":               (row["myop_fuel_consumed_l"] * row["myop_price_fuel"]
                                   if row.get("myop_fuel_consumed_l") and row.get("myop_price_fuel")
                                   else None),
        "alerts":                _j("alerts_json") or [],
        "track":                 _j("gps_track_json"),
        "pidValues":             _j("pid_values_json"),
        "pidSeriesFull":         _j("pid_series_json"),
        "insights":              _j("insights_json") or [],
        "mergedIds":             _j("merged_ids"),
        "vin":                   row.get("vin"),
        **metadata,
        **selected,
    }
