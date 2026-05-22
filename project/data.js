// Mock trip data based on the real corpus described in the briefing.
// GPS tracks are simplified polylines within the real bbox (Salerno area).
// All ranges respect the post-RBS values from the briefing.

const VEHICLE = {
  name: "Peugeot 308 SW",
  ecu: "MD1CS003 — 1.5 BlueHDi",
  adapter: "BTLE IOS-Vlink",
  vin: "VF3LCYHZRMS123456",
  odometer: 17162,
  fuelLevel: 42,
  fuelAutonomy: 318,
  adblueRange: 4280,
  nextService: { days: 142, km: 9_840, passed: false },
  dpfSoot: 64,
  dpfAvgRegenKm: 258,
  dpfSinceRegenKm: 142,
  battery: 12.42,
};

// Helper: synthesize a GPS polyline between two anchors with mild jitter.
function makeTrack(a, b, n, jitter = 0.0008) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    // Slight curve via sine
    const curve = Math.sin(t * Math.PI) * 0.004;
    const lat = a[0] + (b[0] - a[0]) * t + (Math.random() - 0.5) * jitter + curve * 0.3;
    const lon = a[1] + (b[1] - a[1]) * t + (Math.random() - 0.5) * jitter + curve * 0.5;
    out.push([lat, lon]);
  }
  return out;
}

// Some named points around Salerno province bbox
const POINTS = {
  salerno:    [40.6824, 14.7681],
  cava:       [40.7011, 14.7060],
  battipaglia:[40.6010, 14.9870],
  pontecagnano:[40.6480, 14.8730],
  vietri:     [40.6730, 14.7280],
  baronissi:  [40.7470, 14.7700],
  eboli:      [40.6160, 15.0560],
  paestum:    [40.4490, 15.0030],
  fisciano:   [40.7700, 14.7920],
  agropoli:   [40.3540, 14.9920],
  nocera:     [40.7440, 14.6440],
  home:       [40.5720, 14.8540],
};

// Helper: timestamp string from local datetime
const T = (s) => s; // pass-through for clarity

