/* global React */
const { useState, useMemo, useEffect, useRef } = React;

/* ============== Iconography (inline SVG, no library) ============== */
const Icon = ({ name, size = 16, className = "" }) => {
  const paths = {
    list:     "M3 6h18M3 12h18M3 18h18",
    map:      "M9 4l-6 2v14l6-2 6 2 6-2V4l-6 2-6-2zm0 0v14m6-12v14",
    gauge:    "M12 14l5-5M3 14a9 9 0 1118 0",
    pid:      "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    fuel:     "M4 22V4a2 2 0 012-2h8a2 2 0 012 2v18H4zm12-12h2a2 2 0 012 2v5a2 2 0 002 2v-9l-3-3",
    trend:    "M3 17l6-6 4 4 8-8M21 7v6m0-6h-6",
    bell:     "M6 8a6 6 0 1112 0c0 7 3 9 3 9H3s3-2 3-9zm6 13a3 3 0 003-3H9a3 3 0 003 3z",
    search:   "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3",
    download: "M12 3v14m-5-5l5 5 5-5M5 21h14",
    settings: "M12 8a4 4 0 100 8 4 4 0 000-8zm9 4l-2-1-1-2 1-2-2-2-2 1-2-1-1-2H8l-1 2-2 1-2-1-2 2 1 2-1 2 2 1 1 2h3l1-2 2-1 2 1 2-1 1-2 2-1z",
    car:      "M5 17h14M5 17a2 2 0 100 0 2 2 0 100 0zm14 0a2 2 0 100 0 2 2 0 100 0zM3 17v-5l2-5h14l2 5v5h-2M7 7h10",
    chart:    "M3 3v18h18M7 14l3-4 4 3 5-7",
    chevron:  "M9 6l6 6-6 6",
    filter:   "M3 5h18l-7 8v6l-4 2v-8z",
    engine:   "M14 14V5a2 2 0 10-4 0v9a4 4 0 104 0z",
    battery:  "M3 9h14a1 1 0 011 1v4a1 1 0 01-1 1H3a1 1 0 01-1-1v-4a1 1 0 011-1zM21 11v2M6 11v2M9 11v2",
    droplet:  "M12 3s6 7 6 11a6 6 0 11-12 0c0-4 6-11 6-11z",
    wrench:   "M15 7a4 4 0 01-5 5l-5 5 2 2 5-5a4 4 0 005-5l-3 3-2-2 3-3z",
    warn:     "M12 3l9 16H3zM12 10v4M12 17h.01",
    info:     "M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8h.01",
    clock:    "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 3",
    euro:     "M17 5.5A7 7 0 007 8.5m10 10a7 7 0 01-10-3M4 10.5h9M4 13.5h8",
    road:     "M4 21L9 3h6l5 18M12 5v3m0 4v3m0 4v2",
    archive:  "M3 4h18v4H3zM5 8v12h14V8M10 12h4",
  };
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={paths[name] || paths.list} />
    </svg>
  );
};

/** Build separate SVG segments so absent samples remain visible gaps. */
const chartPaths = (points, baseline) => {
  const segments = [];
  points.forEach(p => {
    if (!p) { if (segments.length && segments[segments.length - 1].length) segments.push([]); return; }
    if (!segments.length) segments.push([]);
    segments[segments.length - 1].push(p);
  });
  const lines = segments.filter(s => s.length).map(s => ({
    line: s.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" "),
    first: s[0], last: s[s.length - 1],
  }));
  return { line: lines.map(s => s.line).join(" "),
    fill: lines.map(s => `${s.line} L${s.last.x},${baseline} L${s.first.x},${baseline} Z`).join(" ") };
};

