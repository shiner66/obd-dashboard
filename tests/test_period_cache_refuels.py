"""v0.9 regressions for date scoping, mutable ledger entries, caches and evidence."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from test_operations import client, csv_payload
from app import main
from app.services import insights


def trip(identifier, start, *, source="myopel", **fields):
    """Create one complete observation with known consumption and optional PID data."""
    value = {"id": identifier, "sources": [source], "start": start,
             "end": start[:10] + "T23:59:59", "distanceKm": 10, "durationMin": 20,
             "fuelConsumedL": .5, "odometerKm": 2000, "fuelLevel": 75,
             "track": [[40.0, 14.0], [40.1, 14.1]], **fields}
    if source == "obd":
        value.update(parserVersion=2, fuelRateCoveragePct=100)
    else:
        value["myopId"] = identifier
    return value


def seed(*trips):
    """Persist isolated test observations using the production database helper."""
    for value in trips:
        main.db.save_trip(value)


def test_ui_refuel_timestamp_create_edit_filter_and_delete(client):
    """The exact space-separated UI payload round-trips and invalidates period summaries."""
    payload = {"ts": "2026-09-02 10:30", "liters": 20, "pricePerL": 1.7,
               "odometerKm": 1500, "fullTank": True, "fuelType": "B7", "note": "UI"}
    created = client.post("/api/v1/refuels", json=payload)
    assert created.status_code == 200, created.text
    stored = created.json()["refuel"]
    assert stored["ts"] == "2026-09-02T10:30:00"
    filtered = "/api/v1/dashboard?from_date=2026-09-02&to_date=2026-09-02"
    assert client.get(filtered).json()["fuel"]["period"]["liters"] == 20
    updated = {**payload, "liters": 25, "pricePerL": 1.8, "note": "corretto"}
    edited = client.put(f"/api/v1/refuels/{stored['id']}", json=updated)
    assert edited.status_code == 200
    assert edited.json()["refuel"]["id"] == stored["id"]
    result = client.get(filtered).json()["fuel"]
    assert len(result["refuels"]) == 1
    assert result["period"]["liters"] == 25
    assert result["period"]["costEur"] == 45
    invalid = client.put(f"/api/v1/refuels/{stored['id']}", json={**updated, "liters": -1})
    assert invalid.status_code == 422
    assert client.get(filtered).json()["fuel"]["refuels"][0]["liters"] == 25
    assert client.put("/api/v1/refuels/999999", json=updated).status_code == 404
    assert client.delete(f"/api/v1/refuels/{stored['id']}").status_code == 200
    assert client.get(filtered).json()["fuel"]["period"]["refuelCount"] == 0


@pytest.mark.parametrize("timestamp", ["2026-02-30 10:30", "2026-09-02", "2026-09-02T10:30Z", "2026-09-02T10:30+02:00"])
def test_refuel_rejects_invalid_or_ambiguous_nonlocal_timestamps(client, timestamp):
    """Local refuel dates cannot silently be shifted or relabelled as UTC."""
    assert client.post("/api/v1/refuels", json={"liters": 20, "ts": timestamp}).status_code == 422
    assert main.db.get_refuels() == []


def test_period_is_inclusive_and_shared_by_dashboard_export_and_map(client):
    """Every data surface uses the trip's local start date, including day boundaries."""
    seed(trip("before", "2026-08-31T23:59:59"), trip("first", "2026-09-01T00:00:00"),
         trip("last", "2026-09-02T23:59:59"), trip("after", "2026-09-03T00:00:00", odometerKm=2010))
    query = "?from_date=2026-09-01&to_date=2026-09-02"
    selected = client.get("/api/v1/dashboard" + query).json()
    expected = {"first", "last"}
    assert {t["id"] for t in selected["trips"]} == expected
    assert {t["id"] for t in client.get("/api/v1/trips" + query).json()} == expected
    assert set(client.get("/api/v1/tracks" + query).json()) == expected
    assert {t["id"] for t in client.get("/api/v1/trips" + query + "&summary=true").json()} == expected
    assert selected["aggregates"]["tripCount"] == 2
    assert selected["aggregates"]["totalKm"] == 20
    assert selected["aggregates"]["consumptionKmL"] == 20
    assert selected["vehicle"]["odometer"] == 2010  # latest state is global
    assert selected["meta"]["scope"]["vehicle"] == "latest_global"
    assert all(not set(t) & {"track", "pidValues", "pidSeriesTimes"} for t in selected["trips"])
    assert selected["trips"][0]["routeStart"] == [40, 14]
    assert selected["trips"][0]["routeEnd"] == [40.1, 14.1]
    empty = client.get("/api/v1/dashboard?from_date=2025-01-01&to_date=2025-01-01").json()
    assert empty["trips"] == [] and empty["trendInsights"] == []
    assert empty["aggregates"]["tripCount"] == 0
    assert empty["vehicle"] == selected["vehicle"]
    assert empty["fuel"]["level"] == selected["fuel"]["level"]