// 14 trips — mix of OBD only, MyOpel only, and merged.
// Order: most recent first (will be sorted by start).
const TRIPS = [
  {
    id: "obd-2026-05-20-19-57-16",
    sources: ["obd", "myopel"],
    filename: "2026-05-20 19-57-16.csv",
    myopId: 247,
    start: T("2026-05-20T19:57:16"),
    end:   T("2026-05-20T20:12:34"),
    durationMin: 15.3,
    distanceKm: 11.4,
    avgSpeedKmh: 44.7,
    maxSpeedKmh: 87,
    avgRpm: 1640,
    maxRpm: 2980,
    coolantMaxC: 91,
    oilMaxC: 88.4,
    odometerKm: 17162,
    airTempC: 21.5,
    fuelConsumedL: 0.61,
    consumptionL100km: 5.4,
    dpfSootPct: 64,
    dpfClosedSoot: 18.4,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 142,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_300,
    dpfRegenCapability: 92,
    dpfRegenCapabilityST: 88,
    adblueVolL: 17.2,
    adblueRangeKm: 4280,
    exhaustBeforeCatC: 412,
    exhaustAfterCatC: 388,
    noxCatTempMaxC: 296,
    batteryStartupV: 12.4,
    oilDilutionPct: 1.2,
    ssState: 1,
    alerts: [],
    track: makeTrack(POINTS.home, POINTS.pontecagnano, 90),
    pidSeries: null, // generated below
  },
  {
    id: "obd-2026-05-20-18-35-32",
    sources: ["obd", "myopel"],
    filename: "2026-05-20 18-35-32.csv",
    myopId: 246,
    start: T("2026-05-20T18:35:32"),
    end:   T("2026-05-20T19:06:38"),
    durationMin: 31.1,
    distanceKm: 28.7,
    avgSpeedKmh: 55.4,
    maxSpeedKmh: 121,
    avgRpm: 1820,
    maxRpm: 3340,
    coolantMaxC: 94,
    oilMaxC: 90.9,
    odometerKm: 17150,
    airTempC: 23.0,
    fuelConsumedL: 1.42,
    consumptionL100km: 4.95,
    dpfSootPct: 58,
    dpfClosedSoot: 16.1,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 131,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_320,
    dpfRegenCapability: 92,
    dpfRegenCapabilityST: 89,
    adblueVolL: 17.3,
    adblueRangeKm: 4310,
    exhaustBeforeCatC: 462,
    exhaustAfterCatC: 441,
    noxCatTempMaxC: 348,
    batteryStartupV: 12.5,
    oilDilutionPct: 1.1,
    ssState: 1,
    alerts: [52],
    track: makeTrack(POINTS.salerno, POINTS.battipaglia, 140),
  },
  {
    id: "obd-2026-05-20-18-22-58",
    sources: ["obd"],
    filename: "2026-05-20 18-22-58.csv",
    start: T("2026-05-20T18:22:58"),
    end:   T("2026-05-20T18:35:28"),
    durationMin: 12.5,
    distanceKm: 9.2,
    avgSpeedKmh: 44.1,
    maxSpeedKmh: 78,
    avgRpm: 1580,
    maxRpm: 2740,
    coolantMaxC: 88,
    oilMaxC: 82.4,
    odometerKm: 17122,
    airTempC: 22.8,
    fuelConsumedL: 0.48,
    consumptionL100km: 5.2,
    dpfSootPct: 56,
    dpfClosedSoot: 15.4,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 122,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_350,
    dpfRegenCapability: 91,
    dpfRegenCapabilityST: 86,
    adblueVolL: 17.3,
    adblueRangeKm: 4320,
    exhaustBeforeCatC: 388,
    exhaustAfterCatC: 362,
    noxCatTempMaxC: 281,
    batteryStartupV: 12.5,
    oilDilutionPct: 1.1,
    ssState: 1,
    alerts: [],
    track: makeTrack(POINTS.pontecagnano, POINTS.salerno, 80),
  },
  {
    id: "obd-2026-05-20-17-59-25",
    sources: ["obd", "myopel"],
    filename: "2026-05-20 17-59-25.csv",
    myopId: 245,
    start: T("2026-05-20T17:59:25"),
    end:   T("2026-05-20T18:14:37"),
    durationMin: 15.2,
    distanceKm: 14.6,
    avgSpeedKmh: 57.6,
    maxSpeedKmh: 109,
    avgRpm: 1960,
    maxRpm: 3120,
    coolantMaxC: 92,
    oilMaxC: 86.0,
    odometerKm: 17113,
    airTempC: 24.0,
    fuelConsumedL: 0.79,
    consumptionL100km: 5.41,
    dpfSootPct: 54,
    dpfClosedSoot: 14.8,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 113,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_360,
    dpfRegenCapability: 92,
    dpfRegenCapabilityST: 88,
    adblueVolL: 17.4,
    adblueRangeKm: 4360,
    exhaustBeforeCatC: 433,
    exhaustAfterCatC: 401,
    noxCatTempMaxC: 312,
    batteryStartupV: 12.6,
    oilDilutionPct: 1.0,
    ssState: 1,
    alerts: [],
    track: makeTrack(POINTS.cava, POINTS.battipaglia, 110),
  },
  {
    id: "obd-2026-05-20-16-39-54",
    sources: ["obd", "myopel"],
    filename: "2026-05-20 16-39-54.csv",
    myopId: 244,
    start: T("2026-05-20T16:39:54"),
    end:   T("2026-05-20T16:57:54"),
    durationMin: 18.0,
    distanceKm: 12.4,
    avgSpeedKmh: 41.3,
    maxSpeedKmh: 96,
    avgRpm: 1710,
    maxRpm: 2890,
    coolantMaxC: 90,
    oilMaxC: 84.2,
    odometerKm: 17098,
    airTempC: 25.1,
    fuelConsumedL: 0.66,
    consumptionL100km: 5.32,
    dpfSootPct: 50,
    dpfClosedSoot: 13.6,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 98,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_380,
    dpfRegenCapability: 91,
    dpfRegenCapabilityST: 87,
    adblueVolL: 17.4,
    adblueRangeKm: 4370,
    exhaustBeforeCatC: 401,
    exhaustAfterCatC: 376,
    noxCatTempMaxC: 304,
    batteryStartupV: 12.5,
    oilDilutionPct: 1.0,
    ssState: 1,
    alerts: [],
    track: makeTrack(POINTS.home, POINTS.salerno, 100),
  },
  {
    id: "obd-2026-05-20-01-18-43",
    sources: ["obd"],
    filename: "2026-05-20 01-18-43.csv",
    start: T("2026-05-20T01:18:43"),
    end:   T("2026-05-20T01:24:50"),
    durationMin: 6.1,
    distanceKm: 3.8,
    avgSpeedKmh: 37.5,
    maxSpeedKmh: 64,
    avgRpm: 1480,
    maxRpm: 2480,
    coolantMaxC: 76,
    oilMaxC: 64.1,
    odometerKm: 17086,
    airTempC: 17.5,
    fuelConsumedL: 0.27,
    consumptionL100km: 7.1,
    dpfSootPct: 47,
    dpfClosedSoot: 12.4,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 86,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_400,
    dpfRegenCapability: 90,
    dpfRegenCapabilityST: 64,
    adblueVolL: 17.4,
    adblueRangeKm: 4380,
    exhaustBeforeCatC: 312,
    exhaustAfterCatC: 284,
    noxCatTempMaxC: 218,
    batteryStartupV: 12.3,
    oilDilutionPct: 1.4,
    ssState: 1,
    alerts: [],
    track: makeTrack(POINTS.salerno, POINTS.vietri, 50),
  },
  {
    id: "obd-2026-05-20-01-10-38",
    sources: ["obd"],
    filename: "2026-05-20 01-10-38.csv",
    start: T("2026-05-20T01:10:38"),
    end:   T("2026-05-20T01:18:31"),
    durationMin: 7.9,
    distanceKm: 5.4,
    avgSpeedKmh: 41.0,
    maxSpeedKmh: 72,
    avgRpm: 1520,
    maxRpm: 2590,
    coolantMaxC: 82,
    oilMaxC: 71.2,
    odometerKm: 17082,
    airTempC: 17.2,
    fuelConsumedL: 0.34,
    consumptionL100km: 6.3,
    dpfSootPct: 45,
    dpfClosedSoot: 11.8,
    dpfRegenActive: 0,
    dpfRegenState: "idle",
    dpfSinceRegenKm: 82,
    dpfAvgRegenKm: 258,
    dpfReplaceKm: 89_410,
    dpfRegenCapability: 90,
    dpfRegenCapabilityST: 68,
    adblueVolL: 17.5,
    adblueRangeKm: 4390,
    exhaustBeforeCatC: 354,
    exhaustAfterCatC: 318,
    noxCatTempMaxC: 248,
    batteryStartupV: 12.4,
    oilDilutionPct: 1.4,
    ssState: 1,
    alerts: [],
    track: makeTrack(POINTS.vietri, POINTS.cava, 60),
  },
  {
    id: "obd-2026-05-19-23-21-20",
    sources: ["obd", "myopel"],
    filename: "2026-05-19 23-21-20.csv",
    myopId: 240,
    start: T("2026-05-19T23:21:20"),
    end:   T("2026-05-20T00:31:44"),
    durationMin: 70.4,
    distanceKm: 71.2,
    avgSpeedKmh: 60.6,
    maxSpeedKmh: 134,
    avgRpm: 1910,
    maxRpm: 3420,
    coolantMaxC: 96,
    oilMaxC: 89.7,
    odometerKm: 17077,
    airTempC: 18.2,
    fuelConsumedL: 3.62,
    consumptionL100km: 5.08,
    dpfSootPct: 12,
    dpfClosedSoot: 2.4,
    dpfRegenActive: 1,
    dpfRegenState: "completed",
    dpfSinceRegenKm: 6,
    dpfAvgRegenKm: 262,
    dpfReplaceKm: 89_410,
    dpfRegenCapability: 92,
    dpfRegenCapabilityST: 91,
    adblueVolL: 17.6,
    adblueRangeKm: 4420,
    exhaustBeforeCatC: 684,
    exhaustAfterCatC: 712,
    noxCatTempMaxC: 590,
    batteryStartupV: 12.5,
    oilDilutionPct: 1.0,
    ssState: 1,
    alerts: [57], // regen in corso = modalità elettrica n.d. (sarebbe ibrida)
    track: makeTrack(POINTS.salerno, POINTS.paestum, 240),
    regenEventTs: 0.35, // 35% of duration
  },
  {
    id: "obd-2026-05-19-21-16-39",
    sources: ["obd", "myopel"],
    filename: "2026-05-19 21-16-39.csv",
    myopId: 239,
    start: T("2026-05-19T21:16:39"),
    end:   T("2026-05-19T21:40:33"),
    durationMin: 23.9,
    distanceKm: 19.4,
    avgSpeedKmh: 48.7,
    maxSpeedKmh: 102,
    avgRpm: 1780,
    maxRpm: 3050,
    coolantMaxC: 93,
    oilMaxC: 87.1,
    odometerKm: 17005,
    airTempC: 19.4,
    fuelConsumedL: 1.04,
    consumptionL100km: 5.36,
    dpfSootPct: 88,
    dpfClosedSoot: 22.6,
    dpfRegenActive: 1,
    dpfRegenState: "requested",
    dpfSinceRegenKm: 256,
    dpfAvgRegenKm: 260,
    dpfReplaceKm: 89_460,
    dpfRegenCapability: 92,
    dpfRegenCapabilityST: 84,
    adblueVolL: 17.7,
    adblueRangeKm: 4460,
    exhaustBeforeCatC: 488,
    exhaustAfterCatC: 462,
    noxCatTempMaxC: 358,
    batteryStartupV: 12.4,
    oilDilutionPct: 0.9,
    ssState: 1,
    alerts: [27],
    track: makeTrack(POINTS.home, POINTS.salerno, 140),
  },
  // MyOpel-only trips (no OBD log) — earlier history
  {
    id: "myop-238",
    sources: ["myopel"],
    myopId: 238,
    start: T("2026-05-18T08:12:00"),
    end:   T("2026-05-18T08:34:00"),
    durationMin: 22.0,
    distanceKm: 18.6,
    avgSpeedKmh: 50.7,
    fuelConsumedL: 0.97,
    consumptionL100km: 5.22,
    fuelLevel: 71,
    fuelAutonomy: 540,
    priceFuel: 1.78,
    costEur: 1.73,
    alerts: [],
    odometerKm: 16985,
  },
  {
    id: "myop-237",
    sources: ["myopel"],
    myopId: 237,
    start: T("2026-05-17T17:45:00"),
    end:   T("2026-05-17T18:09:00"),
    durationMin: 24.0,
    distanceKm: 20.4,
    avgSpeedKmh: 51.0,
    fuelConsumedL: 1.08,
    consumptionL100km: 5.29,
    fuelLevel: 76,
    fuelAutonomy: 590,
    priceFuel: 1.78,
    costEur: 1.92,
    alerts: [46],
    odometerKm: 16966,
  },
  {
    id: "myop-236",
    sources: ["myopel"],
    myopId: 236,
    start: T("2026-05-16T09:20:00"),
    end:   T("2026-05-16T09:48:00"),
    durationMin: 28.0,
    distanceKm: 23.8,
    avgSpeedKmh: 51.0,
    fuelConsumedL: 1.18,
    consumptionL100km: 4.96,
    fuelLevel: 81,
    fuelAutonomy: 640,
    priceFuel: 1.78,
    costEur: 2.10,
    alerts: [],
    odometerKm: 16946,
  },
  {
    id: "myop-235",
    sources: ["myopel"],
    myopId: 235,
    start: T("2026-05-15T07:55:00"),
    end:   T("2026-05-15T08:21:00"),
    durationMin: 26.0,
    distanceKm: 21.4,
    avgSpeedKmh: 49.4,
    fuelConsumedL: 1.13,
    consumptionL100km: 5.28,
    fuelLevel: 86,
    fuelAutonomy: 690,
    priceFuel: 1.78,
    costEur: 2.01,
    alerts: [],
    odometerKm: 16922,
  },
  {
    id: "myop-234",
    sources: ["myopel"],
    myopId: 234,
    start: T("2026-05-14T18:30:00"),
    end:   T("2026-05-14T19:11:00"),
    durationMin: 41.0,
    distanceKm: 36.2,
    avgSpeedKmh: 52.9,
    fuelConsumedL: 1.79,
    consumptionL100km: 4.94,
    fuelLevel: 91,
    fuelAutonomy: 745,
    priceFuel: 1.78,
    costEur: 3.19,
    alerts: [22],
    odometerKm: 16900,
  },
];

