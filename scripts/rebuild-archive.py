#!/usr/bin/env python3
"""Rebuild an explicitly separate database from archives, retaining legacy data.

Never opens the input DB for writing and never modifies source files. The output
must not exist. Review the JSON report before replacing any production database.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import database as db
from app.parsers import csv_parser, myop_parser
from app.services import correlator, insights


def source_bytes(path: Path) -> bytes:
    """Read the uncompressed source for content identity without modifying it."""
    return gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()


def completeness(trip: dict) -> tuple:
    """Prefer the observation with most measured samples for an original trip."""
    values = trip.get("pidValues") or {}
    return (sum(p.get("samples", 0) or 0 for p in values.values()),
            len(values), len(trip.get("track") or []))


def rebuild(source_db: Path, output_db: Path, obd: Path, myop: Path) -> dict:
    """Create a recoverable copy, ingest raw revisions and reconcile once."""
    if not source_db.is_file() or not obd.is_dir() or not myop.is_dir():
        raise ValueError("Input database and both source directories must exist")
    if output_db.exists() or output_db.resolve() == source_db.resolve():
        raise ValueError("Output must be a new file distinct from the source DB")
    output_db.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation guards against overwriting a concurrent output.
    output_db.touch(exist_ok=False)
    with sqlite3.connect(source_db.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(output_db) as dst:
            src.backup(dst)
    db.init(output_db)
    before = db.get_all_trips()
    before_settings, before_refuels = db.get_all_settings(), db.get_refuels()
    report = {"beforeTrips": len(before), "sourceFiles": 0,
              "uniqueContents": 0, "duplicateContents": 0, "parseErrors": [],
              "emptyFiles": [], "rawCandidates": 0, "selectedRawTrips": 0}
    seen: set[tuple[str, str]] = set()
    best: dict[str, dict] = {}
    for kind, directory, parser, suffixes in (
        ("obd", obd, csv_parser.parse_file, (".csv", ".csv.gz")),
        ("myop", myop, myop_parser.parse_file, (".myop", ".myop.gz", ".json", ".json.gz")),
    ):
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or not path.name.lower().endswith(suffixes):
                continue
            # Source manifests/backups are not exports even when JSON encoded.
            if any(p in ("backups", "manifests") for p in path.relative_to(directory).parts[:-1]):
                continue
            report["sourceFiles"] += 1
            try:
                content = source_bytes(path)
                digest = hashlib.sha256(content).hexdigest()
                if (kind, digest) in seen:
                    report["duplicateContents"] += 1
                    continue
                seen.add((kind, digest))
                parsed = parser(path)
                if not parsed:
                    report["emptyFiles"].append(str(path.relative_to(directory)))
                for trip in parsed:
                    db.save_raw_trip(trip)
                    db.upsert_pid_catalog(trip.get("pidCatalog") or [])
                    report["rawCandidates"] += 1
                    old = best.get(trip["id"])
                    if old is None or completeness(trip) >= completeness(old):
                        best[trip["id"]] = trip
            except Exception as exc:
                report["parseErrors"].append({"file": str(path.relative_to(directory)),
                                               "error": f"{type(exc).__name__}: {exc}"})
    for trip in best.values():
        # The last revision is the selected complete observation; earlier
        # observations remain available in the immutable history.
        db.save_raw_trip(trip)
    report["uniqueContents"] = len(seen)
    report["selectedRawTrips"] = len(best)
    report["rebuild"] = db.rebuild_materialized_from_raw()
    report["correlation"] = correlator.auto_correlate_all()
    trips = db.get_all_trips()
    # Use the same context and insight refresh as application startup, without
    # starting the watcher or touching source directories.
    from app import main
    main._recompute_all_insights()
    main._refresh_cross_trip_insights()
    trips = db.get_all_trips()
    report["afterTrips"] = len(trips)
    report["missingDates"] = [t["id"] for t in trips if not t.get("start")]
    report["settingsPreserved"] = before_settings == db.get_all_settings()
    report["refuelsPreserved"] = before_refuels == db.get_refuels()
    with sqlite3.connect(output_db) as con:
        report["integrity"] = con.execute("PRAGMA quick_check").fetchone()[0]
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if not report["settingsPreserved"] or not report["refuelsPreserved"] or report["integrity"] != "ok":
        raise RuntimeError("Output validation failed; do not deploy this copy")
    return report


def main() -> None:
    """Parse explicit input/output paths and save a reviewable recovery report."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-db", required=True, type=Path)
    p.add_argument("--output-db", required=True, type=Path)
    p.add_argument("--obd", required=True, type=Path)
    p.add_argument("--myop", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    args = p.parse_args()
    if args.report.exists():
        p.error("Report path must be new")
    report = rebuild(args.source_db, args.output_db, args.obd, args.myop)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
