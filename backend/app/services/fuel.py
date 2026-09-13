"""
Fuel modelling without MyOpel — briefing §1 & §5.

Once the .myop feed is switched off the tank level can still be known two ways:

  1. **OBD sender** — the ``[ECM] Fuel tank level`` PID (``fuelLevelObd``), the
     same linear probe MyOpel reads.
  2. **Ledger − consumption** (the "soluzione estrema"): walk the manual refuels
     and the trips in odometer order, add litres at each fill and subtract every
     trip's OBD-measured burn, then read off the running tank level.

Also derives tank-to-tank economy (pump litres ÷ odometer km, refuel-to-refuel
only — never per-trip level deltas, briefing §1) and flags stale MyOpel
``fuelConsumption`` values (the §1 API cache bug).
"""
from __future__ import annotations

import statistics
import math

# Briefing §5 constants (overridable via settings).
DEFAULT_TANK_L   = 43.5
DENSITY_GL       = {"B7": 835.0, "DIESEL": 835.0, "HVO": 780.0}
DEFAULT_DENSITY  = 835.0

# §1 stale-fuelConsumption detector.
FC_SUSPECT_DELTA_UL = 500      # |fc − fc_prev| below this …
FC_SUSPECT_MIN_KM   = 5.0      # … on a trip longer than this is statistically impossible


def density_for(fuel_type: str | None) -> float:
    """Fuel density in g/L for a fuel-type label (B7/HVO/…), defaulting to diesel."""
    if not fuel_type:
        return DEFAULT_DENSITY
    return DENSITY_GL.get(fuel_type.strip().upper(), DEFAULT_DENSITY)


def _positive(value) -> bool:
    """Accept finite positive measured quantities only."""
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def select_consumption(trip: dict, density: float = DEFAULT_DENSITY) -> dict:
    """Select one comparable measurement with explicit source and coverage.

    Unknown legacy OBD coverage is not upgraded to a reliable total. MyOpel
    consumption stays separate unless its recorded km cover 90–110% of a session.
    """
    km = trip.get("distanceKm")
    liters = None
    source = None
    coverage = None
    denominator = km
    rate = trip.get("obdFuelConsumedL")
    if rate is None and trip.get("fuelSource") == "obd_rate":
        rate = trip.get("fuelConsumedL")
    rate_cov = trip.get("fuelRateCoveragePct")
    mass_cov = trip.get("fuelMassCoveragePct")
    mass = trip.get("fuelMassG")
    if _positive(rate) and rate_cov is not None and rate_cov >= 90:
        liters, source, coverage = rate, "obd_rate", rate_cov
    elif _positive(mass) and mass_cov is not None and mass_cov >= 90 and _positive(density):
        liters, source, coverage = mass / density, "obd_mass", mass_cov
    else:
        myop_l = trip.get("myopFuelConsumedL")
        myop_km = trip.get("myopDistanceKm")
        if "obd" not in trip.get("sources", []) and "myopel" in trip.get("sources", []):
            myop_km = km
            if myop_l is None:
                myop_l = trip.get("fuelConsumedL")
        ratio = myop_km / km if _positive(km) and _positive(myop_km) else None
        if _positive(myop_l) and ratio is not None and 0.9 <= ratio <= 1.1:
            liters, source, coverage = myop_l, "myopel", min(100.0, ratio * 100)
            denominator = myop_km
    l100 = liters / denominator * 100 if _positive(liters) and _positive(denominator) else None
    return {
        "fuelConsumedL": round(liters, 4) if liters is not None else None,
        "fuelSource": source,
        "fuelDistanceKm": denominator if source else None,
        "fuelCoveragePct": round(coverage, 1) if coverage is not None else None,
        "fuelDensityGL": density if source == "obd_mass" else None,
        "consumptionL100km": round(l100, 2) if l100 else None,
        "consumptionKmL": round(100 / l100, 2) if l100 else None,
    }