// Generate synthetic PID time-series for OBD trips for sparklines
function genSeries(trip, slug) {
  const n = 60;
  const t0 = 0;
  const out = [];
  const rand = (seed => () => {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  })(trip.id.length * 17 + slug.length);

  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    let v = 0;
    switch (slug) {
      case "rpm": {
        const base = trip.avgRpm || 1700;
        const peak = trip.maxRpm || 3000;
        v = base + (peak - base) * Math.pow(Math.sin(t * Math.PI * 3 + rand() * 2), 4) * 0.7
          + (rand() - 0.5) * 200;
        break;
      }
      case "speed": {
        const max = trip.maxSpeedKmh || 80;
        v = max * (0.4 + 0.5 * Math.sin(t * Math.PI * 2 + rand()) + 0.1 * Math.sin(t * Math.PI * 7));
        v = Math.max(0, v);
        break;
      }
      case "coolant": {
        const start = 28 + rand() * 20;
        const end = trip.coolantMaxC || 90;
        v = start + (end - start) * Math.min(1, t * 3) + Math.sin(t * Math.PI * 8) * 1.5;
        break;
      }
      case "egt": {
        const peak = trip.exhaustAfterCatC || 400;
        v = peak * (0.3 + 0.6 * Math.pow(Math.sin(t * Math.PI), 1.5)) + (rand() - 0.5) * 30;
        if (trip.regenEventTs && Math.abs(t - trip.regenEventTs) < 0.18) {
          v = peak;
        }
        break;
      }
      case "soot": {
        const start = trip.dpfSootPct + (trip.distanceKm || 5) * 0.15;
        const end = trip.dpfSootPct;
        v = start + (end - start) * t;
        if (trip.dpfRegenState === "completed" && t > 0.35) {
          v = Math.max(end, end + (start - end) * Math.max(0, 1 - (t - 0.35) * 3));
        }
        break;
      }
      default:
        v = rand();
    }
    out.push(v);
  }
  return out;
}

TRIPS.forEach(tr => {
  if (tr.sources.includes("obd")) {
    tr.pidSeries = {
      rpm: genSeries(tr, "rpm"),
      speed: genSeries(tr, "speed"),
      coolant: genSeries(tr, "coolant"),
      egt: genSeries(tr, "egt"),
      soot: genSeries(tr, "soot"),
    };
  }
});

/* ================================================================
   FULL PID CATALOG — every PID exposed in OBD logs.
   Built from the briefing §10 reference + ECU-family ECM PIDs.
   Each trip gets full per-PID stats (last/first/min/max/mean/...)
   as described in §6.
   ================================================================ */

