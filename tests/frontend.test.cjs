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

test("legacy records remain in historical distance but cannot inflate time, economy or cost", () => {
  const valid = { sources: ["obd"], parserVersion: 2, distanceKm: 100, durationMin: 60, fuelConsumedL: 5, fuelDistanceKm: 95, costEur: 10, costDistanceKm: 95 };
  const legacy = { ...valid, legacyIncomplete: true, distanceKm: 900, durationMin: 10000, fuelConsumedL: 100, costEur: 1000 };
  const oldParser = { ...valid, parserVersion: 1, distanceKm: 300 };
  const unknownCoverage = { ...valid, durationMin: null, fuelDistanceKm: null, costDistanceKm: null };
  const metrics = OBD.periodMetrics([valid, legacy, oldParser, unknownCoverage]);
  assert.equal(metrics.tripCount, 4); assert.equal(metrics.totalKm, 1400);
  assert.equal(metrics.excludedFromComparisons, 2);
  assert.equal(metrics.observedMinutes, 60); assert.equal(metrics.observedTripCount, 1);
  assert.equal(metrics.fuelLiters, 5); assert.equal(metrics.fuelDistanceKm, 95); assert.equal(metrics.consumptionKmL, 19);
  assert.equal(metrics.fuelTripCount, 1); assert.equal(metrics.costTripCount, 1); assert.equal(metrics.costEur, 10);
  assert.deepEqual([valid, legacy, oldParser].filter(OBD.usableForAnalysis), [valid]);
});

test("authoritative period aggregates preserve null and zero without replacing them with historical values", () => {
  const stale = [{ sources: ["myopel"], distanceKm: 10, durationMin: 5, fuelConsumedL: 1, fuelDistanceKm: 10, costEur: 2, costDistanceKm: 10 }];
  const metrics = OBD.periodMetrics(stale, { consumptionKmL: null, costEur: 0, costTripCount: 0, observedMinutes: 0, observedTripCount: 0 });
  assert.equal(metrics.consumptionKmL, null); assert.equal(metrics.costEur, 0); assert.equal(metrics.costTripCount, 0);
  assert.equal(metrics.observedMinutes, 0); assert.equal(metrics.observedTripCount, 0);
  assert.equal(OBD.periodMetrics([]).consumptionKmL, null);
});

test("filtered trip selection cannot keep a hidden trip or fill an empty result", () => {
  const trips = [{ id: "obd-a" }, { id: "obd-b" }];
  assert.equal(OBD.selectedTrip(trips, "obd-b"), trips[1]);
  assert.equal(OBD.selectedTrip(trips, "myop-1721"), trips[0]);
  assert.equal(OBD.selectedTrip([], "myop-1721"), null);
});