def test_full_tank_anchor_before_period_still_defines_consumption(client):
    """Filter completed intervals only after computing them against the whole ledger."""
    for ts, odo, liters in [("2026-08-31 12:00", 1000, 40), ("2026-09-02 12:00", 1500, 20)]:
        assert client.post("/api/v1/refuels", json={"ts": ts, "odometerKm": odo, "liters": liters,
                                                   "fullTank": True, "pricePerL": 1.5}).status_code == 200
    result = client.get("/api/v1/dashboard?from_date=2026-09-02&to_date=2026-09-02").json()
    assert len(result["fuel"]["refuels"]) == 1
    assert len(result["fuel"]["tankToTank"]) == 1
    interval = result["fuel"]["tankToTank"][0]
    assert (interval["fromOdo"], interval["toOdo"], interval["kmL"]) == (1000, 1500, 25)
    assert result["fuel"]["period"]["liters"] == 20


@pytest.mark.parametrize("query", ["?from_date=2026-09-03&to_date=2026-09-02", "?from_date=2026-02-30", "?from_date=20260901", "?to_date=2026-09-01T12:00"])
def test_bad_date_periods_fail_consistently(client, query):
    """An invalid range is a validation error rather than an empty result or a 503."""
    for endpoint in ("dashboard", "trips", "tracks", "fuel"):
        assert client.get("/api/v1/" + endpoint + query).status_code == 422


def test_warm_dashboard_and_health_do_not_decompress_heavy_blobs(client, monkeypatch):
    """Repeated reads use lightweight summaries and a revision-bound cache."""
    seed(trip("obd-one", "2026-09-01T10:00:00", source="obd", pidValues={"rpm": {"mean": 1000, "samples": 200}},
              pidSeriesFull={"rpm": list(range(60))}, pidSeriesTimes={"rpm": list(range(60))}))
    assert client.get("/api/v1/dashboard").status_code == 200
    def forbid_unpack(*args, **kwargs):
        """Any repeated raw/blob decoding would break this regression."""
        raise AssertionError("heavy blob decoded on a warm summary read")
    monkeypatch.setattr(main.db, "_unpack_json", forbid_unpack)
    for _ in range(3):
        assert client.get("/api/v1/dashboard").status_code == 200
        assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/dashboard?from_date=2026-09-01").status_code == 200
    assert client.post("/api/v1/refuels", json={"liters": 10, "ts": "2026-09-01 12:00"}).status_code == 200
    assert client.get("/api/v1/dashboard").json()["fuel"]["period"]["liters"] == 10