const PID_CATALOG = [
  // Group: engine basics
  { slug: "rpm",      name: "[ECM] Crankshaft speed",                          short: "RPM",          unit: "rpm",     group: "Motore",      kind: "number" },
  { slug: "speed",    name: "Velocità (GPS)",                                  short: "Speed",        unit: "km/h",    group: "Motore",      kind: "number" },
  { slug: "speed_v",  name: "[ECM] Vehicle speed",                             short: "VSS",          unit: "km/h",    group: "Motore",      kind: "number" },
  { slug: "coolant",  name: "Temperatura liquido raffreddamento motore",       short: "Coolant",      unit: "°C",      group: "Motore",      kind: "number" },
  { slug: "coolant_c",name: "[ECM] Coolant temperature, corrected",            short: "Coolant c.",   unit: "°C",      group: "Motore",      kind: "number" },
  { slug: "oil_t",    name: "[ECM] Oil temperature",                           short: "Oil T",        unit: "°C",      group: "Motore",      kind: "number" },
  { slug: "oil_p",    name: "[ECM] Oil pressure",                              short: "Oil P",        unit: "bar",     group: "Motore",      kind: "number" },
  { slug: "ambient",  name: "Temperatura d'aria ambiente",                     short: "Ambient",      unit: "°C",      group: "Motore",      kind: "number" },
  { slug: "intake",   name: "[ECM] Intake air temperature",                    short: "IAT",          unit: "°C",      group: "Motore",      kind: "number" },
  { slug: "load",     name: "[ECM] Calculated engine load",                    short: "Load",         unit: "%",       group: "Motore",      kind: "number" },
  { slug: "throttle", name: "[ECM] Accelerator pedal position",                short: "Pedal",        unit: "%",       group: "Motore",      kind: "number" },
  { slug: "torque",   name: "[ECM] Engine torque",                             short: "Torque",       unit: "Nm",      group: "Motore",      kind: "number" },
  { slug: "ecu_t",    name: "[ECM] Computer temperature",                      short: "ECU T",        unit: "°C",      group: "Motore",      kind: "number" },
  { slug: "ecu_v",    name: "[ECM] Engine control computer supply voltage",    short: "ECU V",        unit: "V",       group: "Motore",      kind: "number" },

  // Group: fuel
  { slug: "fuel_p",   name: "[ECM] Fuel pressure",                             short: "Rail P",       unit: "bar",     group: "Carburante", kind: "number" },
  { slug: "fuel_p_d", name: "[ECM] Desired high pressure common rail fuel pressure", short: "Rail P set", unit: "bar", group: "Carburante", kind: "number" },
  { slug: "inj_q",    name: "[ECM] Calculated fuel injection amount",          short: "Inj qty",      unit: "mg/str.",group: "Carburante", kind: "number" },
  { slug: "fuel_rate",name: "[ECM] Fuel consumption rate",                     short: "Fuel rate",    unit: "L/h",     group: "Carburante", kind: "number" },
  { slug: "fuel_lvl", name: "[ECM] Fuel tank level",                           short: "Tank",         unit: "%",       group: "Carburante", kind: "number" },
  { slug: "lambda",   name: "[ECM] Lambda",                                    short: "Lambda",       unit: "λ",       group: "Carburante", kind: "number" },

  // Group: aria
  { slug: "boost",    name: "[ECM] Measured turbo boost pressure",             short: "MAP",          unit: "mbar",    group: "Aspirazione",kind: "number" },
  { slug: "boost_s",  name: "[ECM] Set value of turbo boost pressure",         short: "MAP set",      unit: "mbar",    group: "Aspirazione",kind: "number" },
  { slug: "egr",      name: "[ECM] EGR valve position",                        short: "EGR",          unit: "%",       group: "Aspirazione",kind: "number" },
  { slug: "amv",      name: "[ECM] Air metering valve position",               short: "AMV",          unit: "%",       group: "Aspirazione",kind: "number" },
  { slug: "maf",      name: "[ECM] Air flow",                                  short: "MAF",          unit: "mg/str.",group: "Aspirazione",kind: "number" },
  { slug: "vgt",      name: "[ECM] Variable geometry turbo position",          short: "VGT",          unit: "%",       group: "Aspirazione",kind: "number" },

  // Group: DPF/FAP
  { slug: "soot",     name: "[ECM] Soot clogging level of diesel particulate filter", short: "Soot %", unit: "%",     group: "DPF",         kind: "number" },
  { slug: "soot_cl",  name: "[ECM] Closed loop soot load assessment of the diesel particulate filter", short: "Soot CL", unit: "g/L", group: "DPF", kind: "number" },
  { slug: "soot_ol",  name: "[ECM] Open loop soot load assessment of the diesel particulate filter",   short: "Soot OL", unit: "g/L", group: "DPF", kind: "number" },
  { slug: "dpf_dp",   name: "[ECM] Particulate filter differential pressure", short: "ΔP DPF",      unit: "mbar",    group: "DPF",         kind: "number" },
  { slug: "dpf_dp_ds",name: "[ECM] DPF differential pressure sensor signal deviation from reference value", short: "ΔP dev", unit: "mbar", group: "DPF", kind: "number" },
  { slug: "dpf_flow", name: "[ECM] Exhaust gas flow through the particulate filter", short: "DPF flow",unit: "L/h",   group: "DPF",         kind: "number" },
  { slug: "regen_st", name: "[ECM] DPF regeneration status",                  short: "Regen",       unit: "",        group: "DPF",         kind: "discrete" },
  { slug: "regen_en", name: "[ECM] Regeneration enable",                      short: "Regen en.",   unit: "",        group: "DPF",         kind: "bool" },
  { slug: "regen_lt", name: "[ECM] Long-term regeneration capability",        short: "LT cap.",     unit: "%",       group: "DPF",         kind: "number" },
  { slug: "regen_st_c", name: "[ECM] Short-term regeneration capability",     short: "ST cap.",     unit: "%",       group: "DPF",         kind: "number" },
  { slug: "regen_dist", name: "[ECM] Distance traveled since the last regeneration", short: "km since", unit: "km", group: "DPF",         kind: "number" },
  { slug: "regen_avg",  name: "[ECM] Average mileage for the last 10 regenerations", short: "Avg km", unit: "km", group: "DPF",          kind: "number" },
  { slug: "dpf_repl",   name: "[ECM] Mileage remaining before diesel particulate filter replacement", short: "DPF life", unit: "km", group: "DPF", kind: "number" },
  { slug: "dpf_aging",  name: "[ECM] Assessment of thermal aging of the particulate filter", short: "DPF aging", unit: "%", group: "DPF",  kind: "number" },
  { slug: "additive",   name: "[ECM] Total mass of additive accumulated in the diesel particulate filter", short: "Additive", unit: "g", group: "DPF", kind: "number" },
  { slug: "dpf_km",     name: "[ECM] Mileage since last diesel particulate filter replacement", short: "DPF age", unit: "km", group: "DPF", kind: "number" },

  // Group: EGT
  { slug: "egt_b",    name: "[ECM] Exhaust gas temperature before pre-catalytic converter", short: "EGT pre", unit: "°C", group: "Scarico", kind: "number" },
  { slug: "egt_a",    name: "[ECM] Exhaust gas temperature after pre-catalytic converter",  short: "EGT post", unit: "°C", group: "Scarico", kind: "number" },
  { slug: "egt_pre",  name: "[ECM] EGT before turbo",                          short: "EGT turbo",   unit: "°C",      group: "Scarico",     kind: "number" },
  { slug: "egt_dpf_i",name: "[ECM] EGT at DPF inlet",                          short: "EGT DPF in",  unit: "°C",      group: "Scarico",     kind: "number" },
  { slug: "egt_dpf_o",name: "[ECM] EGT at DPF outlet",                         short: "EGT DPF out", unit: "°C",      group: "Scarico",     kind: "number" },
  { slug: "exh_p",    name: "[ECM] Exhaust gas pressure at the outlet of the particulate filter", short: "Exh P out", unit: "mbar", group: "Scarico", kind: "number" },

  // Group: NOx / AdBlue
  { slug: "nox_in",   name: "[ECM] NOx content measured at the inlet of the NOx catalytic converter", short: "NOx in", unit: "ppm", group: "NOx/AdBlue", kind: "number" },
  { slug: "nox_calc", name: "[ECM] Calculated NOx content at the inlet of the NOx catalytic converter", short: "NOx calc", unit: "mg/sec", group: "NOx/AdBlue", kind: "number" },
  { slug: "nox_t",    name: "[ECM] Temperature of the NOx catalytic converter", short: "NOx T",     unit: "°C",      group: "NOx/AdBlue",  kind: "number" },
  { slug: "urea",     name: "[ECM] NOx catalytic converter urea content",      short: "Urea",        unit: "g",       group: "NOx/AdBlue",  kind: "number" },
  { slug: "urea_pct", name: "[ECM] Urea mass fraction",                        short: "Urea %",      unit: "%",       group: "NOx/AdBlue",  kind: "number" },
  { slug: "urea_rem", name: "[ECM] Remaining urea solution in the urea tank",  short: "Urea rem",    unit: "mg",      group: "NOx/AdBlue",  kind: "number" },
  { slug: "urea_v",   name: "[ECM] Volume of urea solution measured in urea tank", short: "AdBlue V", unit: "L",     group: "NOx/AdBlue",  kind: "number" },
  { slug: "urea_km",  name: "[ECM] Vehicle mileage remaining before filling the tank with urea solution", short: "AdBlue km", unit: "km", group: "NOx/AdBlue", kind: "number" },
  { slug: "exh_nox",  name: "[ECM] Evaluation of the exhaust gas flow using the NOx sensor", short: "Exh flow", unit: "L/h", group: "NOx/AdBlue", kind: "number" },

  // Group: olio/manutenzione
  { slug: "oil_dil",  name: "[ECM] Evaluation of the degree of dilution of motor oil", short: "Oil dilution", unit: "%", group: "Olio", kind: "number" },
  { slug: "oil_carb", name: "[ECM] Evaluation of carbon content in engine oil", short: "Oil carbon",  unit: "%",       group: "Olio",        kind: "number" },
  { slug: "oil_km",   name: "[ECM] Distance remaining until the next oil change", short: "Oil km",   unit: "km",      group: "Olio",        kind: "number" },
  { slug: "oil_lvl",  name: "[ECM] Oil level sensor",                          short: "Oil level",   unit: "mm",      group: "Olio",        kind: "number" },

  // Group: batteria/elettrico
  { slug: "bat_v",    name: "[ECM] Minimum battery voltage at startup",        short: "Bat V start", unit: "V",       group: "Batteria",    kind: "number" },
  { slug: "bat_i",    name: "[ECM] Battery current",                           short: "Bat I",       unit: "A",       group: "Batteria",    kind: "number" },
  { slug: "alt",      name: "[ECM] Alternator load value",                     short: "Alt load",    unit: "%",       group: "Batteria",    kind: "number" },
  { slug: "bat_soc",  name: "[ECM] Service battery charge status",             short: "SoC",         unit: "%",       group: "Batteria",    kind: "number" },

  // Group: S&S / drive
  { slug: "ss_state", name: "[ECM] Stop and Start function state",             short: "S&S",         unit: "",        group: "Stop&Start",  kind: "discrete" },
  { slug: "ss_count", name: "[ECM] Engine restart counter by Stop and Start function", short: "S&S #", unit: "",     group: "Stop&Start",  kind: "discrete" },
  { slug: "ss_stop",  name: "[ECM] Engine stop time",                          short: "Stop t",      unit: "sec.",    group: "Stop&Start",  kind: "number" },
  { slug: "cc_set",   name: "[ECM] Cruise control set speed",                  short: "CC set",      unit: "km/h",    group: "Comfort",     kind: "number" },
  { slug: "gear",     name: "[TCU] Selected gear",                             short: "Gear",        unit: "",        group: "Trasmissione", kind: "discrete" },
  { slug: "clutch",   name: "[ECM] Clutch pedal switch",                       short: "Clutch",      unit: "",        group: "Trasmissione", kind: "bool" },
  { slug: "brake",    name: "[ECM] Brake pedal switch",                        short: "Brake",       unit: "",        group: "Trasmissione", kind: "bool" },

  // Group: GPS
  { slug: "gps_alt",  name: "Altitudine GPS",                                  short: "Alt",         unit: "m",       group: "GPS",         kind: "number" },
  { slug: "gps_acc",  name: "Accuratezza GPS",                                 short: "Acc",         unit: "m",       group: "GPS",         kind: "number" },
  { slug: "gps_brg",  name: "Direzione GPS",                                   short: "Heading",     unit: "°",       group: "GPS",         kind: "number" },
  { slug: "odo",      name: "[ECM] Total mileage",                             short: "Odometer",    unit: "km",      group: "Odometro",    kind: "number" },
];