/** Compact historical series: preserve all finite readings, without statistical clipping. */
const Sparkline = ({ data, times, height = 50, color = "var(--accent)", showFill = true, animate = true }) => {
  const gradId = React.useId().replace(/:/g, "");
  const values = OBD.chartValues(data);
  const clean = values.filter(v => v != null);
  if (clean.length < 2) return <span className="chart-empty">{clean.length ? "Un solo campione" : "Serie non disponibile"}</span>;
  const min = Math.min(...clean), max = Math.max(...clean), range = max - min || 1;
  const axis = OBD.chartPositions(values, times).times;
  const from = axis[0], span = axis[axis.length - 1] - from || 1;
  const points = values.map((v, i) => v == null ? null : { x: (axis[i] - from) / span * 100, y: 95 - (v - min) / range * 90 });
  const paths = chartPaths(points, 100);
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img"
         aria-label={`Andamento storico, minimo ${OBD.measurement(min)}, massimo ${OBD.measurement(max)}`}
         style={{ height, width: "100%", display: "block" }}>
      <defs><linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={color} stopOpacity="0.3" /><stop offset="100%" stopColor={color} stopOpacity="0" />
      </linearGradient></defs>
      {showFill && <path d={paths.fill} fill={`url(#${gradId})`} />}
      <path d={paths.line} fill="none" stroke={color} strokeWidth="1.4" vectorEffect="non-scaling-stroke"
            className={animate ? "spark-draw" : ""} />
    </svg>
  );
};

/** Inspect an observed series with elapsed time, pointer/touch and keyboard controls. */
const LineChart = ({ data, times, labels, height = 200, color = "var(--accent)", yLabel = "", animate = true }) => {
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);
  const gradId = React.useId().replace(/:/g, "");
  const values = OBD.chartValues(data);
  const clean = values.filter(v => v != null);
  const axis = OBD.chartPositions(values, times);
  useEffect(() => setHover(null), [data, times]);
  if (clean.length < 2) return <div className="chart-empty">{clean.length ? "Un solo campione disponibile" : "Serie non disponibile"}</div>;
  const min = Math.min(...clean), max = Math.max(...clean), range = max - min || 1;
  const w = 600, padX = 20, padTop = 18, padBottom = 26, innerW = w - padX * 2, innerH = height - padTop - padBottom;
  const from = axis.times[0], span = axis.times[axis.times.length - 1] - from || 1;
  const points = values.map((v, i) => v == null ? null : {
    x: padX + (axis.times[i] - from) / span * innerW,
    y: padTop + innerH - (v - min) / range * innerH,
  });
  const paths = chartPaths(points, padTop + innerH);
  const axisLabel = i => labels?.[i] || (axis.timed ? `${OBD.measurement(axis.times[i] / 60, 1)} min` : `campione ${i + 1}`);
  const pointAt = e => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = (e.clientX - rect.left) / rect.width * w;
    let nearest = null, distance = Infinity;
    points.forEach((p, i) => { if (p && Math.abs(p.x - x) < distance) { nearest = i; distance = Math.abs(p.x - x); } });
    setHover(nearest);
  };
  const keyboard = e => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const indices = values.map((v, i) => v == null ? null : i).filter(i => i != null);
    const at = Math.max(0, indices.indexOf(hover));
    const next = e.key === "Home" ? 0 : e.key === "End" ? indices.length - 1 : Math.max(0, Math.min(indices.length - 1, at + (e.key === "ArrowRight" ? 1 : -1)));
    setHover(indices[next]);
  };
  const selected = hover != null ? points[hover] : null;
  return (
    <div className="line-chart">
      <svg ref={svgRef} viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none"
           role="img" tabIndex={0} aria-label={`Grafico ${yLabel}. Frecce per esplorare i campioni; tocca per leggere un valore.`}
           onKeyDown={keyboard} onFocus={() => setHover(values.findIndex(v => v != null))}
           onPointerDown={pointAt} onPointerMove={pointAt}
           style={{ width: "100%", height, display: "block", touchAction: "pan-y" }}>
        <defs><linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" /><stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient></defs>
        {[0, 0.25, 0.5, 0.75, 1].map(t => <line key={t} x1={padX} x2={w-padX} y1={padTop+t*innerH} y2={padTop+t*innerH} stroke="var(--line-soft)" />)}
        <path d={paths.fill} fill={`url(#${gradId})`} />
        <path d={paths.line} fill="none" stroke={color} strokeWidth="1.6" className={animate ? "lc-draw" : ""} />
        <text x={padX} y={12} fill="var(--fg-2)" fontSize="11">{OBD.measurement(max)} {yLabel}</text>
        <text x={padX} y={height-padBottom-3} fill="var(--fg-2)" fontSize="11">{OBD.measurement(min)}</text>
        <text x={padX} y={height-5} fill="var(--fg-2)" fontSize="10">{axisLabel(0)}</text>
        <text x={w-padX} y={height-5} fill="var(--fg-2)" fontSize="10" textAnchor="end">{axisLabel(values.length-1)}</text>
        {selected && <circle cx={selected.x} cy={selected.y} r={4} fill={color} />}
      </svg>
      <div className="chart-readout" aria-live="polite">{selected
        ? `${axisLabel(hover)} · ${OBD.measurement(values[hover], 2)} ${yLabel}`
        : `${values.length} campioni visualizzati · tocca o usa le frecce per esplorare`}</div>
    </div>
  );
};