def trip_burn_l(trip: dict, density: float = DEFAULT_DENSITY) -> float:
    """Litres from a validated source; never use a partial MyOpel leg as a total."""
    # API trips expose the selected source; raw observations are selected here.
    if trip.get("fuelSource") in ("obd_rate", "myopel") and _positive(trip.get("fuelConsumedL")):
        return trip["fuelConsumedL"]
    return select_consumption(trip, density)["fuelConsumedL"] or 0.0


def estimate_level(refuels: list[dict], trips: list[dict],
                   capacity_l: float = DEFAULT_TANK_L,
                   density: float = DEFAULT_DENSITY) -> dict:
    """Reconstruct the current tank level from the ledger and OBD consumption.

    Returns litres/percent plus the source used and the since-last-refuel roll-up.
    Falls back to the OBD fuel-level sender when the ledger has no full-tank anchor.
    """
    cap = capacity_l or DEFAULT_TANK_L

    # ── Ledger − consumption model ───────────────────────────────────────────
    events: list[tuple[float, int, str, object]] = []
    for r in refuels:
        odo = r.get("odometerKm")
        if odo is None:
            continue
        events.append((odo, 1, "refuel", r))       # 1 → after a trip at same odo
    for t in trips:
        odo = t.get("odometerKm")
        burn = trip_burn_l(t, density)
        if odo is None or burn <= 0:
            continue
        events.append((odo, 0, "trip", burn))
    events.sort(key=lambda e: (e[0], e[1]))

    level: float | None = None
    incomplete = False
    for _odo, _pri, kind, payload in events:
        if kind == "refuel":
            r = payload
            if r.get("fullTank", True):
                level = cap                          # a full fill tops off, anchors the model
                incomplete = False
            elif level is not None:
                level = min(cap, level + (r.get("liters") or 0.0))
        elif level is not None:                      # trip
            level = max(0.0, level - payload)

    # ── Since last refuel roll-up ────────────────────────────────────────────
    since = None
    last_refuel = max(refuels, key=lambda r: (r.get("odometerKm") or 0), default=None) if refuels else None
    odo_now = max((t.get("odometerKm") or 0) for t in trips) if trips else None
    if last_refuel and last_refuel.get("odometerKm") is not None and odo_now:
        r_odo = last_refuel["odometerKm"]
        burned = sum(trip_burn_l(t, density) for t in trips
                     if (t.get("odometerKm") or 0) > r_odo)
        km = odo_now - r_odo
        since = {
            "km":           round(km, 1),
            "litersBurned": round(burned, 2) if burned else None,
            "kmL":          round(km / burned, 2) if burned > 0 else None,
            "l100":         round(burned / km * 100, 2) if km > 0 and burned else None,
        }

    # ── OBD sender fallback ──────────────────────────────────────────────────
    obd_pct = next((t.get("fuelLevelObd") for t in
                    sorted(trips, key=lambda t: t.get("start") or "", reverse=True)
                    if t.get("fuelLevelObd") is not None), None)

    anchor = max((r.get("odometerKm") for r in refuels
                  if r.get("fullTank") and r.get("odometerKm") is not None), default=None)
    anchor_fill = next((r for r in refuels if r.get("fullTank") and r.get("odometerKm") == anchor), {})
    anchor_date = anchor_fill.get("ts")
    missing = [t for t in trips if anchor is not None and
               ((t.get("odometerKm") is not None and t["odometerKm"] > anchor) or
                (t.get("odometerKm") is None and (not anchor_date or not t.get("start") or t["start"] >= anchor_date)))
               and (t.get("odometerKm") is None or trip_burn_l(t, density) <= 0)]
    incomplete = bool(missing)
    if level is not None and not incomplete:
        return {
            "capacityL":  cap,
            "liters":     round(level, 1),
            "pct":        round(level / cap * 100) if cap else None,
            "source":     "ledger",
            "obdPct":     obd_pct,
            "sinceRefuel": since,
            "lastRefuel":  last_refuel,
        }
    return {
        "capacityL":  cap,
        "liters":     round(obd_pct / 100 * cap, 1) if obd_pct is not None else None,
        "pct":        obd_pct,
        "source":     "obd" if obd_pct is not None else None,
        "qualityWarnings": (["Stima da rifornimenti incompleta: mancano consumi o odometri."] if incomplete else []),
        "missingConsumptionTrips": len(missing),
        "obdPct":     obd_pct,
        "sinceRefuel": since,
        "lastRefuel":  last_refuel,
    }