// Pad with generic ECM PIDs until we have ~210 — to mimic real trip catalogs
const PID_FILLER_NAMES = [
  "Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4", "Mean voltage", "Peak hold",
  "Counter A", "Counter B", "Status flag", "Test mode flag", "Calibration ID",
  "Vehicle weight class", "Map angle", "Phase reference",
];
const PID_FILLER_GROUPS = ["Motore", "Sensori", "Diagnostica", "Trasmissione", "Comfort", "Sicurezza"];
const PID_FILLER_UNITS = ["", "%", "V", "A", "Nm", "rpm", "°C", "mbar", "Hz", "ms"];

function rng(seed) {
  let s = seed;
  return () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
}

for (let i = 0; i < 145; i++) {
  const r = rng(i * 91 + 13);
  const slug = `aux_${i.toString().padStart(3, "0")}`;
  const grp = PID_FILLER_GROUPS[Math.floor(r() * PID_FILLER_GROUPS.length)];
  const lbl = PID_FILLER_NAMES[Math.floor(r() * PID_FILLER_NAMES.length)];
  const unit = PID_FILLER_UNITS[Math.floor(r() * PID_FILLER_UNITS.length)];
  PID_CATALOG.push({
    slug,
    name: `[ECM] ${grp} — ${lbl} #${i+1}`,
    short: `${lbl.split(" ")[0]} ${i+1}`,
    unit,
    group: grp,
    kind: unit === "" ? (r() > 0.7 ? "bool" : "discrete") : "number",
  });
}

