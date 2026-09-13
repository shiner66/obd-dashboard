"""Deterministic temporal cohorts and explicit, qualitative insight reliability.

Matching tolerances describe comparable observations, not mechanical limits.
No cohort, event annotation or confidence grade establishes a causal diagnosis.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta

from .fuel import select_consumption


def local_dt(value):
    """Parse recorded local timestamps without converting the historical fake UTC suffix."""
    try:
        return datetime.fromisoformat(value.removesuffix("Z")).replace(tzinfo=None) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def number(value):
    """Keep finite measured numbers and reject booleans and nonnumeric placeholders."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid(trip):
    """Exclude legacy observations whose measurement coverage cannot be reconstructed."""
    return not trip.get("legacyIncomplete") and not (
        "obd" in trip.get("sources", []) and trip.get("parserVersion", 0) < 2)


def unique(trips):
    """Return distinct identified observations in chronological order."""
    return sorted({t["id"]: t for t in trips if t.get("id")}.values(),
                  key=lambda t: t.get("start") or "")


def fuel_metrics(trip):
    """Honor already-selected density while requiring explicit measured coverage."""
    if trip.get("fuelSource") and number(trip.get("consumptionKmL")):
        return trip
    return {**trip, **select_consumption(trip)}


def engine_initial(trip):
    """Use only a coolant observation near recording start; ambient air is not engine state."""
    if number(trip.get("coolantStartC")):
        return trip["coolantStartC"]
    for slug in ("coolant", "coolant_c"):
        stats = (trip.get("pidValues") or {}).get(slug) or {}
        if number(stats.get("first")) and number(stats.get("first_seen_s")) and 0 <= stats["first_seen_s"] <= 60:
            return stats["first"]
    return None


def _distance_bucket(km):
    """Reuse the route-distance groups from the prior comparison implementation."""
    for lo, hi in ((0, 4), (4, 8), (8, 15), (15, 40), (40, float("inf"))):
        if lo <= km < hi:
            return lo, hi
    return None


def peer_baseline(trip, history):
    """Select only comparable predecessors from 180 days; report every absent condition.

    Fuel sources are identical, coverage is >=90% and differs by <=5 points.
    Distance uses the existing bands plus +/-25%; speed +/-25% (minimum 6 km/h).
    When measured on the target, air/coolant temperatures and DPF state must be
    available and comparable on peers too. Unknown target conditions are disclosed.
    """
    target = fuel_metrics(trip)
    when = local_dt(trip.get("start"))
    missing, conditions, peers = [], ["Solo predecessori entro 180 giorni", "Stessa fonte e copertura consumi (±5 punti)",
                                     "Distanza nella stessa fascia e ±25%", "Velocità media ±25% (almeno ±6 km/h)"], []
    air = trip.get("airTempC") if number(trip.get("airTempC")) else None
    engine = engine_initial(trip)
    state = trip.get("dpfRegenState")
    state = state if state and state != "unknown" else None
    for label, value in (("Temperatura ambiente", air), ("Temperatura motore iniziale", engine), ("Stato DPF", state)):
        if value is None:
            missing.append(label)
    if air is not None:
        conditions.append("Temperatura ambiente ±5 °C")
    if engine is not None:
        conditions.append("Temperatura motore iniziale ±10 °C")
    if state:
        conditions.append("Stesso stato DPF osservato")
    essential = bool(when and valid(trip) and target.get("fuelSource") in ("myopel", "obd_rate", "obd_mass") and
                     number(target.get("fuelCoveragePct")) and target["fuelCoveragePct"] >= 90 and
                     number(trip.get("distanceKm")) and trip["distanceKm"] > 0 and
                     number(trip.get("avgSpeedKmh")) and trip["avgSpeedKmh"] > 0)
    if not essential:
        missing.append("Data, distanza, velocità o fonte/copertura consumi affidabili")
    if essential:
        speed, distance = trip["avgSpeedKmh"], trip["distanceKm"]
        for candidate in unique(history):
            dt = local_dt(candidate.get("start"))
            if (candidate.get("id") == trip.get("id") or not valid(candidate) or not dt or
                    not dt < when or when - dt > timedelta(days=180)):
                continue
            measured = fuel_metrics(candidate)
            km, spd, cov = candidate.get("distanceKm"), candidate.get("avgSpeedKmh"), measured.get("fuelCoveragePct")
            if (measured.get("fuelSource") != target["fuelSource"] or not number(cov) or cov < 90 or
                    abs(cov - target["fuelCoveragePct"]) > 5 or not number(km) or km <= 0 or
                    _distance_bucket(km) != _distance_bucket(distance) or abs(km - distance) > distance * .25 or
                    not number(spd) or spd <= 0 or abs(spd - speed) > max(6, speed * .25)):
                continue
            if target["fuelSource"] == "obd_mass" and measured.get("fuelDensityGL") != target.get("fuelDensityGL"):
                continue
            if air is not None and (not number(candidate.get("airTempC")) or abs(candidate["airTempC"] - air) > 5):
                continue
            candidate_engine = engine_initial(candidate)
            if engine is not None and (candidate_engine is None or abs(candidate_engine - engine) > 10):
                continue
            if state and candidate.get("dpfRegenState") != state:
                continue
            kml = measured.get("consumptionKmL")
            if number(kml) and 2 < kml < 60:
                peers.append(measured)
    values = [p["consumptionKmL"] for p in peers]
    median = statistics.median(values) if values else None
    mad = statistics.median(abs(v - median) for v in values) if values else None
    kml = target.get("consumptionKmL")
    return {"tripIds": [p["id"] for p in peers], "median": median, "mad": mad, "unit": "km/L",
            "deltaPct": (kml / median - 1) * 100 if median and number(kml) else None,
            "conditions": conditions, "missingConditions": missing,
            "sufficient": len(peers) >= 5 and essential, "trips": peers}


