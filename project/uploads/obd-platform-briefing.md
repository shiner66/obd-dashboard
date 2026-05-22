# OBD Trip Platform — Technical Briefing

**Vehicle:** Peugeot (ECU: MD1CS003, 1.5 BlueHDi)
**Adapter:** BTLE IOS-Vlink
**Apps:** CarScanner (OBD) + MyOpel (Stellantis)
**Date of last analysis:** 2026-05-22
**Branch:** `claude/evaluate-brc-format-XHYLG`

---

## Table of Contents

1. [Data Sources](#1-data-sources)
2. [CSV Format Specification](#2-csv-format-specification)
3. [BRC Binary Format Specification](#3-brc-binary-format-specification)
4. [MyOpel Export Format (.myop)](#4-myopel-export-format-myop)
5. [Parsed OBD Trip Data Schema](#5-parsed-obd-trip-data-schema)
6. [Per-PID Statistics Schema](#6-per-pid-statistics-schema)
7. [RBS Byte-Swap Correction](#7-rbs-byte-swap-correction)
8. [DPF State Machine](#8-dpf-state-machine)
9. [GPS Data](#9-gps-data)
10. [Full PID Reference](#10-full-pid-reference)
11. [AI Insight Engine](#11-ai-insight-engine)
12. [GPS Map Feature](#12-gps-map-feature)
13. [Platform Architecture & HA Integration Strategy](#13-platform-architecture--ha-integration-strategy)

---

## 1. Data Sources

### Real log corpus (2026-05-19/20)

| File | Rows | PIDs | Duration |
|------|------|------|----------|
| 2026-05-19 21-16-39 | 10,171 | 260 | 23.9 min |
| 2026-05-19 23-18-34 | 3,443 | 180 | 2.7 min |
| 2026-05-19 23-21-20 | 46,431 | 180 | 70.4 min |
| 2026-05-20 01-01-54 | 6,184 | 180 | 6.2 min |
| 2026-05-20 01-09-32 | 949 | 178 | 4.4 min |
| 2026-05-20 01-10-38 | 8,172 | 180 | 7.9 min |
| 2026-05-20 01-18-43 | 3,664 | 180 | 6.1 min |
| 2026-05-20 16-39-54 | 23,225 | 180 | 18.0 min |
| 2026-05-20 17-59-25 | 9,988 | 180 | 15.2 min |
| 2026-05-20 18-22-58 | 10,400 | 180 | 12.5 min |
| 2026-05-20 18-35-32 | 21,787 | 207 | 31.1 min |
| 2026-05-20 19-57-16 | 7,765 | 180 | 15.3 min |

GPS bounding box: lat 40.447–40.698 / lon 14.707–15.000 (Salerno province, Italy).
Total GPS points across all logs: **152,052** (100% coverage — every CSV row has lat/lon).

---

## 2. CSV Format Specification

### File naming

`YYYY-MM-DD HH-MM-SS.csv` — timestamp is local recording time.

### Schema

```
"SECONDS";"PID";"VALUE";"UNITS";"LATITUDE";"LONGTITUDE";
```

| Column | Type | Notes |
|--------|------|-------|
| SECONDS | float64 | Seconds since a per-session epoch (not UTC midnight); monotonic |
| PID | string | Human-readable PID name (Italian or English, depending on app locale) |
| VALUE | float64 | Sensor reading in UNITS |
| UNITS | string | Physical unit string (empty for boolean/discrete PIDs) |
| LATITUDE | float64 | WGS-84 decimal degrees; 0.0 if GPS not yet locked |
| LONGTITUDE | float64 | WGS-84 decimal degrees; note typo in column name — use index [5] |

- Delimiter: semicolon (`;`), not comma.
- Encoding: UTF-8-BOM (`utf-8-sig`).
- Header row always present.
- Rows not sorted by PID; sorted by SECONDS.
- Latitude/longitude present for **every row**, including non-GPS PIDs.
- `SECONDS` value is offset by a large constant (e.g. `60003.87`) — not a wall-clock time.
- Trip wall-clock time is encoded in the filename only.

### Parsing pseudocode

```python
import csv
rows = []
with open(path, newline="", encoding="utf-8-sig") as f:
    reader = csv.reader(f, delimiter=";")
    next(reader)  # skip header
    for row in reader:
        if len(row) < 4:
            continue
        try:
            sec, pid, val = float(row[0]), row[1], float(row[2])
        except (ValueError, IndexError):
            continue
        unit = row[3].strip() if len(row) > 3 else ""
        lat  = float(row[4]) if len(row) > 4 and row[4] else 0.0
        lon  = float(row[5]) if len(row) > 5 and row[5] else 0.0
```

---

## 3. BRC Binary Format Specification

CarScanner binary export: **~4.5× smaller than CSV**. Fully reverse-engineered
from 14 paired BRC/CSV exports; 11/14 produce exact record counts; 3 have known
format differences (CSV adds boundary values per PID, BRC captures a small number
of extra PIDs not exported to CSV).

### Module-level constants (Python)

```python
RECORD_MARKER = b"\x50\x43\x27\x01"   # 4 bytes — marks every data record
CATALOG_SEP   = b"\x90\x61\xd5\x00"   # 4 bytes — inter-catalog-entry separator
                                        #           AND GPS bbox terminator
```

### File layout (top to bottom)

```
[FIXED HEADER]
  pascal_string  "CARSCANNERRECORD"   (1-byte len + bytes)
  uint32 LE      version = 2
  pascal_string  device_id            e.g. "VXKUBYHTKM4329850"
  pascal_string  car_name
  pascal_string  ecu_model            e.g. "Peugeot 1.5 BlueHDI ECU MD1CS003"
  pascal_string  adapter_type         e.g. "BTLE IOS-Vlink"

[GPS BOUNDING BOX]
  variable-size block (short trips: 8 bytes, longer trips: larger)
  ends with CATALOG_SEP (b"\x90\x61\xd5\x00")
  scanner: advance byte-by-byte until CATALOG_SEP found, then skip past it

[CATALOG]
  one entry per PID:
    pid_id          4 bytes (CarScanner-internal LE uint32)
    pascal_string   full_name          e.g. "[ECM] Crankshaft speed"
    pascal_string   short_name         e.g. "RPM"
    uint32 LE       type_flags         (semantics: GPS-coupled flag etc.)
    RECORD_MARKER record  24 bytes     embedded sample (ts=0.0, val=first_value)
    [optional 28-byte GPS extra block if next 4B ≠ RECORD_MARKER, ≠ CATALOG_SEP]
    CATALOG_SEP     4 bytes            (absent after last entry)

[DATA RECORDS]
  contiguous 24-byte records starting at the same offset as the catalog
  (catalog-embedded records ARE valid data samples; scan from catalog_start)
  each record:
    RECORD_MARKER    4 bytes
    rel_ts           float64 LE   seconds since session start
    pid_id           4 bytes      matches catalog key
    value            float64 LE   sensor reading in display units
  GPS-extended records (pid_id for "Altitudine GPS" = b"\x30\x03\x03\x00"):
    [24-byte record as above]
    [28-byte GPS extra]:
      field4         4 bytes      (unknown; possibly GPS event reference)
      secondary      float64 LE   (possibly GPS speed or bearing)
      latitude       float64 LE   WGS-84 decimal degrees
      longitude      float64 LE   WGS-84 decimal degrees
```

### Parser algorithm

```python
def parse_brc(data: bytes) -> dict[bytes, list[tuple[float, float]]]:
    pos = 0
    # 1. Header
    _, pos = read_pstr(data, pos)   # magic
    pos += 4                         # version uint32
    for _ in range(4):
        _, pos = read_pstr(data, pos)  # device, car, ecu, adapter
    # 2. GPS bbox — scan for CATALOG_SEP
    end_search = min(pos + 512, len(data) - 4)
    while pos <= end_search and data[pos:pos+4] != CATALOG_SEP:
        pos += 1
    if pos + 4 <= len(data):
        pos += 4  # skip the CATALOG_SEP terminator
    catalog_start = pos
    # 3. Build pid_id → name map
    pid_map = parse_catalog(data, catalog_start)
    # 4. Scan ALL records from catalog_start
    pids = {}
    pos = catalog_start
    while pos + 24 <= len(data):
        if data[pos:pos+4] != RECORD_MARKER:
            pos += 1
            continue
        rel_ts = struct.unpack_from("<d", data, pos + 4)[0]
        pid_id = data[pos+12:pos+16]
        val    = struct.unpack_from("<d", data, pos + 16)[0]
        pos   += 24
        name = pid_map.get(pid_id)
        if name:
            pids.setdefault(name, []).append((rel_ts, val))
        # GPS-extended: 28 extra bytes when next 4 bytes not a known marker
        if pos + 4 <= len(data):
            peek = data[pos:pos+4]
            if peek != RECORD_MARKER and peek != CATALOG_SEP:
                pos += 28
    return pids
```

### Key implementation details

- **Catalog-embedded records are valid data.** Scanning from `catalog_start` (not
  from after the catalog) is critical — embedded samples represent the initial
  value of each PID and must be included in statistics.
- **GPS bbox is variable-size.** Do NOT assume a fixed 40-byte GPS section.
  Short trips produce smaller boxes (as few as 8 bytes + 4-byte CATALOG_SEP).
- **GPS extra detection is heuristic.** Checking `next_4_bytes != RECORD_MARKER
  and != CATALOG_SEP` correctly skips the 28-byte GPS block on Altitudine GPS
  records. False positives are theoretically possible but not observed in 14 files.
- **Record count vs CSV.** BRC produces ~1 fewer record per PID than CSV because
  CSV adds a boundary/initial value for each PID at export time. For a 180-PID
  trip this means ~180 fewer BRC records (e.g. 9,994 BRC vs 10,171 CSV for one
  trip).

---

## 4. MyOpel Export Format (.myop)

### What it is

The **MyOpel app** (Stellantis official) periodically emails the vehicle owner a
cumulative snapshot of all trips as a `.myop` file attachment. This is the
**official Stellantis data channel** — separate from CarScanner/OBD2, uses the
factory telematics unit (TCU) via the Stellantis cloud.

Key characteristic: **each email contains ALL trips since ever**, not just new
ones. This is a cumulative snapshot, not a delta. The latest file supersedes all
older ones (same trip IDs).

### Delivery pipeline

```
Vehicle TCU → Stellantis cloud → MyOpel mobile app → "Share" → Email
                                                    → iOS Shortcuts → trips.json / trips.export
                                                    → IMAP auto-download → .myop file
```

In the HA integration: an IMAP fetcher (with IDLE push support) watches the
inbox for emails from the Stellantis sender address and saves the `.myop`
attachment to the configured folder.

### File format

A `.myop` file is **plain JSON** with `.myop` extension (rename to `.json` to
inspect). Structure:

```json
[
  {
    "vin": "VF3XXXXXXXXXXXXXXX",
    "trips": [
      {
        "id": 42,
        "start": {
          "date": "2026-05-20T07:45:00Z",
          "mileage": 17050
        },
        "end": {
          "date": "2026-05-20T08:10:00Z",
          "mileage": 17062
        },
        "distance": 12.0,
        "travelTime": 1500,
        "fuelLevel": 55,
        "fuelAutonomy": 420,
        "fuelConsumption": 532536,
        "priceFuel": 1.89,
        "alerts": [27, 52],
        "daysUntilNextMaintenance": 180,
        "distanceToNextMaintenance": 12000,
        "maintenancePassed": false
      }
    ]
  }
]
```

### Field reference

| Field | Type | Unit / Notes |
|-------|------|--------------|
| `vin` | string | Full VIN (17 chars) |
| `trips[].id` | int | Stellantis-internal trip ID (monotonic) |
| `start.date` | string | ISO 8601 with `Z` suffix — **CAUTION: actually CET/CEST local time**, not UTC. Stellantis server bug. |
| `start.mileage` | int | Odometer at trip start (km) |
| `end.date` | string | ISO 8601 — same timezone caveat as start |
| `end.mileage` | int | Odometer at trip end (km) |
| `distance` | float | Trip distance (km) |
| `travelTime` | int | Trip duration (seconds) |
| `fuelLevel` | int | Tank fill % at trip end |
| `fuelAutonomy` | int | Estimated remaining range (km) |
| `fuelConsumption` | int | **Proprietary Stellantis unit** — divide by 1,000,000 → litres |
| `priceFuel` | float | Fuel price at time of trip (€/L, set manually in app) |
| `alerts` | list[int] | Vehicle alert codes fired during the trip (see §Alert codes) |
| `daysUntilNextMaintenance` | int | Days until next scheduled service |
| `distanceToNextMaintenance` | int | km until next scheduled service |
| `maintenancePassed` | bool | True if maintenance is overdue |

### fuelConsumption unit — verified calibration

```
Trip: 2026-03-20, 11.9 km
Raw: fuelConsumption = 532536
Calculated: 532536 / 1,000,000 = 0.5326 L
App display: "0.5 L" (22.3 km/L) ✓
```

### Timestamp timezone caveat

The `Z` suffix implies UTC, but Stellantis servers stamp values in Italian local
time (CET = UTC+1 in winter, CEST = UTC+2 in summer) without adjusting for DST.
The HA integration applies a configurable offset (`CONF_TIME_OFFSET`, default = 1).
For a standalone platform: subtract 1 hour (winter) or 2 hours (summer) from all
trip timestamps to get true UTC.

### Alert codes (subset)

| Code | Description |
|------|-------------|
| 0 | Pressione olio motore anomala |
| 1 | Temperatura motore troppo elevata |
| 8 | Livello olio motore insufficiente |
| 17 | Anomalia ESP / ASR |
| 20 | Anomalia del filtro gasolio |
| 22 | Livello carburante basso |
| 25 | Anomalia del sistema antinquinamento |
| 26 | Anomalia ABS |
| 27 | **Rischio di intasamento del filtro antiparticolato (FAP)** |
| 29 | Livello additivo filtro antiparticolato insufficiente |
| 46 | Livello liquido lavacristalli insufficiente |
| 52 | Pressione pneumatico/i insufficiente |
| 57 | Modalità elettrica non disponibile: rigenerazione FAP in corso |
| 59–62 | Pressione pneumatico insufficiente (singola ruota) |

Full table: 100+ codes in `alerts.py`.

### Computed fields (derived in HA integration)

| Derived key | Formula |
|-------------|---------|
| `fuel_consumption_l` | `fuelConsumption / 1_000_000` |
| `fuel_consumption_kmpl` | `distance / fuel_consumption_l` |
| `last_trip_avg_speed` | `distance / (travelTime / 3600)` |
| `last_trip_cost` | `fuel_consumption_l × priceFuel` |
| Monthly / today / refueling aggregates | sum/mean over filtered subsets |

### What .myop does NOT contain

- GPS track (no lat/lon for individual trip waypoints)
- Engine RPM, temperatures, DPF state, or any ECU-level OBD PIDs
- Instantaneous sensor readings (snapshot only: fuel level, alerts)
- Precise trip start/end times at second resolution (rounded to minute)

This is why CarScanner CSV/BRC is the complementary source: `.myop` gives
**what** happened (fuel used, distance, alerts), OBD gives **how** it happened
(temperature curves, DPF soot trajectory, regen events).

---

## 5. Parsed OBD Trip Data Schema

Output of `_compute_stats` / `_parse_csv_file` / `_parse_brc_file`. All fields
in the top-level dict:

| Key | Type | Description |
|-----|------|-------------|
| `obd_filename` | str | Source filename (basename) |
| `obd_trip_start` | str \| None | ISO 8601 UTC timestamp parsed from filename |
| `obd_trip_duration_min` | float | Engine-on window in minutes (1 decimal) |
| `obd_trip_distance_km` | float \| None | Trip distance in km (from odometer delta or GPS speed integral) |
| `obd_trip_avg_speed_kmh` | float \| None | Mean GPS speed |
| `obd_trip_max_speed_kmh` | float \| None | Peak GPS speed |
| `obd_trip_avg_rpm` | int \| None | Mean crankshaft speed |
| `obd_trip_max_rpm` | int \| None | Peak crankshaft speed |
| `obd_trip_coolant_temp_max_c` | float \| None | Peak coolant temperature |
| `obd_trip_oil_temp_max_c` | float \| None | Peak oil temperature |
| `obd_trip_odometer_km` | int \| None | Total mileage at trip end |
| `obd_trip_air_temp_c` | float \| None | Ambient air temperature at trip start |
| `obd_trip_fuel_consumed_l` | float \| None | Total fuel used (trapezoid integral of L/h PID) |
| `obd_trip_consumption_l100km` | float \| None | Average fuel consumption in L/100 km |
| `obd_trip_dpf_soot_pct` | float \| None | DPF soot clogging % at trip end |
| `obd_trip_dpf_closed_soot` | float \| None | Closed-loop DPF soot load (g/L) at trip end |
| `obd_trip_dpf_regen_active` | float \| None | Max DPF regen status code (0=inactive, ≥1=active) |
| `obd_trip_dpf_regen_enable` | float \| None | Max regen enable flag |
| `obd_trip_dpf_regen_capability` | float \| None | Long-term regen capability % at trip end |
| `obd_trip_dpf_regen_capability_st` | float \| None | Short-term regen capability % at trip end |
| `obd_trip_dpf_since_regen_km` | float \| None | Distance since last regen at trip end (km, RBS-corrected) |
| `obd_trip_dpf_avg_regen_km` | float \| None | Average km between last 10 regens (RBS-corrected) |
| `obd_trip_dpf_replace_km` | float \| None | DPF remaining life in km |
| `obd_trip_adblue_vol_l` | float \| None | AdBlue volume in tank (L) at trip end |
| `obd_trip_adblue_range_km` | float \| None | Remaining range on current AdBlue tank (km) |
| `obd_trip_exhaust_before_cat_c` | float \| None | Peak EGT before pre-cat (°C) |
| `obd_trip_exhaust_after_cat_c` | float \| None | Peak EGT after pre-cat (°C) |
| `obd_trip_nox_cat_temp_max_c` | float \| None | Peak NOx catalyst temperature (°C) |
| `obd_trip_battery_startup_v` | float \| None | Battery voltage at last startup (V) |
| `obd_trip_oil_dilution_pct` | float \| None | Oil dilution % (fuel in oil) |
| `obd_trip_ss_state` | float \| None | Stop-and-Start function state (last value) |
| `obd_dpf_regen_state` | str | DPF regeneration state: `idle` / `requested` / `active` / `completed` / `post_regen` |
| `obd_pid_values` | dict | Per-PID detailed stats — see §5 |
| `_pid_catalog` | dict | Slug → {name, unit, kind} — stripped before persistence |

### Observed value ranges (real logs)

| Field | Min | Max | Notes |
|-------|-----|-----|-------|
| `obd_trip_duration_min` | 0.3 | 70.4 | — |
| `obd_trip_coolant_temp_max_c` | ~26 | ~96 | cold starts lower |
| `obd_trip_oil_temp_max_c` | 22.5 | 90.9 | — |
| `obd_trip_dpf_soot_pct` | 75 | 6326 | **pre-RBS** values; post-fix ≈ 0–100% |
| `obd_trip_dpf_since_regen_km` | 0 | 4081 | pre-RBS; post-fix ≈ 0–255 km |
| `obd_trip_dpf_avg_regen_km` | 1649 | 3297 | pre-RBS; post-fix ≈ 252–262 km |
| `obd_trip_exhaust_after_cat_c` | 26 | 724 | >550 signals active regen |
| `obd_trip_adblue_range_km` | 4200 | 4800 | — |
| `obd_trip_ss_state` | 1 | 9 | discrete: 0=off, 1=active, 9=fault |
| `obd_trip_odometer_km` | 17059 | 17162 | km across this log session |

---

## 6. Per-PID Statistics Schema

`trip["obd_pid_values"][slug]` — where `slug = _slugify_pid(pid_name)`:

```python
{
    "last":                float,   # last sample in engine window
    "first":               float,   # first sample in engine window
    "min":                 float,
    "max":                 float,
    "mean":                float,
    "mode":                float,   # most frequent value (ties: first seen)
    "samples":             int,     # count of samples in window
    "kind":                str,     # "bool" | "discrete" | "number"
    "first_seen_s":        float,   # seconds after window start when PID first appeared
    "last_seen_s":         float,   # seconds after window start when PID last appeared
    "age_from_trip_end_s": float,   # seconds between last sample and window end
    "coverage_pct":        float,   # % of engine-on window covered by this PID
    "sample_rate_hz":      float,   # samples / covered_span
    "is_stale":            bool,    # True if age_from_trip_end_s > 60
}
```

### PID kind classification

| Kind | Criteria |
|------|----------|
| `bool` | All samples ∈ {0, 1} AND unit is empty |
| `discrete` | All samples are integer-valued AND ≤ 32 distinct values AND unit is empty |
| `number` | Everything else (any non-empty unit → always `number`) |

### Slug generation

```python
import re
_SLUG_RE = re.compile(r"[^a-z0-9]+")
def _slugify_pid(name: str) -> str:
    s = name.lower().strip()
    s = s.replace("°", "deg").replace("²", "2").replace("µ", "u")
    s = _SLUG_RE.sub("_", s).strip("_")
    return s or "pid"
```

Examples:
- `"[ECM] Crankshaft speed"` → `"ecm_crankshaft_speed"`
- `"Giri motore"` → `"giri_motore"`
- `"[ECM] Exhaust gas temperature after pre-catalytic converter"` → `"ecm_exhaust_gas_temperature_after_pre_catalytic_converter"`

---

## 7. RBS Byte-Swap Correction

### Background

CarScanner's MD1CS003 profile sets `RBS=true` on certain 2-byte PIDs where the
ECU transmits big-endian but the profile expects little-endian. The result is a
systematically wrong value: `(B*256+A)*mul/div + ofs` instead of the correct
`(A*256+B)*mul/div + ofs`.

### Detection signature

The byte-swap artifact produces a characteristic ~2× ratio between the two
distinct values a PID shows across trips:

```
[ECM] Average mileage for last 10 regens: 1649 km and 3296.94 km
→ 3296.94 ≈ 2 × 1649  (classic byte-swap symptom with div=16)
```

### Reversal formula

```python
def _rbs_swap_value(value: float, div: float, mul: float, ofs: float) -> float:
    raw = round((value - ofs) * div / mul)
    if raw < 0:
        raw += 0x10000        # handle signed int16 interpretation
    if not 0 <= raw <= 0xFFFF:
        return value          # no-op: value doesn't fit uint16
    swapped = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
    return swapped * mul / div + ofs
```

### Verified example

`[ECM] Average mileage for last 10 regens`, `div=16, mul=1, ofs=0`:

| CSV value | Raw (×16) | Hex | Swapped | Corrected |
|-----------|-----------|-----|---------|-----------|
| 1649 km | 26384 | `0x6710` | `0x1067` = 4199 | **262.4 km** |
| 3296.94 km | 52751 | `0xCE0F` | `0x0FCE` = 4046 | **252.9 km** |

Both decode to ~252–262 km average DPF regen interval — physically realistic.
Without the fix: 1649 km and 3297 km — wildly incorrect.

### Full `_RBS_FIXES` table

| PID name | div | mul | ofs | Uncorrected range | Corrected range |
|----------|-----|-----|-----|-------------------|-----------------|
| `[ECM] Distance traveled since the last regeneration` | 16 | 1 | 0 | 0–4081 km | 0–255 km |
| `[ECM] Average mileage for the last 10 regenerations` | 16 | 1 | 0 | 1649–3297 km | 252–262 km |
| `[ECM] Soot clogging level of diesel particulate filter` | 10.24 | 1 | 0 | 75–6326 % | 0–100 % |
| `[ECM] Open loop soot load assessment of the diesel particulate filter` | 1024 | 1 | 0 | high | 0–64 g/L |
| `[ECM] Set value of turbo boost pressure` | 1 | 1 | 0 | varies | corrected |
| `[ECM] EGR valve position` | 100 | 1 | 0 | varies | 0–100 % |
| `[ECM] Air metering valve position` | 100 | 1 | 0 | varies | 0–100 % |
| `[ECM] NOx content measured at the inlet of the NOx catalytic converter` | 1 | 0.1 | 0 | varies | corrected |
| `[ECM] Total mass of additive accumulated in the diesel particulate filter` | 128 | 1 | 0 | varies | corrected |
| `[ECM] Mileage remaining before diesel particulate filter replacement` | 1 | 16 | 0 | varies | corrected |
| `[ECM] Exhaust gas pressure at the outlet of the particulate filter` | 999.999… | 1 | 0 | varies | corrected |

Users opt in per PID via the options flow; default is empty (no correction).
This is safe for users on other ECUs/profiles where the bug does not exist.

---

## 8. DPF State Machine

### Input PIDs

| PID | Slug | Role |
|-----|------|------|
| `[ECM] DPF regeneration status` | `ecm_dpf_regeneration_status` | Primary regen flag (0=off, ≥1=active, 255=completed) |
| `[ECM] Regeneration enable` | `ecm_regeneration_enable` | Gate: regen allowed by ECU (bool) |
| `[ECM] Exhaust gas temperature after pre-catalytic converter` | `ecm_exhaust_gas_temperature_after_pre_catalytic_converter` | Thermal confirmation (°C) |
| `[ECM] Temperature of the NOx catalytic converter` | `ecm_temperature_of_the_nox_catalytic_converter` | Secondary thermal (°C) |
| `[ECM] Closed loop soot load assessment of the diesel particulate filter` | `ecm_closed_loop_soot_load_assessment_of_the_diesel_particulate_filter` | Soot g/L |
| `[ECM] Distance traveled since the last regeneration` | `ecm_distance_traveled_since_the_last_regeneration` | Counter reset detects completed regen |

### State determination logic

```python
_regen_requested = max(regen_status_vals) >= 1
_regen_enabled   = (not regen_enable_vals) or max(regen_enable_vals) >= 1
_thermal_regen   = (max(egt_after_vals) > 550) or (max(nox_cat_vals) > 550)
_regen_active    = _regen_requested and _regen_enabled and _thermal_regen

# Within-trip detection: distance counter resets by ≥90% from a value > 20 km
_dist_reset_in_trip = (
    len(dist_regen_recs) >= 2
    and dist_regen_recs[0][1] > 20
    and any(v < dist_regen_recs[0][1] * 0.1 for _, v in dist_regen_recs[1:])
)

# Post-regen cooldown started before recording began
_cooldown_started = (
    dist_regen_recs[0][1] < 1.0
    and _thermal_regen and not _regen_requested
)

if _regen_active and (_dist_reset_in_trip or soot_end <= 0.5):
    state = "completed"
elif _regen_active:
    state = "active"
elif _regen_requested and not _thermal_regen:
    state = "requested"
elif (dist_end < 20 and not _regen_active) or _cooldown_started:
    state = "post_regen"
else:
    state = "idle"
```

### State meanings

| State | Meaning |
|-------|---------|
| `idle` | No regen activity; normal driving |
| `requested` | ECU has requested regen but EGT not yet at threshold |
| `active` | Regen in progress (EGT >550 °C, status flag set) |
| `completed` | Regen completed within this trip (distance counter reset or soot near 0) |
| `post_regen` | Regen completed before this trip started (distance at 0, EGT still high, OR cross-file detection) |

### Inter-file regen detection

Applied in the coordinator between trips: if the first `distance_since_regen`
reading in the new trip is < 50% of the previous trip's last value AND the new
trip's regen state is `idle` or `post_regen`, the new trip state is forced to
`post_regen`.

---

## 9. GPS Data

### CSV source (primary, reliable)

Every row in every CSV file carries lat/lon regardless of PID type.

```python
# Extract GPS track from CSV
gps_track = [
    (float(row[0]), float(row[4]), float(row[5]))   # (seconds, lat, lon)
    for row in rows
    if len(row) >= 6 and row[4] and float(row[4]) != 0.0
]
```

Resolution: one GPS point per OBD sample (≈0.3–1 Hz). Values are precise to
~11 decimal places (sub-millimeter, i.e. GPS noise limited). Zero values indicate
no GPS lock (only at the very start of a session).

### BRC source (secondary, GPS-extended records only)

In BRC files, GPS coordinates appear only in the 28-byte extra block appended
to `Altitudine GPS` records (`pid_id = b"\x30\x03\x03\x00"`):

```
[24-byte record]
[28-byte GPS extra]:
  bytes  0–3:  field4 (unknown purpose)
  bytes  4–11: secondary double LE (possibly GPS speed or bearing)
  bytes 12–19: latitude  float64 LE
  bytes 20–27: longitude float64 LE
```

These GPS records are sparse compared to CSV (one point per GPS altitude update,
not per OBD sample). For map rendering, prefer CSV lat/lon.

### Building a GPS polyline

```python
from collections import namedtuple
GpsPoint = namedtuple("GpsPoint", ["ts", "lat", "lon"])

def extract_gps_track(rows) -> list[GpsPoint]:
    pts = []
    for row in rows:
        try:
            lat, lon = float(row[4]), float(row[5])
        except (ValueError, IndexError):
            continue
        if lat == 0.0 and lon == 0.0:
            continue  # GPS not locked yet
        pts.append(GpsPoint(float(row[0]), lat, lon))
    # Deduplicate consecutive identical coordinates
    deduped = [pts[0]] if pts else []
    for p in pts[1:]:
        if p.lat != deduped[-1].lat or p.lon != deduped[-1].lon:
            deduped.append(p)
    return deduped
```

### GPS bounding box for real logs

```
SW corner: 40.447°N, 14.707°E
NE corner: 40.698°N, 15.000°E
Center:    40.572°N, 14.854°E  (Salerno area)
```

---

## 10. Full PID Reference

### Curated PIDs (always extracted)

| Data key | PID name(s) | Unit | Aggregation |
|----------|-------------|------|-------------|
| `obd_trip_distance_km` | Distanza percorsa: | km | last |
| `obd_trip_avg_speed_kmh` | Velocità (GPS) | km/h | mean |
| `obd_trip_max_speed_kmh` | Velocità (GPS) | km/h | max |
| `obd_trip_avg_rpm` | Giri motore / [ECM] Crankshaft speed | rpm | mean |
| `obd_trip_max_rpm` | Giri motore / [ECM] Crankshaft speed | rpm | max |
| `obd_trip_coolant_temp_max_c` | Temperatura liquido raffreddamento / [ECM] Coolant temperature, corrected | °C | max |
| `obd_trip_oil_temp_max_c` | [ECM] Oil temperature | °C | max |
| `obd_trip_odometer_km` | [ECM] Total mileage | km | last |
| `obd_trip_air_temp_c` | Temperatura d'aria ambiente / [ECM] Outside air temperature | °C | first |
| `obd_trip_dpf_soot_pct` | [ECM] Soot clogging level of diesel particulate filter | % | last |
| `obd_trip_dpf_closed_soot` | [ECM] Closed loop soot load assessment of the diesel particulate filter | g/L | last |
| `obd_trip_dpf_regen_active` | [ECM] DPF regeneration status | — | max |
| `obd_trip_dpf_regen_enable` | [ECM] Regeneration enable | — | max |
| `obd_trip_dpf_regen_capability` | [ECM] Long-term regeneration capability | % | last |
| `obd_trip_dpf_regen_capability_st` | [ECM] Short-term regeneration capability | % | last |
| `obd_trip_dpf_since_regen_km` | [ECM] Distance traveled since the last regeneration | km | last |
| `obd_trip_dpf_avg_regen_km` | [ECM] Average mileage for the last 10 regenerations | km | last |
| `obd_trip_dpf_replace_km` | [ECM] Mileage remaining before diesel particulate filter replacement | km | last |
| `obd_trip_adblue_vol_l` | [ECM] Volume of urea solution measured in urea tank | L | last |
| `obd_trip_adblue_range_km` | [ECM] Vehicle mileage remaining before filling the tank with urea solution | km | last |
| `obd_trip_exhaust_before_cat_c` | [ECM] Exhaust gas temperature before pre-catalytic converter | °C | max |
| `obd_trip_exhaust_after_cat_c` | [ECM] Exhaust gas temperature after pre-catalytic converter | °C | max |
| `obd_trip_nox_cat_temp_max_c` | [ECM] Temperature of the NOx catalytic converter | °C | max |
| `obd_trip_battery_startup_v` | [ECM] Minimum battery voltage at startup | V | last |
| `obd_trip_oil_dilution_pct` | [ECM] Evaluation of the degree of dilution of motor oil | % | last |
| `obd_trip_ss_state` | [ECM] Stop and Start function state | — | last |

### Engine-on window anchors (tried in order)

```python
_ENGINE_ANCHOR_PIDS = (
    "Giri motore",
    "[ECM] Crankshaft speed",
    "Velocità veicolo",
    "[ECM] Vehicle speed",
    "Distanza percorsa:",
)
```

First match determines `t_start` and `t_end` of the engine window. All
aggregations operate on samples within `[t_start, t_end]`.

### Notable ECM PIDs (non-curated)

Selected from the 202 ECM PIDs observed in real logs:

| PID | Unit | Notes |
|-----|------|-------|
| [ECM] Oil pressure | bar | — |
| [ECM] Fuel pressure | bar | common rail |
| [ECM] Desired high pressure common rail fuel pressure | bar | vs measured |
| [ECM] Measured turbo boost pressure | mbar | — |
| [ECM] Set value of turbo boost pressure | mbar | RBS-affected |
| [ECM] EGR valve position | % | RBS-affected |
| [ECM] Air metering valve position | % | RBS-affected |
| [ECM] Calculated fuel injection amount | mg/str. | — |
| [ECM] Air flow | mg/str. | — |
| [ECM] Particulate filter differential pressure | mbar | DPF blockage indicator |
| [ECM] DPF differential pressure sensor signal deviation from reference value | mbar | — |
| [ECM] Exhaust gas flow through the particulate filter | L/h | — |
| [ECM] Assessment of thermal aging of the particulate filter | % | — |
| [ECM] Total mass of additive accumulated in the diesel particulate filter | g | RBS-affected |
| [ECM] Mileage since last diesel particulate filter replacement | km | — |
| [ECM] NOx content measured at the inlet of the NOx catalytic converter | ppm | RBS-affected |
| [ECM] Calculated NOx content at the inlet of the NOx catalytic converter | mg/sec | — |
| [ECM] NOx catalytic converter urea content | g | — |
| [ECM] Urea mass fraction | % | — |
| [ECM] Remaining urea solution in the urea tank | mg | — |
| [ECM] Evaluation of the exhaust gas flow using the NOx sensor | L/h | — |
| [ECM] Distance remaining until the next oil change | km | — |
| [ECM] Evaluation of carbon content in engine oil | % | — |
| [ECM] Battery current | A | — |
| [ECM] Engine control computer supply voltage | V | — |
| [ECM] Computer temperature | °C | — |
| [ECM] Alternator load value | % | — |
| [ECM] Cruise control set speed | km/h | — |
| [ECM] Service battery charge status | % | — |
| [ECM] Engine restart counter by Stop and Start function | — | discrete |
| [ECM] Engine stop time | sec. | time at idle stop |

---

## 11. AI Insight Engine

### Architecture overview

```
trips[]  ──→  per-trip analyzer  ──→  trip insights
         ──→  cross-trip analyzer ──→  trend insights
         ──→  anomaly detector    ──→  alerts
```

All rules operate on the `_compute_stats` output schema (§4 / §5). No external
ML library needed for the rule-based layer; a time-series store (SQLite or
InfluxDB) powers the trend layer.

---

### 10.1 Per-trip insights

Each of the following produces a human-readable insight string or is suppressed
when the necessary PID is absent.

#### Engine warm-up profile

```python
coolant_first = pid_values["temperatura_liquido_raffreddamento_motore"]["first"]
coolant_last  = pid_values["temperatura_liquido_raffreddamento_motore"]["last"]
warm_up_time  = pid_values["temperatura_liquido_raffreddamento_motore"]["first_seen_s"]

if coolant_first < 40:
    warmup_delta = coolant_last - coolant_first
    insight = f"Motore partito freddo ({coolant_first:.0f} °C). "
              f"Raggiunto {coolant_last:.0f} °C in {warmup_delta:.0f} °C."
```

#### Regen status narrative

```python
state_labels = {
    "idle":      "Nessuna rigenerazione DPF in questo viaggio.",
    "requested": "Rigenerazione DPF richiesta ma non completata — viaggio troppo breve.",
    "active":    "Rigenerazione DPF in corso durante il viaggio.",
    "completed": "Rigenerazione DPF completata durante il viaggio.",
    "post_regen":"Rigenerazione DPF appena completata prima di questo viaggio.",
}
```

#### DPF soot level alert

```python
soot = trip["obd_trip_dpf_soot_pct"]   # after RBS correction → 0–100 %
if soot is not None:
    if soot >= 90:
        alert(CRITICAL, f"DPF intasato al {soot:.0f}% — rigenerazione urgente")
    elif soot >= 70:
        alert(WARNING,  f"DPF al {soot:.0f}% — presto necessaria rigenerazione")
```

#### Short-term regen capability drop

```python
st_cap = trip["obd_trip_dpf_regen_capability_st"]
lt_cap = trip["obd_trip_dpf_regen_capability"]
if st_cap is not None and lt_cap is not None and st_cap < lt_cap * 0.5:
    alert(WARNING, f"Capacità rigenerazione a breve termine ({st_cap:.0f}%) "
                   f"molto inferiore al lungo termine ({lt_cap:.0f}%). "
                   "Possibile problema con la rigenerazione post-guida.")
```

#### EGT spike detection

```python
egt = trip["obd_trip_exhaust_after_cat_c"]
if egt is not None and egt > 700:
    alert(WARNING, f"Temperatura gas scarico insolita: {egt:.0f} °C. "
                   "Verificare stato DPF e condizioni di guida.")
```

#### Oil dilution trend

```python
dil = trip["obd_trip_oil_dilution_pct"]
if dil is not None:
    if dil > 3.0:
        alert(CRITICAL, f"Diluizione olio elevata: {dil:.1f}%. "
                        "Verificare se le rigenerazioni DPF sono frequenti o incomplete.")
    elif dil > 1.5:
        alert(WARNING, f"Diluizione olio: {dil:.1f}% — monitorare.")
```

#### Stop-and-Start health

```python
ss_state = trip["obd_trip_ss_state"]
# State 9 = fault condition
if ss_state == 9:
    alert(WARNING, "Stop&Start in stato di guasto (codice 9) — controllare batteria.")
```

#### Fuel consumption context

```python
l100 = trip["obd_trip_consumption_l100km"]
dist = trip["obd_trip_distance_km"]
if l100 is not None and dist is not None:
    if dist < 5 and l100 > 15:
        insight = (f"Viaggio molto breve ({dist:.1f} km): consumo {l100:.1f} L/100km "
                   "elevato tipicamente dovuto a riscaldamento motore.")
    elif l100 < 4.5:
        insight = f"Consumo eccellente: {l100:.1f} L/100km."
    elif l100 > 8.0:
        insight = f"Consumo elevato ({l100:.1f} L/100km) — verificare pressione gomme e filtro aria."
```

---

### 10.2 Cross-trip (trend) insights

Require a history of ≥3 trips with the same PID available.

#### Soot accumulation rate

```python
# Compute soot increase per km driven between consecutive regens
soot_per_km = []
for i in range(1, len(trips)):
    if trips[i]["obd_dpf_regen_state"] not in ("completed", "post_regen"):
        soot_delta = trips[i]["obd_trip_dpf_soot_pct"] - trips[i-1]["obd_trip_dpf_soot_pct"]
        km_delta   = trips[i]["obd_trip_distance_km"]
        if soot_delta > 0 and km_delta:
            soot_per_km.append(soot_delta / km_delta)

# If rate is accelerating, alert
if len(soot_per_km) >= 3 and soot_per_km[-1] > mean(soot_per_km[:-1]) * 1.5:
    alert(WARNING, "La velocità di accumulo fuliggine DPF è aumentata del 50% "
                   "rispetto alla media — possibile intasamento EGR o uso in città.")
```

#### Regen frequency trend

```python
regen_intervals = [
    trips[i]["obd_trip_dpf_avg_regen_km"]
    for i in range(len(trips))
    if trips[i]["obd_trip_dpf_avg_regen_km"] is not None
]
# Decreasing interval (after RBS correction) → regens happening more often → sign of stress
if len(regen_intervals) >= 5:
    slope = linear_regression_slope(range(len(regen_intervals)), regen_intervals)
    if slope < -10:   # km per regen decreasing by >10 km per trip
        alert(WARNING, f"Intervallo medio tra rigenerazioni in calo: "
                       f"{regen_intervals[-1]:.0f} km (era {regen_intervals[0]:.0f} km). "
                       "Verificare cicli di guida (troppi percorsi urbani brevi).")
```

#### Battery voltage degradation

```python
startup_voltages = [t["obd_trip_battery_startup_v"] for t in trips
                    if t.get("obd_trip_battery_startup_v") is not None]
if len(startup_voltages) >= 5:
    slope = linear_regression_slope(range(len(startup_voltages)), startup_voltages)
    if slope < -0.02:   # V per trip
        alert(WARNING, f"Tensione avviamento batteria in calo: "
                       f"{startup_voltages[-1]:.2f} V. Considerare diagnosi batteria.")
```

#### AdBlue consumption rate

```python
adblue_ranges = [t["obd_trip_adblue_range_km"] for t in trips
                 if t.get("obd_trip_adblue_range_km") is not None]
if adblue_ranges and adblue_ranges[-1] < 500:
    alert(CRITICAL, f"Autonomia AdBlue: {adblue_ranges[-1]:.0f} km. Rifornire presto.")
elif adblue_ranges and len(adblue_ranges) >= 3:
    drop_per_trip = (adblue_ranges[0] - adblue_ranges[-1]) / len(adblue_ranges)
    if drop_per_trip > 100:
        alert(WARNING, f"Consumo AdBlue elevato: -{drop_per_trip:.0f} km/viaggio.")
```

#### Oil change reminder

```python
km_to_service = [t.get("obd_pid_values", {}).get(
    "ecm_distance_remaining_until_the_next_oil_change", {}).get("last")
    for t in trips]
latest = next((v for v in reversed(km_to_service) if v is not None), None)
if latest is not None and latest < 2000:
    alert(WARNING, f"Prossimo cambio olio tra {latest:.0f} km.")
```

---

### 10.3 Insight data model

```python
@dataclass
class Insight:
    trip_id:    str                          # obd_filename or UUID
    timestamp:  datetime
    category:   str                          # "dpf" | "engine" | "fuel" | "battery" | "adblue" | "maintenance"
    level:      str                          # "info" | "warning" | "critical"
    title:      str                          # short human-readable title
    body:       str                          # detailed explanation
    pids_used:  list[str]                    # PIDs that triggered this insight
    values:     dict[str, float]             # key → value snapshot
    is_trend:   bool                         # False = per-trip, True = cross-trip
    trip_range: tuple[str, str] | None       # (first_trip_id, last_trip_id) for trend insights
```

---

## 12. GPS Map Feature

### Data pipeline

```
CSV rows  ──→  extract_gps_track()  ──→  GpsPoint list
                                          │
                                          ├── polyline (lat/lon array)
                                          └── time index (ts → point index)

obd_pid_values  ──→  pid_time_series(slug)  ──→  [(ts, value), ...]
                                                   │
                                                   └── interpolate at GPS ts → colored polyline
```

### Map layers

#### Layer 1: Trip polyline

```javascript
// GeoJSON LineString from GPS track
{
  "type": "Feature",
  "geometry": {
    "type": "LineString",
    "coordinates": [[lon, lat], [lon, lat], ...]
  },
  "properties": {
    "trip_id": "2026-05-20 16-39-54",
    "duration_min": 18.0,
    "distance_km": 12.4,
    "dpf_regen_state": "idle"
  }
}
```

#### Layer 2: PID color overlay

Map a selected PID value to a color gradient along the route:

```python
def pid_colored_segments(gps_track, pid_series, colormap="RdYlGn_r"):
    """
    Returns list of GeoJSON LineString segments, each colored by PID value.
    gps_track: list[GpsPoint(ts, lat, lon)]
    pid_series: list[(ts, value)] — raw samples, interpolated at each GPS point
    """
    # 1. Interpolate PID value at each GPS timestamp
    pid_ts = [t for t, _ in pid_series]
    pid_vs = [v for _, v in pid_series]
    colors = []
    for pt in gps_track:
        val = interpolate_linear(pid_ts, pid_vs, pt.ts)
        colors.append(val)
    # 2. Normalize to [0, 1]
    v_min, v_max = min(colors), max(colors)
    norm = [(v - v_min) / max(v_max - v_min, 1e-9) for v in colors]
    # 3. Build segments
    segments = []
    for i in range(len(gps_track) - 1):
        segments.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [gps_track[i].lon, gps_track[i].lat],
                    [gps_track[i+1].lon, gps_track[i+1].lat],
                ]
            },
            "properties": {
                "value": colors[i],
                "normalized": norm[i],
                "color": colormap_hex(colormap, norm[i]),
            }
        })
    return segments
```

Suggested colormaps per PID category:

| Category | Colormap | Direction |
|----------|----------|-----------|
| EGT / temperatures | RdYlGn_r (red=hot) | high=bad |
| RPM | viridis | high=intense |
| Speed | plasma | high=fast |
| DPF soot | RdYlGn_r | high=bad |
| Fuel consumption | RdYlGn | low=good |
| Oil temperature | RdYlGn_r | high=bad |

#### Layer 3: Event markers

Point markers at notable events:

```python
events = []
# Regen start: first sample where regen_status >= 1
# Regen end: last sample where regen_status >= 1
# Max EGT: GPS point at time of peak exhaust temperature
# Speed limit exceeded: GPS points where speed > threshold
# Engine cold start: first sample with coolant < 40°C
for category, ts, value, label in detected_events:
    pt = gps_track_at(ts)
    events.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [pt.lon, pt.lat]},
        "properties": {"category": category, "value": value, "label": label}
    })
```

### Time-sync slider

A scrubber UI synced to the map:

```
Timeline: |====|==== [40%] ====|====|
           start          current   end
                          ↑
                     updates:
                     - highlighted position on map polyline
                     - sidebar showing all PID values at this timestamp
                     - marker showing speed, RPM, EGT, soot simultaneously
```

Implementation: index GPS track by relative seconds, binary search on slider
position, update map viewport to follow vehicle position.

### Recommended mapping library

| Library | Notes |
|---------|-------|
| **Leaflet.js** + OpenStreetMap | Lightest; no API key |
| **MapLibre GL JS** + MapTiler | WebGL, smooth gradient segments |
| **Deck.gl** TripsLayer | Best for animated trip replay |
| **Folium** (Python) | Quick prototyping, not interactive |

For the full platform: MapLibre GL JS with a `TripsLayer` for replay and
`PathLayer` with data-driven coloring for static PID overlays.

---

## 13. Platform Architecture & HA Integration Strategy

### The core question: standalone platform vs. HA-native

The integration currently lives entirely inside Home Assistant as a custom
component. The alternative is a **standalone platform** (FastAPI + web UI) that
exposes selected sensors back to HA via a lightweight channel.

#### Option A — Keep everything in HA (current approach)

```
CarScanner CSV/BRC ──→  HA OBD coordinator  ──→  HA sensor entities
MyOpel .myop        ──→  HA main coordinator ──→  HA sensor entities
                                               ──→  Lovelace card (custom JS)
```

**Pros:** Single codebase, HA automations work natively (DPF alert → push
notification), persists through reboots, no extra service to run.

**Cons:** HA entity model is flat and stateless — no GPS map, no cross-trip
trend charts, no AI insight card that goes beyond what Lovelace can render.
UI customisation is limited to Lovelace YAML + custom cards.

#### Option B — Standalone platform + MQTT bridge to HA ✓ Recommended

```
CarScanner CSV/BRC ──→  Standalone backend ──→  SQLite (full history)
MyOpel .myop        ──→  (FastAPI + Python)  ──→  GPS map + PID overlay
                         │                    ──→  AI insight engine
                         │ MQTT autodiscovery
                         ▼
                    HA MQTT broker  ──→  HA sensor entities (auto-discovered)
                                    ──→  HA automations (DPF alert → notify)
                                    ──→  HA Energy/LTS dashboard
```

This is **strictly better** than option A for the rich-UI features you want.
The MQTT bridge means HA **still** gets all its sensors — with autodiscovery,
they appear automatically in the HA entity registry with zero config. You keep
all HA automations while the platform adds what HA can't do: GPS map, trend
charts, AI insights.

#### Option C — Sidecar sharing HA's storage (avoid)

Reading HA's internal `.storage/` JSON from outside HA is fragile — the format
is undocumented, locks can corrupt it, and HA updates can silently break parsers.
Don't do this.

---

### Recommended architecture: standalone + MQTT

```
┌──────────────────────────────────────────────────────────┐
│  Web App (React/Vue + MapLibre GL JS)                    │
│  - Trip timeline + detail cards                          │
│  - GPS map with PID overlay, time-sync slider            │
│  - AI insight cards (per-trip + cross-trip)              │
│  - PID explorer, sparklines, trend charts                │
└──────────────────────┬───────────────────────────────────┘
                       │ REST + WebSocket
┌──────────────────────▼───────────────────────────────────┐
│  Backend (FastAPI / Python)                               │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Ingestion layer                                     │ │
│  │  - File watcher (watchdog): CSV, BRC, .myop          │ │
│  │  - IMAP fetcher (clone of imap_fetcher.py)           │ │
│  │  - Parser: _parse_csv_file / _parse_brc_file / myop  │ │
│  └──────────────────────┬──────────────────────────────┘ │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Analysis layer                                       │ │
│  │  - RBS correction, DPF state machine                  │ │
│  │  - AI insight engine (per-trip + trend rules)         │ │
│  │  - GPS track builder                                  │ │
│  └──────────────────────┬──────────────────────────────┘ │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  MQTT publisher (paho-mqtt or aiomqtt)                │ │
│  │  - Publishes HA MQTT autodiscovery config on start    │ │
│  │  - Publishes sensor state updates after each parse    │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
                       │ MQTT
┌──────────────────────▼───────────────────────────────────┐
│  Home Assistant                                           │
│  - MQTT integration (built-in, already common)           │
│  - Auto-discovered sensors: DPF soot, AdBlue, EGT...     │
│  - Automations: DPF alert → mobile push notification      │
│  - Energy dashboard: fuel consumption over time           │
└──────────────────────────────────────────────────────────┘
```

---

### MQTT autodiscovery — how it works

HA's MQTT integration supports **autodiscovery**: publishing a config payload to
a specific topic causes HA to create the sensor entity automatically, with no
YAML configuration needed.

```python
import json
import paho.mqtt.client as mqtt

HA_DISCOVERY_PREFIX = "homeassistant"
DEVICE_ID = "myopel_obd"

def publish_sensor_discovery(client: mqtt.Client, key: str, name: str,
                              unit: str | None, device_class: str | None) -> None:
    config = {
        "name": name,
        "unique_id": f"{DEVICE_ID}_{key}",
        "state_topic": f"{DEVICE_ID}/state",
        "value_template": f"{{{{ value_json.{key} }}}}",
        "unit_of_measurement": unit,
        "device_class": device_class,
        "device": {
            "identifiers": [DEVICE_ID],
            "name": "MyOpel OBD",
            "manufacturer": "Peugeot/Citroën",
            "model": "MD1CS003 1.5 BlueHDi",
        },
    }
    topic = f"{HA_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{key}/config"
    client.publish(topic, json.dumps(config), retain=True)

# Publish all sensor discoveries once at startup
def publish_all_discoveries(client: mqtt.Client) -> None:
    sensors = [
        ("obd_trip_dpf_soot_pct",       "DPF Soot %",         "%",      None),
        ("obd_trip_dpf_regen_state",     "DPF Regen State",    None,     None),
        ("obd_trip_dpf_since_regen_km",  "km Since Regen",     "km",     "distance"),
        ("obd_trip_dpf_avg_regen_km",    "Avg km per Regen",   "km",     "distance"),
        ("obd_trip_adblue_range_km",     "AdBlue Range",       "km",     "distance"),
        ("obd_trip_exhaust_after_cat_c", "EGT After Cat",      "°C",     "temperature"),
        ("obd_trip_fuel_consumed_l",     "Fuel Consumed",      "L",      None),
        ("obd_trip_consumption_l100km",  "Consumption",        "L/100km",None),
        ("obd_trip_distance_km",         "Trip Distance",      "km",     "distance"),
        ("obd_trip_duration_min",        "Trip Duration",      "min",    "duration"),
        ("obd_trip_max_speed_kmh",       "Max Speed",          "km/h",   "speed"),
        ("obd_trip_oil_dilution_pct",    "Oil Dilution",       "%",      None),
        ("obd_trip_battery_startup_v",   "Battery Startup",    "V",      "voltage"),
        # .myop sensors
        ("myop_fuel_level_pct",          "Fuel Level",         "%",      None),
        ("myop_fuel_autonomy_km",        "Fuel Range",         "km",     "distance"),
        ("myop_last_trip_alert_count",   "Trip Alerts",        None,     None),
        ("myop_odometer_km",             "Odometer",           "km",     "distance"),
    ]
    for key, name, unit, device_class in sensors:
        publish_sensor_discovery(client, key, name, unit, device_class)

# After parsing a trip, publish state update in one JSON payload
def publish_trip_state(client: mqtt.Client, trip: dict) -> None:
    payload = {k: v for k, v in trip.items() if not isinstance(v, dict)}
    client.publish(f"{DEVICE_ID}/state", json.dumps(payload), retain=True)
```

With `retain=True`, the last known value persists in the broker — HA sensors
survive a restart without needing a new parse.

---

### Unified data model (both sources merged)

A combined trip record joins OBD + .myop data matched by timestamp proximity:

```python
@dataclass
class UnifiedTrip:
    # Identity
    id:              str          # obd_filename or myop trip id
    recorded_at:     datetime     # from filename (OBD) or end.date (myop)

    # From CarScanner OBD (CSV/BRC)
    obd_duration_min:       float | None
    obd_distance_km:        float | None
    obd_fuel_consumed_l:    float | None
    obd_l100km:             float | None
    obd_dpf_soot_pct:       float | None
    obd_dpf_regen_state:    str   | None
    obd_dpf_since_regen_km: float | None
    obd_dpf_avg_regen_km:   float | None
    obd_adblue_range_km:    float | None
    obd_egt_after_max_c:    float | None
    obd_oil_dilution_pct:   float | None
    obd_battery_startup_v:  float | None
    gps_track:              list[GpsPoint]
    pid_samples:            dict[str, list[tuple[float, float]]]

    # From MyOpel .myop
    myop_trip_id:           int   | None
    myop_fuel_level_pct:    int   | None
    myop_fuel_autonomy_km:  int   | None
    myop_stellantis_fuel_l: float | None   # fuelConsumption / 1_000_000
    myop_alerts:            list[int]
    myop_days_to_service:   int   | None
    myop_km_to_service:     int   | None
    myop_price_fuel:        float | None

    # Cross-source derived
    fuel_cross_check_ok:    bool | None  # OBD vs Stellantis fuel ±15%
    insights:               list[Insight]
```

### Trip matching between sources — asynchronous by design

**The two export paths are independent and will rarely arrive together:**

- `.myop` files arrive when the Stellantis server decides to send an email
  (typically once per day, sometimes days late, sometimes batched).
- OBD CSV/BRC files arrive immediately when the driver taps "export" in
  CarScanner (or automatically via Shortcuts).

This means at any point in time you may have:
- OBD trips with no matching `.myop` record yet (OBD arrived first)
- `.myop` trips with no OBD data (short trip, CarScanner not running)
- Both sources available → opportunity to correlate

The correct architecture treats each trip record as **potentially partial**,
enriching it retroactively when the other source arrives later.

```python
# trips table always exists; myop fields are nullable
# When a .myop file arrives days after the OBD parse:
def enrich_with_myop(obd_trip_id: str, myop_trip: MyOpTrip, db: sqlite3.Connection):
    db.execute("""
        UPDATE trips SET
            myop_trip_id       = ?,
            myop_fuel_level_pct  = ?,
            myop_fuel_autonomy_km= ?,
            myop_stellantis_fuel_l = ?,
            myop_alerts        = ?,
            myop_days_to_service = ?,
            myop_km_to_service   = ?,
            myop_price_fuel      = ?
        WHERE id = ?
    """, (myop_trip.id, myop_trip.fuel_level, myop_trip.autonomy,
          myop_trip.fuel_l, json.dumps(myop_trip.alerts),
          myop_trip.days_to_service, myop_trip.km_to_service,
          myop_trip.price_fuel, obd_trip_id))
```

### Trip correlation algorithm

Match OBD trips to `.myop` trips when both are present, using timestamp +
distance as a double-check. Handle the case where either source is absent.

```python
from datetime import datetime, timedelta, timezone

TIMEZONE_OFFSET_H = 1  # Italy CET (use 2 for CEST)

def correlate(
    obd_trips: list[dict],       # from OBD parser; have 'recorded_at' (UTC datetime)
    myop_trips: list[dict],      # from .myop; have 'start.date', 'end.date' (CET strings)
    tolerance_min: int = 10,     # max allowed gap between OBD start and myop start
    distance_tolerance_pct: float = 0.20,  # 20% distance discrepancy allowed
) -> list[dict]:
    """
    Returns unified records. Each record has:
      - obd_data: dict | None
      - myop_data: dict | None
      - correlation_confidence: "high" | "low" | "none"
    Unmatched trips from either source are emitted as partial records.
    """
    def myop_start_utc(myop: dict) -> datetime:
        raw = myop["start"]["date"].rstrip("Z")
        local = datetime.fromisoformat(raw)
        return local.replace(tzinfo=timezone.utc) - timedelta(hours=TIMEZONE_OFFSET_H)

    used_myop: set[int] = set()
    unified: list[dict] = []

    for obd in obd_trips:
        t_obd = obd["recorded_at"]
        d_obd = obd.get("obd_trip_distance_km") or 0.0

        best_myop = None
        best_score = float("inf")

        for myop in myop_trips:
            if myop["id"] in used_myop:
                continue
            t_myop = myop_start_utc(myop)
            delta_min = abs((t_obd - t_myop).total_seconds()) / 60

            if delta_min > tolerance_min:
                continue

            d_myop = myop.get("distance", 0.0)
            dist_ok = (
                d_obd == 0 or d_myop == 0 or
                abs(d_obd - d_myop) / max(d_obd, 0.01) <= distance_tolerance_pct
            )
            if not dist_ok:
                continue

            # Score: primarily by timestamp delta, tiebreak by distance match
            score = delta_min
            if score < best_score:
                best_score = score
                best_myop = myop

        confidence = (
            "high" if best_myop is not None and best_score <= 3 else
            "low"  if best_myop is not None else
            "none"
        )
        if best_myop is not None:
            used_myop.add(best_myop["id"])

        unified.append({
            "obd_data":              obd,
            "myop_data":             best_myop,
            "correlation_confidence": confidence,
        })

    # Emit unmatched .myop trips as partial records (OBD absent)
    for myop in myop_trips:
        if myop["id"] not in used_myop:
            unified.append({
                "obd_data":              None,
                "myop_data":             myop,
                "correlation_confidence": "none",
            })

    return unified
```

### What you gain from correlation

When both sources are available for the same trip:

| Check | OBD value | .myop value | Use for |
|-------|-----------|-------------|---------|
| Distance cross-check | `obd_trip_distance_km` | `distance` | Detect GPS drift or odometer error |
| Fuel cross-check | `obd_trip_fuel_consumed_l` (trapezoid) | `myop_stellantis_fuel_l` | Validate OBD fuel integration accuracy |
| Departure time | filename timestamp | `start.date` − 1h | Verify clock sync between phone and car |
| Alert context | DPF soot %, EGT curve | alert code 27 ("FAP intasamento") | Correlate OBD DPF state with official alert |

```python
# Fuel cross-check example
def fuel_cross_check(obd_l: float | None, myop_l: float | None) -> bool | None:
    if obd_l is None or myop_l is None or myop_l == 0:
        return None
    discrepancy_pct = abs(obd_l - myop_l) / myop_l
    return discrepancy_pct <= 0.15   # within 15% = acceptable

# Alert enrichment: alert code 27 = "DPF at risk"
def enrich_dpf_alert(trip: UnifiedTrip) -> str | None:
    if trip.myop_data and 27 in (trip.myop_data.get("alerts") or []):
        soot = trip.obd_data and trip.obd_data.get("obd_trip_dpf_soot_pct")
        if soot:
            return (f"Allerta DPF (cod. 27) confermata da OBD: "
                    f"intasamento al {soot:.0f}%")
        return "Allerta DPF (cod. 27) da Opel — dati OBD non disponibili per questo viaggio"
    return None
```

---

### Storage schema (SQLite — extended for both sources)

```sql
CREATE TABLE trips (
    id               TEXT PRIMARY KEY,  -- obd_filename or "myop:{myop_trip_id}"
    source           TEXT,              -- "obd_csv" | "obd_brc" | "myop" | "unified"
    recorded_at      TEXT,              -- ISO 8601 UTC
    myop_trip_id     INTEGER,           -- Stellantis trip id (nullable)

    -- OBD fields
    obd_duration_min     REAL,
    obd_distance_km      REAL,
    obd_fuel_consumed_l  REAL,
    obd_l100km           REAL,
    obd_dpf_soot_pct     REAL,
    obd_dpf_regen_state  TEXT,
    obd_dpf_since_regen_km REAL,
    obd_dpf_avg_regen_km   REAL,
    obd_adblue_range_km    REAL,
    obd_egt_after_max_c    REAL,
    obd_battery_startup_v  REAL,
    obd_oil_dilution_pct   REAL,
    obd_odometer_km        INTEGER,
    obd_max_speed_kmh      REAL,
    obd_avg_rpm            INTEGER,
    obd_coolant_max_c      REAL,
    obd_oil_temp_max_c     REAL,

    -- MyOpel fields
    myop_fuel_level_pct    INTEGER,
    myop_fuel_autonomy_km  INTEGER,
    myop_stellantis_fuel_l REAL,
    myop_alerts            TEXT,       -- JSON array of int
    myop_days_to_service   INTEGER,
    myop_km_to_service     INTEGER,
    myop_price_fuel        REAL,

    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE pid_samples (
    trip_id   TEXT NOT NULL REFERENCES trips(id),
    slug      TEXT NOT NULL,
    ts        REAL NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (trip_id, slug, ts)
);
CREATE INDEX idx_pid_samples_trip_slug ON pid_samples(trip_id, slug);

CREATE TABLE gps_tracks (
    trip_id   TEXT NOT NULL REFERENCES trips(id),
    ts        REAL NOT NULL,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    PRIMARY KEY (trip_id, ts)
);

CREATE TABLE insights (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id    TEXT,
    timestamp  TEXT,
    category   TEXT,
    level      TEXT,
    title      TEXT,
    body       TEXT,
    is_trend   INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

### API endpoints

```
GET  /trips                       → list of unified trip summaries (paginated)
GET  /trips/{id}                  → full trip dict (OBD + myop + insights)
GET  /trips/{id}/gps              → GeoJSON FeatureCollection
GET  /trips/{id}/pid/{slug}       → [(ts, value)] time series
GET  /trips/{id}/pid/{slug}/map   → GeoJSON colored segments for map overlay
GET  /insights?level=warning      → insights filtered by level
GET  /insights/summary            → count per category + latest critical
POST /upload/obd                  → accept CSV/BRC, parse and analyze
POST /upload/myop                 → accept .myop JSON, merge trips
WS   /live                        → push when new trip parsed
```

---

### Why standalone is better for your use case

| Feature | HA-native | Standalone + MQTT |
|---------|-----------|-------------------|
| GPS map with PID overlay | Not possible in Lovelace | Full MapLibre GL JS |
| Cross-trip trend charts | Very limited (LTS only) | Arbitrary SQL queries |
| AI insight cards | Custom card only | Full web UI flexibility |
| Trip timeline / history | Only last-trip sensors | Unlimited SQLite history |
| HA automations (DPF alert) | Native | Via MQTT (same result) |
| HA Energy dashboard | Via LTS injection | Via MQTT + device class |
| HA restart survivability | Built-in persistence | MQTT retain=true |
| Mobile app / PWA | Not possible | Full PWA capability |
| OBD parser reuse | Already implemented | Copy 4 functions from coordinator_obd.py |
| .myop parser reuse | Already implemented | Copy _parse_file from __init__.py |

The only thing you'd lose by going standalone is that the HA integration is
already battle-tested with watchdog, IMAP IDLE, deduplication, and RBS fixes.
But you copy those ~400 lines of pure-Python logic — zero HA imports — and the
standalone backend gets all of that for free.

**Bottom line:** build the standalone platform, expose 15–20 key sensors via
MQTT autodiscovery, decommission the HA custom integration. You gain everything,
you lose nothing that MQTT can't replicate.

---

## Appendix A: RBS verification script

```python
"""Verify RBS correction on real CSV values."""
from custom_components.myopel.coordinator_obd import _rbs_swap_value, _RBS_FIXES

cases = [
    ("[ECM] Average mileage for the last 10 regenerations", 1649.0),
    ("[ECM] Average mileage for the last 10 regenerations", 3296.94),
    ("[ECM] Distance traveled since the last regeneration", 2176.75),
    ("[ECM] Distance traveled since the last regeneration", 4080.75),
]
for pid_name, raw_csv in cases:
    fix = _RBS_FIXES[pid_name]
    corrected = _rbs_swap_value(raw_csv, fix["div"], fix["mul"], fix["ofs"])
    print(f"{pid_name}: {raw_csv:.2f} → {corrected:.4f}")

# Expected output:
# [ECM] Average mileage...: 1649.00 → 262.4375
# [ECM] Average mileage...: 3296.94 → 252.8750
# [ECM] Distance traveled...: 2176.75 → 200.5000
# [ECM] Distance traveled...: 4080.75 → 207.9375
```

---

## Appendix B: BRC vs CSV record count differences (observed)

| Trip | BRC records | CSV rows | Diff | Explanation |
|------|-------------|----------|------|-------------|
| 21-16-39 | 9,994 | 10,171 | -177 | 177 PIDs × ~1 boundary value each |
| 23-21-20 | 46,423 | 46,431 | -8 | 8 boundary values |
| 18-35-32 | 21,799 | 21,787 | +12 | BRC captures 13 extra PIDs not exported to CSV |
| All others | exact match | exact match | 0 | — |

---

## Appendix C: Frequently confused PID names

| Issue | PID | Notes |
|-------|-----|-------|
| Column typo | CSV column is `LONGTITUDE` not `LONGITUDE` | Use index [5], not column name |
| Two "Giri motore" PIDs | `Giri motore` (rpm) and `Giri motore x1000` (rpm×1000) | Use `Giri motore` for RPM |
| Two closed-loop soot PIDs | `[ECM] Closed loop soot load assessment...` (g/L) vs `[ECM] Open loop soot load assessment...` (g/L) | Open-loop is also RBS-affected |
| `Distanza percorsa:` colon | The colon is part of the PID name — must match exactly | Compare with `[ECM] Total mileage` for odometer fallback |
| Stop-and-Start state 9 | Observed in real logs; means fault/unavailable, not a speed value | Filter out 9 when computing mean |