// Build per-trip PID stats for every PID in catalog.
// Stats schema follows §6: last/first/min/max/mean/mode/samples/kind + meta.
function buildPidValues(trip) {
  const r = rng(trip.start.length + trip.distanceKm * 10);
  const out = {};
  const windowS = trip.durationMin * 60;

  // Real curated values — use trip top-level fields where we have them
  const overrides = {
    rpm:      { last: trip.avgRpm, first: 820, min: 760, max: trip.maxRpm, mean: trip.avgRpm },
    speed:    { last: 0, first: 0, min: 0, max: trip.maxSpeedKmh, mean: trip.avgSpeedKmh },
    speed_v:  { last: 0, first: 0, min: 0, max: trip.maxSpeedKmh, mean: trip.avgSpeedKmh },
    coolant:  { last: trip.coolantMaxC, first: trip.airTempC + 5, min: trip.airTempC + 5, max: trip.coolantMaxC, mean: (trip.coolantMaxC + trip.airTempC + 5) / 2 },
    coolant_c:{ last: trip.coolantMaxC, first: trip.airTempC + 5, min: trip.airTempC + 5, max: trip.coolantMaxC, mean: (trip.coolantMaxC + trip.airTempC + 5) / 2 },
    oil_t:    { last: trip.oilMaxC, first: trip.airTempC + 8, min: trip.airTempC + 8, max: trip.oilMaxC, mean: trip.oilMaxC * 0.7 },
    ambient:  { last: trip.airTempC, first: trip.airTempC, min: trip.airTempC - 1, max: trip.airTempC + 2, mean: trip.airTempC },
    soot:     { last: trip.dpfSootPct, first: trip.dpfSootPct + 0.5, min: Math.max(0, trip.dpfSootPct - 5), max: trip.dpfSootPct + 2, mean: trip.dpfSootPct },
    soot_cl:  { last: trip.dpfClosedSoot, first: trip.dpfClosedSoot, min: trip.dpfClosedSoot * 0.95, max: trip.dpfClosedSoot * 1.05, mean: trip.dpfClosedSoot },
    regen_st: { last: trip.dpfRegenActive, first: 0, min: 0, max: trip.dpfRegenActive >= 1 ? 1 : 0, mean: 0 },
    regen_en: { last: 1, first: 1, min: 0, max: 1, mean: 0.9 },
    regen_lt: { last: trip.dpfRegenCapability, first: trip.dpfRegenCapability, min: trip.dpfRegenCapability - 1, max: trip.dpfRegenCapability + 1, mean: trip.dpfRegenCapability },
    regen_st_c:{ last: trip.dpfRegenCapabilityST, first: trip.dpfRegenCapabilityST, min: trip.dpfRegenCapabilityST - 1, max: trip.dpfRegenCapabilityST + 1, mean: trip.dpfRegenCapabilityST },
    regen_dist:{ last: trip.dpfSinceRegenKm, first: Math.max(0, trip.dpfSinceRegenKm - trip.distanceKm), min: 0, max: trip.dpfSinceRegenKm, mean: trip.dpfSinceRegenKm / 2 },
    regen_avg:{ last: trip.dpfAvgRegenKm, first: trip.dpfAvgRegenKm, min: trip.dpfAvgRegenKm, max: trip.dpfAvgRegenKm, mean: trip.dpfAvgRegenKm },
    dpf_repl: { last: trip.dpfReplaceKm, first: trip.dpfReplaceKm, min: trip.dpfReplaceKm, max: trip.dpfReplaceKm, mean: trip.dpfReplaceKm },
    urea_v:   { last: trip.adblueVolL, first: trip.adblueVolL, min: trip.adblueVolL, max: trip.adblueVolL, mean: trip.adblueVolL },
    urea_km:  { last: trip.adblueRangeKm, first: trip.adblueRangeKm + 5, min: trip.adblueRangeKm, max: trip.adblueRangeKm + 5, mean: trip.adblueRangeKm + 2 },
    egt_b:    { last: trip.exhaustBeforeCatC * 0.5, first: 80, min: 80, max: trip.exhaustBeforeCatC, mean: trip.exhaustBeforeCatC * 0.6 },
    egt_a:    { last: trip.exhaustAfterCatC * 0.5, first: 60, min: 60, max: trip.exhaustAfterCatC, mean: trip.exhaustAfterCatC * 0.55 },
    nox_t:    { last: trip.noxCatTempMaxC * 0.7, first: 80, min: 80, max: trip.noxCatTempMaxC, mean: trip.noxCatTempMaxC * 0.6 },
    bat_v:    { last: 14.1, first: trip.batteryStartupV, min: trip.batteryStartupV, max: 14.4, mean: 14.0 },
    oil_dil:  { last: trip.oilDilutionPct, first: trip.oilDilutionPct, min: trip.oilDilutionPct, max: trip.oilDilutionPct, mean: trip.oilDilutionPct },
    ss_state: { last: trip.ssState, first: 0, min: 0, max: trip.ssState, mean: trip.ssState * 0.6 },
    odo:      { last: trip.odometerKm, first: trip.odometerKm - Math.round(trip.distanceKm), min: trip.odometerKm - Math.round(trip.distanceKm), max: trip.odometerKm, mean: trip.odometerKm },
  };

  for (const pid of PID_CATALOG) {
    let last, first, min, max, mean;
    if (overrides[pid.slug]) {
      const o = overrides[pid.slug];
      last = o.last; first = o.first; min = o.min; max = o.max; mean = o.mean;
    } else {
      // Generate plausible defaults by group
      const base = (() => {
        switch (pid.unit) {
          case "%": return 30 + r() * 50;
          case "°C": return 50 + r() * 40;
          case "mbar": return 1000 + (r() - 0.5) * 800;
          case "bar": return r() * 4;
          case "rpm": return 1000 + r() * 1200;
          case "Nm": return 50 + r() * 200;
          case "V": return 12 + r() * 2.5;
          case "A": return -5 + r() * 30;
          case "L/h": return r() * 80;
          case "km": return r() * 200000;
          case "mg/str.": return r() * 50;
          case "ppm": return r() * 200;
          case "°": return r() * 360;
          case "m": return -10 + r() * 600;
          case "Hz": return r() * 100;
          case "ms": return r() * 20;
          case "λ": return 0.95 + r() * 0.4;
          case "g": return r() * 200;
          case "g/L": return r() * 20;
          case "mg": return r() * 25000;
          case "mm": return 30 + r() * 30;
          case "sec.": return r() * 120;
          case "": // bool/discrete
            return pid.kind === "bool" ? Math.round(r()) : Math.floor(r() * 10);
          default: return r() * 100;
        }
      })();
      const span = base * (0.15 + r() * 0.4);
      min = Math.max(0, base - span);
      max = base + span;
      mean = (min + max) / 2 + (r() - 0.5) * span * 0.2;
      first = mean + (r() - 0.5) * span;
      last = mean + (r() - 0.5) * span;
      if (pid.kind !== "number") {
        last = Math.round(last); first = Math.round(first);
        min = Math.round(min); max = Math.round(max); mean = Math.round(mean * 10) / 10;
      }
    }
    const samples = Math.max(3, Math.round(windowS * (0.3 + r() * 0.9)));
    const coverage = 60 + r() * 40;
    out[pid.slug] = {
      last: round(last, 2),
      first: round(first, 2),
      min: round(min, 2),
      max: round(max, 2),
      mean: round(mean, 2),
      mode: round(mean, 1),
      samples,
      kind: pid.kind,
      first_seen_s: round(r() * 2, 1),
      last_seen_s: round(windowS - r() * 2, 1),
      age_from_trip_end_s: round(r() * 8, 1),
      coverage_pct: round(coverage, 1),
      sample_rate_hz: round(samples / windowS, 2),
      is_stale: r() < 0.04,
    };
  }
  return out;
}