/** Inspect daily distances using pointer/touch or a keyboard. */
const BarChart = ({ data, height = 120, color = "var(--accent)", yLabel = "" }) => {
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);
  if (!data?.length) return <div className="chart-empty">Nessun dato nel periodo</div>;
  const w = 600, padX = 10, padTop = 16, padBot = 18;
  const innerW = w - padX * 2, innerH = height - padTop - padBot;
  const max = Math.max(...data.map(d => d.value), 1), step = innerW / data.length;
  const inspect = e => {
    const rect = svgRef.current.getBoundingClientRect();
    setHover(Math.max(0, Math.min(data.length - 1, Math.floor(((e.clientX-rect.left)/rect.width*w-padX)/step))));
  };
  return <div>
    <svg ref={svgRef} viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" tabIndex={0} role="img"
         aria-label="Distanze giornaliere. Frecce o tocco per leggere i valori."
         style={{ width: "100%", height, display: "block", touchAction: "pan-y" }}
         onPointerDown={inspect} onPointerMove={inspect} onFocus={() => setHover(0)}
         onKeyDown={e => { if (["ArrowLeft", "ArrowRight"].includes(e.key)) { e.preventDefault(); setHover(Math.max(0, Math.min(data.length-1, (hover ?? 0) + (e.key === "ArrowRight" ? 1 : -1)))); } }}>
      <text x={padX} y={12} fill="var(--fg-2)" fontSize="10">{OBD.measurement(max, 0)} {yLabel}</text>
      {data.map((d, i) => {
        const h = Math.max(d.value > 0 ? 2 : 0, d.value / max * innerH);
        return <rect key={i} x={padX+i*step+1} y={padTop+innerH-h} width={Math.max(2,step-2)} height={h} rx={2} fill={color} opacity={hover===i?1:0.75} />;
      })}
      <text x={padX} y={height-3} fill="var(--fg-2)" fontSize="10">{data[0].label}</text>
      <text x={w-padX} y={height-3} fill="var(--fg-2)" fontSize="10" textAnchor="end">{data[data.length-1].label}</text>
    </svg>
    <div className="chart-readout" aria-live="polite">{hover != null && data[hover] ? `${data[hover].label} · ${OBD.measurement(data[hover].value)} ${yLabel}` : "Tocca o usa le frecce per esplorare"}</div>
  </div>;
};

/* ============== Radial gauge ============== */
/* thresholds: color shifts to warn/crit as the value fills — right for "high
   is bad" quantities (soot, temperature). Pass false for fuel/charge levels. */
