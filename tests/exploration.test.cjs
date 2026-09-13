"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const E = require("../frontend/exploration-core.js");

test("zoom preserves real timestamps, peaks and gaps rather than compressing acquisition time", () => {
  const series = E.prepareSeries([180, 220, 640, 210], [0, 10, 20, 600]);
  assert.equal(series.timed, true);
  assert.deepEqual(series.axis, [0, 10, 20, 600]);
  assert.equal(series.gaps.length, 1);
  const full = E.visibleSeries(series, [0, 600]);
  assert.equal(full.max, 640);
  assert.deepEqual(full.segments.map(segment => segment.map(point => point.value)), [[180, 220, 640], [210]]);
  const zoomed = E.visibleSeries(series, [5, 25]);
  assert.deepEqual(zoomed.points.map(point => point.time), [10, 20]);
  assert.equal(zoomed.max, 640);
});

test("empty zoom intervals stay empty and reset restores the full original series", () => {
  const series = E.prepareSeries([10, 20, 30], [0, 10, 600]);
  assert.deepEqual(E.visibleSeries(series, [100, 300]).points, []);
  assert.deepEqual(E.visibleSeries(series, [700, 900]).points, []);
  assert.equal(E.visibleSeries(series, null).points.length, 3);
  assert.deepEqual(E.clampRange(null, [0, 600]), [0, 600]);
  assert.deepEqual(E.zoomRange([0, 600], [0, 600], 0.5, 10), [0, 300]);
  assert.deepEqual(E.zoomRange([100, 200], [0, 600], 100), [0, 600]);
});

test("sample-only series are explicit and never inherit recording-time events", () => {
  for (const times of [undefined, [0, 1], [2, 1, 0], [0, Infinity, 20]]) {
    const series = E.prepareSeries([0, null, 5], times, [{from: 1, to: 2}]);
    assert.equal(series.timed, false);
    assert.deepEqual(series.axis, [0, 1, 2]);
    assert.equal(series.gaps.length, 0);
    assert.equal(E.visibleSeries(series, null).segments.length, 2);
  }
});

test("constant values and single samples remain observations rather than missing data", () => {
  const constant = E.visibleSeries(E.prepareSeries([0, 0, 0], [0, 1, 2]), null);
  assert.equal(constant.points.length, 3);
  assert.equal(constant.min, 0);
  assert.equal(constant.max, 0);
  assert.equal(E.visibleSeries(E.prepareSeries([12], [4]), null).points[0].value, 12);
});

test("recording windows are merged and missing intervals remain exact", () => {
  const gaps = E.recordingGaps({observedWindows: [[120, 180], [0, 30], [20, 50], [200, 220]]});
  assert.deepEqual(gaps.map(gap => [gap.from, gap.to]), [[50, 120], [180, 200]]);
  assert.deepEqual(E.recordingGaps({recordingGapSeconds: 3600}), []);
  const explicit = E.prepareSeries([10, 11, 12], [0, 60, 180], gaps);
  assert.equal(E.visibleSeries(explicit, null).segments.length, 3);
});

test("DPF markers require a sampled ECU flag with actual time and never assert an episode", () => {
  const trip = {dpfRegenState: "active", pidSeriesFull: {regen_st: [0, 1, 0, 1]}, pidSeriesTimes: {regen_st: [0, 10, 20, 600]}};
  const marks = E.dpfObservations(trip);
  assert.deepEqual(marks.map(mark => [mark.from, mark.to]), [[10, 10], [600, 600]]);
  assert.ok(marks.every(mark => mark.label.includes("campione")));
  assert.deepEqual(E.dpfObservations({dpfRegenState: "active"}), []);
  assert.deepEqual(E.dpfObservations({...trip, pidSeriesTimes: {}}), []);
});