def tank_to_tank(refuels: list[dict]) -> list[dict]:
    """Refuel-to-refuel economy between consecutive full tanks (briefing §1).

    Fuel burned from full tank A to full tank B = litres added between them; the
    economy is attributed to the fuel that was *in* the tank (added at A), which
    is the off-by-one every refuel app gets wrong.
    """
    fulls = sorted([r for r in refuels if r.get("fullTank") and r.get("odometerKm") is not None],
                   key=lambda r: r["odometerKm"])
    fills = sorted([r for r in refuels if r.get("odometerKm") is not None],
                   key=lambda r: r["odometerKm"])
    out: list[dict] = []
    for a, b in zip(fulls, fulls[1:]):
        dist = b["odometerKm"] - a["odometerKm"]
        if dist <= 0:
            continue
        liters = sum(r.get("liters") or 0.0 for r in fills
                     if a["odometerKm"] < r["odometerKm"] <= b["odometerKm"])
        if liters <= 0:
            continue
        cost = sum((r.get("liters") or 0.0) * (r.get("pricePerL") or 0.0) for r in fills
                   if a["odometerKm"] < r["odometerKm"] <= b["odometerKm"]
                   and r.get("pricePerL"))
        out.append({
            "fromOdo":  a["odometerKm"],
            "toOdo":    b["odometerKm"],
            "km":       round(dist, 1),
            "liters":   round(liters, 2),
            "kmL":      round(dist / liters, 2),
            "l100":     round(liters / dist * 100, 2),
            "fuelType": a.get("fuelType"),          # §1: the tank that was burned
            "eurKm":    round(cost / dist, 4) if cost else None,
            "date":     b.get("ts"),
        })
    return out


def detect_fc_suspects(trips: list[dict]) -> list[dict]:
    """MyOpel trips whose fuelConsumption looks stale (§1 API cache bug).

    Flags a trip when |fc − fc_prev| < 500 µL on a > 5 km drive — statistically
    impossible, so the API almost certainly echoed the previous trip's value.
    """
    myop = sorted([t for t in trips if t.get("myopFuelUl")],
                  key=lambda t: t.get("start") or "")
    suspects: list[dict] = []
    prev = None
    for t in myop:
        fc = t.get("myopFuelUl")
        km = t.get("distanceKm") or 0
        if prev is not None and km > FC_SUSPECT_MIN_KM and abs(fc - prev) < FC_SUSPECT_DELTA_UL:
            suspects.append({
                "id":     t["id"],
                "start":  t.get("start"),
                "km":     round(km, 1),
                "fcUl":   fc,
                "prevUl": prev,
                "deltaUl": abs(fc - prev),
            })
        prev = fc
    return suspects


def fuel_summary(refuels: list[dict], trips: list[dict], settings: dict,
                 myop_legs: list[dict] | None = None) -> dict:
    """One-call payload for the frontend Fuel view / dashboard gauge."""
    cap = settings.get("tank_capacity_l") or DEFAULT_TANK_L
    density = settings.get("fuel_density_gl") or DEFAULT_DENSITY
    level = estimate_level(refuels, trips, cap, density)
    intervals = tank_to_tank(refuels)
    return {
        "level":       level,
        "tankToTank":  intervals,
        "fcSuspects":  detect_fc_suspects(myop_legs if myop_legs is not None else [t for t in trips if "obd" not in t.get("sources", [])]),
        "fcCheckedLegs": len(myop_legs) if myop_legs is not None else sum(1 for t in trips if "obd" not in t.get("sources", []) and t.get("myopFuelUl")),
        "capacityL":   cap,
        "densityGL":   density,
    }
