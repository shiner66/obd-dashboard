/* Pure, source-aware helpers for chart inspection and trip comparison. */
(function (root) {
  "use strict";
  const finite = value => typeof value === "number" && Number.isFinite(value);
  /** Retain observed time axes; a missing or corrupt axis stays sample-index based. */
  function prepareSeries(data, times, explicitGaps = []) {
    const values = Array.isArray(data) ? data.map(v => finite(v) ? v : null) : [];
    const timed = Array.isArray(times) && times.length === values.length && times.length > 0
      && times.every((t, i) => finite(t) && (i === 0 || t >= times[i - 1]));
    const axis = timed ? times.slice() : values.map((_, i) => i);
    const deltas = axis.slice(1).map((t, i) => t - axis[i]).filter(d => d > 0).sort((a, b) => a - b);
    const median = deltas.length ? deltas[Math.floor((deltas.length - 1) / 2)] : 0;
    const gaps = timed ? explicitGaps.filter(g => finite(g.from) && finite(g.to) && g.to > g.from).map(g => ({...g})) : [];
    if (timed) axis.slice(1).forEach((t, i) => {
      if (t - axis[i] > Math.max(60, median * 4) && !gaps.some(g => g.from <= axis[i] && g.to >= t))
        gaps.push({from: axis[i], to: t, kind: "samples", label: "Intervallo senza campioni mostrati"});
    });
    return {values, axis, timed, gaps, domain: axis.length ? [axis[0], axis[axis.length - 1]] : [0, 0]};
  }
  /** Clamp a selected interval to the actual domain and keep a usable minimum span. */
  function clampRange(range, domain) {
    const [lo, hi] = domain;
    if (!range || !finite(range[0]) || !finite(range[1]) || hi <= lo) return [lo, hi];
    const from = Math.max(lo, Math.min(hi, range[0]));
    const to = Math.max(lo, Math.min(hi, range[1]));
    return from < to ? [from, to] : [lo, hi];
  }
  /** Zoom around an observed cursor, bounded to the original signal domain. */
  function zoomRange(current, domain, factor, center) {
    const [from, to] = clampRange(current, domain);
    if (!(to > from) || !finite(factor) || factor <= 0) return [from, to];
    const full = domain[1] - domain[0], width = Math.min(full, Math.max(full / 1000, (to - from) * factor));
    const pivot = finite(center) ? Math.max(from, Math.min(to, center)) : (from + to) / 2;
    const left = Math.max(domain[0], Math.min(domain[1] - width, pivot - width / 2));
    return [left, left + width];
  }
  /** Exclude points outside the range, splitting paths across missing observations. */
  function visibleSeries(series, range) {
    const [from, to] = range && finite(range[0]) && finite(range[1]) && range[0] < range[1] ? range : series.domain;
    const indices = series.values.map((value, index) => ({value, index, time: series.axis[index]}))
      .filter(point => point.time >= from && point.time <= to);
    const segments = [];
    let segment = [], previous = null;
    indices.forEach(point => {
      const interrupted = previous && series.gaps.some(gap => previous.time < gap.to && point.time > gap.from);
      if (point.value == null || interrupted) {
        if (segment.length) segments.push(segment);
        segment = [];
      }
      if (point.value != null) segment.push(point);
      previous = point;
    });
    if (segment.length) segments.push(segment);
    const points = indices.filter(point => point.value != null);
    return {points, segments, gaps: series.gaps.filter(g => g.to > from && g.from < to),
      min: points.length ? Math.min(...points.map(p => p.value)) : null,
      max: points.length ? Math.max(...points.map(p => p.value)) : null};
  }
  /** Locate recording gaps from observed windows without guessing engine shutdown. */
  function recordingGaps(trip) {
    const windows = Array.isArray(trip?.observedWindows) ? trip.observedWindows
      .filter(w => Array.isArray(w) && finite(w[0]) && finite(w[1]) && w[1] >= w[0])
      .map(w => w.slice()).sort((a, b) => a[0] - b[0]) : [];
    const merged = [];
    windows.forEach(window => {
      const last = merged[merged.length - 1];
      if (last && window[0] <= last[1]) last[1] = Math.max(last[1], window[1]);
      else merged.push(window);
    });
    return merged.slice(1).filter((window, index) => window[0] > merged[index][1])
      .map((window, index) => ({from: merged[index][1], to: window[0], kind: "recording", label: "Buco nella registrazione"}));
  }
  /** Show ECU flag samples, never upgrading a sampled flag to a confirmed DPF episode. */
  function dpfObservations(trip) {
    const series = prepareSeries(trip?.pidSeriesFull?.regen_st, trip?.pidSeriesTimes?.regen_st);
    if (!series.timed) return [];
    return series.values.flatMap((value, index) => value != null && value >= 1
      ? [{from: series.axis[index], to: series.axis[index], kind: "dpf", label: "Segnale ECU DPF ≥ 1 (campione)"}] : []);
  }
  /** Rank measured conditions, with an optional proximity bonus for recorded GPS endpoints. */
  function similarTrips(reference, trips, limit = 5) {
    if (!reference || reference.legacyIncomplete) return [];
    const definitions = [["distanceKm", 1], ["durationMin", 1], ["airTempC", 10]];
    return trips.filter(trip => trip.id !== reference.id && !trip.legacyIncomplete).map(trip => {
      const compared = definitions.filter(([key]) => finite(reference[key]) && finite(trip[key])
        && (key === "airTempC" || reference[key] > 0 && trip[key] > 0));
      const score = compared.reduce((sum, [key, scale]) => sum + Math.abs(trip[key] - reference[key]) /
        (key === "airTempC" ? scale : Math.max(Math.abs(reference[key]), scale)), 0) / (compared.length || 1)
        + (definitions.length - compared.length) * 0.25;
      const endpoints = routeEvidence({track: [reference.routeStart, reference.routeEnd]},
        {track: [trip.routeStart, trip.routeEnd]});
      // At most a small bonus when BOTH recorded endpoints are within 500 m.
      // Absent or distant GPS leaves the conditions-only score unchanged.
      const endpointBonus = endpoints.available ? 0.15 * Math.max(0, 1 - Math.max(endpoints.startMeters, endpoints.endMeters) / 500) : 0;
      return {trip, score: score - endpointBonus, compared: compared.map(([key]) => key),
        routeUsed: false, endpoints, endpointBonus};
    }).filter(candidate => candidate.compared.length > 0)
      .sort((a, b) => a.score - b.score || String(a.trip.id).localeCompare(String(b.trip.id)))
      .slice(0, limit);
  }
  /** Derive source coverage for comparisons without upgrading unknown completeness. */
  function fuelObservation(trip) {
    const coverage = finite(trip?.fuelCoveragePct) && trip.fuelCoveragePct >= 0 && trip.fuelCoveragePct <= 100 ? trip.fuelCoveragePct : null;
    const distance = finite(trip?.fuelDistanceKm) ? trip.fuelDistanceKm : trip?.fuelSource === "myopel" && trip?.sources?.includes("obd")
      ? trip.myopDistanceKm : trip?.distanceKm;
    const liters = trip?.fuelConsumedL;
    return {source: trip?.fuelSource || null, coverage, kmL: finite(distance) && distance > 0 && finite(liters) && liters > 0
      ? distance / liters : null, distance: finite(distance) ? distance : null};
  }
  /** Distinguish descriptive differences from comparisons with incompatible evidence. */
  function comparisonLimits(a, b) {
    const notes = [], first = fuelObservation(a), second = fuelObservation(b);
    if (a?.legacyIncomplete || b?.legacyIncomplete) notes.push("Storico legacy incompleto: confronto quantitativo non affidabile");
    if (!first.source || !second.source || first.source !== second.source) notes.push("Fonti dei consumi diverse o non identificate");
    if (first.coverage == null || second.coverage == null) notes.push("Copertura dei consumi non disponibile per entrambi");
    else if (Math.min(first.coverage, second.coverage) < 90) notes.push("Almeno un consumo copre meno del 90% del viaggio");
    if (!finite(a?.airTempC) || !finite(b?.airTempC)) notes.push("Temperatura esterna non disponibile per entrambi");
    else if (Math.abs(a.airTempC - b.airTempC) > 5) notes.push("Temperatura esterna diversa di oltre 5 °C");
    for (const [key, label] of [["distanceKm", "Distanza"], ["durationMin", "Durata osservata"]]) {
      if (!finite(a?.[key]) || !finite(b?.[key])) notes.push(`${label} non disponibile per entrambi`);
      else if (Math.abs(a[key] - b[key]) / Math.max(a[key], b[key], 1) > 0.2) notes.push(`${label} diversa di oltre il 20%`);
    }
    if ((a?.recordingGapSeconds || 0) > 0 || (b?.recordingGapSeconds || 0) > 0) notes.push("Sono presenti intervalli non registrati");
    return {notes, first, second, economyComparable: notes.length === 0 && first.kmL != null && second.kmL != null};
  }
  /** Compare recorded endpoints only; nearby endpoints do not prove the same route. */
  function routeEvidence(a, b) {
    const valid = trip => Array.isArray(trip?.track) ? trip.track.filter(p => Array.isArray(p) && p.length >= 2 && finite(p[0]) && finite(p[1])
      && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180 && (p[0] !== 0 || p[1] !== 0)) : [];
    const first = valid(a), second = valid(b);
    if (first.length < 2 || second.length < 2) return {available: false, startMeters: null, endMeters: null};
    const meters = (p, q) => {
      const rad = x => x * Math.PI / 180, dlat = rad(q[0] - p[0]), dlon = rad(q[1] - p[1]);
      const h = Math.sin(dlat / 2) ** 2 + Math.cos(rad(p[0])) * Math.cos(rad(q[0])) * Math.sin(dlon / 2) ** 2;
      return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
    };
    return {available: true, startMeters: meters(first[0], second[0]), endMeters: meters(first[first.length - 1], second[second.length - 1])};
  }
  /** Preserve explicit evidence references supplied by the rule engine or parent view. */
  function insightEvidence(insight) {
    const refs = insight?.evidence || {};
    const ids = (Array.isArray(refs.tripIds) ? refs.tripIds : [insight?.tripId]).filter(id => typeof id === "string" && id.length > 0);
    const slugs = (Array.isArray(refs.pidSlugs) ? refs.pidSlugs : []).filter(slug => typeof slug === "string" && slug.length > 0);
    return {tripIds: [...new Set(ids)], pidSlugs: [...new Set(slugs)], sampleCount: finite(refs.sampleCount) ? refs.sampleCount : null};
  }
  const api = {prepareSeries, clampRange, zoomRange, visibleSeries, recordingGaps, dpfObservations, similarTrips,
    fuelObservation, comparisonLimits, routeEvidence, insightEvidence};
  root.OBDExploration = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