const RadialGauge = ({ value, max = 100, label = "%", strokeColor = "var(--accent)", duration = 900, decimals = 0, thresholds = true }) => {
  const known = typeof value === "number" && Number.isFinite(value);
  // Animate known observations only. Missing measurements remain unavailable.
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!known) { setShown(0); return; }
    let raf, start;
    const animDur = (document.documentElement.dataset.anim === "off" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) ? 0 : duration;
    if (animDur === 0) { setShown(value); return; }
    const ease = (t) => 1 - Math.pow(1 - t, 3); // ease-out cubic
    const step = (ts) => {
      if (!start) start = ts;
      const t = Math.min(1, (ts - start) / animDur);
      setShown(value * ease(t));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => raf && cancelAnimationFrame(raf);
  }, [value, duration, known]);

  const pct = Math.max(0, Math.min(1, shown / max));
  const r = 50, c = 60;
  const circ = 2 * Math.PI * r;
  const dash = circ * pct;
  const finalPct = value / max;
  const color = !thresholds ? strokeColor
    : finalPct >= 0.9 ? "var(--crit)" : finalPct >= 0.7 ? "var(--warn)" : strokeColor;
  const gid = `gauge-${String(strokeColor).replace(/[^\w]/g, "")}-${Math.round(finalPct * 100)}`;
  return (
    <div className="gauge-radial">
      <svg viewBox="0 0 120 120">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.55" />
            <stop offset="100%" stopColor={color} />
          </linearGradient>
        </defs>
        <circle cx={c} cy={c} r={r} fill="none" stroke="oklch(0.5 0.02 256 / 0.18)" strokeWidth="9" />
        <circle cx={c} cy={c} r={r} fill="none" stroke={`url(#${gid})`}
                strokeWidth="9" strokeLinecap="round"
                strokeDasharray={`${dash} ${circ - dash}`} />
      </svg>
      <div className="gauge-value">
        <div className="v">{known ? shown.toFixed(decimals) : "—"}</div>
        <div className="l">{label}</div>
      </div>
    </div>
  );
};

/* ============== Animated number (count-up) ============== */
const AnimatedNumber = ({ value, decimals = 0, duration = 700, prefix = "", suffix = "" }) => {
  const [shown, setShown] = useState(0);
  const prev = useRef(0);
  useEffect(() => {
    let raf, start;
    const animDur = (document.documentElement.dataset.anim === "off" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) ? 0 : duration;
    if (animDur === 0) { setShown(value); prev.current = value; return; }
    const from = prev.current;
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const step = (ts) => {
      if (!start) start = ts;
      const t = Math.min(1, (ts - start) / animDur);
      setShown(from + (value - from) * ease(t));
      if (t < 1) raf = requestAnimationFrame(step);
      else prev.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => raf && cancelAnimationFrame(raf);
  }, [value, duration]);
  const fmt = (typeof value === "number")
    ? shown.toLocaleString("it-IT", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
    : value;
  return <span>{prefix}{fmt}{suffix}</span>;
};

/* ============== Animated bar ============== */
const AnimatedBar = ({ value, max = 100, color = "var(--accent)", height = 6, duration = 800, gradient = false }) => {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const animOff = document.documentElement.dataset.anim === "off";
    if (animOff) { setShown(value); return; }
    const id = requestAnimationFrame(() => setShown(value));
    return () => cancelAnimationFrame(id);
  }, [value]);
  const pct = Math.max(0, Math.min(100, (shown / max) * 100));
  return (
    <div className="bar-track" style={{ height }}>
      <div
        className={`bar-fill ${gradient ? "" : "solo-accent"}`}
        style={{
          width: `${pct}%`,
          background: gradient ? undefined : color,
          transition: `width ${duration}ms cubic-bezier(.16,1,.3,1)`,
        }}
      />
    </div>
  );
};