def bounded_history(selected, history=None, days=90):
    """Keep the diagnostic horizon tied to selected end, never to today or filter start."""
    dates = [d for t in selected if (d := local_dt(t.get("start")))]
    if not dates:
        return []
    end = max(dates)
    return [t for t in unique([*(history if history is not None else selected), *selected])
            if valid(t) and (d := local_dt(t.get("start"))) and timedelta(0) <= end - d <= timedelta(days=days)]


def segment_history(trips, events=None):
    """Mask only measurements preceding the latest relevant recorded maintenance event.

    Sources stay immutable. Event timing defines a new series, never a claim that
    the intervention caused a change or that a malfunction has been resolved.
    """
    dates = [d for t in trips if (d := local_dt(t.get("start")))]
    if not dates:
        return trips, {}
    end = max(dates)
    boundaries = {}
    for event in events or []:
        dt = local_dt(event.get("ts"))
        kind = event.get("type")
        if event.get("archived") or not dt or dt > end or kind not in ("oil_change", "battery", "tyres", "service"):
            continue
        if kind not in boundaries or dt > local_dt(boundaries[kind]["ts"]):
            boundaries[kind] = event
    fields = {"oil_change": ("oilDilutionPct",), "battery": ("batteryStartupV",),
              "tyres": ("ratioWg",), "service": ("kmToService", "daysToService")}
    result = []
    for trip in trips:
        value = dict(trip)
        dt = local_dt(trip.get("start"))
        for kind, event in boundaries.items():
            if dt and dt < local_dt(event["ts"]):
                for key in fields[kind]:
                    value.pop(key, None)
        result.append(value)
    return result, boundaries


def structure(card, rule_id, inputs, *, finding=None, baseline=None, hypotheses=None,
              limitations=None, counter_evidence=None, action=None, deviation=None, min_count=5):
    """Attach stable semantics and qualitative reliability to actual diagnostic inputs."""
    inputs = unique(inputs)
    dates = [d for t in inputs if (d := local_dt(t.get("start")))]
    count, day_count = len(inputs), len({d.date() for d in dates})
    missing = (baseline or {}).get("missingConditions", [])
    grade = "moderate" if count >= min_count and day_count >= 2 else "low"
    if count >= 20 and day_count >= 10 and not missing and not hypotheses:
        grade = "high"
    if missing:
        grade = "low"  # observed peers cannot control the missing operating conditions
    inferred = finding or ("anomaly" if card["level"] in ("warning", "critical") else "normal")
    if inferred == "normal" and count < min_count:
        inferred = "insufficient"
    if inferred == "insufficient":
        grade = "low"
    reasons = [f"{count} viaggi su {day_count} giorni con osservazioni pertinenti"]
    if count < min_count:
        reasons.append(f"Meno di {min_count} osservazioni: confronto diagnostico insufficiente")
    if missing:
        reasons.append("Condizioni non controllate: " + ", ".join(missing))
    reasons.append("Valutazione qualitativa delle prove, non probabilità di guasto")
    limits = list(limitations or [])
    if missing:
        limits.append("Non misurate: " + ", ".join(missing))
    value = {**card, "ruleId": rule_id, "finding": inferred,
             "observedAt": max(dates).isoformat() if dates else None,
             "confidence": {"grade": grade, "reasons": reasons, "tripCount": count, "dayCount": day_count},
             "observation": card["body"], "hypotheses": list(hypotheses or []),
             "limitations": limits, "counterEvidence": list(counter_evidence or []),
             "action": action or "Confronta le osservazioni disponibili e verifica eventuali sintomi prima di intervenire."}
    if inferred == "insufficient":
        value["level"] = "info"
    if baseline is not None:
        value["baseline"] = {k: v for k, v in baseline.items() if k not in ("trips", "sufficient")}
    if deviation is not None:
        value["deviation"] = deviation
    return value
