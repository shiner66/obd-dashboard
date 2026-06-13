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
  };
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={paths[name] || paths.list} />
    </svg>
  );
};

/* Filter obvious outliers using 3×IQR rule; null values are kept as gaps */
const filterOutliers = (data) => {
  if (!data || data.length < 6) return data;
  const clean = data.filter(v => v != null && isFinite(v));
  if (clean.length < 4) return data;
  const sorted = [...clean].sort((a, b) => a - b);
  const q1 = sorted[Math.floor(sorted.length * 0.25)];
  const q3 = sorted[Math.floor(sorted.length * 0.75)];
  const iqr = q3 - q1;
  if (iqr === 0) return data;
  const lo = q1 - 3 * iqr;
  const hi = q3 + 3 * iqr;
  return data.map(v => (v != null && isFinite(v) && v >= lo && v <= hi) ? v : null);
};

/* ============== Sparkline (tiny SVG line chart) ============== */
const Sparkline = ({ data, height = 50, color = "var(--accent)", showFill = true, showMinMax = true, animate = true }) => {
  if (!data || data.length < 2) return null;
  const filtered = filterOutliers(data) || data;
  const cleanVals = filtered.filter(v => v != null && isFinite(v));
  if (cleanVals.length < 2) return null;
  const min = Math.min(...cleanVals);
  const max = Math.max(...cleanVals);
  const range = max - min || 1;
  const w = 100;
  const pts = filtered.map((v, i) => v != null ? { x: (i / (filtered.length - 1)) * w, y: 100 - ((v - min) / range) * 100 } : null);
  const path = pts.reduce((acc, pt, i) => {
    if (pt == null) return acc + ` M${pts.find((p, j) => j > i && p != null)?.x ?? 0},${pts.find((p, j) => j > i && p != null)?.y ?? 0}`;
    const prev = i === 0 || pts[i-1] == null;
    return acc + ` ${prev ? 'M' : 'L'}${pt.x.toFixed(2)},${pt.y.toFixed(2)}`;
  }, "").trim();
  // Fill path: connect only non-null segments to baseline
  const firstNonNull = pts.find(p => p != null);
  const lastNonNull = [...pts].reverse().find(p => p != null);
  const fillPath = firstNonNull && lastNonNull
    ? path + ` L${lastNonNull.x.toFixed(2)},100 L${firstNonNull.x.toFixed(2)},100 Z`
    : path;
  // Live pulse marker on the last non-null point
  const lastX = lastNonNull ? lastNonNull.x : w;
  const lastY = lastNonNull ? lastNonNull.y : 100;
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ height, width: "100%", display: "block", overflow: "visible" }}>
      <defs>
        <linearGradient id={`sgrad-${color.replace(/[^\w]/g, "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {showFill && <path d={fillPath} fill={`url(#sgrad-${color.replace(/[^\w]/g, "")})`} className={animate ? "spark-fill-in" : ""} />}
      <path d={path} fill="none" stroke={color} strokeWidth="1.4" vectorEffect="non-scaling-stroke"
            className={animate ? "spark-draw" : ""} />
      {animate && (
        <circle cx={lastX} cy={lastY} r="1.6" fill={color}>
          <animate attributeName="r" values="1.6;3;1.6" dur="2.4s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0.4;1" dur="2.4s" repeatCount="indefinite" />
        </circle>
      )}
    </svg>
  );
};