/* ============== Map (Leaflet) ============== */
const TripMap = ({ trip, allTrips = null, height = 340 }) => {
  const ref = useRef(null);
  const mapRef = useRef(null);
  const layersRef = useRef([]);

  useEffect(() => {
    if (!ref.current || !window.L) return;
    if (!mapRef.current) {
      mapRef.current = window.L.map(ref.current, {
        zoomControl: true,
        attributionControl: true,
        preferCanvas: true,
      }).setView([40.572, 14.854], 11);
      window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
        maxZoom: 18,
      }).addTo(mapRef.current);
    }
    // Clear previous layers
    layersRef.current.forEach(l => mapRef.current.removeLayer(l));
    layersRef.current = [];

    if (allTrips) {
      allTrips.forEach(t => {
        if (!t.track) return;
        const isActive = trip && t.id === trip.id;
        const line = window.L.polyline(t.track, {
          color: isActive ? "oklch(0.86 0.16 200)" : "oklch(0.5 0.04 240)",
          weight: isActive ? 4 : 2,
          opacity: isActive ? 1 : 0.5,
        }).addTo(mapRef.current);
        layersRef.current.push(line);
      });
      const all = allTrips.filter(t => t.track).flatMap(t => t.track);
      if (all.length) {
        mapRef.current.fitBounds(all, { padding: [20, 20] });
      }
    } else if (trip && Array.isArray(trip.track) && trip.track.length >= 2) {
      const line = window.L.polyline(trip.track, {
        color: "oklch(0.86 0.16 200)",
        weight: 4,
        opacity: 1,
      }).addTo(mapRef.current);
      layersRef.current.push(line);

      // Start marker
      const start = window.L.circleMarker(trip.track[0], {
        radius: 6, color: "oklch(0.78 0.14 152)", fillColor: "oklch(0.78 0.14 152)",
        fillOpacity: 0.9, weight: 2,
      }).addTo(mapRef.current);
      const end = window.L.circleMarker(trip.track[trip.track.length - 1], {
        radius: 6, color: "oklch(0.68 0.20 22)", fillColor: "oklch(0.68 0.20 22)",
        fillOpacity: 0.9, weight: 2,
      }).addTo(mapRef.current);
      layersRef.current.push(start, end);
      mapRef.current.fitBounds(trip.track, { padding: [30, 30] });
    }
  }, [trip, allTrips]);

  useEffect(() => {
    // Resize map when container resizes
    const obs = new ResizeObserver(() => {
      if (mapRef.current) mapRef.current.invalidateSize();
    });
    if (ref.current) obs.observe(ref.current);
    return () => { obs.disconnect(); mapRef.current?.remove(); mapRef.current = null; };
  }, []);

  return <div ref={ref} style={{ height, width: "100%" }} />;
};

/* ============== DPF state pill ============== */
const DpfPill = ({ state }) => {
  const labels = {
    idle: "Nessuna regen osservata",
    unknown: "Stato DPF non determinabile",
    requested: "Regen richiesta",
    active: "Regen osservata",
    completed: "Regen completata",
    post_regen: "Post-regen",
  };
  if (!state) return null;
  return (
    <span className={`dpf-pill ${state}`}>
      <span className="dot"></span>
      {labels[state] || state}
    </span>
  );
};

/* ============== Alert chip ============== */
const AlertChip = ({ code }) => {
  const a = (typeof ALERTS !== "undefined" ? ALERTS : {})[code];
  if (!a) return <span className="alert-chip info">#{code}</span>;
  return (
    <span className={`alert-chip ${a.sev}`} title={a.label}>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.7 }}>#{code}</span>
      {a.label}
    </span>
  );
};

/* ============== Stat card ============== */
const StatCard = ({ label, value, unit, sub, icon }) => (
  <div className="stat-card">
    {icon && <div className="stat-ico"><Icon name={icon} size={15} /></div>}
    <div className="lbl">{label}</div>
    <div className="val">
      {value}
      {unit && <span className="unit">{unit}</span>}
    </div>
    {sub && <div className="delta">{sub}</div>}
  </div>
);

