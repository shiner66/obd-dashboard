"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const OBD = require("../frontend/dashboard-core.js");

test("diagnostic graphs retain a short regeneration peak and cold-start readings", () => {
  // The previous IQR filter removed the entire >550°C event on a real active trip.
  const egt = [166.06, ...Array(45).fill(270), 442.06, 560.06, 610.46, 649.66, 646.46];
  assert.deepEqual(OBD.chartValues(egt), egt);
  assert.equal(Math.max(...OBD.chartValues(egt)), 649.66);
  const coolant = [32, 48, 67, 85, ...Array(50).fill(90), 94];
  assert.deepEqual(OBD.chartValues(coolant), coolant);
  assert.deepEqual(OBD.chartValues([0, null, NaN, Infinity, "12", -Infinity, 12]), [0, null, null, null, null, null, 12]);
});

test("unknown levels remain unavailable while a measured empty tank stays zero", () => {
  for (const missing of [null, undefined, NaN, Infinity]) assert.equal(OBD.measurement(missing), "—");
  assert.equal(OBD.measurement(0), "0");
});

test("refuel datetime defaults follow the local wall clock in summer and winter", () => {
  const previous = process.env.TZ;
  try {
    process.env.TZ = "Europe/Rome";
    assert.equal(OBD.localDateTime(new Date("2026-09-13T12:30:00+02:00")), "2026-09-13T12:30");
    assert.equal(OBD.localDateTime(new Date("2026-01-13T00:05:00+01:00")), "2026-01-13T00:05");
  } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
});

test("overall tank economy uses total kilometres over total pump litres", () => {
  assert.equal(OBD.weightedEconomy([{ km: 100, liters: 10 }, { km: 900, liters: 30 }]), 25);
  assert.equal(OBD.weightedEconomy([{ km: 0, liters: 20 }, { km: 50, liters: null }]), null);
  assert.equal(OBD.weightedEconomy([]), null);
});

test("trip aggregate distance follows the selected source and never expands partial MyOpel coverage", () => {
  const partial = { sources: ["obd", "myopel"], fuelSource: "myopel", distanceKm: 100, myopDistanceKm: 95 };
  assert.equal(OBD.tripEconomyDistance(partial), 95);
  assert.equal(OBD.tripEconomyDistance({ ...partial, fuelDistanceKm: 94.8 }), 94.8);
  assert.equal(OBD.tripEconomyDistance({ ...partial, fuelSource: "obd_rate" }), 100);
  assert.equal(OBD.tripEconomyDistance({ ...partial, myopDistanceKm: null }), null);
});

test("filtered trip selection cannot keep a hidden trip or fill an empty result", () => {
  const trips = [{ id: "obd-a" }, { id: "obd-b" }];
  assert.equal(OBD.selectedTrip(trips, "obd-b"), trips[1]);
  assert.equal(OBD.selectedTrip(trips, "myop-1721"), trips[0]);
  assert.equal(OBD.selectedTrip([], "myop-1721"), null);
});

test("switching to MyOpel resets unavailable DPF/PID tabs, preserving available tabs", () => {
  const myop = { sources: ["myopel"] }, obd = { sources: ["obd", "myopel"] };
  for (const tab of ["dpf", "pids"]) {
    assert.equal(OBD.tripTab(tab, myop), "overview");
    assert.equal(OBD.tripTab(tab, obd), tab);
  }
  assert.equal(OBD.tripTab("insights", myop), "insights");
});

test("incomplete refresh snapshots are rejected before a caller can replace valid data", () => {
  const good = { vehicle: {}, trips: [], pidCatalog: [], trendInsights: [], settings: {}, fuel: {}, alerts: {}, pidGroups: {} };
  assert.equal(OBD.validateSnapshot(good), good);
  let current = good;
  assert.throws(() => { current = OBD.validateSnapshot({ trips: [] }); }, /incompleta/);
  assert.equal(current, good);
});

test("chart time positions preserve acquisition gaps and identify legacy sample-only axes", () => {
  assert.deepEqual(OBD.chartPositions([10, 20, 30], [0, 10, 600]), { times: [0, 10, 600], timed: true });
  for (const invalid of [undefined, [0, 10], [10, 0, 600], [0, NaN, 600]]) {
    assert.deepEqual(OBD.chartPositions([10, 20, 30], invalid), { times: [0, 1, 2], timed: false });
  }
});

test("appearance restore accepts known options without persisting arbitrary stored keys", () => {
  const defaults = { theme: "midnight", accent: "cyan", anim: "subtle" };
  assert.deepEqual(OBD.restoreTweaks(defaults, { theme: "warm", accent: "not-a-color", arbitrary: true }),
    { theme: "warm", accent: "cyan", anim: "subtle" });
  assert.deepEqual(OBD.restoreTweaks(defaults, null), defaults);
});

test("import status distinguishes missing metadata from an idle healthy queue", () => {
  assert.equal(OBD.ingestionStatus(null), null);
  const idle = OBD.ingestionStatus({ ingestion: { running: true, pending: 0, errors: [] }, legacyRebuildNeeded: 0 });
  assert.deepEqual(idle, { running: true, pending: 0, errors: [], legacyCount: 0, legacyIds: [] });
  const unknown = OBD.ingestionStatus({ ingestion: {} });
  assert.equal(unknown.running, null);
  assert.equal(unknown.pending, null);
  assert.equal(unknown.errors, null);
  assert.equal(unknown.legacyCount, null);
});

test("import failures retain the actionable message, filename and legacy trip ids", () => {
  const failed = OBD.ingestionStatus({
    ingestion: { running: true, pending: 2, errors: [{ file: "/data/myop/export.myop", error: "File incompleto" }] },
    legacyRebuildNeeded: 1, legacyRebuildIds: ["obd-2026-06-16_12-30-43"],
  });
  assert.deepEqual(failed.errors, [{ file: "export.myop", message: "File incompleto" }]);
  assert.equal(failed.pending, 2);
  assert.equal(failed.legacyCount, 1);
  assert.deepEqual(failed.legacyIds, ["obd-2026-06-16_12-30-43"]);
});
