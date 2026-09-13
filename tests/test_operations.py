"""Regression tests for source preservation, live ingestion and defensive APIs."""
from __future__ import annotations

import gzip
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app import main
from app.services import ingestion
from app.services.watcher import Watcher


def myop_payload(identifier=1, day=1, distance=10):
    """Make a small normal MyOpel source with one completed trip."""
    return json.dumps({"trips": [{"id": identifier, "distance": distance, "travelTime": 600,
        "start": {"date": f"2026-09-{day:02d}T10:00:00Z", "mileage": 1000 + day * 20},
        "end": {"date": f"2026-09-{day:02d}T10:10:00Z", "mileage": 1010 + day * 20}}]}).encode()


def csv_payload(distance=10):
    """Make a normal CSV with a trip counter and a ten-minute interval."""
    return f"Time;PID;Value;Units\n0;Distanza percorsa:;0;km\n600;Distanza percorsa:;{distance};km\n".encode()


def wait_until(predicate, timeout=3):
    """Bound asynchronous assertions without minute-long test sleeps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Timed out waiting for ingestion"


@pytest.fixture
def client(tmp_path, monkeypatch, request):
    """Run the production lifespan against a disposable database and source tree."""
    monkeypatch.setattr(main, "OBD_FILES_DIR", tmp_path / "obd")
    monkeypatch.setattr(main, "MYOP_FILES_DIR", tmp_path / "myop")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "db" / "trips.db")
    monkeypatch.setattr(main, "SOURCE_ARCHIVE", "gzip")
    monkeypatch.setattr(main, "DEFAULT_SETTINGS", {"myop_enabled": getattr(request, "param", True), "tank_capacity_l": 43.5, "fuel_density_gl": 835})
    monkeypatch.setattr(main, "Watcher", lambda: Watcher(settle_seconds=0.05, retry_delays=(0.02, 0.03)))
    with TestClient(main.app, raise_server_exceptions=False) as connection:
        yield connection


def test_archive_versions_same_name_preserve_all_originals(tmp_path):
    source = tmp_path / "export.myop"
    first = myop_payload(1, 1)
    second = myop_payload(2, 2)
    source.write_bytes(first)
    archived_first = ingestion.archive_snapshot(source, tmp_path)
    source.write_bytes(second)
    archived_second = ingestion.archive_snapshot(source, tmp_path)
    assert archived_first != archived_second
    assert gzip.decompress(archived_first.read_bytes()) == first
    assert gzip.decompress(archived_second.read_bytes()) == second
    assert source.read_bytes() == second


def test_failed_snapshot_keeps_source_and_publishes_nothing(tmp_path, monkeypatch):
    source = tmp_path / "export.myop"
    source.write_bytes(myop_payload())
    def failed_copy(*args, **kwargs):
        """Simulate interrupted storage while the immutable copy is being prepared."""
        raise OSError("simulated full disk")
    monkeypatch.setattr(ingestion.shutil, "copyfileobj", failed_copy)
    with pytest.raises(OSError):
        ingestion.archive_snapshot(source, tmp_path)
    assert source.read_bytes() == myop_payload()
    assert not list((tmp_path / "archive").rglob("*.gz"))
    assert not list((tmp_path / "archive").rglob(".snapshot-*"))


def test_corrupt_snapshot_is_replaced_from_retained_original(tmp_path):
    source = tmp_path / "export.myop"
    source.write_bytes(myop_payload())
    archived = ingestion.archive_snapshot(source, tmp_path)
    archived.write_bytes(b"incomplete storage copy")
    assert ingestion.archive_snapshot(source, tmp_path) == archived
    assert gzip.decompress(archived.read_bytes()) == source.read_bytes()


def test_slow_csv_copy_is_debounced_and_later_modifications_import(client):
    source = main.OBD_FILES_DIR / "2026-09-01_10-00-00.csv"
    with source.open("wb") as target:
        target.write(b"Time;PID;Value;Units\n0;Distanza percorsa:;0;km\n")
        target.flush()
        time.sleep(0.025)
        assert main.db.get_all_trips() == []
        target.write(b"600;Distanza percorsa:;10;km\n")
        target.flush()
    wait_until(lambda: len(main.db.get_all_trips()) == 1)
    assert source.exists()
    assert main.db.get_all_trips()[0]["distanceKm"] == 10
    source.write_bytes(csv_payload(12))
    wait_until(lambda: main.db.get_all_trips()[0]["distanceKm"] == 12)
    assert source.read_bytes() == csv_payload(12)


def test_watcher_serializes_retries_and_reports_failure(tmp_path):
    attempts = []
    active = 0
    maximum = 0
    def callback(path):
        """Fail one normal input initially and check callbacks never overlap."""
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            attempts.append(path.name)
            if path.name == "first.csv" and attempts.count(path.name) == 1:
                raise ValueError("temporary import failure")
            time.sleep(0.01)
        finally:
            active -= 1
    watcher = Watcher(settle_seconds=0.01, retry_delays=(0.01,))
    watcher.watch(tmp_path, callback, (".csv",))
    watcher.start()
    try:
        (tmp_path / "first.csv").write_text("complete first file")
        (tmp_path / "second.csv").write_text("complete second file")
        wait_until(lambda: attempts.count("first.csv") == 2 and attempts.count("second.csv") >= 1)
        wait_until(lambda: not watcher.status()["errors"])
        assert maximum == 1
    finally:
        watcher.stop()


def test_same_named_uploads_are_distinct_and_replay_is_idempotent(client):
    for identifier in (1, 2):
        response = client.post("/api/v1/upload/myop", files={"file": ("export.myop", myop_payload(identifier, identifier))})
        assert response.status_code == 200, response.text
    assert len(list((main.MYOP_FILES_DIR / "uploads").rglob("export.myop"))) == 2
    assert len(list((main.MYOP_FILES_DIR / "archive").rglob("export.myop.gz"))) == 2
    assert len(main.db.get_all_trips()) == 2
    response = client.post("/api/v1/upload/myop", files={"file": ("export.myop", myop_payload(1, 1))})
    assert response.json()["new_trips"] == []
    assert len(main.db.get_all_trips()) == 2


def test_historical_obd_archive_does_not_replace_newer_raw_revision(client):
    name = "2026-09-01_10-00-00.csv"
    for distance in (10, 12, 10):
        response = client.post("/api/v1/upload/obd", files={"file": (name, csv_payload(distance))})
        assert response.status_code == 200, response.text
    assert main.db.get_all_trips()[0]["distanceKm"] == 12
    main._scan_directory(main.OBD_FILES_DIR, main._process_obd_file, (".csv",))
    assert main.db.get_all_trips()[0]["distanceKm"] == 12


def test_upload_revision_of_merged_primary_preserves_other_segment(client):
    """A corrected CSV reimports its segment while retaining the complete session."""
    first_name = "2026-09-01_10-00-00.csv"
    second_name = "2026-09-01_10-12-00.csv"
    for filename, distance in ((first_name, 10), (second_name, 20)):
        response = client.post("/api/v1/upload/obd", files={"file": (filename, csv_payload(distance))})
        assert response.status_code == 200, response.text
    assert len(main.db.get_all_trips()) == 1
    assert main.db.get_all_trips()[0]["distanceKm"] == 30
    response = client.post("/api/v1/upload/obd", files={"file": (first_name, csv_payload(12))})
    assert response.status_code == 200, response.text
    trips = main.db.get_all_trips()
    assert len(trips) == 1
    assert trips[0]["distanceKm"] == 32
    assert set(trips[0]["mergedIds"]) == {"obd-2026-09-01_10-00-00", "obd-2026-09-01_10-12-00"}
    assert main.db.get_raw_trip("obd-2026-09-01_10-12-00")["distanceKm"] == 20
    assert main.db.get_trip("obd-2026-09-01_10-12-00") is None


@pytest.mark.parametrize("payload", [
    {"myop_enabled": False, "tank_capacity_l": "Infinity"},
    {"tank_capacity_l": -1}, {"fuel_density_gl": 0}, {"myop_enabled": "false"},
    {"tank_capacity_l": None}, {"tank_capacity_l": True}, {"unknownSetting": 123},
])
def test_settings_reject_invalid_batch_without_partial_commit(client, payload):
    before = main.db.get_all_settings()
    assert client.put("/api/v1/settings", json=payload).status_code == 422
    assert main.db.get_all_settings() == before
    assert client.get("/api/v1/health").status_code == 200


@pytest.mark.parametrize("payload", [
    {"liters": -25}, {"liters": "Infinity"}, {"liters": 0},
    {"liters": 25, "odometerKm": -100}, {"liters": 25, "pricePerL": -1},
    {"liters": 25, "fullTank": "false"}, {"liters": True},
    {"liters": 25, "ts": "not-a-date"},
])
def test_refuels_reject_invalid_values_before_writing(client, payload):
    assert client.post("/api/v1/refuels", json=payload).status_code == 422
    assert main.db.get_refuels() == []
    assert client.get("/api/v1/fuel").status_code == 200


def test_settings_and_refuels_accept_valid_boundaries(client):
    response = client.put("/api/v1/settings", json={"tank_capacity_l": 45, "fuel_density_gl": 780})
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == {"tank_capacity_l": 45, "fuel_density_gl": 780}
    response = client.post("/api/v1/refuels", json={"liters": 25, "odometerKm": 0, "pricePerL": 0,
                                                  "fullTank": False, "ts": "2026-09-13T12:30"})
    assert response.status_code == 200, response.text
    assert response.json()["refuel"]["fullTank"] is False
    assert response.json()["refuel"]["ts"] == "2026-09-13T12:30"


@pytest.mark.parametrize("client", [True, False], indirect=True)
def test_runtime_toggle_recovers_files_dropped_while_disabled(client):
    assert client.put("/api/v1/settings", json={"myop_enabled": False}).status_code == 200
    source = main.MYOP_FILES_DIR / "pending.myop"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(myop_payload())
    time.sleep(0.08)
    assert main.db.get_all_trips() == []
    assert client.post("/api/v1/upload/myop", files={"file": ("export.myop", myop_payload())}).status_code == 409
    assert client.put("/api/v1/settings", json={"myop_enabled": True}).status_code == 200
    wait_until(lambda: len(main.db.get_all_trips()) == 1)


def test_dashboard_contract_omits_heavy_fields(client):
    response = client.post("/api/v1/upload/obd", files={"file": ("2026-09-01_10-00-00.csv", csv_payload())})
    assert response.status_code == 200, response.text
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert set(payload) == {"vehicle", "trips", "alerts", "trendInsights", "pidCatalog", "pidGroups", "settings", "fuel", "meta"}
    assert not {"track", "pidValues", "pidSeriesFull", "pidSeriesTimes"}.intersection(payload["trips"][0])
    assert client.get("/api/v1/data.js").status_code == 200


def test_readiness_and_bootstrap_expose_database_failure(client, monkeypatch):
    def unavailable():
        """Represent a real database read failure after application startup."""
        raise OSError("database unavailable")
    monkeypatch.setattr(main.db, "get_all_trips", unavailable)
    assert client.get("/api/v1/dashboard").status_code == 503
    assert client.get("/api/v1/health").status_code == 503
    bootstrap = client.get("/api/v1/data.js")
    assert bootstrap.status_code == 503
    assert "DASHBOARD_BOOTSTRAP_ERROR" in bootstrap.text
    assert "TRIPS=[]" not in bootstrap.text


def test_brc_is_explicitly_unsupported_and_sources_stay_available(client):
    response = client.post("/api/v1/upload/obd", files={"file": ("trip.brc", b"BRC sample")})
    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]
    source = main.OBD_FILES_DIR / "trip.brc"
    source.write_bytes(b"BRC sample")
    wait_until(lambda: bool(main._watcher.status()["errors"]))
    assert source.read_bytes() == b"BRC sample"