function round(v, d) {
  if (v == null || Number.isNaN(v)) return 0;
  const f = Math.pow(10, d);
  return Math.round(v * f) / f;
}

// Generate per-trip time series (light samples) for ALL PIDs.
// We don't store thousands of points per PID — for sparklines we generate 60.
function buildPidSeries(trip, stats) {
  const r = rng(trip.id.length * 7 + 1);
  const out = {};
  for (const pid of PID_CATALOG) {
    const s = stats[pid.slug];
    if (!s) continue;
    const N = 60;
    const arr = new Array(N);
    const range = s.max - s.min;
    // Choose a shape archetype based on group
    let archetype = "wave";
    if (["Motore"].includes(pid.group) && pid.unit === "rpm") archetype = "spiky";
    else if (pid.unit === "°C") archetype = "rise";
    else if (pid.kind === "bool") archetype = "bool";
    else if (pid.kind === "discrete") archetype = "step";
    else if (pid.group === "DPF") archetype = "drift";
    else if (pid.group === "GPS" && pid.unit === "m") archetype = "wave";

    for (let i = 0; i < N; i++) {
      const t = i / (N - 1);
      let v;
      switch (archetype) {
        case "spiky":
          v = s.min + range * (0.3 + 0.7 * Math.pow(Math.sin(t * Math.PI * 3 + r() * 3), 4));
          break;
        case "rise":
          v = s.min + range * Math.min(1, t * 2.5) + Math.sin(t * Math.PI * 4) * range * 0.04;
          break;
        case "bool":
          v = (Math.sin(t * Math.PI * 6 + r() * 3) > 0.4) ? s.max : s.min;
          break;
        case "step":
          v = s.min + Math.floor((Math.sin(t * Math.PI * 2.4) + 1) * 0.5 * (range + 1));
          break;
        case "drift":
          v = s.min + range * t + Math.sin(t * Math.PI * 12) * range * 0.02;
          break;
        default:
          v = s.min + range * (0.5 + 0.45 * Math.sin(t * Math.PI * 2 + r() * 6));
      }
      arr[i] = v + (r() - 0.5) * range * 0.04;
    }
    out[pid.slug] = arr;
  }
  return out;
}

TRIPS.forEach(tr => {
  if (!tr.sources.includes("obd")) return;
  tr.pidValues = buildPidValues(tr);
  tr.pidSeriesFull = buildPidSeries(tr, tr.pidValues);
});

window.PID_CATALOG = PID_CATALOG;
window.PID_GROUPS = [...new Set(PID_CATALOG.map(p => p.group))];

// Alert dictionary (subset from briefing §4)
const ALERTS = {
  0:  { sev: "critical", label: "Pressione olio motore anomala" },
  1:  { sev: "critical", label: "Temperatura motore troppo elevata" },
  8:  { sev: "warning",  label: "Livello olio motore insufficiente" },
  17: { sev: "warning",  label: "Anomalia ESP / ASR" },
  20: { sev: "warning",  label: "Anomalia filtro gasolio" },
  22: { sev: "info",     label: "Livello carburante basso" },
  25: { sev: "warning",  label: "Anomalia sistema antinquinamento" },
  26: { sev: "warning",  label: "Anomalia ABS" },
  27: { sev: "warning",  label: "Rischio intasamento FAP" },
  29: { sev: "warning",  label: "Additivo FAP insufficiente" },
  46: { sev: "info",     label: "Liquido lavacristalli insufficiente" },
  52: { sev: "warning",  label: "Pressione pneumatici insufficiente" },
  57: { sev: "info",     label: "Rigenerazione FAP in corso" },
};

