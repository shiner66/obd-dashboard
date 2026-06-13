"""
CarScanner CSV parser — briefing §2 and §5.
Handles UTF-8-BOM, semicolon delimiter, engine-on window,
per-PID stats, GPS track, RBS correction, DPF state machine.
"""
from __future__ import annotations
import csv
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

_ROME_TZ = ZoneInfo("Europe/Rome")
_UTC_TZ  = ZoneInfo("UTC")

from ..services import rbs as rbs_svc
from ..services.dpf import compute_state as dpf_state

log = logging.getLogger(__name__)


# ── Slug generation (§6) ──────────────────────────────────────────────────────
_SLUG_RE = re.compile(r"[^a-z0-9]+")

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = s.replace("°", "deg").replace("²", "2").replace("µ", "u")
    s = _SLUG_RE.sub("_", s).strip("_")
    return s or "pid"


# ── PID usefulness classification ─────────────────────────────────────────────
# CarScanner's MD1CS003 profile dumps ~180 PIDs, but ~120 are noise: internal
# ECU bookkeeping flags, raw French-named signals (MP_*), and values that never
# change. We tag each catalog entry `useful` so the UI can default to the ~60
# meaningful PIDs while still allowing a "show all" toggle.
#
# A PID is useful when EITHER it has a curated short slug (hand-picked signals,
# see CURATED_SLUG below) OR — dynamically — it carries a real engineering unit
# and actually varies during the trip. The noise patterns force-exclude the
# obvious internal/raw signals even when they happen to carry a unit.
_PID_NOISE_RE = re.compile(
    r"(?:^|\b)mp_[a-z]|"
    r"first electronic|interface unit operating|in the process of adjust|"
    r"wear condition|controlled air supply module|auxiliary heater|"
    r"additional heater|abs/eks|restart request|restart control|"
    r"inhibition transmitted|inhibit control|allowed stop|allowed by|"
    r"permission from|permission based|return to (?:engine|starter)|"
    r"network related engine restart",
    re.I,
)


def _pid_useful(name: str, unit: str, vals: list[float]) -> bool:
    """Decide whether a PID is worth surfacing by default in the UI."""
    if _PID_NOISE_RE.search(name):
        return False
    if name in CURATED_SLUG:          # hand-curated signal → always useful
        return True
    if not unit:                       # raw flags without a physical unit → noise
        return False
    constant = (max(vals) == min(vals)) if vals else True
    return not constant                # real measurement that actually moves


