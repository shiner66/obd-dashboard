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
  /** Keep a selection only when it still belongs to the visible result set. */
  function selectedTrip(trips, selectedId) {
    return trips.find(t => t.id === selectedId) || trips[0] || null;
  }
  /** Reconcile tabs when a different trip lacks OBD details. */
  function tripTab(tab, trip) {
    return ["dpf", "pids"].includes(tab) && !trip?.sources?.includes("obd") ? "overview" : tab;
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
  const api = { chartValues, localDateTime, weightedEconomy, tripEconomyDistance, selectedTrip, tripTab, measurement, validateSnapshot, chartPositions, restoreTweaks, ingestionStatus };
  root.OBD = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