test("candidate ranking uses only available conditions and excludes incomplete legacy history", () => {
  const reference = {id: "a", distanceKm: 20, durationMin: 30, airTempC: 20, hasTrack: true};
  const trips = [reference,
    {id: "b", distanceKm: 21, durationMin: 31, airTempC: 20, hasTrack: false},
    {id: "c", distanceKm: 20, hasTrack: true},
    {id: "legacy", distanceKm: 20, durationMin: 30, airTempC: 20, legacyIncomplete: true},
    {id: "empty"}];
  const ranked = E.similarTrips(reference, trips);
  assert.deepEqual(ranked.map(candidate => candidate.trip.id), ["b", "c"]);
  assert.equal(ranked[0].compared.length, 3);
  assert.equal(ranked[1].compared.length, 1);
  assert.ok(ranked.every(candidate => candidate.routeUsed === false));
  assert.deepEqual(E.similarTrips({...reference, legacyIncomplete: true}, trips), []);
});

test("fuel comparison respects actual covered distance and requires compatible coverage", () => {
  const partial = {sources: ["obd", "myopel"], fuelSource: "myopel", distanceKm: 100, myopDistanceKm: 10, fuelConsumedL: 1, fuelCoveragePct: 10};
  assert.equal(E.fuelObservation(partial).kmL, 10);
  assert.equal(E.fuelObservation({...partial, myopDistanceKm: null}).kmL, null);
  const good = {distanceKm: 20, durationMin: 30, airTempC: 20, fuelSource: "obd_rate", fuelConsumedL: 1, fuelCoveragePct: 100};
  assert.equal(E.comparisonLimits(good, {...good, fuelConsumedL: 1.1}).economyComparable, true);
  for (const changed of [{fuelCoveragePct: null}, {fuelCoveragePct: 80}, {fuelSource: "myopel"}, {airTempC: null}, {legacyIncomplete: true}])
    assert.equal(E.comparisonLimits(good, {...good, ...changed}).economyComparable, false);
});

test("nearby recorded endpoints add an optional bonus without penalizing absent GPS", () => {
  const reference = {id: "reference", distanceKm: 20, durationMin: 30, airTempC: 20,
    routeStart: [40, 14], routeEnd: [40.01, 14]};
  const plain = {id: "plain", distanceKm: 20, durationMin: 30, airTempC: 20};
  const close = {...plain, id: "close", routeStart: [40, 14.0001], routeEnd: [40.01, 14.0001]};
  const far = {...plain, id: "far", routeStart: [41, 15], routeEnd: [41.01, 15]};
  const ranked = E.similarTrips(reference, [plain, close, far]);
  assert.equal(ranked[0].trip.id, "close");
  assert.ok(ranked[0].endpointBonus > 0 && ranked[0].endpointBonus <= 0.15);
  assert.equal(ranked.find(candidate => candidate.trip.id === "plain").score, 0);
  assert.equal(ranked.find(candidate => candidate.trip.id === "far").score, 0);
  const withoutGps = E.similarTrips({...reference, routeStart: undefined, routeEnd: undefined}, [plain, close, far]);
  assert.ok(withoutGps.every(candidate => candidate.score === 0 && candidate.endpointBonus === 0));
  assert.ok(ranked.every(candidate => candidate.routeUsed === false));
});

test("GPS availability alone never establishes a similar route", () => {
  assert.equal(E.routeEvidence({hasTrack: true}, {hasTrack: true}).available, false);
  assert.equal(E.routeEvidence({track: [[0, 0], [91, 10]]}, {track: [[40, 14], [40.01, 14]]}).available, false);
  const a = {track: [[40, 14], [40.01, 14]]}, b = {track: [[40, 14], [40.01, 14.001]]};
  const evidence = E.routeEvidence(a, b);
  assert.equal(evidence.available, true);
  assert.equal(evidence.startMeters, 0);
  assert.ok(evidence.endMeters > 80 && evidence.endMeters < 90);
  assert.equal(evidence.similarRoute, undefined);
});

test("insight navigation follows explicit source references without fabricated fallback IDs", () => {
  assert.deepEqual(E.insightEvidence({category: "dpf", title: "Un insight"}), {tripIds: [], pidSlugs: [], sampleCount: null});
  assert.deepEqual(E.insightEvidence({evidence: {tripIds: ["obd-a", "obd-a", "obd-b"], pidSlugs: ["egt_a"], sampleCount: 20}}),
    {tripIds: ["obd-a", "obd-b"], pidSlugs: ["egt_a"], sampleCount: 20});
});