def test_trip_mutations_invalidate_only_the_changed_summary(client, monkeypatch):
    """A new reading rebuilds its cached summary, and deleted rows cannot leak back."""
    seed(trip("one", "2026-09-01T10:00:00"), trip("two", "2026-09-02T10:00:00"))
    assert client.get("/api/v1/dashboard").json()["aggregates"]["totalKm"] == 20
    original = main.db._row_to_trip
    decoded = []
    def observed(row):
        """Record which dirty materialized rows have to be decoded."""
        decoded.append(row["id"])
        return original(row)
    monkeypatch.setattr(main.db, "_row_to_trip", observed)
    seed(trip("two", "2026-09-02T10:00:00", distanceKm=12))
    assert client.get("/api/v1/dashboard").json()["aggregates"]["totalKm"] == 22
    assert decoded == ["two"]
    main.db.delete_trip("one")
    assert {t["id"] for t in client.get("/api/v1/dashboard").json()["trips"]} == {"two"}


def test_density_updates_stay_consistent_across_summary_detail_and_export(client):
    """A settings mutation invalidates selected mass-to-volume metrics everywhere."""
    seed(trip("mass", "2026-09-01T10:00:00", source="obd", fuelConsumedL=None,
              fuelMassG=835, fuelMassCoveragePct=100))
    assert client.get("/api/v1/dashboard").json()["trips"][0]["fuelConsumedL"] == 1
    assert client.put("/api/v1/settings", json={"fuel_density_gl": 780}).status_code == 200
    summary = client.get("/api/v1/dashboard").json()["trips"][0]
    detail = client.get("/api/v1/trips/mass").json()
    export = client.get("/api/v1/trips").json()[0]
    assert summary["fuelDensityGL"] == 780
    assert summary["fuelConsumedL"] == detail["fuelConsumedL"] == export["fuelConsumedL"]
    assert summary["fuelConsumedL"] > 1.07


def test_known_sources_skip_parse_but_same_size_mtime_changes_are_imported(client, tmp_path, monkeypatch):
    """File metadata is insufficient: content hashes detect equal-size producer rewrites."""
    source = main.OBD_FILES_DIR / "2026-09-01_10-00-00.csv"
    source.write_bytes(csv_payload(10))
    original_parse = main.csv_parser.parse_file
    assert main._process_obd_file(source)
    stat = source.stat()
    def forbidden(*args, **kwargs):
        """Identical content must not re-run the costly CSV parser."""
        raise AssertionError("known content parsed again")
    monkeypatch.setattr(main.csv_parser, "parse_file", forbidden)
    assert main._process_obd_file(source) == []
    monkeypatch.setattr(main.csv_parser, "parse_file", original_parse)
    source.write_bytes(csv_payload(12))
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    assert main._process_obd_file(source)
    assert main.db.get_trip("obd-2026-09-01_10-00-00")["distanceKm"] == 12


def test_legacy_warning_survives_without_sources_and_cannot_drive_comparisons(client):
    """A restart's empty in-memory legacy set cannot erase durable recovery warnings."""
    legacy = trip("legacy", "2026-09-01T10:00:00", source="obd", batteryStartupV=2)
    legacy.pop("parserVersion")
    main.db.save_trip(legacy, record_raw=False)
    main._legacy_rebuild_needed.clear()
    result = client.get("/api/v1/dashboard").json()
    assert result["meta"]["legacyRebuildIds"] == ["legacy"]
    assert result["trips"][0]["legacyIncomplete"] is True
    assert result["aggregates"]["excludedFromComparisons"] == 1
    assert result["trendInsights"] == []
    assert result["trips"][0]["insights"][0]["category"] == "data"


