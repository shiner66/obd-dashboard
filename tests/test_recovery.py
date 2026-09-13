"""Offline recovery must retain observations which cannot be reconstructed."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest
from app import database as db


def load_recovery():
    """Load the operator CLI without executing its argument parser."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "rebuild-archive.py"
    spec = importlib.util.spec_from_file_location("archive_recovery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recovery_preserves_legacy_and_user_data_without_sources(tmp_path):
    """An empty source directory must not delete historical or user-entered data."""
    source, output = tmp_path / "original.db", tmp_path / "candidate.db"
    obd, myop = tmp_path / "obd", tmp_path / "myop"
    obd.mkdir(); myop.mkdir()
    db.init(source)
    db.save_trip({"id": "obd-legacy", "sources": ["obd"],
                  "start": "2026-08-01T10:00:00", "end": "2026-08-01T10:20:00",
                  "distanceKm": 10, "durationMin": 20}, record_raw=False)
    db.set_settings({"tank_capacity_l": 44.5})
    db.add_refuel({"ts": "2026-08-01T09:00:00", "odometerKm": 20000,
                   "liters": 35, "fullTank": True, "fuelType": "B7", "pricePerL": 1.7})
    before = source.read_bytes()
    report = load_recovery().rebuild(source, output, obd, myop)
    assert report["settingsPreserved"] and report["refuelsPreserved"]
    assert report["beforeTrips"] == report["afterTrips"] == 1
    assert "obd-legacy" in report["rebuild"]["preservedLegacy"]
    assert report["integrity"] == "ok"
    assert source.read_bytes() == before
    with sqlite3.connect(output) as con:
        assert con.execute("SELECT COUNT(*) FROM refuels").fetchone()[0] == 1


def test_recovery_refuses_to_replace_an_existing_database(tmp_path):
    """Wrong output arguments fail before either existing database is modified."""
    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    folder = tmp_path / "files"
    folder.mkdir()
    before = source.read_bytes()
    with pytest.raises(ValueError, match="new file"):
        load_recovery().rebuild(source, source, folder, folder)
    assert source.read_bytes() == before