/* ============== Trip card (list item) ============== */
const TripCard = ({ trip, active, onClick }) => {
  const d = new Date(trip.start);
  const dateStr = d.toLocaleDateString("it-IT", { day: "2-digit", month: "short", year: "2-digit" });
  const timeStr = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  return (
    <button type="button" className={`trip-card ${active ? "active" : ""}`} onClick={onClick} aria-pressed={!!active}>
      <div className="trip-card-head">
        <span className="trip-date">{dateStr}</span>
        <span className="trip-time">{timeStr}</span>
        <span className="trip-sources">
          {trip.sources.includes("obd")    && <span className="src-tag obd">OBD</span>}
          {trip.sources.includes("myopel") && <span className="src-tag myopel">MYOPEL</span>}
        </span>
      </div>
      <div className="trip-card-stats">
        <div className="trip-stat">
          <span className="lbl">Distanza</span>
          <span className="val">{trip.distanceKm?.toFixed(1) ?? "—"}<span className="unit"> km</span></span>
        </div>
        <div className="trip-stat">
          <span className="lbl">Tempo osservato</span>
          <span className="val">{trip.durationMin?.toFixed(0) ?? "—"}<span className="unit"> min</span></span>
        </div>
        <div className="trip-stat">
          <span className="lbl">Vel. osservata</span>
          <span className="val">{trip.avgSpeedKmh?.toFixed(0) ?? "—"}<span className="unit"> km/h</span></span>
        </div>
        <div className="trip-stat">
          <span className="lbl">Consumo</span>
          <span className="val">{trip.consumptionKmL?.toFixed(1) ?? "—"}<span className="unit"> km/L</span></span>
        </div>
      </div>
      <div className="trip-card-foot">
        {trip.dpfRegenState && <DpfPill state={trip.dpfRegenState} />}
        {trip.alerts && trip.alerts.length > 0 && (
          <div className="row" style={{ gap: 4 }}>
            {trip.alerts.slice(0, 2).map(c => <AlertChip key={c} code={c} />)}
            {trip.alerts.length > 2 && <span className="muted" style={{ fontSize: 11 }}>+{trip.alerts.length - 2}</span>}
          </div>
        )}
      </div>
    </button>
  );
};

/* ============== Insight card ============== */
const INSIGHT_ICON = {
  dpf: "filter", fuel: "fuel", engine: "engine", battery: "battery",
  adblue: "droplet", service: "wrench", tyres: "gauge",
};
const INSIGHT_CAT_LABEL = {
  dpf: "DPF / FAP", fuel: "Carburante", engine: "Motore", battery: "Batteria",
  adblue: "AdBlue", service: "Tagliando", tyres: "Gomme",
};
const INSIGHT_COLOR = {
  critical: "var(--crit)", warning: "var(--warn)", info: "var(--accent)",
};
const InsightCard = ({ insight }) => {
  const icon = INSIGHT_ICON[insight.category]
            || (insight.level === "critical" || insight.level === "warning" ? "warn" : "info");
  return (
    <div className={`insight ${insight.level}`}>
      <div className="insight-ico"><Icon name={icon} size={17} /></div>
      <div className="insight-body">
        <div className="insight-cat">{INSIGHT_CAT_LABEL[insight.category] || insight.category}</div>
        <div className="insight-title">{insight.title}</div>
        <div className="insight-text">{insight.body}</div>
        {insight.series && insight.series.length >= 3 && (
          <div className="insight-spark">
            <Sparkline data={insight.series} height={34}
                       color={INSIGHT_COLOR[insight.level] || "var(--accent)"} animate={false} />
            {insight.unit && <span className="insight-unit mono">{insight.unit}</span>}
          </div>
        )}
      </div>
    </div>
  );
};

Object.assign(window, {
  Icon, Sparkline, LineChart, BarChart, RadialGauge, TripMap,
  DpfPill, AlertChip, StatCard, TripCard, InsightCard,
  AnimatedNumber, AnimatedBar,
});
