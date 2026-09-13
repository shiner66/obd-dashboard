/* Pure helpers shared by the browser and the regression tests. */
(function (root) {
  "use strict";
  /** Keep every finite observation, including diagnostic peaks and cold starts. */
  function chartValues(values) {
    return Array.isArray(values) ? values.map(v => typeof v === "number" && Number.isFinite(v) ? v : null) : [];
  }
  /** Format a datetime-local value without converting the user's clock to UTC. */
  function localDateTime(date = new Date()) {
    const pad = n => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  /** Combine economy only across intervals with positive distance and fuel. */
  function weightedEconomy(intervals) {
    const valid = intervals.filter(x => Number.isFinite(x.km) && x.km > 0 && Number.isFinite(x.liters) && x.liters > 0);
    const liters = valid.reduce((sum, x) => sum + x.liters, 0);
    return liters > 0 ? valid.reduce((sum, x) => sum + x.km, 0) / liters : null;
  }
  /** Use the distance covered by the selected fuel source when aggregating trips. */
  function tripEconomyDistance(trip) {
    return trip.fuelDistanceKm ?? (trip.fuelSource === "myopel" && trip.sources?.includes("obd")
      ? trip.myopDistanceKm : trip.distanceKm);
  }
  /** Match the server's exclusion of legacy records from numerical comparisons. */
  function usableForAnalysis(trip) {
    return !trip.legacyIncomplete && !(trip.sources?.includes("obd") && (trip.parserVersion || 0) < 2);
  }
  /** Use server aggregates, with an equally conservative fallback for older snapshots. */
  function periodMetrics(trips, aggregates) {
    const valid = trips.filter(usableForAnalysis);
    const fueled = valid.filter(trip => Number.isFinite(trip.fuelConsumedL) && trip.fuelConsumedL >= 0 && trip.fuelDistanceKm > 0);
    const costed = valid.filter(trip => Number.isFinite(trip.costEur) && trip.costDistanceKm > 0);
    const observed = valid.filter(trip => Number.isFinite(trip.durationMin));
    const sum = (items, key) => items.reduce((total, trip) => total + (Number.isFinite(trip[key]) ? trip[key] : 0), 0);
    const fuelLiters = sum(fueled, "fuelConsumedL"), fuelDistanceKm = sum(fueled, "fuelDistanceKm");
    const fallback = { tripCount: trips.length, totalKm: sum(trips, "distanceKm"), observedMinutes: sum(observed, "durationMin"),
      observedTripCount: observed.length, fuelTripCount: fueled.length, fuelLiters, fuelDistanceKm,
      consumptionKmL: fuelLiters > 0 ? fuelDistanceKm / fuelLiters : null,
      costTripCount: costed.length, costEur: sum(costed, "costEur"), costDistanceKm: sum(costed, "costDistanceKm"),
      excludedFromComparisons: trips.length - valid.length };
    return { ...fallback, ...aggregates };
  }
  /** Keep a selection only when it still belongs to the visible result set. */
  function selectedTrip(trips, selectedId) {
    return trips.find(t => t.id === selectedId) || trips[0] || null;
  }
  /** Reconcile tabs when a different trip lacks OBD details. */
  function tripTab(tab, trip) {
    return ["dpf", "pids", "analysis"].includes(tab) && !trip?.sources?.includes("obd") ? "overview" : tab;
  }
  /** Unknown measurements remain unknown; never substitute a reassuring zero. */
  function measurement(value, decimals = 1) {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("it-IT", { maximumFractionDigits: decimals }) : "—";
  }
  /** Reject incomplete snapshots before replacing the last successfully loaded data. */
  function validateSnapshot(data) {
    if (!data || !data.vehicle || !Array.isArray(data.trips) || !Array.isArray(data.pidCatalog)
      || !Array.isArray(data.trendInsights) || !data.settings || !data.fuel || !data.alerts || !data.pidGroups) {
      throw new Error("Risposta dashboard incompleta");
    }
    return data;
  }
  /** Supply elapsed seconds when present, otherwise explicitly use sample indices. */
  function chartPositions(values, times) {
    const validTimes = Array.isArray(times) && times.length === values.length
      && times.every((t, i) => Number.isFinite(t) && (i === 0 || t >= times[i - 1]));
    return { times: validTimes ? times : values.map((_, i) => i), timed: validTimes };
  }
  /** Validate and selectively restore only known appearance settings. */
  function restoreTweaks(defaults, saved) {
    const options = { theme: ["midnight", "graphite", "warm", "lights-out"], accent: ["cyan", "amber", "violet", "mint", "magenta", "sky"],
      density: ["compact", "regular", "spacious"], anim: ["off", "subtle", "playful"], sidebar: ["auto", "full", "compact"] };
    const values = { ...defaults };
    for (const [key, choices] of Object.entries(options)) if (choices.includes(saved?.[key])) values[key] = saved[key];
    return values;
  }
  /** Present ingestion availability without turning missing status into zero errors. */
  function ingestionStatus(meta) {
    const ingestion = meta?.ingestion;
    if (!ingestion) return null;
    const ids = Array.isArray(meta.legacyRebuildIds) ? meta.legacyRebuildIds : [];
    return {
      running: typeof ingestion.running === "boolean" ? ingestion.running : null,
      pending: Number.isFinite(ingestion.pending) ? ingestion.pending : null,
      errors: Array.isArray(ingestion.errors) ? ingestion.errors.map(error => ({
        file: String(error.file || "File non identificato").split(/[\\/]/).pop(),
        message: String(error.error || "Errore di importazione non specificato"),
      })) : null,
      legacyCount: Number.isFinite(meta.legacyRebuildNeeded) ? meta.legacyRebuildNeeded : null,
      legacyIds: ids,
    };
  }
  /** Validate a calendar date without UTC conversion or silently fixing impossible days. */
  function calendarDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const [year, month, day] = value.split("-").map(Number), date = new Date(year, month - 1, day, 12);
    return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day;
  }
  /** Resolve an inclusive local-calendar period; rolling periods include today. */
  function periodRange(selection = { preset: "30d" }, now = new Date()) {
    const preset = ["7d", "30d", "month", "custom", "all"].includes(selection?.preset) ? selection.preset : "30d";
    const today = localDateTime(now).slice(0, 10);
    if (preset === "all") return { preset, fromDate: null, toDate: null, label: "Tutto lo storico", query: "" };
    let fromDate, toDate = today;
    if (preset === "custom") {
      fromDate = selection.fromDate; toDate = selection.toDate;
      if (!calendarDate(fromDate) || !calendarDate(toDate)) return { preset, error: "Indica entrambe le date del periodo." };
      if (fromDate > toDate) return { preset, error: "La data iniziale deve precedere quella finale." };
    } else {
      const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
      if (preset === "month") start.setDate(1); else start.setDate(start.getDate() - (preset === "7d" ? 6 : 29));
      fromDate = localDateTime(start).slice(0, 10);
    }
    const format = value => { const [y, m, d] = value.split("-"); return `${d}/${m}/${y}`; };
    return { preset, fromDate, toDate, label: `${format(fromDate)} – ${format(toDate)}`,
      query: `?from_date=${fromDate}&to_date=${toDate}` };
  }
  /** Restore only complete, valid saved periods; use 30 days for invalid storage. */
  function restorePeriod(saved) {
    if (!saved || !["7d", "30d", "month", "custom", "all"].includes(saved.preset)) return { preset: "30d" };
    if (periodRange(saved).error) return { preset: "30d" };
    return { preset: saved.preset, ...(saved.preset === "custom" ? { fromDate: saved.fromDate, toDate: saved.toDate } : {}) };
  }
  /** Keep asynchronous period responses from committing after a newer selection. */
  function requestGate() {
    let generation = 0, key = null;
    return {
      select(nextKey) { if (key !== nextKey) { key = nextKey; generation += 1; } return generation; },
      current(ticket) { return ticket === generation; },
      ticket() { return generation; },
    };
  }
  /** Slice a list into bounded pages and reconcile a page after filters shrink it. */
  function pageItems(items, requestedPage = 1, size = 20) {
    const pageSize = Math.max(1, Math.floor(size)), pages = Math.max(1, Math.ceil(items.length / pageSize));
    const page = Math.max(1, Math.min(pages, Math.floor(requestedPage) || 1));
    const start = (page - 1) * pageSize;
    return { items: items.slice(start, start + pageSize), page, pages, total: items.length, start: items.length ? start + 1 : 0, end: Math.min(start + pageSize, items.length) };
  }
  /** Translate standard API validation payloads into readable inline messages. */
  function apiError(detail, fallback = "Operazione non riuscita") {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map(error => {
      const field = { ts: "Data e ora", odometerKm: "Odometro", liters: "Litri", pricePerL: "Prezzo", fuelType: "Carburante", fullTank: "Pieno" }[error.loc?.slice(-1)[0]] || "Valore";
      const messages = { datetime_from_date_parsing: "data e ora non valide", datetime_parsing: "data e ora non valide",
        float_parsing: "inserisci un numero", int_parsing: "inserisci un numero intero", finite_number: "inserisci un numero finito",
        missing: "campo obbligatorio", greater_than: `il valore deve superare ${error.ctx?.gt ?? "il minimo"}`,
        greater_than_equal: `il valore minimo è ${error.ctx?.ge ?? "quello indicato"}`, less_than_equal: `il valore massimo è ${error.ctx?.le ?? "quello indicato"}` };
      return `${field}: ${messages[error.type] || error.msg?.replace(/^Value error, /, "") || "controlla il valore inserito"}`;
    }).join(" · ") || fallback;
    return fallback;
  }
  /** Normalize an existing refuel for datetime-local without appending seconds twice. */
  function refuelDraft(refuel, odometer, now = new Date()) {
    return { ts: refuel?.ts ? refuel.ts.replace(" ", "T").slice(0, 16) : localDateTime(now),
      odometerKm: refuel?.odometerKm ?? odometer ?? "", liters: refuel?.liters ?? "", pricePerL: refuel?.pricePerL ?? "",
      fuelType: refuel?.fuelType || "B7", fullTank: refuel?.fullTank ?? true, note: refuel?.note || "" };
  }
  /** Validate and serialize the complete refuel edit using a local ISO timestamp. */
  function refuelPayload(form) {
    const fail = text => { throw new Error(text); };
    if (!form.ts || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(form.ts) || !calendarDate(form.ts.slice(0, 10))) fail("Inserisci una data e un'ora valide.");
    const [hour, minute] = form.ts.slice(11).split(":").map(Number);
    if (hour > 23 || minute > 59) fail("Inserisci un'ora valida.");
    const number = (value, label, min, max, optional) => {
      if (value === "" && optional) return null;
      const n = Number(value);
      if (!Number.isFinite(n) || n < min || n > max || (value === "" && !optional)) fail(`${label}: inserisci un valore tra ${min} e ${max}.`);
      return n;
    };
    return { ts: form.ts + ":00", liters: number(form.liters, "Litri", 0.01, 200, false),
      odometerKm: number(form.odometerKm, "Odometro", 0, 2000000, true), pricePerL: number(form.pricePerL, "Prezzo", 0, 100, true),
      fuelType: form.fuelType, fullTank: Boolean(form.fullTank), note: form.note.trim() || null };
  }
  /** Aggregate the complete selected interval, using months for long histories. */
  function activitySeries(trips, range) {
    const dates = trips.map(trip => trip.start?.slice(0, 10)).filter(calendarDate).sort();
    if (!dates.length) return { data: [], unit: "giorno" };
    const from = range.fromDate || dates[0], to = range.toDate || dates[dates.length - 1];
    const start = new Date(from + "T12:00:00"), end = new Date(to + "T12:00:00");
    const monthly = (end - start) / 86400000 > 62, totals = new Map();
    trips.forEach(trip => {
      if (!trip.start) return;
      const key = trip.start.slice(0, monthly ? 7 : 10);
      totals.set(key, (totals.get(key) || 0) + (Number.isFinite(trip.distanceKm) ? trip.distanceKm : 0));
    });
    if (monthly) start.setDate(1);
    const data = [];
    for (const cursor = new Date(start); cursor <= end; monthly ? cursor.setMonth(cursor.getMonth() + 1) : cursor.setDate(cursor.getDate() + 1)) {
      const key = localDateTime(cursor).slice(0, monthly ? 7 : 10);
      data.push({ label: cursor.toLocaleDateString("it-IT", monthly ? { month: "short", year: "2-digit" } : { day: "2-digit", month: "short" }), value: totals.get(key) || 0 });
    }
    return { data, unit: monthly ? "mese" : "giorno" };
  }
  const api = { chartValues, localDateTime, weightedEconomy, tripEconomyDistance, usableForAnalysis, periodMetrics, selectedTrip, tripTab, measurement, validateSnapshot, chartPositions, restoreTweaks, ingestionStatus,
    calendarDate, periodRange, restorePeriod, requestGate, pageItems, apiError, refuelDraft, refuelPayload, activitySeries };
  root.OBD = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