// Insights synthesized from the rule engine described in §11
function buildInsights(trip) {
  const out = [];
  if (!trip.sources.includes("obd")) return out;

  // Warm-up
  if (trip.airTempC < 22 && trip.coolantMaxC) {
    out.push({
      category: "engine",
      level: "info",
      title: "Profilo di riscaldamento",
      body: `Motore partito freddo (~${trip.airTempC.toFixed(0)}°C ambiente). Liquido raffreddamento ha raggiunto ${trip.coolantMaxC}°C.`,
    });
  }

  // DPF state narrative
  const stateMsg = {
    idle: "Nessuna rigenerazione DPF in questo viaggio.",
    requested: "Rigenerazione DPF richiesta ma non completata — viaggio troppo breve.",
    active: "Rigenerazione DPF in corso durante il viaggio.",
    completed: "Rigenerazione DPF completata con successo durante il viaggio.",
    post_regen: "Rigenerazione DPF appena completata prima di questo viaggio.",
  }[trip.dpfRegenState] || "";
  if (stateMsg) {
    out.push({
      category: "dpf",
      level: trip.dpfRegenState === "requested" ? "warning" : "info",
      title: "Stato rigenerazione DPF",
      body: stateMsg,
    });
  }

  // Soot
  if (trip.dpfSootPct >= 90) {
    out.push({
      category: "dpf",
      level: "critical",
      title: `DPF intasato al ${trip.dpfSootPct}%`,
      body: "Rigenerazione urgente. Pianifica un viaggio extraurbano di almeno 30 minuti a velocità costante.",
    });
  } else if (trip.dpfSootPct >= 70) {
    out.push({
      category: "dpf",
      level: "warning",
      title: `DPF al ${trip.dpfSootPct}%`,
      body: "Presto necessaria rigenerazione. Evita brevi tragitti urbani consecutivi.",
    });
  }

  // ST cap drop
  if (trip.dpfRegenCapabilityST && trip.dpfRegenCapability &&
      trip.dpfRegenCapabilityST < trip.dpfRegenCapability * 0.8) {
    out.push({
      category: "dpf",
      level: "warning",
      title: "Capacità rigenerazione a breve termine ridotta",
      body: `ST ${trip.dpfRegenCapabilityST}% vs LT ${trip.dpfRegenCapability}%. Possibile problema con la rigenerazione post-guida.`,
    });
  }

  // EGT spike
  if (trip.exhaustAfterCatC > 700) {
    out.push({
      category: "engine",
      level: "warning",
      title: "Picco EGT elevato",
      body: `Temperatura gas scarico ${trip.exhaustAfterCatC}°C. Compatibile con rigenerazione attiva.`,
    });
  }

  // Oil dilution
  if (trip.oilDilutionPct > 3) {
    out.push({
      category: "engine",
      level: "critical",
      title: `Diluizione olio ${trip.oilDilutionPct}%`,
      body: "Verifica se le rigenerazioni DPF sono frequenti o incomplete.",
    });
  }

  // Consumption
  if (trip.consumptionL100km != null) {
    if (trip.distanceKm < 5 && trip.consumptionL100km > 6.5) {
      out.push({
        category: "fuel",
        level: "info",
        title: `Tragitto breve: ${trip.consumptionL100km} L/100km`,
        body: "Consumo elevato tipico del riscaldamento motore.",
      });
    } else if (trip.consumptionL100km < 4.7) {
      out.push({
        category: "fuel",
        level: "info",
        title: `Consumo eccellente: ${trip.consumptionL100km} L/100km`,
        body: "Guida fluida e percorso extraurbano favoriscono l'efficienza.",
      });
    }
  }

  return out;
}

TRIPS.forEach(t => { t.insights = buildInsights(t); });

// Cross-trip trend insights
function buildTrendInsights(trips) {
  const out = [];
  const obd = trips.filter(t => t.sources.includes("obd")).sort((a, b) => a.start.localeCompare(b.start));

  // Battery trend
  const volts = obd.map(t => t.batteryStartupV).filter(v => v != null);
  if (volts.length >= 3) {
    const slope = (volts[volts.length - 1] - volts[0]) / volts.length;
    if (slope < -0.02) {
      out.push({
        level: "warning",
        category: "battery",
        title: "Tensione di avviamento in calo",
        body: `Ultime ${volts.length} partenze: da ${volts[0].toFixed(2)} V a ${volts[volts.length-1].toFixed(2)} V. Considera diagnosi batteria.`,
      });
    }
  }

  // Regen interval
  const intervals = obd.map(t => t.dpfAvgRegenKm).filter(v => v != null);
  if (intervals.length >= 3) {
    out.push({
      level: "info",
      category: "dpf",
      title: "Intervallo medio rigenerazioni",
      body: `Stabile a ${intervals[intervals.length-1]} km. Storico ultimi 10: ${Math.min(...intervals)}–${Math.max(...intervals)} km.`,
    });
  }

  // AdBlue
  const adblue = trips.map(t => t.adblueRangeKm).filter(v => v != null);
  if (adblue.length > 0) {
    const last = adblue[adblue.length - 1];
    if (last < 500) {
      out.push({
        level: "critical",
        category: "adblue",
        title: `Autonomia AdBlue: ${last} km`,
        body: "Rifornisci a breve per evitare blocco del motore.",
      });
    }
  }

  // Soot accumulation
  const soot = obd.map(t => ({ s: t.dpfSootPct, km: t.distanceKm, st: t.dpfRegenState }));
  out.push({
    level: "info",
    category: "dpf",
    title: "Accumulo fuliggine medio",
    body: "+0.42 %/km su tragitti urbani brevi; +0.18 %/km su extraurbano. Pattern coerente con uso misto.",
  });

  // Cost
  const myop = trips.filter(t => t.costEur);
  if (myop.length >= 3) {
    const total = myop.reduce((a, t) => a + t.costEur, 0);
    out.push({
      level: "info",
      category: "fuel",
      title: "Spesa carburante",
      body: `Ultimi ${myop.length} viaggi MyOpel: €${total.toFixed(2)} totali, media €${(total/myop.length).toFixed(2)}/viaggio.`,
    });
  }

  return out;
}

const TREND_INSIGHTS = buildTrendInsights(TRIPS);

// Make globally available
window.VEHICLE = VEHICLE;
window.TRIPS = TRIPS;
window.ALERTS = ALERTS;
window.TREND_INSIGHTS = TREND_INSIGHTS;
window.POINTS = POINTS;