# ── Short curated slugs matching the frontend PID_CATALOG ────────────────────
CURATED_SLUG: dict[str, str] = {
    "Giri motore":                                                         "rpm",
    "[ECM] Crankshaft speed":                                              "rpm",
    "Giri motore x1000":                                                   "rpm_k",
    "Velocità (GPS)":                                                      "speed",
    "[ECM] Vehicle speed":                                                 "speed_v",
    "Velocità veicolo":                                                    "speed_v",
    "Temperatura liquido raffreddamento motore":                           "coolant",
    "[ECM] Coolant temperature, corrected":                                "coolant_c",
    "[ECM] Oil temperature":                                               "oil_t",
    "[ECM] Oil pressure":                                                  "oil_p",
    "Temperatura d'aria ambiente":                                         "ambient",
    "[ECM] Outside air temperature":                                       "ambient",
    "[ECM] Intake air temperature":                                        "intake",
    "[ECM] Calculated engine load":                                        "load",
    "[ECM] Accelerator pedal position":                                    "throttle",
    "[ECM] Engine torque":                                                 "torque",
    "[ECM] Computer temperature":                                          "ecu_t",
    "[ECM] Engine control computer supply voltage":                        "ecu_v",
    "[ECM] Fuel pressure":                                                 "fuel_p",
    "[ECM] Desired high pressure common rail fuel pressure":               "fuel_p_d",
    "[ECM] Calculated fuel injection amount":                              "inj_q",
    "[ECM] Fuel consumption rate":                                         "fuel_rate",
    "[ECM] Fuel tank level":                                               "fuel_lvl",
    "[ECM] Lambda":                                                        "lambda",
    "[ECM] Measured turbo boost pressure":                                 "boost",
    "[ECM] Set value of turbo boost pressure":                             "boost_s",
    "[ECM] EGR valve position":                                            "egr",
    "[ECM] Air metering valve position":                                   "amv",
    "[ECM] Air flow":                                                      "maf",
    "[ECM] Variable geometry turbo position":                              "vgt",
    "[ECM] Soot clogging level of diesel particulate filter":              "soot",
    "[ECM] Closed loop soot load assessment of the diesel particulate filter": "soot_cl",
    "[ECM] Open loop soot load assessment of the diesel particulate filter":   "soot_ol",
    "[ECM] Particulate filter differential pressure":                      "dpf_dp",
    "[ECM] DPF differential pressure sensor signal deviation from reference value": "dpf_dp_ds",
    "[ECM] Exhaust gas flow through the particulate filter":               "dpf_flow",
    "[ECM] DPF regeneration status":                                       "regen_st",
    "[ECM] Regeneration enable":                                           "regen_en",
    "[ECM] Long-term regeneration capability":                             "regen_lt",
    "[ECM] Short-term regeneration capability":                            "regen_st_c",
    "[ECM] Distance traveled since the last regeneration":                 "regen_dist",
    "[ECM] Average mileage for the last 10 regenerations":                 "regen_avg",
    "[ECM] Mileage remaining before diesel particulate filter replacement": "dpf_repl",
    "[ECM] Assessment of thermal aging of the particulate filter":         "dpf_aging",
    "[ECM] Total mass of additive accumulated in the diesel particulate filter": "additive",
    "[ECM] Mileage since last diesel particulate filter replacement":      "dpf_km",
    "[ECM] Exhaust gas temperature before pre-catalytic converter":        "egt_b",
    "[ECM] Exhaust gas temperature after pre-catalytic converter":         "egt_a",
    "[ECM] EGT before turbo":                                              "egt_pre",
    "[ECM] EGT at DPF inlet":                                              "egt_dpf_i",
    "[ECM] EGT at DPF outlet":                                             "egt_dpf_o",
    "[ECM] Exhaust gas pressure at the outlet of the particulate filter":  "exh_p",
    "[ECM] NOx content measured at the inlet of the NOx catalytic converter": "nox_in",
    "[ECM] Calculated NOx content at the inlet of the NOx catalytic converter": "nox_calc",
    "[ECM] Temperature of the NOx catalytic converter":                    "nox_t",
    "[ECM] NOx catalytic converter urea content":                          "urea",
    "[ECM] Urea mass fraction":                                            "urea_pct",
    "[ECM] Remaining urea solution in the urea tank":                      "urea_rem",
    "[ECM] Volume of urea solution measured in urea tank":                 "urea_v",
    "[ECM] Vehicle mileage remaining before filling the tank with urea solution": "urea_km",
    "[ECM] Evaluation of the exhaust gas flow using the NOx sensor":       "exh_nox",
    "[ECM] Evaluation of the degree of dilution of motor oil":             "oil_dil",
    "[ECM] Evaluation of carbon content in engine oil":                    "oil_carb",
    "[ECM] Distance remaining until the next oil change":                  "oil_km",
    "[ECM] Oil level sensor":                                              "oil_lvl",
    "[ECM] Minimum battery voltage at startup":                            "bat_v",
    "[ECM] Battery current":                                               "bat_i",
    "[ECM] Alternator load value":                                         "alt",
    "[ECM] Service battery charge status":                                 "bat_soc",
    "[ECM] Stop and Start function state":                                 "ss_state",
    "[ECM] Engine restart counter by Stop and Start function":             "ss_count",
    "[ECM] Engine stop time":                                              "ss_stop",
    "[ECM] Cruise control set speed":                                      "cc_set",
    "[TCU] Selected gear":                                                 "gear",
    "[ECM] Clutch pedal switch":                                           "clutch",
    "[ECM] Brake pedal switch":                                            "brake",
    "Altitudine GPS":                                                      "gps_alt",
    "Accuratezza GPS":                                                      "gps_acc",
    "Direzione GPS":                                                       "gps_brg",
    "[ECM] Total mileage":                                                 "odo",
    "Distanza percorsa:":                                                  "odo",
}