/* ============== Bigger chart (axis-less line) ============== */
const LineChart = ({ data, height = 200, color = "var(--accent)", yLabel, accent = "accent", animate = true }) => {
  if (!data || data.length < 2) return null;
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);

  const filteredData = filterOutliers(data) || data;
  const cleanVals = filteredData.filter(v => v != null && isFinite(v));
  if (cleanVals.length < 2) return null;
  const min = Math.min(...cleanVals);
  const max = Math.max(...cleanVals);
  const range = (max - min) || 1;
  const w = 600;
  const h = height;
  const padX = 16, padY = 12;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;

  const pts = filteredData.map((v, i) =>
    v != null ? { x: padX + (i / (filteredData.length - 1)) * innerW, y: padY + innerH - ((v - min) / range) * innerH } : null
  );
  const path = pts.reduce((acc, pt, i) => {
    if (pt == null) return acc + ` M${pts.find((p, j) => j > i && p != null)?.x ?? padX},${pts.find((p, j) => j > i && p != null)?.y ?? (padY + innerH)}`;
    const prev = i === 0 || pts[i-1] == null;
    return acc + ` ${prev ? 'M' : 'L'}${pt.x.toFixed(2)},${pt.y.toFixed(2)}`;
  }, "").trim();

  const firstNonNull = pts.find(p => p != null);
  const lastNonNull = [...pts].reverse().find(p => p != null);
  const fillPath = firstNonNull && lastNonNull
    ? path + ` L${lastNonNull.x.toFixed(2)},${h - padY} L${firstNonNull.x.toFixed(2)},${h - padY} Z`
    : path;

  const gridY = [0, 0.25, 0.5, 0.75, 1].map(t => padY + t * innerH);
  const gradId = `lc-${color.replace(/[^\w]/g, "")}`;

  const handleMouseMove = (e) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / rect.width) * w;
    const step = innerW / Math.max(filteredData.length - 1, 1);
    const idx = Math.max(0, Math.min(filteredData.length - 1, Math.round((mouseX - padX) / step)));
    const val = filteredData[idx];
    if (val == null) { setHover(null); return; }
    const cx = padX + (idx / (filteredData.length - 1)) * innerW;
    const cy = padY + innerH - ((val - min) / range) * innerH;
    setHover({ idx, val, cx, cy });
  };

  return (
    <svg ref={svgRef} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         style={{ width: "100%", height, display: "block" }}
         onMouseMove={handleMouseMove} onMouseLeave={() => setHover(null)}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {gridY.map((y, i) => (
        <line key={i} x1={padX} x2={w - padX} y1={y} y2={y}
              stroke="var(--line-soft)" strokeWidth="1" strokeDasharray={i === 0 || i === gridY.length - 1 ? "" : "2,3"} />
      ))}
      <path d={fillPath} fill={`url(#${gradId})`} className={animate ? "lc-fill-in" : ""} />
      <path d={path} fill="none" stroke={color} strokeWidth="1.6" className={animate ? "lc-draw" : ""} />
      {/* y labels */}
      <text x={padX + 4} y={padY + 4} fill="var(--fg-3)" fontSize="10" fontFamily="var(--font-mono)">{max.toFixed(1)}</text>
      <text x={padX + 4} y={h - padY - 2} fill="var(--fg-3)" fontSize="10" fontFamily="var(--font-mono)">{min.toFixed(1)}</text>
      {yLabel && <text x={w - padX} y={padY + 4} fill="var(--fg-3)" fontSize="10" fontFamily="var(--font-mono)" textAnchor="end">{yLabel}</text>}
      {hover && (
        <g>
          <circle cx={hover.cx} cy={hover.cy} r={3} fill={color} />
          <rect x={hover.cx + 6} y={hover.cy - 16} width={52} height={18} rx={4} fill="var(--bg-3)" opacity={0.95} />
          <text x={hover.cx + 10} y={hover.cy - 4} fill="var(--fg-0)" fontSize="10" fontFamily="var(--font-mono)">
            {hover.val.toFixed(2)}
          </text>
        </g>
      )}
    </svg>
  );
};

/* ============== Radial gauge ============== */
const RadialGauge = ({ value, max = 100, label = "%", strokeColor = "var(--accent)", duration = 900 }) => {
  // Animate from 0 to value on mount
  const [shown, setShown] = useState(0);
  useEffect(() => {
    let raf, start;
    const animDur = document.documentElement.dataset.anim === "off" ? 0 : duration;
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
  }, [value, duration]);

  const pct = Math.max(0, Math.min(1, shown / max));
  const r = 50, c = 60;
  const circ = 2 * Math.PI * r;
  const dash = circ * pct;
  const finalPct = value / max;
  const color = finalPct >= 0.9 ? "var(--crit)" : finalPct >= 0.7 ? "var(--warn)" : strokeColor;
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
        <div className="v">{Math.round(shown)}</div>
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
    const animDur = document.documentElement.dataset.anim === "off" ? 0 : duration;
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
    } else if (trip && trip.track) {
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
    return () => obs.disconnect();
  }, []);

  return <div ref={ref} style={{ height, width: "100%" }} />;
};

/* ============== DPF state pill ============== */
const DpfPill = ({ state }) => {
  const labels = {
    idle: "DPF idle",
    requested: "Regen richiesta",
    active: "Regen attiva",
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
const StatCard = ({ label, value, unit, sub }) => (
  <div className="stat-card">
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
    <div className={`trip-card ${active ? "active" : ""}`} onClick={onClick}>
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
          <span className="lbl">Durata</span>
          <span className="val">{trip.durationMin?.toFixed(0) ?? "—"}<span className="unit"> min</span></span>
        </div>
        <div className="trip-stat">
          <span className="lbl">Media</span>
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
    </div>
  );
};

/* ============== Insight card ============== */
const InsightCard = ({ insight }) => (
  <div className={`insight ${insight.level}`}>
    <div className="insight-cat">{insight.category}</div>
    <div className="insight-body">
      <div className="insight-title">{insight.title}</div>
      <div className="insight-text">{insight.body}</div>
    </div>
  </div>
);

Object.assign(window, {
  Icon, Sparkline, LineChart, RadialGauge, TripMap,
  DpfPill, AlertChip, StatCard, TripCard, InsightCard,
  AnimatedNumber, AnimatedBar,
});