test("switching to MyOpel resets unavailable DPF/PID tabs, preserving available tabs", () => {
  const myop = { sources: ["myopel"] }, obd = { sources: ["obd", "myopel"] };
  for (const tab of ["dpf", "pids", "analysis"]) {
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

test("global presets use inclusive local calendar bounds and default to 30 days", () => {
  const now = new Date(2026, 8, 13, 12);
  assert.deepEqual(OBD.periodRange(undefined, now), {
    preset: "30d", fromDate: "2026-08-15", toDate: "2026-09-13", label: "15/08/2026 – 13/09/2026", query: "?from_date=2026-08-15&to_date=2026-09-13",
  });
  assert.equal(OBD.periodRange({ preset: "7d" }, now).fromDate, "2026-09-07");
  assert.equal(OBD.periodRange({ preset: "month" }, now).fromDate, "2026-09-01");
  assert.equal(OBD.periodRange({ preset: "all" }, now).query, "");
});

test("rolling periods advance at midnight and stay calendar based across DST", () => {
  const before = new Date(2026, 8, 13, 23, 59), after = new Date(2026, 8, 14, 0, 1);
  assert.equal(OBD.periodRange({ preset: "30d" }, before).fromDate, "2026-08-15");
  assert.equal(OBD.periodRange({ preset: "30d" }, after).fromDate, "2026-08-16");
  assert.equal(OBD.periodRange({ preset: "30d" }, after).toDate, "2026-09-14");
  const previous = process.env.TZ;
  try {
    process.env.TZ = "Europe/Rome";
    const range = OBD.periodRange({ preset: "7d" }, new Date("2026-03-30T00:10:00+02:00"));
    assert.equal(range.fromDate, "2026-03-24"); assert.equal(range.toDate, "2026-03-30");
  } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
});

test("custom periods reject missing, impossible or reversed dates without becoming all history", () => {
  for (const range of [{ fromDate: "2026-02-30", toDate: "2026-03-01" }, { fromDate: "2026-09-14", toDate: "2026-09-13" }, { fromDate: "2026-09-01" }]) {
    assert.ok(OBD.periodRange({ preset: "custom", ...range }).error);
    assert.equal(OBD.periodRange({ preset: "custom", ...range }).query, undefined);
  }
  const oneDay = OBD.periodRange({ preset: "custom", fromDate: "2026-09-13", toDate: "2026-09-13" });
  assert.equal(oneDay.query, "?from_date=2026-09-13&to_date=2026-09-13");
  assert.equal(OBD.calendarDate("2024-02-29"), true);
  assert.equal(OBD.calendarDate("2026-02-29"), false);
});

test("period persistence restores valid choices and ignores corrupt saved filters", () => {
  assert.deepEqual(OBD.restorePeriod(null), { preset: "30d" });
  assert.deepEqual(OBD.restorePeriod({ preset: "unknown" }), { preset: "30d" });
  assert.deepEqual(OBD.restorePeriod({ preset: "custom", fromDate: "bad", toDate: "bad" }), { preset: "30d" });
  assert.deepEqual(OBD.restorePeriod({ preset: "all" }), { preset: "all" });
  assert.deepEqual(OBD.restorePeriod({ preset: "custom", fromDate: "2026-01-01", toDate: "2026-02-01", injected: true }), { preset: "custom", fromDate: "2026-01-01", toDate: "2026-02-01" });
});

test("an older async period response cannot replace a newer snapshot, including empty periods", async () => {
  const gate = OBD.requestGate(); let oldResolve, currentResolve, visible = null;
  const oldTicket = gate.select("30d");
  const oldResponse = new Promise(resolve => { oldResolve = resolve; }).then(value => { if (gate.current(oldTicket)) visible = value; });
  const currentTicket = gate.select("custom-empty");
  const currentResponse = new Promise(resolve => { currentResolve = resolve; }).then(value => { if (gate.current(currentTicket)) visible = value; });
  currentResolve({ period: "custom-empty", trips: [] }); await currentResponse;
  oldResolve({ period: "30d", trips: ["old-trip"] }); await oldResponse;
  assert.deepEqual(visible, { period: "custom-empty", trips: [] });
  assert.equal(gate.current(oldTicket), false);
  assert.equal(gate.select("custom-empty"), currentTicket);
});

test("large trip lists render one bounded page and clamp after a filter shrinks the list", () => {
  const trips = Array.from({ length: 472 }, (_, id) => ({ id }));
  const first = OBD.pageItems(trips, 1, 20), last = OBD.pageItems(trips, 99, 20);
  assert.equal(first.items.length, 20); assert.equal(first.pages, 24);
  assert.equal(last.page, 24); assert.equal(last.items.length, 12); assert.equal(last.start, 461); assert.equal(last.end, 472);
  const filtered = OBD.pageItems(trips.slice(0, 3), 24, 20);
  assert.equal(filtered.page, 1); assert.equal(filtered.items.length, 3);
  assert.equal(OBD.pageItems([], 24).start, 0);
});

test("refuel create and edit round-trip local ISO timestamps without UTC or duplicate seconds", () => {
  const existing = { id: 7, ts: "2026-09-13 12:39:00", liters: 32.14, odometerKm: 21500, pricePerL: 1.899, fuelType: "HVO", fullTank: false, note: "  prova  " };
  const draft = OBD.refuelDraft(existing, 99999);
  assert.equal(draft.ts, "2026-09-13T12:39");
  assert.deepEqual(OBD.refuelPayload(draft), { ts: "2026-09-13T12:39:00", liters: 32.14, odometerKm: 21500, pricePerL: 1.899, fuelType: "HVO", fullTank: false, note: "prova" });
  assert.equal(OBD.refuelDraft({ ...existing, ts: "2026-09-13T12:39:00" }).ts, draft.ts);
});

test("invalid refuel values fail inline before serializing NaN to null or posting negative litres", () => {
  const form = { ts: "2026-09-13T12:39", liters: "32", odometerKm: "", pricePerL: "", fuelType: "B7", fullTank: true, note: "" };
  assert.equal(OBD.refuelPayload(form).odometerKm, null);
  for (const liters of ["", "-1", "Infinity", "invalid", "201"]) assert.throws(() => OBD.refuelPayload({ ...form, liters }), /Litri/);
  assert.throws(() => OBD.refuelPayload({ ...form, ts: "2026-09-13T27:00" }), /ora/);
  assert.throws(() => OBD.refuelPayload({ ...form, odometerKm: "-3" }), /Odometro/);
});

test("structured refuel validation errors become readable Italian field messages", () => {
  const message = OBD.apiError([{ loc: ["body", "liters"], type: "greater_than", ctx: { gt: 0 }, msg: "Input should be greater than 0" }]);
  assert.equal(message, "Litri: il valore deve superare 0");
  assert.equal(message.includes("[object Object]"), false);
  assert.equal(OBD.apiError("Rifornimento non trovato"), "Rifornimento non trovato");
});

test("activity charts cover the selected interval with honest gaps instead of only the latest trips", () => {
  const trips = [{ start: "2026-09-01T10:00:00", distanceKm: 10 }, { start: "2026-09-03T10:00:00", distanceKm: 20 }];
  const series = OBD.activitySeries(trips, { fromDate: "2026-09-01", toDate: "2026-09-03" });
  assert.deepEqual(series.data.map(x => x.value), [10, 0, 20]);
  assert.equal(series.unit, "giorno");
  const monthly = OBD.activitySeries([{ start: "2026-01-01T10:00:00", distanceKm: 5 }, { start: "2026-09-01T10:00:00", distanceKm: 15 }], {});
  assert.equal(monthly.unit, "mese"); assert.equal(monthly.data.length, 9);
  assert.equal(monthly.data.reduce((sum, point) => sum + point.value, 0), 20);
  assert.deepEqual(OBD.activitySeries([], {}), { data: [], unit: "giorno" });
});
