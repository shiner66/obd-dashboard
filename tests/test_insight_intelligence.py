"""v0.10 temporal, cohort, reliability and event-boundary regressions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app.services import insights
from app.services import insight_analysis as analysis


def observation(identifier, day, kml=20, **extra):
    """Build a complete comparable local observation with measured operating conditions."""
    value = {"id": identifier, "start": f"2026-09-{day:02d}T10:00:00", "sources": ["obd"],
             "parserVersion": 2, "distanceKm": 10, "avgSpeedKmh": 40, "durationMin": 15,
             "airTempC": 20, "dpfRegenState": "idle", "fuelSource": "obd_rate",
             "fuelCoveragePct": 100, "fuelRateCoveragePct": 100, "fuelConsumedL": 10 / kml,
             "consumptionKmL": kml,
             "pidValues": {"coolant": {"first": 20, "first_seen_s": 0, "samples": 10},
                           "fuel_rate": {"samples": 100}}}
    value.update(extra)
    return value


def fuel_card(trip, history):
    """Find the actual per-trip cohort card regardless of presentation changes."""
    return next(c for c in insights.per_trip(trip, insights.build_context(history))
                if c["ruleId"] == "trip.fuel.peer_comparison")


def by_rule(cards, rule):
    """Select one stable diagnostic identity, independent of title or ordering."""
    return next(c for c in cards if c["ruleId"] == rule)


def test_personal_baseline_is_strictly_preceding_and_never_includes_self_or_future():
    peers = [observation(f"peer-{i}", i + 1) for i in range(6)]
    target = observation("target", 12, 12)
    rejected = [observation("future", 13, 50), {**target, "consumptionKmL": 55},
                observation("same-time", 12, 45),
                observation("too-old", 1, 55, start="2026-01-01T10:00:00"),
                observation("legacy", 7, 50, legacyIncomplete=True)]
    result = fuel_card(target, [*rejected, *reversed(peers), target])
    assert result["baseline"]["tripIds"] == [t["id"] for t in peers]
    assert result["baseline"]["median"] == 20
    assert result["baseline"]["mad"] == 0
    assert result["finding"] == "anomaly"
    assert result["confidence"]["grade"] == "moderate"
    assert result["observedAt"] == target["start"]
    assert set(result["evidence"]["tripIds"]) == {target["id"], *(p["id"] for p in peers)}
    assert result["deviation"] == {"metric": "fuel_peer_deficit_pct", "value": 40, "unit": "%"}


@pytest.mark.parametrize("changes", [
    {"fuelSource": "myopel"}, {"fuelCoveragePct": 89}, {"fuelCoveragePct": 94},
    {"distanceKm": 13}, {"avgSpeedKmh": 60}, {"airTempC": 26},
    {"airTempC": None}, {"dpfRegenState": "active"}, {"dpfRegenState": "unknown"},
    {"pidValues": {"coolant": {"first": 90, "first_seen_s": 0}}},
    {"pidValues": {"coolant": {"first": 20, "first_seen_s": 300}}},
])
def test_noncomparable_observations_do_not_make_a_sparse_cohort_reliable(changes):
    target = observation("target", 12, 12)
    four = [observation(f"peer-{i}", i + 1) for i in range(4)]
    result = fuel_card(target, [*four, observation("not-comparable", 8, **changes)])
    assert result["finding"] == "insufficient"
    assert len(result["baseline"]["tripIds"]) == 4
    assert result["confidence"]["grade"] == "low"
    assert "almeno 5" in result["body"]


def test_missing_conditions_reduce_confidence_without_fabricating_a_cold_start():
    peers = [observation(f"peer-{i}", i + 1) for i in range(6)]
    target = observation("target", 12, airTempC=10, coolantMaxC=85, pidValues={}, dpfRegenState="unknown")
    peers = [{**p, "airTempC": 10} for p in peers]
    cards = insights.per_trip(target, insights.build_context(peers))
    result = by_rule(cards, "trip.fuel.peer_comparison")
    assert result["finding"] == "normal" and result["confidence"]["grade"] == "low"
    assert "Temperatura motore iniziale" in result["baseline"]["missingConditions"]
    assert "Stato DPF" in result["baseline"]["missingConditions"]
    assert not any(c["title"] == "Partenza a freddo" for c in cards)
    assert analysis.engine_initial(target) is None


def test_robust_dispersion_prevents_a_wide_cohort_from_becoming_a_false_anomaly():
    peers = [observation(f"p-{i}", i + 1, k) for i, k in enumerate([10, 12, 18, 22, 28, 30])]
    result = fuel_card(observation("target", 12, 16), peers)
    assert result["baseline"]["median"] == 20
    assert result["baseline"]["mad"] == 8
    assert result["finding"] == "normal"
    assert any("dispersione" in text for text in result["counterEvidence"])


def test_causal_cohort_exact_180_day_boundary_and_different_density():
    target = observation("target", 12, fuelSource="obd_mass", fuelDensityGL=835)
    when = datetime.fromisoformat(target["start"])
    edge = {**observation("edge", 1, fuelSource="obd_mass", fuelDensityGL=835),
            "start": (when - timedelta(days=180)).isoformat()}
    outside = {**edge, "id": "outside", "start": (when - timedelta(days=180, seconds=1)).isoformat()}
    density = observation("density", 2, fuelSource="obd_mass", fuelDensityGL=780)
    result = analysis.peer_baseline(target, [edge, outside, density])
    assert result["tripIds"] == ["edge"]


def test_diagnostic_reference_is_identical_for_equal_selected_end_and_has_no_future():
    history = [observation(f"battery-{i}", i + 1, batteryStartupV=10,
                           pidValues={"bat_v": {"samples": 100}}) for i in range(8)]
    history += [observation("future", 20, batteryStartupV=6),
                observation("ancient", 1, batteryStartupV=6, start="2026-01-01T10:00:00")]
    narrow = insights.cross_trip([history[7]], history=history)
    broad = insights.cross_trip(history[:8], history=history)
    diagnostic_narrow = [c for c in narrow if c["ruleId"].startswith("diagnostic.")]
    assert diagnostic_narrow == [c for c in broad if c["ruleId"].startswith("diagnostic.")]
    battery = by_rule(narrow, "diagnostic.battery.cranking")
    assert battery["finding"] == "normal"
    assert battery["observedAt"] == "2026-09-08T10:00:00"
    assert battery["confidence"]["tripCount"] == 8
    assert "future" not in battery["evidence"]["tripIds"]
    assert "ancient" not in battery["evidence"]["tripIds"]
    assert by_rule(narrow, "period.fuel.economy")["evidence"]["tripIds"] == ["battery-7"]
    assert len(by_rule(broad, "period.fuel.economy")["evidence"]["tripIds"]) == 8
    assert insights.cross_trip([], history=history) == []


def test_observed_at_excludes_later_daily_activity_used_only_for_projection_context():
    oil = [observation(f"oil-{i}", i + 1, oilDilutionPct=3.1 + i * .5 / 7,
                       odometerKm=1000 + i * (1000 / .37) * .5 / 7,
                       pidValues={"oil_dil": {"samples": 10}}) for i in range(8)]
    later = observation("later-activity", 12)
    card = by_rule(insights.cross_trip([later], history=[*oil, later]), "diagnostic.oil.dilution")
    assert card["observedAt"] == "2026-09-08T10:00:00"
    assert card["confidence"]["tripCount"] == 8
    assert "euristica" in card["body"]
    assert "()" not in card["body"]


def test_maintenance_events_segment_only_relevant_series_without_claiming_recovery():
    history = [observation(f"p-{i}", i + 1, batteryStartupV=10, oilDilutionPct=3,
                           odometerKm=1000 + i * 100) for i in range(8)]
    original = deepcopy(history)
    event = {"id": 1, "type": "battery", "ts": "2026-09-06T00:00:00", "archived": False}
    cards = insights.cross_trip(history, events=[event])
    battery = by_rule(cards, "diagnostic.battery.cranking")
    oil = by_rule(cards, "diagnostic.oil.dilution")
    assert battery["finding"] == "insufficient"
    assert battery["evidence"]["tripIds"] == ["p-5", "p-6", "p-7"]
    assert battery["evidence"]["eventIds"] == [1]
    assert oil["confidence"]["tripCount"] == 8
    assert history == original
    for ignored in ({**event, "archived": True}, {**event, "ts": "2026-09-20T00:00:00"}):
        normal = by_rule(insights.cross_trip(history, events=[ignored]), "diagnostic.battery.cranking")
        assert normal["finding"] == "normal"
        assert normal["confidence"]["tripCount"] == 8


def test_stable_rule_identity_survives_changed_outcome_and_has_complete_structure():
    normal = [observation(f"p-{i}", i + 1, batteryStartupV=10) for i in range(6)]
    abnormal = [{**t, "batteryStartupV": 8} for t in normal]
    good = by_rule(insights.cross_trip(normal), "diagnostic.battery.cranking")
    bad = by_rule(insights.cross_trip(abnormal), "diagnostic.battery.cranking")
    assert good["finding"] == "normal" and bad["finding"] == "anomaly"
    assert good["ruleId"] == bad["ruleId"]
    required = {"ruleId", "finding", "observedAt", "confidence", "observation", "hypotheses",
                "limitations", "counterEvidence", "action", "evidence"}
    for card in insights.cross_trip(normal) + insights.per_trip(normal[-1], insights.build_context(normal)):
        assert required <= card.keys()
        assert card["finding"] in ("anomaly", "normal", "insufficient", "information")
        assert all(isinstance(card[key], list) for key in ("hypotheses", "limitations", "counterEvidence"))
        assert len({c["ruleId"] for c in insights.cross_trip(normal)}) == len(insights.cross_trip(normal))


def test_mechanical_claims_and_historical_forecast_dates_are_not_fabricated():
    history = [observation(f"p-{i}", i + 1, start=f"2020-01-{i*3+1:02d}T10:00:00",
                           ratioWg=1 + i * .0005, batteryStartupV=10, maxRpm=3000,
                           oilDilutionPct=3.1 + i * .05, odometerKm=1000 + i * 100,
                           pidValues={"boost": {"max": 1800, "samples": 10}}) for i in range(10)]
    cards = insights.cross_trip(history)
    text = " ".join(c["title"] + " " + c["body"] for c in cards)
    assert "Turbo in forma" not in text and "pressione stabile" not in text
    assert "−0,4/0,5 bar" not in text and "Causa tipica: rigenerazioni DPF interrotte" not in text
    assert "nessuna conversione in bar" in text
    assert insights._fmt_future(10, "2020-01-01T10:00:00") == ""
    assert insights._fmt_future(10, "9999-12-31T10:00:00") == ""
    assert "()" not in text
    assert all(c["finding"] == "information" for c in cards if c["ruleId"].startswith("period."))


def test_future_subsecond_observation_is_excluded_and_proof_time_keeps_precision():
    """Fractional local timestamps cannot collapse a future record into the cutoff."""
    target = observation("target", 12, start="2026-09-12T10:00:00.100000")
    future = observation("future", 12, start="2026-09-12T10:00:00.200000")
    assert analysis.bounded_history([target], [target, future]) == [target]
    card = fuel_card(target, [future])
    assert card["observedAt"] == target["start"]
    assert card["baseline"]["unit"] == "km/L"


def test_idle_variation_is_measured_even_with_eight_observations():
    """A sufficient eight-trip series must not have its spread silently forced to zero."""
    history = [observation(f"p-{i}", i + 1, pidValues={"rpm": {"mode": 650 if i % 2 else 950}})
               for i in range(8)]
    card = by_rule(insights.cross_trip(history), "diagnostic.engine.idle")
    assert card["finding"] == "anomaly"
    assert card["confidence"]["grade"] == "moderate"


def test_lifecycle_deviations_keep_stable_units_and_allow_measured_improvement():
    """A smaller battery voltage deficit is comparable without inventing a failure score."""
    first = [observation(f"p-{i}", i + 1, batteryStartupV=8) for i in range(6)]
    improved = [{**t, "batteryStartupV": 8.4} for t in first]
    before = by_rule(insights.cross_trip(first), "diagnostic.battery.cranking")
    after = by_rule(insights.cross_trip(improved), "diagnostic.battery.cranking")
    assert before["deviation"]["metric"] == after["deviation"]["metric"] == "battery_median_deficit_8_8v"
    assert before["deviation"]["unit"] == "V"
    assert after["deviation"]["value"] < before["deviation"]["value"] * .8
    assert before["finding"] == after["finding"] == "anomaly"
    assert before["confidence"]["grade"] == after["confidence"]["grade"] == "moderate"


def test_oil_rail_and_dpf_deviations_describe_measured_signals_only():
    """Names and units distinguish incomplete DPF observation from a mechanical diagnosis."""
    history = [observation(f"p-{i}", i + 1, oilDilutionPct=3.2, odometerKm=1000 + i * 100,
                           dpfRegenState="active" if i < 6 else "completed",
                           pidValues={"fuel_p_d": {"mean": 100},
                                      "ecm_measured_high_pressure_common_rail_fuel_pressure": {"mean": 100 if i < 5 else 120}})
               for i in range(10)]
    cards = insights.cross_trip(history)
    assert by_rule(cards, "diagnostic.oil.dilution")["deviation"] == {"metric": "oil_dilution_pct", "value": 3.2, "unit": "%"}
    assert by_rule(cards, "diagnostic.dpf.observed_outcomes")["deviation"] == {
        "metric": "dpf_unobserved_outcome_share_pct", "value": 60, "unit": "%"}
    rail = by_rule(cards, "diagnostic.engine.rail")["deviation"]
    assert rail["metric"] == "rail_recent_deviation_from_median_pct"
    assert rail["value"] > 0 and rail["unit"] == "%"
    completed = [{**t, "dpfRegenState": "completed"} for t in history]
    dpf = by_rule(insights.cross_trip(completed), "diagnostic.dpf.observed_outcomes")
    assert dpf["finding"] == "normal" and dpf["deviation"]["value"] == 0
