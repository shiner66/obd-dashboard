"""Regression tests for source integrity, consumption, merged trips and timing."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import database as db
from app.parsers import csv_parser
from app.services import correlator, dpf, fuel, insights


class IntegrityTests(unittest.TestCase):
    """Exercise real SQLite persistence and pure parsers using isolated fixtures."""

    def setUp(self):
        """Create a disposable database for each regression."""
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.database = self.folder / "trips.db"
        db.init(self.database)

    def tearDown(self):
        """Remove only this test's disposable data."""
        self.temp.cleanup()

    def obd(self, tid="obd-a", start="10:00:00", end="10:20:00", km=10, **fields):
        """Build a measured OBD observation with realistic provenance."""
        return {"id": tid, "sources": ["obd"], "start": "2026-09-01T" + start,
                "end": "2026-09-01T" + end, "durationMin": 20, "distanceKm": km,
                "parserVersion": 2, "fuelMassCoveragePct": 100,
                "fuelRateCoveragePct": 100, **fields}

    def myop(self, tid, start, km=10, liters=1):
        """Build an independently identifiable original MyOpel leg."""
        return {"id": f"myop-{tid}", "myopId": tid, "sources": ["myopel"],
                "start": "2026-09-01T" + start, "end": "2026-09-01T11:00:00",
                "durationMin": 20, "distanceKm": km, "fuelConsumedL": liters,
                "myopFuelUl": round(liters * 1_000_000), "priceFuel": 2}

    def test_partial_myop_cannot_become_whole_trip_consumption(self):
        """The 163.9 km/L audit example must never reach insights or the tank ledger."""
        db.save_trip(self.obd(fuelMassG=304.3, fuelMassCoveragePct=None))
        db.enrich_with_myop("obd-a", {"myopId": 1, "myopLegIds": [1],
                                    "myopDistanceKm": 1, "fuelConsumedL": .061})
        t = db.get_trip("obd-a")
        self.assertEqual(t["myopFuelConsumedL"], .061)
        self.assertIsNone(t["fuelConsumedL"])
        self.assertIsNone(insights._km_l(t))
        self.assertEqual(fuel.trip_burn_l(t), 0)

    def test_mass_fallback_has_coverage_and_respects_density(self):
        """A complete OBD mass measurement outranks a partial MyOpel volume."""
        db.save_trip(self.obd(fuelMassG=835))
        db.enrich_with_myop("obd-a", {"myopId": 1, "myopDistanceKm": 1, "fuelConsumedL": .061})
        t = db.get_trip("obd-a")
        self.assertEqual((t["fuelSource"], t["fuelCoveragePct"]), ("obd_mass", 100))
        self.assertEqual(t["fuelConsumedL"], 1)
        self.assertAlmostEqual(fuel.trip_burn_l(t, 780), 835 / 780, places=3)

    def test_merge_preserves_raw_and_aggregates_every_segment(self):
        """Merging changes the session while retaining both original observations."""
        stats = lambda high: {"first": 900, "last": 900, "min": 900, "max": high,
                              "mean": 1000, "mode": 900, "samples": 2,
                              "first_seen_s": 0, "last_seen_s": 1200, "coverage_pct": 100}
        db.save_trip(self.obd(odometerKm=1010, fuelMassG=400, fuelConsumedL=.5,
                             avgRpm=1000, acCurrentMa=300, dpfRegenState="idle",
                             dpfSinceRegenKm=250, pidValues={"rpm": stats(2000)},
                             pidSeriesFull={"rpm": [900, 2000]}, pidSeriesTimes={"rpm": [0, 1200]}))
        db.save_trip(self.obd("obd-b", "10:22:00", "10:42:00", 20,
                             odometerKm=1030, fuelMassG=800, fuelConsumedL=1,
                             dpfRegenState="completed", dpfSinceRegenKm=2,
                             pidValues={"rpm": stats(3000)}, pidSeriesFull={"rpm": [3000, 900]},
                             pidSeriesTimes={"rpm": [0, 1200]}))
        correlator.auto_correlate_all()
        merged = db.get_trip("obd-a")
        self.assertEqual((merged["distanceKm"], merged["odometerKm"], merged["fuelMassG"]), (30, 1030, 1200))
        self.assertEqual(merged["gPerKm"], 40)
        self.assertEqual(merged["acCurrentMa"], 300)  # no dilution by missing measurements
        self.assertEqual(merged["dpfRegenState"], "completed")
        self.assertEqual(merged["dpfSinceRegenKm"], 2)
        self.assertEqual(merged["pidValues"]["rpm"]["max"], 3000)
        self.assertEqual(merged["pidSeriesTimes"]["rpm"], [0, 1200, 1320, 2520])
        self.assertEqual(len(db.get_raw_trips("obd")), 2)
        self.assertTrue(db.trip_is_absorbed("obd-b"))
        self.assertEqual(sum(correlator.auto_correlate_all().values()), 0)

    def test_updated_original_segment_keeps_the_full_merged_session(self):
        """A genuinely new upload of the primary raw segment keeps all siblings."""
        first = self.obd(fuelMassG=400, odometerKm=1010)
        second = self.obd("obd-b", "10:22:00", "10:42:00", 20,
                          fuelMassG=800, odometerKm=1030)
        db.save_trip(first)
        db.save_trip(second)
        db.merge_trips("obd-a", ["obd-b"])
        revised = {**first, "fuelMassG": 500}
        self.assertTrue(db.save_raw_trip(revised))
        self.assertTrue(db.apply_source_observation(revised))
        merged = db.get_trip("obd-a")
        self.assertEqual(merged["distanceKm"], 30)
        self.assertEqual(merged["odometerKm"], 1030)
        self.assertEqual(merged["fuelMassG"], 1300)
        self.assertEqual(set(merged["mergedIds"]), {"obd-a", "obd-b"})
        self.assertIsNone(db.get_trip("obd-b"))

    def test_partial_mass_merge_cannot_make_a_complete_g_per_km(self):
        """Unmeasured segments lower coverage instead of creating false economy."""
        db.save_trip(self.obd(fuelMassG=400))
        db.save_trip(self.obd("obd-b", "10:22:00", "10:42:00", 10,
                             fuelMassCoveragePct=None))
        result = db.merge_trips("obd-a", ["obd-b"])
        self.assertEqual(result["fuelMassCoveragePct"], 50)
        self.assertIsNone(result["gPerKm"])
        self.assertIsNone(result["fuelConsumedL"])

    def test_incremental_myop_keeps_previous_legs_and_is_idempotent(self):
        """Separate imports must equal one cumulative import, including microlitres."""
        db.save_trip(self.obd(end="11:00:00", km=20))
        db.save_trip(self.myop(1, "10:02:00"))
        correlator.auto_correlate_all()
        db.save_trip(self.myop(2, "10:30:00"))
        correlator.auto_correlate_all()
        t = db.get_trip("obd-a")
        self.assertEqual(t["myopLegIds"], [1, 2])
        self.assertEqual((t["myopDistanceKm"], t["myopFuelConsumedL"], t["myopFuelUl"]), (20, 2, 2_000_000))
        self.assertEqual(sum(correlator.auto_correlate_all().values()), 0)
        self.assertEqual(len(db.get_raw_trips("myopel")), 2)

    def test_score_fallback_association_survives_repeated_reconciliation(self):
        """Previously correlated fallback legs must remain eligible after restart."""
        db.save_trip(self.obd())
        db.save_trip(self.myop(1, "09:50:00"))
        correlator.auto_correlate_all()
        self.assertEqual(db.get_trip("obd-a")["myopLegIds"], [1])
        self.assertEqual(sum(correlator.auto_correlate_all().values()), 0)
        self.assertEqual(db.get_trip("obd-a")["myopLegIds"], [1])
        self.assertFalse(db.trip_exists("myop-1"))

    def test_reissued_myop_is_deduped_before_consumption_aggregation(self):
        """A reissued leg with a new ID cannot double the fuel inside one session."""
        db.save_trip(self.obd(end="11:00:00", km=20))
        db.save_trip(self.myop(1, "10:02:00"))
        db.save_trip(self.myop(2, "10:02:00"))
        correlator.auto_correlate_all()
        t = db.get_trip("obd-a")
        self.assertEqual(t["myopDistanceKm"], 10)
        self.assertEqual(t["myopFuelConsumedL"], 1)
        self.assertEqual(len(t["myopLegIds"]), 1)
        self.assertEqual(sum(correlator.auto_correlate_all().values()), 0)
        self.assertEqual(len(db.get_raw_trips("myopel")), 2)

    def test_corrected_raw_leg_can_become_standalone_without_stale_enrichment(self):
        """A timestamp correction removes the obsolete association and restores its leg."""
        db.save_trip(self.obd())
        db.save_trip(self.myop(1, "10:02:00"))
        correlator.auto_correlate_all()
        corrected = self.myop(1, "14:00:00")
        corrected["end"] = "2026-09-01T14:20:00"
        db.save_trip(corrected)
        correlator.auto_correlate_all()
        self.assertIsNone(db.get_trip("obd-a")["myopId"])
        self.assertTrue(db.trip_exists("myop-1"))
        self.assertEqual(sum(correlator.auto_correlate_all().values()), 0)

    def test_raw_revisions_retain_previous_content_and_do_not_promote_replays(self):
        """Historical archives remain recognizable after a new revision is selected."""
        old = self.myop(1, "10:00:00")
        changed = {**old, "fuelConsumedL": 1.2}
        self.assertTrue(db.save_raw_trip(old))
        self.assertTrue(db.save_raw_trip(changed))
        self.assertTrue(db.raw_trip_revision_known(old))
        self.assertEqual(db.get_raw_trip("myop-1")["fuelConsumedL"], 1.2)
        self.assertFalse(db.save_raw_trip(changed))

    def test_zip_alias_and_gaps_preserve_elapsed_and_observed_time(self):
        """A four-hour dropout is not displayed as four hours of engine-on time."""
        path = self.folder / "2026-09-01_10-00-00__zip_5af42161.csv"
        path.write_text("SECONDS;PID;VALUE;UNITS\n"
                        "0;Velocità (GPS);60;km/h\n30;Velocità (GPS);60;km/h\n"
                        "14400;Velocità (GPS);60;km/h\n14430;Velocità (GPS);60;km/h\n"
                        "0;Distanza percorsa:;0;km\n14430;Distanza percorsa:;5;km\n")
        # No RPM anchor; the trip counter itself is an anchor with just two polls,
        # so observed duration may be unavailable but elapsed remains explicit.
        t = csv_parser.parse_file(path)[0]
        self.assertEqual(t["id"], "obd-2026-09-01_10-00-00")
        self.assertTrue(t["start"])
        self.assertGreater(t["recordingGapSeconds"], 14000)
        self.assertLess(t["durationMin"], 2)
        self.assertIsNone(t["engineOnDurationMin"])
        self.assertIsNone(t["odometerKm"])  # trip distance is not the vehicle odometer
        self.assertEqual(t["elapsedDurationMin"], 240.5)
        self.assertEqual(t["pidSeriesTimes"]["trip_km"], [0, 14430])

    def test_dpf_rejects_separated_spikes_and_nonconcurrent_heat(self):
        """Three isolated ECU spikes plus later heat are not an active regen."""
        status = [(i, int(i in (1, 10, 30))) for i in range(61)]
        result = dpf.assess_state({dpf._REGEN_STATUS: status,
                                   dpf._EGT_AFTER: [(0, 200), (59, 200), (60, 600)]})
        self.assertEqual(result["active"], 0)
        self.assertFalse(result["endObservedActive"])

    def test_dpf_does_not_infer_shutdown_or_completion_from_stopped_flag(self):
        """A real activity episode with unconfirmed outcome is explicitly unknown."""
        status = [(0, 1), (8, 1), (16, 1), (24, 1), (32, 0)]
        result = dpf.assess_state({dpf._REGEN_STATUS: status,
                                   dpf._EGT_AFTER: [(0, 600), (16, 600), (32, 300)]})
        self.assertEqual((result["state"], result["active"]), ("unknown", 1))
        self.assertFalse(result["endObservedActive"])

    def test_migration_and_offline_rebuild_preserve_missing_legacy(self):
        """Missing source files never cause automatic historical or refuel loss."""
        db.save_trip(self.obd(), record_raw=False)
        db.add_refuel({"liters": 20, "odometerKm": 1000})
        db.set_settings({"tank_capacity_l": 45})
        with sqlite3.connect(self.database) as con:
            con.execute("DROP TABLE trips_before_v9")
            con.execute("PRAGMA user_version=8")
        db.init(self.database)
        report = db.rebuild_materialized_from_raw()
        self.assertEqual(report["preservedLegacy"], ["obd-a"])
        self.assertEqual(len(db.get_all_trips()), 1)
        self.assertEqual(len(db.get_refuels()), 1)
        self.assertEqual(db.get_all_settings()["tank_capacity_l"], 45)
        with sqlite3.connect(self.database) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM trips_before_v9").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 9)


if __name__ == "__main__":
    unittest.main()