def test_insight_evidence_contains_only_actual_rule_inputs_and_available_pids(client):
    """Battery evidence excludes unrelated or rejected values and counts real PID records."""
    valid = [trip(f"battery-{i}", f"2026-09-{i+1:02d}T10:00:00", source="obd",
                  batteryStartupV=10, pidValues={"bat_v": {"samples": 100, "mean": 10},
                                                "unused": {"samples": 9999, "mean": 7}}) for i in range(6)]
    invalid = trip("out-of-range", "2026-09-07T10:00:00", source="obd", batteryStartupV=20)
    cards = insights.cross_trip([*valid, invalid])
    card = next(c for c in cards if c["category"] == "battery")
    assert set(card["evidence"]["tripIds"]) == {t["id"] for t in valid}
    assert card["evidence"]["pidSlugs"] == ["bat_v"]
    assert card["evidence"]["sampleCount"] == 600
    seed(*valid, invalid)
    filtered = client.get("/api/v1/dashboard?from_date=2026-09-01&to_date=2026-09-03").json()
    battery = next(c for c in filtered["trendInsights"] if c["category"] == "battery")
    assert battery["finding"] == "insufficient"
    assert set(battery["evidence"]["tripIds"]) == {t["id"] for t in valid[:3]}
    visible_ids = {t["id"] for t in filtered["trips"]}
    assert all(set(c["evidence"]["tripIds"]) <= visible_ids for c in filtered["trendInsights"])


def test_maximum_calendar_day_is_inclusive_without_datetime_overflow(client):
    """The last representable date works on every period-aware data surface."""
    seed(trip("previous", "9999-12-30T23:59:59"),
         trip("maximum", "9999-12-31T23:59:59.999999"))
    query = "?from_date=9999-12-31&to_date=9999-12-31"
    for endpoint in ("dashboard", "fuel", "trips", "tracks"):
        response = client.get("/api/v1/" + endpoint + query)
        assert response.status_code == 200, response.text
    assert {t["id"] for t in client.get("/api/v1/trips" + query).json()} == {"maximum"}
    assert {t["id"] for t in client.get("/api/v1/trips" + query + "&summary=true").json()} == {"maximum"}
    assert set(client.get("/api/v1/tracks" + query).json()) == {"maximum"}


def test_insight_numbers_are_italian_without_rewriting_metadata_or_thousands():
    """Localize numeric display operands; evidence, dates and numeric chart data stay intact."""
    observations = [trip(f"trip-1.5-{i}", f"2026-09-{i+1:02d}T10:00:00", source="obd",
                         oilDilutionPct=3.1 + i * .5 / 7, odometerKm=1000 + i * (1000 / .37) * .5 / 7,
                         pidValues={"oil_dil": {"samples": 10}}) for i in range(8)]
    card = next(c for c in insights.cross_trip(observations) if c["title"] == "Diluizione olio in aumento")
    assert "Da 3,1% a 3,6% (+0,37%/1000 km)" in card["body"]
    assert "~3.784 km" in card["body"]
    assert card["series"] == [round(t["oilDilutionPct"], 3) for t in observations]
    assert card["evidence"]["tripIds"] == [t["id"] for t in observations]
    assert insights._it_number(-1.25, "+.2f") == "-1,25"
    assert insights._it_number(1500.25, ",.2f") == "1.500,25"
    untouched = insights._ins("data", "info", "v0.9.0 · 2026-09-13", "ID 1.500.2")
    assert untouched["title"] == "v0.9.0 · 2026-09-13"
    assert untouched["body"] == "ID 1.500.2"


def test_observed_duration_and_cost_coverage_exclude_incomplete_legacy():
    """Comparative durations and costs use valid observations while historical km remain visible."""
    legacy = trip("legacy", "2026-09-01T10:00:00", source="obd", legacyIncomplete=True,
                  durationMin=100, costEur=20, costDistanceKm=10)
    valid = trip("valid", "2026-09-02T10:00:00", durationMin=20, costEur=2, costDistanceKm=10)
    unknown = trip("unknown", "2026-09-03T10:00:00", durationMin=None)
    result = main._period_aggregates([legacy, valid, unknown])
    assert result["tripCount"] == 3 and result["totalKm"] == 30
    assert result["observedMinutes"] == 20 and result["observedTripCount"] == 1
    assert result["costEur"] == 2 and result["costTripCount"] == 1