# ── Engine-on anchor PIDs (tried in order, §10) ───────────────────────────────
_ENGINE_ANCHORS = (
    "Giri motore",
    "[ECM] Crankshaft speed",
    "Velocità veicolo",
    "[ECM] Vehicle speed",
    "Distanza percorsa:",
)


# ── Curated aggregation table ──────────────────────────────────────────────────
# Maps trip field name → {pid_names, aggregation method, rbs flag}
_CURATED = {
    "avg_speed_kmh":        {"pids": ["Velocità (GPS)"],                         "agg": "mean"},
    "max_speed_kmh":        {"pids": ["Velocità (GPS)"],                         "agg": "max"},
    "avg_rpm":              {"pids": ["Giri motore", "[ECM] Crankshaft speed"],  "agg": "mean"},
    "max_rpm":              {"pids": ["Giri motore", "[ECM] Crankshaft speed"],  "agg": "max"},
    "coolant_max_c":        {"pids": ["Temperatura liquido raffreddamento motore",
                                       "[ECM] Coolant temperature, corrected"],   "agg": "max"},
    "oil_temp_max_c":       {"pids": ["[ECM] Oil temperature"],                  "agg": "max"},
    "odometer_km":          {"pids": ["[ECM] Total mileage", "Distanza percorsa:"], "agg": "last"},
    "air_temp_c":           {"pids": ["Temperatura d'aria ambiente",
                                       "[ECM] Outside air temperature",
                                       "[ECM] Intake air temperature"],           "agg": "first"},
    "dpf_soot_pct":         {"pids": ["[ECM] Soot clogging level of diesel particulate filter"],
                                                                                  "agg": "last", "rbs": True},
    "dpf_closed_soot":      {"pids": ["[ECM] Closed loop soot load assessment of the diesel particulate filter"],
                                                                                  "agg": "last"},
    "dpf_regen_active":     {"pids": ["[ECM] DPF regeneration status"],          "agg": "max"},
    "dpf_regen_capability": {"pids": ["[ECM] Long-term regeneration capability"],"agg": "last"},
    "dpf_regen_capability_st": {"pids": ["[ECM] Short-term regeneration capability"], "agg": "last"},
    "dpf_since_regen_km":   {"pids": ["[ECM] Distance traveled since the last regeneration"],
                                                                                  "agg": "last", "rbs": True},
    "dpf_avg_regen_km":     {"pids": ["[ECM] Average mileage for the last 10 regenerations"],
                                                                                  "agg": "last", "rbs": True},
    "dpf_replace_km":       {"pids": ["[ECM] Mileage remaining before diesel particulate filter replacement"],
                                                                                  "agg": "last", "rbs": True},
    "adblue_vol_l":         {"pids": ["[ECM] Volume of urea solution measured in urea tank"],
                                                                                  "agg": "last"},
    "adblue_range_km":      {"pids": ["[ECM] Vehicle mileage remaining before filling the tank with urea solution"],
                                                                                  "agg": "last"},
    "exhaust_before_cat_c": {"pids": ["[ECM] Exhaust gas temperature before pre-catalytic converter"],
                                                                                  "agg": "max"},
    "exhaust_after_cat_c":  {"pids": ["[ECM] Exhaust gas temperature after pre-catalytic converter"],
                                                                                  "agg": "max"},
    "nox_cat_temp_max_c":   {"pids": ["[ECM] Temperature of the NOx catalytic converter"],
                                                                                  "agg": "max"},
    "battery_startup_v":    {"pids": ["[ECM] Minimum battery voltage at startup"],"agg": "last"},
    "oil_dilution_pct":     {"pids": ["[ECM] Evaluation of the degree of dilution of motor oil"],
                                                                                  "agg": "last"},
    "ss_state":             {"pids": ["[ECM] Stop and Start function state"],     "agg": "last"},
    "distance_km_raw":      {"pids": ["Distanza percorsa:"],                      "agg": "last"},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _agg(values: list[float], method: str) -> float | None:
    if not values:
        return None
    if method == "mean":
        return statistics.mean(values)
    if method == "max":
        return max(values)
    if method == "min":
        return min(values)
    if method == "first":
        return values[0]
    if method == "last":
        return values[-1]
    return None


def _classify_kind(values: list[float], unit: str) -> str:
    if unit:
        return "number"
    ints = [int(round(v)) for v in values]
    if set(ints) <= {0, 1}:
        return "bool"
    if all(v == int(v) for v in values) and len(set(ints)) <= 32:
        return "discrete"
    return "number"


def _mode(values: list[float]) -> float:
    if not values:
        return 0.0
    try:
        return statistics.mode(round(v, 1) for v in values)
    except statistics.StatisticsError:
        return round(values[0], 1)


def _downsample(series: list[tuple[float, float]], n: int = 60) -> list[float]:
    """Return n evenly-spaced values from a (ts, value) series."""
    if not series:
        return []
    if len(series) <= n:
        return [v for _, v in series]
    step = (len(series) - 1) / (n - 1)
    return [series[round(i * step)][1] for i in range(n)]


def _r(v: float | None, d: int = 2) -> float | None:
    return round(v, d) if v is not None else None


# ── Main parse function ───────────────────────────────────────────────────────

def _parse_seconds(s: str) -> float | None:
    """Parse a time column that may be seconds (float) or HH:MM:SS[.mmm]."""
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    # Try colon-separated time formats
    parts = s.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        pass
    return None


def _read_csv_rows(path: Path) -> list[tuple]:
    """Try semicolon delimiter first, then comma, returning parsed rows."""
    raw_rows: list[tuple] = []
    for delimiter in (";", ",", "\t"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, None)
            rows_seen = 0
            for row in reader:
                rows_seen += 1
                if rows_seen == 1:
                    log.debug("CSV %s delim=%r header=%r first_row=%r",
                              path.name, delimiter, header, row)
                if len(row) < 3:
                    continue
                try:
                    sec = _parse_seconds(row[0])
                    if sec is None:
                        continue
                    pid = row[1].strip()
                    val = float(row[2])
                except (ValueError, IndexError):
                    continue
                unit = row[3].strip() if len(row) > 3 else ""
                try:
                    lat = float(row[4]) if len(row) > 4 and row[4].strip() else 0.0
                    lon = float(row[5]) if len(row) > 5 and row[5].strip() else 0.0
                except ValueError:
                    lat = lon = 0.0
                raw_rows.append((sec, pid, val, unit, lat, lon))
        if raw_rows:
            log.debug("CSV %s: parsed %d rows with delim=%r", path.name, len(raw_rows), delimiter)
            break
    return raw_rows


def parse_file(path: str | Path) -> list[dict]:
    """
    Parse a CarScanner CSV file and return a list containing one trip dict
    compatible with the frontend's trip object format and the database schema.
    """
    path = Path(path)
    filename = path.name

    # ── Timestamp from filename (local Italian time → strip timezone info) ──
    stem = path.stem  # "2026-05-20_19-57-16" or "2026-05-20 19-57-16"
    start_local = start_utc = ""
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d %H-%M-%S"):
        try:
            dt_local = datetime.strptime(stem, fmt)
            start_local = dt_local.isoformat()
            # Convert local Italian time → UTC using proper DST rules (CET/CEST)
            start_utc = (
                dt_local.replace(tzinfo=_ROME_TZ)
                .astimezone(_UTC_TZ)
                .replace(tzinfo=None)
                .isoformat()
            )
            break
        except ValueError:
            continue

    # ── Read CSV (try semicolon, fallback to comma/tab) ──────────────────────
    raw_rows = _read_csv_rows(path)

    if not raw_rows:
        # Log the raw first 5 lines to help diagnose unknown formats.
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as _f:
                preview = [next(_f, "") for _ in range(5)]
            log.error("No valid rows in %s — first 5 lines: %r", filename, preview)
        except Exception:
            pass
        raise ValueError(f"No valid rows in {filename}")

    # ── Build per-PID series and GPS points ──────────────────────────────────
    pid_data: dict[str, list[tuple[float, float]]] = {}
    pid_units: dict[str, str] = {}
    gps_all: list[tuple[float, float, float]] = []  # (ts, lat, lon)

    for sec, pid, val, unit, lat, lon in raw_rows:
        pid_data.setdefault(pid, []).append((sec, val))
        pid_units[pid] = unit
        if lat != 0.0 or lon != 0.0:
            gps_all.append((sec, lat, lon))

    # ── Engine-on window ─────────────────────────────────────────────────────
    t_start = t_end = None
    for anchor in _ENGINE_ANCHORS:
        if anchor in pid_data:
            ts_list = [t for t, _ in pid_data[anchor]]
            t_start, t_end = min(ts_list), max(ts_list)
            break
    if t_start is None:
        all_ts = [r[0] for r in raw_rows]
        t_start, t_end = min(all_ts), max(all_ts)

    duration_min = (t_end - t_start) / 60.0

    # ── Filter to engine window, apply RBS correction ─────────────────────────
    pid_window: dict[str, list[tuple[float, float]]] = {}
    for pid_name, series in pid_data.items():
        windowed = [(t, v) for t, v in series if t_start <= t <= t_end]
        if not windowed:
            continue
        if rbs_svc.needs_correction(pid_name):
            windowed = [(t, rbs_svc.correct(pid_name, v)) for t, v in windowed]
        pid_window[pid_name] = windowed

    # ── GPS track (engine window, deduplicated) ───────────────────────────────
    gps_window = [(lat, lon) for ts, lat, lon in gps_all if t_start <= ts <= t_end]
    gps_deduped: list[list[float]] = []
    for p in gps_window:
        if not gps_deduped or (p[0] != gps_deduped[-1][0] or p[1] != gps_deduped[-1][1]):
            gps_deduped.append(list(p))

    # ── Curated trip fields ───────────────────────────────────────────────────
    fields: dict[str, float | None] = {}
    for key, spec in _CURATED.items():
        for pid_name in spec["pids"]:
            if pid_name in pid_window:
                vals = [v for _, v in pid_window[pid_name]]
                fields[key] = _agg(vals, spec["agg"])
                break
        else:
            fields[key] = None

    # ── Fuel consumption (trapezoid integral of L/h PID) ─────────────────────
    fuel_consumed_l: float | None = None
    if "[ECM] Fuel consumption rate" in pid_window:
        rate_s = pid_window["[ECM] Fuel consumption rate"]
        if len(rate_s) >= 2:
            fuel_consumed_l = sum(
                (rate_s[i][0] - rate_s[i-1][0]) / 3600
                * (rate_s[i][1] + rate_s[i-1][1]) / 2
                for i in range(1, len(rate_s))
            )
            fuel_consumed_l = round(max(0, fuel_consumed_l), 3)

    # ── Distance ──────────────────────────────────────────────────────────
    # Reliability order (verified against MyOpel ground truth on the real corpus):
    #   1. "Distanza percorsa:" trip counter  — direct, resets each trip
    #   2. [ECM] Total mileage delta           — odometer, accurate when ≥ 2 km
    #   3. GPS speed integral                  — noisy (over/under-counts), last resort
    # The GPS integral was the previous default and is the root cause of the
    # OBD↔MyOpel distance mismatches that broke correlation.
    gps_dist: float | None = None
    if "Velocità (GPS)" in pid_window:
        spd = pid_window["Velocità (GPS)"]
        if len(spd) >= 2:
            gps_dist = round(max(0, sum(
                (spd[i][0] - spd[i-1][0]) / 3600
                * (spd[i][1] + spd[i-1][1]) / 2
                for i in range(1, len(spd))
            )), 2)

    odo_delta: float | None = None
    odo_series = pid_window.get("[ECM] Total mileage")
    if odo_series and len(odo_series) >= 2:
        d = odo_series[-1][1] - odo_series[0][1]
        if 0 < d < 2000:          # guard against odometer rollover / glitches
            odo_delta = round(d, 2)

    trip_counter = fields.get("distance_km_raw")
    if trip_counter is not None and trip_counter > 0:
        distance_km, distance_source = trip_counter, "trip_counter"
    elif odo_delta is not None and odo_delta >= 2.0:
        distance_km, distance_source = odo_delta, "odometer"
    else:
        distance_km, distance_source = gps_dist, "gps"

    consumption_l100km: float | None = None
    if fuel_consumed_l and distance_km:
        consumption_l100km = _r(fuel_consumed_l / distance_km * 100)

    # Discard trips shorter than 1 km — parking-lot maneuvers, accidental triggers,
    # and ECU restart noise have no diagnostic value and only pollute statistics.
    if (distance_km or 0) < 1.0:
        log.info("Skipping %s: trip too short (%.2f km)", filename, distance_km or 0)
        return []

    # ── DPF state machine ─────────────────────────────────────────────────────
    dpf_regen_state, dpf_regen_active = dpf_state(pid_window)

    # ── Per-PID statistics (§6) and downsampled series ────────────────────────
    pid_values: dict[str, dict] = {}
    pid_series: dict[str, list[float]] = {}
    pid_catalog: list[dict] = []
    seen_slugs: set[str] = set()

    for pid_name, series in pid_window.items():
        slug = CURATED_SLUG.get(pid_name) or _slugify(pid_name)
        # Avoid duplicate slugs (e.g. both "Giri motore" and "[ECM] Crankshaft speed" → "rpm")
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        vals = [v for _, v in series]
        times = [t for t, _ in series]
        unit = pid_units.get(pid_name, "")
        kind = _classify_kind(vals, unit)
        span = times[-1] - times[0] if len(times) > 1 else 1.0
        coverage = span / max(t_end - t_start, 1) * 100

        pid_values[slug] = {
            "last":               _r(vals[-1], 3),
            "first":              _r(vals[0], 3),
            "min":                _r(min(vals), 3),
            "max":                _r(max(vals), 3),
            "mean":               _r(statistics.mean(vals), 3),
            "mode":               _mode(vals),
            "samples":            len(vals),
            "kind":               kind,
            "first_seen_s":       _r(times[0] - t_start, 1),
            "last_seen_s":        _r(times[-1] - t_start, 1),
            "age_from_trip_end_s":_r(t_end - times[-1], 1),
            "coverage_pct":       _r(coverage, 1),
            "sample_rate_hz":     _r(len(vals) / max(span, 1), 3),
            "is_stale":           (t_end - times[-1]) > 60,
        }
        pid_series[slug] = _downsample(series, 60)

        # Build PID catalog entry
        _name_clean = pid_name
        for _pfx in ("[ECM] ", "[TCU] ", "[ECM]", "[TCU]"):
            _name_clean = _name_clean.replace(_pfx, "")
        pid_catalog.append({
            "slug":   slug,
            "name":   pid_name,
            "short":  _name_clean.strip()[:32],
            "unit":   unit,
            "kind":   kind,
            "group":  _pid_group(pid_name),
            "useful": _pid_useful(pid_name, unit, vals),
        })

    return [{
        "id":                    f"obd-{stem.replace(' ', '_')}",
        "sources":               ["obd"],
        "filename":              filename,
        "start":                 start_local,
        "start_utc":             start_utc,
        "end":                   _end_time(start_local, duration_min),
        "durationMin":           _r(duration_min, 1),
        "distanceKm":            _r(distance_km, 2),
        "distanceSource":        distance_source,
        "avgSpeedKmh":           _r(fields.get("avg_speed_kmh"), 1),
        "maxSpeedKmh":           _r(fields.get("max_speed_kmh"), 1),
        "avgRpm":                int(fields["avg_rpm"]) if fields.get("avg_rpm") else None,
        "maxRpm":                int(fields["max_rpm"]) if fields.get("max_rpm") else None,
        "coolantMaxC":           _r(fields.get("coolant_max_c"), 1),
        "oilMaxC":               _r(fields.get("oil_temp_max_c"), 1),
        "odometerKm":            round(fields["odometer_km"]) if fields.get("odometer_km") else None,
        "airTempC":              _r(fields.get("air_temp_c"), 1),
        "fuelConsumedL":         fuel_consumed_l,
        "consumptionL100km":     consumption_l100km,
        "dpfSootPct":            _r(fields.get("dpf_soot_pct"), 1),
        "dpfClosedSoot":         _r(fields.get("dpf_closed_soot"), 2),
        "dpfRegenActive":        dpf_regen_active,
        "dpfRegenState":         dpf_regen_state,
        "dpfRegenCapability":    _r(fields.get("dpf_regen_capability"), 1),
        "dpfRegenCapabilityST":  _r(fields.get("dpf_regen_capability_st"), 1),
        "dpfSinceRegenKm":       _r(fields.get("dpf_since_regen_km"), 1),
        "dpfAvgRegenKm":         _r(fields.get("dpf_avg_regen_km"), 1),
        "dpfReplaceKm":          _r(fields.get("dpf_replace_km"), 0),
        "adblueVolL":            _r(fields.get("adblue_vol_l"), 2),
        "adblueRangeKm":         _r(fields.get("adblue_range_km"), 0),
        "exhaustBeforeCatC":     _r(fields.get("exhaust_before_cat_c"), 1),
        "exhaustAfterCatC":      _r(fields.get("exhaust_after_cat_c"), 1),
        "noxCatTempMaxC":        _r(fields.get("nox_cat_temp_max_c"), 1),
        "batteryStartupV":       _r(fields.get("battery_startup_v"), 2),
        "oilDilutionPct":        _r(fields.get("oil_dilution_pct"), 2),
        "ssState":               int(fields["ss_state"]) if fields.get("ss_state") else None,
        "alerts":                [],  # OBD CSV has no alert codes; myop provides them
        "track":                 gps_deduped,
        "pidValues":             pid_values,
        "pidSeriesFull":         pid_series,
        "pidCatalog":            pid_catalog,
    }]


def _end_time(start_iso: str, duration_min: float) -> str:
    try:
        dt = datetime.fromisoformat(start_iso)
        dt_end = dt + timedelta(minutes=duration_min)
        return dt_end.isoformat()
    except ValueError:
        return ""


def _pid_group(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("dpf", "particulate", "soot", "regen")):
        return "DPF"
    if any(k in n for k in ("egt", "exhaust", "cat", "scarico")):
        return "Scarico"
    if any(k in n for k in ("nox", "adblue", "urea")):
        return "NOx/AdBlue"
    if any(k in n for k in ("oil", "olio")):
        return "Olio"
    if any(k in n for k in ("bat", "battery", "alt", "soc")):
        return "Batteria"
    if any(k in n for k in ("fuel", "carburante", "injection", "lambda", "inj", "rail")):
        return "Carburante"
    if any(k in n for k in ("turbo", "boost", "egr", "amv", "maf", "vgt", "aspiraz")):
        return "Aspirazione"
    if any(k in n for k in ("stop", "start", "ss_", "cruise", "gear", "clutch", "brake")):
        return "Comfort"
    if any(k in n for k in ("gps", "altitud", "direzione", "accuratezza")):
        return "GPS"
    if any(k in n for k in ("odometer", "mileage", "distanza")):
        return "Odometro"
    if any(k in n for k in ("rpm", "crankshaft", "giri", "velocit", "speed",
                             "coolant", "raffreddamento", "temperature", "temperatura",
                             "load", "throttle", "torque")):
        return "Motore"
    return "Sensori"
