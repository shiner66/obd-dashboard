"""
MyOpel .myop parser — briefing §4.
A .myop file is plain JSON with a list of {vin, trips[]} objects.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from pathlib import Path


def _to_local_iso(date_str: str) -> str:
    """Return the Stellantis timestamp as local ISO, stripping the spurious Z suffix.

    Stellantis sends Italian local time but marks it as UTC (Z). The Z is wrong —
    the value is already in local time — so we just strip it and use it as-is.
    """
    return date_str.rstrip("Z")
    0:  {"sev": "critical", "label": "Pressione olio motore anomala"},
    1:  {"sev": "critical", "label": "Temperatura motore troppo elevata"},
    8:  {"sev": "warning",  "label": "Livello olio motore insufficiente"},
    17: {"sev": "warning",  "label": "Anomalia ESP / ASR"},
    20: {"sev": "warning",  "label": "Anomalia filtro gasolio"},
    22: {"sev": "info",     "label": "Livello carburante basso"},
    25: {"sev": "warning",  "label": "Anomalia sistema antinquinamento"},
    26: {"sev": "warning",  "label": "Anomalia ABS"},
    27: {"sev": "warning",  "label": "Rischio intasamento FAP"},
    29: {"sev": "warning",  "label": "Additivo FAP insufficiente"},
    46: {"sev": "info",     "label": "Liquido lavacristalli insufficiente"},
    52: {"sev": "warning",  "label": "Pressione pneumatici insufficiente"},
    57: {"sev": "info",     "label": "Rigenerazione FAP in corso"},
    59: {"sev": "warning",  "label": "Pressione pneumatico FL insufficiente"},
    60: {"sev": "warning",  "label": "Pressione pneumatico FR insufficiente"},
    61: {"sev": "warning",  "label": "Pressione pneumatico RL insufficiente"},
    62: {"sev": "warning",  "label": "Pressione pneumatico RR insufficiente"},
}



# Alert code → {sev, label}  (briefing §4 + data.js ALERTS)
ALERT_DICT: dict[int, dict] = {

def parse_file(path: str | Path) -> list[dict]:
    """
    Parse a .myop file and return a list of trip dicts in frontend format.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        data = [data]

    trips: list[dict] = []
    for vehicle_block in data:
        vin = vehicle_block.get("vin", "")
        for raw in vehicle_block.get("trips", []):
            trip = _parse_trip(raw, vin)
            trips.append(trip)

    return trips


def _parse_trip(raw: dict, vin: str) -> dict:
    trip_id = raw.get("id", 0)
    start_raw = raw.get("start", {}).get("date", "")
    end_raw   = raw.get("end",   {}).get("date", "")
    start_iso = _to_local_iso(start_raw)
    end_iso   = _to_local_iso(end_raw)

    start_odo = raw.get("start", {}).get("mileage", 0)
    end_odo   = raw.get("end",   {}).get("mileage", 0)

    travel_s   = raw.get("travelTime", 0) or 0
    distance   = raw.get("distance", 0.0) or 0.0
    fuel_raw   = raw.get("fuelConsumption", 0) or 0   # divide by 1_000_000 → litres
    fuel_l     = fuel_raw / 1_000_000
    price_fuel = raw.get("priceFuel")
    alerts_raw = raw.get("alerts") or []

    avg_speed = distance / (travel_s / 3600) if travel_s > 0 else None
    l100km    = fuel_l / distance * 100 if distance > 0 and fuel_l > 0 else None
    cost      = fuel_l * price_fuel if fuel_l and price_fuel else None

    return {
        "id":                f"myop-{trip_id}",
        "sources":           ["myopel"],
        "myopId":            trip_id,
        "vin":               vin,
        "start":             start_iso,
        "end":               end_iso,
        "durationMin":       round(travel_s / 60, 1),
        "distanceKm":        round(distance, 2),
        "avgSpeedKmh":       round(avg_speed, 1) if avg_speed else None,
        "fuelConsumedL":     round(fuel_l, 3),
        "consumptionL100km": round(l100km, 2) if l100km else None,
        "fuelLevel":         raw.get("fuelLevel"),
        "fuelAutonomy":      raw.get("fuelAutonomy"),
        "priceFuel":         price_fuel,
        "costEur":           round(cost, 2) if cost else None,
        "odometerKm":        end_odo or start_odo,
        "alerts":            [int(a) for a in alerts_raw if isinstance(a, (int, float))],
        "daysToService":     raw.get("daysUntilNextMaintenance"),
        "kmToService":       raw.get("distanceToNextMaintenance"),
        "maintenancePassed": raw.get("maintenancePassed", False),
        "track":             None,   # MyOpel has no GPS waypoints
        "pidValues":         None,
        "pidSeriesFull":     None,
        "pidCatalog":        [],
        "dpfRegenState":     None,
        "insights":          [],
    }
